#!/usr/bin/env python3
"""Newland NLS 扫码枪 串口版 软件触发（USB-COM 虚拟串口模式）。

前置条件：扫码枪 USB 接口类型必须为 “USB-COM / 虚拟串口”（非“USB键盘”）。
  - 用资源里的 ScanTool(Windows) 或 NLS 说明书的配置码把接口切到虚拟串口；
  - 切换后系统会出现 /dev/ttyACM*（或新 /dev/ttyUSB*）。

用法：
  python3 scan_trigger_serial.py --port /dev/ttyACM0 --trigger   # 触发一次扫描并等待条码
  python3 scan_trigger_serial.py --trigger                        # 自动探测端口
  python3 scan_trigger_serial.py --stop                           # 停止读码

UDI 指令（ASCII）：
  开始读码(触发一次) : ~<SOH>0000#SCNTRG1;<ETX>
  停止读码            : ~<SOH>0000#SCNTRG0;<ETX>
"""
import argparse
import glob
import sys
import time

import serial

SOH = b"\x01"
ETX = b"\x03"


def cmd(trig):
    return b"~" + SOH + b"0000" + b"#SCNTRG" + (b"1" if trig else b"0") + b";" + ETX


def find_port():
    cands = []
    for pat in ("/dev/ttyACM*", "/dev/ttyUSB*"):
        cands += [p for p in glob.glob(pat) if not p.endswith("ttyUSB1")]  # 排除已知非扫码口
    return cands


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="", help="串口设备，如 /dev/ttyACM0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--trigger", action="store_true", help="开始读码（触发扫描）")
    ap.add_argument("--stop", action="store_true", help="停止读码")
    ap.add_argument("--timeout", type=float, default=8.0, help="等待条码秒数")
    args = ap.parse_args()

    port = args.port or (find_port() or [""])[0]
    if not port:
        print("未找到串口设备；请确认扫码枪已切到 USB-COM 模式")
        return 1

    trig = not args.stop
    frame = cmd(trig)
    print("使用端口: {}  指令: {}".format(port, frame.hex(" ")))
    try:
        ser = serial.Serial(port, args.baud, timeout=0.3)
    except Exception as e:
        print("打开串口失败: {}（可能需 sudo，或权限组 dialout）".format(e))
        return 1

    try:
        ser.write(frame)
        print("已发送触发指令，等待条码...", flush=True)
        buf = b""
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            data = ser.read(256)
            if data:
                buf += data
            if b"\x03" in buf or b"\r" in buf or b"\n" in buf:
                break
        text = ""
        for b in buf:
            if 32 <= b < 127:
                text += chr(b)
        print("收到原始字节: {}".format(buf.hex(" ")))
        if text:
            print("条码内容: {}".format(text))
        else:
            print("未收到条码（可能未对准/模式不对/超时）")
    finally:
        ser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())