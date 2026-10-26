# -*- coding: utf-8 -*-
"""
NFC リーダー診断ツール その2（書き込みはしません）

ACR1552 で「シールを外してもしばらく『載っている』と判断されたままになり、
すぐに次のシールを置いても反応しない」問題について、解消できる方法を3通り試します。

使い方:
    python3 diag_nfc2.py

シールを2枚用意して、画面の案内に従ってください。
最後に出る「まとめ」を含めて、表示された文字をすべてコピーして送ってください。
"""

import sys
import time

try:
    from smartcard.System import readers
    from smartcard.scard import (
        SCardEstablishContext, SCardReleaseContext, SCardGetStatusChange,
        SCardConnect, SCardDisconnect, SCardTransmit,
        SCARD_SCOPE_USER, SCARD_SHARE_SHARED, SCARD_SHARE_DIRECT,
        SCARD_PROTOCOL_T0, SCARD_PROTOCOL_T1,
        SCARD_LEAVE_CARD, SCARD_RESET_CARD, SCARD_UNPOWER_CARD,
        SCARD_STATE_UNAWARE, SCARD_STATE_PRESENT, SCARD_STATE_EMPTY, SCARD_STATE_MUTE,
        SCARD_S_SUCCESS,
    )
except ImportError as e:
    print("pyscard が入っていません、または古いです: %s" % e)
    print("    python3 -m pip install --upgrade pyscard")
    sys.exit(1)

PHASE_SECONDS = 30
POLL = 0.05

PHASES = [
    ("A", "今までと同じやり方", SCARD_UNPOWER_CARD, False),
    ("B", "シールの電源を切らずに切断する", SCARD_LEAVE_CARD, False),
    ("C", "外したあとリーダーをリセットする", SCARD_UNPOWER_CARD, True),
]


def pick_reader(rs):
    for r in rs:
        if "PICC" in str(r).upper():
            return str(r)
    for r in rs:
        if "SAM" not in str(r).upper():
            return str(r)
    return str(rs[0])


def state_name(st):
    if st & SCARD_STATE_EMPTY:
        return "カードなし"
    if st & SCARD_STATE_PRESENT and st & SCARD_STATE_MUTE:
        return "カードあり・応答なし"
    if st & SCARD_STATE_PRESENT:
        return "カードあり"
    return "不明(%X)" % st


def try_read_uid(ctx, reader, disposition):
    """つないで UID を読む。読めたら UID 文字列、だめなら None"""
    try:
        hr, hcard, proto = SCardConnect(ctx, reader, SCARD_SHARE_SHARED, SCARD_PROTOCOL_T0 | SCARD_PROTOCOL_T1)
    except Exception:
        return None
    if hr != SCARD_S_SUCCESS:
        return None
    uid = None
    try:
        hr, resp = SCardTransmit(hcard, proto, [0xFF, 0xCA, 0x00, 0x00, 0x00])
        if hr == SCARD_S_SUCCESS and len(resp) >= 2 and resp[-2:] == [0x90, 0x00]:
            uid = "".join("%02X" % b for b in resp[:-2])
    except Exception:
        pass
    try:
        SCardDisconnect(hcard, disposition)
    except Exception:
        pass
    return uid


def reset_reader(ctx, reader):
    """カードが無くてもつなげる『直接接続』でつなぎ、リセットして切る"""
    try:
        hr, hcard, proto = SCardConnect(ctx, reader, SCARD_SHARE_DIRECT, 0)
    except Exception as e:
        return "失敗(%s)" % type(e).__name__
    if hr != SCARD_S_SUCCESS:
        return "失敗(%08X)" % (hr & 0xFFFFFFFF)
    try:
        hr2 = SCardDisconnect(hcard, SCARD_RESET_CARD)
    except Exception as e:
        return "切断失敗(%s)" % type(e).__name__
    return "OK" if hr2 == SCARD_S_SUCCESS else "切断失敗(%08X)" % (hr2 & 0xFFFFFFFF)


def run_phase(ctx, reader, code, title, disposition, do_reset):
    print()
    print("=" * 60)
    print("【方法%s】%s" % (code, title))
    print("シールを2枚交互に使って、")
    print("  置く →「検出」と出たら外す → すぐ（1秒以内に）もう1枚を置く")
    print("を %d 秒間くり返してください。" % PHASE_SECONDS)
    input("準備ができたら Enter を押してください（押すと開始します）")
    t0 = time.time()
    ts = lambda: "%5.2f秒" % (time.time() - t0)

    states = [(reader, SCARD_STATE_UNAWARE)]
    last_state = None
    present = False
    removed_at = None
    delays = []
    detections = 0
    reset_logged = False

    while time.time() - t0 < PHASE_SECONDS:
        try:
            hr, new_states = SCardGetStatusChange(ctx, 0, states)
            if hr == SCARD_S_SUCCESS:
                st = new_states[0][1]
                states = [(reader, st)]
                name = state_name(st)
                if name != last_state:
                    print("%s  状態: %s" % (ts(), name))
                    last_state = name
        except Exception:
            pass

        uid = try_read_uid(ctx, reader, disposition)
        if uid and not present:
            present = True
            detections += 1
            if removed_at is not None:
                d = time.time() - removed_at
                delays.append(d)
                print("%s  ◎ 検出 UID=%s（外してから %.1f 秒）" % (ts(), uid, d))
            else:
                print("%s  ◎ 検出 UID=%s" % (ts(), uid))
            reset_logged = False
        elif not uid and present:
            present = False
            removed_at = time.time()
            print("%s  × 外れた" % ts())

        if do_reset and not present and last_state == "カードあり・応答なし":
            r = reset_reader(ctx, reader)
            if not reset_logged:
                print("%s  ↻ リーダーをリセット: %s" % (ts(), r))
                reset_logged = True
            time.sleep(0.2)

        time.sleep(POLL)

    avg = (sum(delays) / len(delays)) if delays else None
    print("--- 方法%s 終了：検出 %d 回 / 外してから次を検出するまで 平均 %s ---"
          % (code, detections, ("%.1f 秒" % avg) if avg is not None else "測れず"))
    return detections, avg


def main():
    rs = readers()
    print("見つかったリーダー:", [str(r) for r in rs])
    if not rs:
        return
    reader = pick_reader(rs)
    print("使うリーダー:", reader)
    print("Python:", sys.version.split()[0], " OS:", sys.platform)

    hr, ctx = SCardEstablishContext(SCARD_SCOPE_USER)
    results = []
    try:
        for code, title, disposition, do_reset in PHASES:
            results.append((code, title) + run_phase(ctx, reader, code, title, disposition, do_reset))
    except KeyboardInterrupt:
        print()
        print("中断しました")
    finally:
        try:
            SCardReleaseContext(ctx)
        except Exception:
            pass

    print()
    print("====== まとめ ======")
    for code, title, n, avg in results:
        print("方法%s（%s）: 検出 %d 回 / 平均 %s" % (code, title, n, ("%.1f 秒" % avg) if avg is not None else "測れず"))


if __name__ == "__main__":
    main()
