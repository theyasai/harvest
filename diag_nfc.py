# -*- coding: utf-8 -*-
"""
NFC リーダー診断ツール（書き込みはしません）

使い方:
    python3 diag_nfc.py

30秒間、リーダーの状態を記録して表示します。
その間に「シールを1回だけ置いて、数秒後に外す」を2〜3枚分やってください。
表示された文字をすべてコピーして送ってください。
"""

import sys
import time

try:
    from smartcard.System import readers
except ImportError:
    print("pyscard が入っていません: python3 -m pip install pyscard")
    sys.exit(1)
try:
    from smartcard.scard import (
        SCardEstablishContext, SCardGetStatusChange, SCARD_SCOPE_USER,
        SCARD_STATE_UNAWARE, SCARD_STATE_PRESENT, SCARD_STATE_EMPTY,
        SCARD_STATE_MUTE, SCARD_STATE_INUSE, SCARD_STATE_EXCLUSIVE,
        SCARD_STATE_UNPOWERED, SCARD_S_SUCCESS,
    )
    HAS_STATUS = True
except Exception as _e:
    print("（状態の監視は使えません: %s）" % _e)
    HAS_STATUS = False

DURATION = 30
T0 = time.time()


def ts():
    return "%6.2f秒" % (time.time() - T0)


def flags(state):
    names = []
    for n, f in [("カードあり", SCARD_STATE_PRESENT), ("カードなし", SCARD_STATE_EMPTY),
                 ("応答なし", SCARD_STATE_MUTE), ("使用中", SCARD_STATE_INUSE),
                 ("占有中", SCARD_STATE_EXCLUSIVE), ("電源なし", SCARD_STATE_UNPOWERED)]:
        if state & f:
            names.append(n)
    return ",".join(names) or "-"


def pick_reader(rs):
    """シールをかざす側（PICC / 非接触）のリーダーを選ぶ。SAM（ICチップ差し込み口）は使わない"""
    for r in rs:
        if "PICC" in str(r).upper():
            return r
    for r in rs:
        if "SAM" not in str(r).upper() and ("ACR" in str(r) or "ACS" in str(r)):
            return r
    return rs[0]


def main():
    rs = readers()
    print("見つかったリーダー:", [str(r) for r in rs])
    if not rs:
        return
    reader = pick_reader(rs)
    name = str(reader)
    print("使うリーダー:", name)
    print("Python:", sys.version.split()[0], " OS:", sys.platform)
    print()
    print("=== %d 秒間記録します。シールを1回ずつ置いて、外してください ===" % DURATION)

    ctx = None
    if HAS_STATUS:
        try:
            hr, ctx = SCardEstablishContext(SCARD_SCOPE_USER)
            states = [(name, SCARD_STATE_UNAWARE)]
        except Exception as e:
            print("（状態の監視は使えません: %s）" % e)
            ctx = None
    last_flags = None
    last_conn = None
    while time.time() - T0 < DURATION:
        # 1) OS から見たリーダーの状態
        if ctx is not None:
            try:
                hr, new_states = SCardGetStatusChange(ctx, 100, states)
                if hr == SCARD_S_SUCCESS:
                    _, st, atr = new_states[0]
                    f = flags(st)
                    if f != last_flags:
                        print("%s  状態: %s  ATR=%s" % (ts(), f, "".join("%02X" % b for b in atr) or "-"))
                        last_flags = f
                    states = [(name, st)]
            except Exception as e:
                print("（状態の監視でエラー: %s）" % e)
                ctx = None
        # 2) 書き込みツールと同じ方法でつないでみる
        conn = reader.createConnection()
        try:
            conn.connect()
            try:
                data, sw1, sw2 = conn.transmit([0xFF, 0xCA, 0x00, 0x00, 0x00])
                res = "接続OK UID=%s SW=%02X%02X" % ("".join("%02X" % b for b in data), sw1, sw2)
            except Exception as e:
                res = "接続OK だが UID 読めず: %s: %s" % (type(e).__name__, e)
            conn.disconnect()
        except Exception as e:
            res = "接続できず: %s: %s" % (type(e).__name__, str(e).splitlines()[0] if str(e) else "")
        if res != last_conn:
            print("%s  接続: %s" % (ts(), res))
            last_conn = res
        time.sleep(0.1)
    print("=== 終了 ===")


if __name__ == "__main__":
    main()
