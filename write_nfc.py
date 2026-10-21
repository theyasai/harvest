# -*- coding: utf-8 -*-
"""
NFC シール / カード 書き込みツール（ACR122U + NTAG213/215/216 用）

使い方:
    python write_nfc.py

メニューから選びます:
    1. コンテナシールを連続で書き込む（0001〜1000）
    2. 作業者カードを書き込む
    3. 端末番号設定カードを書き込む
    4. 書き込み内容を確認する（読み取りのみ）

必要なもの:
    pip install pyscard
"""

import csv
import os
import sys
import time
import urllib.parse
from datetime import datetime

try:
    from smartcard.System import readers
    from smartcard.Exceptions import NoCardException, CardConnectionException
except ImportError:
    print("pyscard が入っていません。次のコマンドを実行してください:")
    print("    pip install pyscard")
    sys.exit(1)

# ====== 設定 ======================================================
BASE_URL = "https://theyasai.github.io/harvest/"
CONTAINER_MIN = 1
CONTAINER_MAX = 1000
DEVICE_COUNT = 5

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(HERE, "write_log.csv")        # 書き込み記録
PROGRESS_FILE = os.path.join(HERE, "progress.txt")    # 次に書く番号（中断しても続きから）
# ================================================================

# NDEF の URI 省略コード: 0x04 = "https://"
URI_PREFIX_HTTPS = 0x04
URI_PREFIXES = {0x00: "", 0x01: "http://www.", 0x02: "https://www.", 0x03: "http://", 0x04: "https://"}


class NfcError(Exception):
    pass


# ------------------------------------------------------------------
# カードリーダー操作
# ------------------------------------------------------------------
def get_reader():
    rs = readers()
    if not rs:
        print("カードリーダーが見つかりません。ACR122U を USB に挿してください。")
        sys.exit(1)
    for r in rs:
        if "ACR122" in str(r) or "ACS" in str(r):
            return r
    return rs[0]


def transmit(conn, apdu):
    data, sw1, sw2 = conn.transmit(apdu)
    if (sw1, sw2) != (0x90, 0x00):
        raise NfcError("カードとの通信に失敗しました (SW=%02X%02X)" % (sw1, sw2))
    return data


def wait_card(reader):
    """カードが置かれるまで待って、接続を返す"""
    while True:
        conn = reader.createConnection()
        try:
            conn.connect()
            return conn
        except (NoCardException, CardConnectionException):
            time.sleep(0.2)


def wait_remove(reader):
    """カードが取り除かれるまで待つ"""
    while True:
        conn = reader.createConnection()
        try:
            conn.connect()
            conn.disconnect()
            time.sleep(0.2)
        except (NoCardException, CardConnectionException):
            return


def get_uid(conn):
    return "".join("%02X" % b for b in transmit(conn, [0xFF, 0xCA, 0x00, 0x00, 0x00]))


def read_pages(conn, page):
    """指定ページから 4ページ（16バイト）読む"""
    return transmit(conn, [0xFF, 0xB0, 0x00, page, 0x10])


def write_page(conn, page, data4):
    transmit(conn, [0xFF, 0xD6, 0x00, page, 0x04] + list(data4))


# ------------------------------------------------------------------
# NDEF（NFC に URL を書き込むときの決まった形式）
# ------------------------------------------------------------------
def build_ndef(url):
    if not url.startswith("https://"):
        raise NfcError("URL は https:// で始まる必要があります")
    payload = bytes([URI_PREFIX_HTTPS]) + url[len("https://"):].encode("utf-8")
    if len(payload) > 250:
        raise NfcError("URL が長すぎます")
    record = bytes([0xD1, 0x01, len(payload), 0x55]) + payload
    tlv = bytes([0x03, len(record)]) + record + bytes([0xFE])
    while len(tlv) % 4:
        tlv += b"\x00"
    return tlv


def check_cc(conn):
    """Capability Container（ページ3）を確認して、書き込める容量を返す"""
    cc = read_pages(conn, 3)[0:4]
    if cc == [0, 0, 0, 0]:
        # 未フォーマットのタグ。NTAG213 として初期化（ページ3は一度しか書けない）
        write_page(conn, 3, [0xE1, 0x10, 0x12, 0x00])
        cc = [0xE1, 0x10, 0x12, 0x00]
    if cc[0] != 0xE1:
        raise NfcError("NTAG213/215/216 ではないようです（対応していないタグ）")
    if cc[3] & 0x0F:
        raise NfcError("このタグはロック（書き込み禁止）されています")
    return cc[2] * 8


def read_url(conn):
    """タグに書かれている URL を読む（無ければ None）"""
    data = []
    for page in range(4, 4 + 16 * 4, 4):  # 最大256バイトまで読む
        try:
            data += read_pages(conn, page)
        except NfcError:
            break
        if 0xFE in data:
            break
    i = 0
    while i < len(data):
        t = data[i]
        if t == 0x00:
            i += 1
            continue
        if t == 0xFE or i + 1 >= len(data):
            return None
        length = data[i + 1]
        if t == 0x03:
            rec = data[i + 2:i + 2 + length]
            if len(rec) >= 5 and rec[3] == 0x55:
                plen = rec[2]
                payload = bytes(rec[4:4 + plen])
                prefix = URI_PREFIXES.get(payload[0], "")
                return prefix + payload[1:].decode("utf-8", errors="replace")
            return None
        i += 2 + length
    return None


def write_url(conn, url):
    ndef = build_ndef(url)
    capacity = check_cc(conn)
    if len(ndef) > capacity:
        raise NfcError("容量不足です（必要 %d バイト / 容量 %d バイト）" % (len(ndef), capacity))
    for i in range(0, len(ndef), 4):
        write_page(conn, 4 + i // 4, ndef[i:i + 4])
    # 書き込めたか読み直して確認
    got = read_url(conn)
    if got != url:
        raise NfcError("書き込み確認に失敗しました（読み取り: %s）" % got)


# ------------------------------------------------------------------
# 記録ファイル
# ------------------------------------------------------------------
def log(kind, value, uid, url):
    new = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["書き込み日時", "種類", "内容", "UID", "URL"])
        w.writerow([datetime.now().strftime("%Y/%m/%d %H:%M:%S"), kind, value, uid, url])


def load_progress():
    try:
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return CONTAINER_MIN


def save_progress(n):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        f.write(str(n))


def ask_int(prompt, default, lo, hi):
    while True:
        s = input("%s [%d]: " % (prompt, default)).strip()
        if not s:
            return default
        if s.isdigit() and lo <= int(s) <= hi:
            return int(s)
        print("  %d〜%d の数字を入力してください" % (lo, hi))


def container_url(n):
    return BASE_URL + "?c=%04d" % n


def container_from_url(url):
    if url and url.startswith(BASE_URL + "?c="):
        return url[len(BASE_URL + "?c="):]
    return None


# ------------------------------------------------------------------
# 1. コンテナシール連続書き込み
# ------------------------------------------------------------------
def mode_containers(reader):
    start = ask_int("開始番号", load_progress(), CONTAINER_MIN, CONTAINER_MAX)
    end = ask_int("終了番号", CONTAINER_MAX, start, CONTAINER_MAX)
    print()
    print("シールを1枚ずつリーダーに置いてください。")
    print("「OK」が出たらシールを外し、次のシールを置きます。中断は Ctrl + C です。")
    print()

    n = start
    while n <= end:
        url = container_url(n)
        print("▶ コンテナ %04d のシールを置いてください…" % n, end="", flush=True)
        conn = wait_card(reader)
        try:
            uid = get_uid(conn)
            existing = read_url(conn)
            old = container_from_url(existing)
            if old == "%04d" % n:
                print(" すでに %04d が書き込まれています（スキップ）" % n)
            else:
                if old is not None:
                    print()
                    ans = input("  このシールには %s が書かれています。%04d で上書きしますか？ (y/n): " % (old, n))
                    if ans.strip().lower() != "y":
                        print("  上書きしませんでした。別のシールを置いてください。")
                        conn.disconnect()
                        wait_remove(reader)
                        continue
                write_url(conn, url)
                log("コンテナ", "%04d" % n, uid, url)
                print(" OK  (UID %s)" % uid)
            n += 1
            save_progress(n if n <= CONTAINER_MAX else CONTAINER_MIN)
        except NfcError as e:
            print()
            print("  ✖ エラー: %s" % e)
            print("  シールを置き直してください。（同じ番号でやり直します）")
        except CardConnectionException:
            print()
            print("  ✖ 途中でシールが離れました。置き直してください。")
        finally:
            try:
                conn.disconnect()
            except Exception:
                pass
        wait_remove(reader)

    print()
    print("完了しました！ %04d 〜 %04d" % (start, end))


# ------------------------------------------------------------------
# 2. 作業者カード
# ------------------------------------------------------------------
def mode_worker(reader):
    print("作業者名を入力してください（空のまま Enter でメニューに戻る）")
    while True:
        name = input("作業者名: ").strip()
        if not name:
            return
        url = BASE_URL + "?w=" + urllib.parse.quote(name)
        print("▶ %s さんのカードを置いてください…" % name, end="", flush=True)
        write_one(reader, "作業者", name, url)


# ------------------------------------------------------------------
# 3. 端末番号設定カード
# ------------------------------------------------------------------
def mode_device(reader):
    n = ask_int("端末番号", 1, 1, DEVICE_COUNT)
    key = input("合言葉（Code.gs の ACCESS_KEY と同じもの）: ").strip()
    url = BASE_URL + "?device=%02d" % n
    if key:
        url += "&key=" + urllib.parse.quote(key)
    print("▶ 端末%02d 設定カードを置いてください…" % n, end="", flush=True)
    write_one(reader, "端末設定", "%02d" % n, url)


def write_one(reader, kind, value, url):
    conn = wait_card(reader)
    try:
        uid = get_uid(conn)
        write_url(conn, url)
        log(kind, value, uid, url)
        print(" OK  (UID %s)" % uid)
        print("   書き込み内容: %s" % urllib.parse.unquote(url))
    except NfcError as e:
        print()
        print("  ✖ エラー: %s" % e)
    except CardConnectionException:
        print()
        print("  ✖ 途中でカードが離れました。")
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass
    print("   カードを外してください。")
    wait_remove(reader)


# ------------------------------------------------------------------
# 4. 読み取り確認
# ------------------------------------------------------------------
def mode_check(reader):
    print("確認したいシール/カードを置いてください。（Ctrl + C でメニューに戻る）")
    try:
        while True:
            conn = wait_card(reader)
            try:
                uid = get_uid(conn)
                url = read_url(conn)
                print("UID %s : %s" % (uid, urllib.parse.unquote(url) if url else "（何も書かれていません）"))
            except (NfcError, CardConnectionException) as e:
                print("読み取りエラー: %s" % e)
            finally:
                try:
                    conn.disconnect()
                except Exception:
                    pass
            wait_remove(reader)
    except KeyboardInterrupt:
        print()


# ------------------------------------------------------------------
def main():
    reader = get_reader()
    print("カードリーダー: %s" % reader)
    while True:
        print()
        print("==== NFC 書き込みツール ====")
        print(" 1. コンテナシールを連続で書き込む")
        print(" 2. 作業者カードを書き込む")
        print(" 3. 端末番号設定カードを書き込む")
        print(" 4. 書き込み内容を確認する")
        print(" 0. 終了")
        choice = input("番号を選んでください: ").strip()
        try:
            if choice == "1":
                mode_containers(reader)
            elif choice == "2":
                mode_worker(reader)
            elif choice == "3":
                mode_device(reader)
            elif choice == "4":
                mode_check(reader)
            elif choice == "0":
                break
        except KeyboardInterrupt:
            print()
            print("中断しました。")


if __name__ == "__main__":
    main()
