#!/usr/bin/env python3
"""Newland NLS 扫码枪 软件触发 工具（实验性）。

扫码枪 USB 接口形态（NLS-N1 USB POS KBW, VID 1eab:1d22）：
  :1.0  HID 键盘   -> hidrawX  扫描数据（输入）
  :1.1  厂商 HID   -> hidrawY  指令通道（Output 报告 ID=0x04 / Feature 报告 ID=0xFE）

用法：
  python3 scanner_hidctl.py --find          # 列出识别到的 hidraw 与接口
  python3 scanner_hidctl.py --trigger       # 向厂商接口发送触发扫描指令（尝试多组帧）
  python3 scanner_hidctl.py --trigger --frame hex   # 用自定义指令帧发送（Output 报告）
  python3 scanner_hidctl.py --dev /dev/hidraw1 --trigger

注意：以下“触发帧”为资料中常见候选字节，未经贵司/新大陆 SDK 确认前请自行验证；
发送其它字节可能被解析为配置指令，理论上可能改变扫码枪设置，请谨慎。
"""
import argparse
import glob
import os
import sys

try:
    import fcntl
except Exception:
    fcntl = None

HIDIOCSFEATURE = 0xC0094806
HIDIOCSFEATURE_32 = 0xC0094806

# 常见“单次软触发”候选帧（需按型号确认；未证实）
CANDIDATES = [
    bytes.fromhex("7e 00 08 0a 00 00 00 00 00 00"),
    bytes.fromhex("7e 00 07 0a 00 00 00 00 00"),
    bytes.fromhex("7e 00 0d 0a 00 00 00 00 00 00 00 00 00 00"),
    bytes([0x00]),
]


def get_hidraw_devices():
    """返回 [{path, ifnum, product}] ：通过 /sys/class/hidraw/*/device 向上找接口号与产品名。"""
    out = []
    for path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        dev = os.path.basename(path)
        try:
            real = os.path.realpath(os.path.join(path, "device"))
        except Exception:
            real = ""
        # bInterfaceNumber 位于 hidraw 的父目录（usb_interface），或 device 的同级
        ifnum = ""
        cand = [os.path.dirname(real), real]
        for c in cand:
            f = os.path.join(c, "bInterfaceNumber")
            if os.path.exists(f):
                try:
                    ifnum = open(f).read().strip()
                    break
                except Exception:
                    pass
        # 通过父 usb 设备取产品型号
        product = ""
        for parent in (os.path.dirname(os.path.dirname(real)),):
            f = os.path.join(parent, "product")
            if os.path.exists(f):
                try:
                    product = open(f).read().strip()
                except Exception:
                    pass
        out.append({"path": "/dev/{}".format(dev), "ifnum": ifnum, "product": product})
    return out


def find_control_dev(dev=None):
    devs = get_hidraw_devices()
    if dev:
        return dev
    for d in devs:
        if "newland" in d["product"].lower():
            return d["path"]
    # 回退：产品含 Newland 的键盘接口旁的 ifnum=1 接口
    for d in devs:
        if d["ifnum"] == "1":
            return d["path"]
    # 最后回退到所有 hidraw 中 product 非空的第一个
    for d in devs:
        if d["product"]:
            return d["path"]
    return None


def send_feature(fd, report_id, payload):
    # HIDIOCSFEATURE(buf): 首字节为报告ID
    buf = bytearray([report_id]) + payload
    data = (bytes(buf) + b"\x00" * 252)
    buf = bytearray(256)
    buf[:len(data)] = data
    fcntl.ioctl(fd, HIDIOCSFEATURE, data, True)
    print("FEATURE id=0x{:02X} sent: {}".format(report_id, payload.hex(" ")), flush=True)


def send_output(fd, report_id, payload):
    # hidraw write：首字节为报告ID
    data = bytes([report_id]) + payload
    n = os.write(fd, data)
    print("OUTPUT id=0x{:02X} sent ({}B): {}".format(report_id, n, data.hex(" ")), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--find", action="store_true", help="列出 hidraw 设备")
    ap.add_argument("--trigger", action="store_true", help="发送触发扫描指令")
    ap.add_argument("--frame", default="", help="自定义触发帧(hex，不带报告ID)")
    ap.add_argument("--report-id", default="04", help="报告ID: 04=OUTPUT / fe=FEATURE")
    ap.add_argument("--dev", default="", help="指定 hidraw 设备")
    args = ap.parse_args()

    devs = get_hidraw_devices()
    if args.find or not args.trigger:
        print("识别到的 hidraw:")
        for d in devs:
            print("  {} iface#{}  {}".format(d["path"], d["ifnum"], d["product"] or "(unknown)"))
        if not args.trigger:
            return 0

    dev = find_control_dev(args.dev)
    if not dev:
        print("未找到 Newland 扫码枪的指令接口，请用 --dev 指定")
        return 1
    print("使用指令接口: {}".format(dev))

    rid = int(args.report_id, 16)
    if args.frame:
        frames = [bytes.fromhex(args.frame)]
    else:
        frames = CANDIDATES
    fd = os.open(dev, os.O_RDWR)
    try:
        for fr in frames:
            print("尝试: {}".format(fr.hex(" ")), flush=True)
            try:
                if rid == 0xFE:
                    send_feature(fd, rid, fr)
                else:
                    send_output(fd, rid, fr)
            except Exception as e:
                print("  发送失败: {}".format(e), flush=True)
    finally:
        os.close(fd)
    print("已发送。请观察扫码枪是否出光扫描；若出光则当前帧有效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())