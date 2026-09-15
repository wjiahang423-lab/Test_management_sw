#!/usr/bin/env python3
"""ZLG USB-CANFD-200U <-> PEAK PCAN-USB 双向 CAN 通讯测试（独立程序）。

前置：
  1. 两块适配器已物理接在同一根 CAN 总线上（CAN_H/CAN_L 并联，共地，120Ω 终端至少一端开启）。
  2. PCAN 走 socketcan：can0 已配置并 up（一次性 root 命令）：
         sudo ip link set can0 down
         sudo ip link set can0 type can bitrate 1000000
         sudo ip link set can0 up
  3. ZLG 设备节点对本用户可访问（一次性 root 命令，之后重插/触发）：
         udev 规则：SUBSYSTEM=="usb", ATTR{idVendor}=="3068", MODE="0666"

运行（本机，需在含 library/ 的工作目录，如仓库根目录或 ~/eol）：
  python3 can_zlg_pcan_test.py                  # 从仓库根目录运行
  python3 can_zlg_pcan_test.py --count 50 --pcan can0 --channel 0
"""
import argparse
import os
import sys
import time

EXT_DIR_DEFAULT = "/tmp/ext"  # 装有 python-can+zlgcan+zlgcan_driver 的外部包目录


def _argument_parser():
    ap = argparse.ArgumentParser(description="ZLG-200U <-> PCAN-USB CAN 通讯测试")
    ap.add_argument("--count", type=int, default=30, help="每个方向发送帧数(默认30)")
    ap.add_argument("--pcan", default="can0", help="PCAN socketcan 接口名(默认 can0)")
    ap.add_argument("--channel", type=int, default=1, help="ZLG 通道号(默认1，实际接线在 ch1)")
    ap.add_argument("--ext", default=EXT_DIR_DEFAULT, help="外部包目录(默认 /tmp/ext)")
    ap.add_argument("--workdir", default="", help="工作目录(含 library/，默认当前目录)")
    ap.add_argument("--timeout", type=float, default=4.0, help="接收等待秒数")
    return ap


def main():
    args = _argument_parser().parse_args()
    if args.workdir:
        os.chdir(args.workdir)
    if args.ext:
        sys.path.insert(0, args.ext)

    import can

    Z_START = 0x200       # ZLG 发送的 ID 区间
    P_START = 0x300       # PCAN 发送的 ID 区间

    print("[TEST] ZLG-200U <-> PCAN-USB 双向 CAN 通讯测试", flush=True)
    print("[TEST] 参数: count={} pcan={} zlg_ch={} bitrate=1Mbps(经典CAN)".format(
        args.count, args.pcan, args.channel), flush=True)

    # ---------- 打开两条总线 ----------
    print("[TEST] 打开 ZLG (zlgcan, 1M) ...", flush=True)
    try:
        zbus = can.Bus(interface="zlgcan", channel=args.channel,
                       bitrate=1000000, dbitrate=5000000,
                       device_type=41, device_index=0,
                       libpath="./library",
                       configs=[{"channel": args.channel,
                                 "bitrate": 1000000, "dbitrate": 5000000, "resistance": 1}])
        print("[TEST] ZLG OPEN OK", flush=True)
    except Exception as e:
        print("[TEST] ZLG 打开失败: {}: {}".format(type(e).__name__, e), flush=True)
        return 1

    print("[TEST] 打开 PCAN (socketcan {}) ...".format(args.pcan), flush=True)
    try:
        pbus = can.Bus(interface="socketcan", channel=args.pcan)
        print("[TEST] PCAN OPEN OK", flush=True)
    except Exception as e:
        print("[TEST] PCAN 打开失败: {}（请先 ip link 配置并 up can0）".format(e), flush=True)
        return 1

    result = {}

    # ---------- 方向1：ZLG 发 -> PCAN 收 ----------
    print("---- 方向1: ZLG 发 -> PCAN 收 ----", flush=True)
    sent = 0
    seq = 0
    for i in range(args.count):
        seq = (seq + 1) & 0xFF
        payload = bytearray([0xAA, seq, i & 0xFF, (i >> 8) & 0xFF, 0x01, 0x02, 0x03, 0x04])
        m = can.Message(arbitration_id=Z_START + i, data=payload,
                        is_extended_id=False, is_fd=False)
        try:
            zbus.send(m)
            sent += 1
        except can.CanError as e:
            print("[FAIL] ZLG 发送第{}帧失败: {}".format(i, e), flush=True)
        time.sleep(0.02)
    got = set()
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        m = pbus.recv(timeout=0.2)
        if m is None:
            continue
        aid = int(m.arbitration_id)
        if Z_START <= aid < Z_START + args.count:
            got.add(aid)
    z2p_ok = len(got) >= max(1, int(args.count * 0.9))
    result["ZLG->PCAN"] = "{}/{}".format(len(got), sent)
    print("[RESULT] ZLG->PCAN 收到 {}/{}  {}".format(len(got), sent,
          "PASS" if z2p_ok else "FAIL"), flush=True)

    # ---------- 方向2：PCAN 发 -> ZLG 收 ----------
    print("---- 方向2: PCAN 发 -> ZLG 收 ----", flush=True)
    sent2 = 0
    for i in range(args.count):
        payload = bytearray([0x55, i & 0xFF, (i >> 8) & 0xFF, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE])
        m = can.Message(arbitration_id=P_START + i, data=payload,
                        is_extended_id=False, is_fd=False)
        try:
            pbus.send(m)
            sent2 += 1
        except can.CanError as e:
            print("[FAIL] PCAN 发送第{}帧失败: {}".format(i, e), flush=True)
        time.sleep(0.02)
    got2 = set()
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        m = zbus.recv(timeout=0.2)
        if m is None:
            continue
        aid = int(m.arbitration_id)
        if P_START <= aid < P_START + args.count:
            got2.add(aid)
    p2z_ok = len(got2) >= max(1, int(args.count * 0.9))
    result["PCAN->ZLG"] = "{}/{}".format(len(got2), sent2)
    print("[RESULT] PCAN->ZLG 收到 {}/{}  {}".format(len(got2), sent2,
          "PASS" if p2z_ok else "FAIL"), flush=True)

    # ---------- 汇总 ----------
    print("", flush=True)
    print("================= 测试结论 =================", flush=True)
    for k, v in result.items():
        print("  {} : {}".format(k, v), flush=True)
    overall = z2p_ok and p2z_ok and sent > 0 and sent2 > 0
    print("  总体 : {}".format("PASS —— 双向 CAN 通讯正常" if overall else "FAIL"), flush=True)
    if not overall:
        print("  排查：接线/终端电阻/波特率/网卡是否up", flush=True)
    print("============================================", flush=True)

    try:
        zbus.shutdown()
    except Exception:
        pass
    try:
        pbus.shutdown()
    except Exception:
        pass
    return 0 if overall else 2


if __name__ == "__main__":
    sys.exit(main())