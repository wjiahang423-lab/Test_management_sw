#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""吉翼灵巧手 RS485 功能测试脚本（USB-485 转串口）。

依赖：pyserial（pip install pyserial）。
用法（示例）：
    python rs485_test.py --port /dev/ttyUSB0 --dev-id 0x20 --list
    python rs485_test.py --port /dev/ttyUSB0 --dev-id 0x20 --read 0x06
    python rs485_test.py --port /dev/ttyUSB0 --dev-id 0x20 --write 0x03 100 --joint 1
    python rs485_test.py --port /dev/ttyUSB0 --dev-id 0x20 --run-all

协议要点（摘自《吉翼灵巧手协议(3).xlsx》RS485协议）：
  - 参数: 1Mbps, 8数据位, 无校验, 1停止位
  - 帧: 0xEB 0x90 | 标志字节 | 设备ID | 地址 | 数据长度 | 数据区 | CRC16_L | CRC16_H
  - 标志字节: Bit4(0主机/1从机) Bit3(0写/1读) Bit0-2 预留
  - CRC16 采用 Modbus CRC16-IBM
"""
from __future__ import annotations

import argparse
import os
import sys
import time

try:
    import serial
    from serial import SerialException
except ImportError:
    print("缺少 pyserial，请先安装：pip install pyserial")
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hand_protocol as hp

# --------------------------------------------------------------------------
# 默认参数
# --------------------------------------------------------------------------
DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_BAUD = 1000000                  # 协议: 1Mbps
DEFAULT_DEV_ID = 0x20                   # 协议设备ID: 0x10 左手 / 0x20 右手
DEFAULT_RECV_TIMEOUT = 1.0
DEFAULT_JOINTS = 16


# --------------------------------------------------------------------------
# 串口封装
# --------------------------------------------------------------------------
class HandRs485:
    """封装 USB-485 串口，按 RS485 协议收发一帧。"""

    def __init__(self, port=DEFAULT_PORT, baud=DEFAULT_BAUD, dev_id=DEFAULT_DEV_ID,
                 rx_timeout=DEFAULT_RECV_TIMEOUT):
        self.dev_id = dev_id
        self.port = serial.Serial(port=port, baudrate=baud, bytesize=8,
                                  parity=serial.PARITY_NONE, stopbits=1,
                                  timeout=rx_timeout)
        self.rx_timeout = rx_timeout

    def close(self):
        try:
            self.port.close()
        except Exception:
            pass

    def _drain(self):
        """清空接收缓冲。"""
        try:
            self.port.reset_input_buffer()
        except Exception:
            pass

    def send(self, addr, rw, data=b"", master_send=True, recv=True):
        """发送一帧 RS485 数据，可选等待应答帧并做 CRC 校验。

        返回 (ok, response_dict, message)。
        """
        frame = hp.build_rs485_frame(addr, rw, self.dev_id, data, master_send=master_send)
        try:
            self._drain()
            self.port.write(frame)
            self.port.flush()
        except Exception as e:
            return False, None, "发送失败: {}: {}".format(type(e).__name__, e)

        if not recv:
            return True, None, "ok(sent)"

        resp = self._recv_frame()
        if resp is None:
            return False, None, "接收超时(无应答)"
        if not resp.get("ok"):
            return False, resp, "应答 CRC 校验失败"
        return True, resp, "ok"

    def _recv_frame(self):
        """解析一帧：先读帧头/长度，再读齐数据与 CRC。"""
        deadline = time.monotonic() + self.rx_timeout
        while time.monotonic() < deadline:
            # 找帧头 0xEB
            b = self.port.read(1)
            if not b:
                continue
            if b[0] != 0xEB:
                continue
            head = self.port.read(1)
            if not head or head[0] != 0x90:
                continue
            # 读 标志/设备ID/地址/数据长度
            rest = self.port.read(4)
            if len(rest) < 4:
                return None
            flag, dev, addr, dlen = rest[0], rest[1], rest[2], rest[3]
            data = self.port.read(dlen) if dlen else b""
            if dlen and len(data) < dlen:
                return None
            crc = self.port.read(2)
            if len(crc) < 2:
                return None
            frame = bytes([0xEB, 0x90]) + rest + data + crc
            return hp.parse_rs485_frame(frame)
        return None


# --------------------------------------------------------------------------
# 测量入口：供 measurement 形式测试 plan 调用。
# 每个函数自建/关闭串口连接，接收连接参数 + 测试参数，返回可判定 dict。
# 返回值 dict 键：value(量化值) / ok(1/0) / pass(整体判定)。
# --------------------------------------------------------------------------
def _open_hand(port, baud, dev_id, rx_timeout):
    global DEFAULT_RECV_TIMEOUT
    DEFAULT_RECV_TIMEOUT = rx_timeout if rx_timeout else DEFAULT_RECV_TIMEOUT
    return HandRs485(port=port, baud=baud, dev_id=dev_id, rx_timeout=rx_timeout)


def _run(fn, port=DEFAULT_PORT, baud=DEFAULT_BAUD, dev_id=DEFAULT_DEV_ID,
         rx_timeout=DEFAULT_RECV_TIMEOUT, **kw):
    hand = _open_hand(port, baud, dev_id, rx_timeout)
    try:
        return fn(hand, **kw)
    finally:
        hand.close()


# -------- 读类 --------
def mea_read_info(port=DEFAULT_PORT, baud=DEFAULT_BAUD, dev_id=DEFAULT_DEV_ID,
                  rx_timeout=DEFAULT_RECV_TIMEOUT):
    return _run(test_read_info, port=port, baud=baud, dev_id=dev_id, rx_timeout=rx_timeout)


def mea_read_fault(port=DEFAULT_PORT, baud=DEFAULT_BAUD, dev_id=DEFAULT_DEV_ID,
                   rx_timeout=DEFAULT_RECV_TIMEOUT):
    return _run(test_read_fault, port=port, baud=baud, dev_id=dev_id, rx_timeout=rx_timeout)


def mea_read_bus_voltage(port=DEFAULT_PORT, baud=DEFAULT_BAUD, dev_id=DEFAULT_DEV_ID,
                         rx_timeout=DEFAULT_RECV_TIMEOUT):
    return _run(test_read_bus_voltage, port=port, baud=baud, dev_id=dev_id, rx_timeout=rx_timeout)


def mea_read_current(port=DEFAULT_PORT, baud=DEFAULT_BAUD, dev_id=DEFAULT_DEV_ID,
                     rx_timeout=DEFAULT_RECV_TIMEOUT):
    return _run(test_read_current, port=port, baud=baud, dev_id=dev_id, rx_timeout=rx_timeout)


def mea_read_temp(port=DEFAULT_PORT, baud=DEFAULT_BAUD, dev_id=DEFAULT_DEV_ID,
                  rx_timeout=DEFAULT_RECV_TIMEOUT):
    return _run(test_read_temp, port=port, baud=baud, dev_id=dev_id, rx_timeout=rx_timeout)


def mea_read_mode(port=DEFAULT_PORT, baud=DEFAULT_BAUD, dev_id=DEFAULT_DEV_ID,
                  rx_timeout=DEFAULT_RECV_TIMEOUT):
    return _run(test_read_mode, port=port, baud=baud, dev_id=dev_id, rx_timeout=rx_timeout)


def mea_read_pressure(finger=0, port=DEFAULT_PORT, baud=DEFAULT_BAUD, dev_id=DEFAULT_DEV_ID,
                      rx_timeout=DEFAULT_RECV_TIMEOUT):
    return _run(test_read_pressure, finger=finger, port=port, baud=baud,
                dev_id=dev_id, rx_timeout=rx_timeout)


def mea_read_position(joint=1, port=DEFAULT_PORT, baud=DEFAULT_BAUD, dev_id=DEFAULT_DEV_ID,
                      rx_timeout=DEFAULT_RECV_TIMEOUT):
    return _run(test_read_position, joint=joint, port=port, baud=baud,
                dev_id=dev_id, rx_timeout=rx_timeout)


def mea_read_speed(joint=1, port=DEFAULT_PORT, baud=DEFAULT_BAUD, dev_id=DEFAULT_DEV_ID,
                   rx_timeout=DEFAULT_RECV_TIMEOUT):
    return _run(test_read_speed, joint=joint, port=port, baud=baud,
                dev_id=dev_id, rx_timeout=rx_timeout)


def mea_read_torque(joint=1, port=DEFAULT_PORT, baud=DEFAULT_BAUD, dev_id=DEFAULT_DEV_ID,
                    rx_timeout=DEFAULT_RECV_TIMEOUT):
    return _run(test_read_torque, joint=joint, port=port, baud=baud,
                dev_id=dev_id, rx_timeout=rx_timeout)


def mea_read_calib(port=DEFAULT_PORT, baud=DEFAULT_BAUD, dev_id=DEFAULT_DEV_ID,
                   rx_timeout=DEFAULT_RECV_TIMEOUT):
    return _run(test_read_reg, addr=0x20, port=port, baud=baud,
                dev_id=dev_id, rx_timeout=rx_timeout)


def mea_read_param(addr=0xC0, port=DEFAULT_PORT, baud=DEFAULT_BAUD, dev_id=DEFAULT_DEV_ID,
                   rx_timeout=DEFAULT_RECV_TIMEOUT):
    return _run(test_read_reg, addr=int(addr, 0) if isinstance(addr, str) else addr,
                port=port, baud=baud, dev_id=dev_id, rx_timeout=rx_timeout)


# -------- 写类 --------
def mea_zero_calib(port=DEFAULT_PORT, baud=DEFAULT_BAUD, dev_id=DEFAULT_DEV_ID,
                   rx_timeout=DEFAULT_RECV_TIMEOUT):
    return _run(test_zero_calib, port=port, baud=baud, dev_id=dev_id, rx_timeout=rx_timeout)


def mea_set_position(joint=1, position=100, port=DEFAULT_PORT, baud=DEFAULT_BAUD,
                     dev_id=DEFAULT_DEV_ID, rx_timeout=DEFAULT_RECV_TIMEOUT):
    return _run(test_set_position, joint=joint, position=position, port=port,
                baud=baud, dev_id=dev_id, rx_timeout=rx_timeout)


def mea_set_speed(joint=1, speed=100, port=DEFAULT_PORT, baud=DEFAULT_BAUD,
                  dev_id=DEFAULT_DEV_ID, rx_timeout=DEFAULT_RECV_TIMEOUT):
    return _run(test_set_speed, joint=joint, speed=speed, port=port,
                baud=baud, dev_id=dev_id, rx_timeout=rx_timeout)


def mea_set_torque(joint=1, torque=100, port=DEFAULT_PORT, baud=DEFAULT_BAUD,
                   dev_id=DEFAULT_DEV_ID, rx_timeout=DEFAULT_RECV_TIMEOUT):
    return _run(test_set_torque, joint=joint, torque=torque, port=port,
                baud=baud, dev_id=dev_id, rx_timeout=rx_timeout)


def mea_write_param(addr=0xC0, value=50, port=DEFAULT_PORT, baud=DEFAULT_BAUD,
                    dev_id=DEFAULT_DEV_ID, rx_timeout=DEFAULT_RECV_TIMEOUT):
    return _run(test_read_write_param, addr=int(addr, 0) if isinstance(addr, str) else addr,
                value=value, port=port, baud=baud, dev_id=dev_id, rx_timeout=rx_timeout)


def mea_save_params(port=DEFAULT_PORT, baud=DEFAULT_BAUD, dev_id=DEFAULT_DEV_ID,
                    rx_timeout=DEFAULT_RECV_TIMEOUT):
    return _run(test_save_params, port=port, baud=baud, dev_id=dev_id, rx_timeout=rx_timeout)


def mea_factory_reset(port=DEFAULT_PORT, baud=DEFAULT_BAUD, dev_id=DEFAULT_DEV_ID,
                      rx_timeout=DEFAULT_RECV_TIMEOUT):
    return _run(test_factory_reset, port=port, baud=baud, dev_id=dev_id, rx_timeout=rx_timeout)


# --------------------------------------------------------------------------
# 报告辅助
# --------------------------------------------------------------------------
def _report(procedure, addr, ok, resp, message, value=None, expected=None):
    print("\n[{}] 地址=0x{:02X} ({}) 属性={}".format(
        procedure, addr, hp.capability_get(addr)["name"], hp.capability_get(addr)["attr"]))
    print("    判定: {}".format("PASS" if ok else "FAIL"))
    print("    结果: {}".format(message))
    if resp is not None:
        print("    应答: dev_id=0x{:02X} is_read={} data_len={} data={}".format(
            resp.get("dev_id"), resp.get("is_read"), resp.get("data_len"),
            list(resp.get("data", []))))
        if value is not None:
            print("    解析值: {}".format(value))
    if expected is not None:
        print("    期望值: {}".format(expected))
    return {
        "value": value,
        "pass": ok,
        "ok": 1 if ok else 0,
        "message": message,
        "addr": addr,
    }


def _check_value(value, lower=None, upper=None, expect=None, tol=0):
    if value is None:
        return False, "数值解析为空"
    if expect is not None:
        if abs(value - expect) <= tol:
            return True, "值={} 符合期望={}".format(value, expect)
        return False, "值={} 不符合期望={}".format(value, expect)
    if lower is not None and upper is not None:
        if lower <= value <= upper:
            return True, "值={} 在区间[{},{}]".format(value, lower, upper)
        return False, "值={} 不在区间[{},{}]".format(value, lower, upper)
    return True, "值={}".format(value)


# --------------------------------------------------------------------------
# 测试项
# --------------------------------------------------------------------------
def test_read_reg(hand, addr, label=None, lower=None, upper=None):
    if not hp.allowed(addr, "r"):
        return _report("READ", addr, False, None, "该地址属性为 {} 不允许读".format(
            hp.capability_get(addr)["attr"]))
    ok, resp, msg = hand.send(addr, "r", b"")
    val = None
    if ok and resp is not None:
        val = hp.u16(resp["data"], 0)
    cok, desc = (True, msg)
    if ok:
        cok, desc = _check_value(val, lower=lower, upper=upper)
    return _report(label or hp.capability_get(addr)["name"], addr, ok and cok,
                   resp, desc, value=val)


def test_write_reg(hand, addr, data, label=None):
    if not hp.allowed(addr, "w"):
        return _report("WRITE", addr, False, None, "该地址属性为 {} 不允许写".format(
            hp.capability_get(addr)["attr"]))
    ok, resp, msg = hand.send(addr, "w", data)
    return _report(label or hp.capability_get(addr)["name"], addr, ok, resp, msg,
                   value=list(data))


def test_read_info(hand):
    ok, resp, msg = hand.send(0x00, "r", b"")
    return _report("产品信息", 0x00, ok, resp, msg)


def test_read_fault(hand):
    ok, resp, msg = hand.send(0x01, "r", b"")
    return _report("关节故障码", 0x01, ok, resp, msg, value=resp and hp.u16(resp["data"], 0))


def test_zero_calib(hand):
    ok, resp, msg = hand.send(0x02, "w", bytes([0x03]))
    return _report("0位标定(使能)", 0x02, ok, resp, msg)


def test_set_position(hand, joint=1, position=100):
    data = (int(position) & 0xFFFF).to_bytes(2, "little")
    ok, resp, msg = hand.send(0x03, "w", data)
    return _report("关节{}目标位置".format(joint), 0x03, ok, resp, msg, value=list(data))


def test_set_speed(hand, joint=1, speed=100):
    data = (int(speed) & 0xFFFF).to_bytes(2, "little")
    ok, resp, msg = hand.send(0x04, "w", data)
    return _report("关节{}目标速度".format(joint), 0x04, ok, resp, msg, value=list(data))


def test_set_torque(hand, joint=1, torque=100):
    data = (int(torque) & 0xFFFF).to_bytes(2, "little")
    ok, resp, msg = hand.send(0x05, "w", data)
    return _report("关节{}目标扭矩".format(joint), 0x05, ok, resp, msg, value=list(data))


def test_read_position(hand, joint=1):
    return test_read_reg(hand, 0x06, label="关节{}当前位置".format(joint))


def test_read_speed(hand, joint=1):
    return test_read_reg(hand, 0x07, label="关节{}当前速度".format(joint))


def test_read_torque(hand, joint=1):
    return test_read_reg(hand, 0x08, label="关节{}当前扭矩".format(joint))


def test_read_pressure(hand, finger=0):
    return test_read_reg(hand, 0x09 + finger, label="手指{}压力".format(finger))


def test_read_bus_voltage(hand):
    return test_read_reg(hand, 0x30, label="直流总线电压")


def test_read_current(hand):
    return test_read_reg(hand, 0x31, label="关节电流")


def test_read_temp(hand):
    return test_read_reg(hand, 0x32, label="关节温度")


def test_read_mode(hand):
    return test_read_reg(hand, 0x33, label="工作模式")


def test_read_write_param(hand, addr, value, label=None):
    r_ok, r_resp, r_msg = hand.send(addr, "r", b"")
    r_val = r_resp and hp.u16(r_resp["data"], 0)
    w_ok, w_resp, w_msg = hand.send(addr, "w", (int(value) & 0xFFFF).to_bytes(2, "little"))
    rr_ok, rr_resp, rr_msg = hand.send(addr, "r", b"")
    rr_val = rr_resp and hp.u16(rr_resp["data"], 0)
    cok, desc = _check_value(rr_val, expect=int(value), tol=0)
    msg = "读回默认={} 写入={} 读回={} {}（写:{} 读:{}）".format(
        r_val, value, rr_val, desc, "OK" if w_ok else "FAIL", "OK" if rr_ok else "FAIL")
    return _report(label or hp.capability_get(addr)["name"], addr,
                   w_ok and rr_ok and cok, rr_resp, msg, value=rr_val, expected=value)


def test_save_params(hand):
    ok, resp, msg = hand.send(0xCF, "w", b"")
    return _report("保存参数", 0xCF, ok, resp, msg)


def test_factory_reset(hand):
    ok, resp, msg = hand.send(0xC8, "w", b"")
    return _report("恢复出厂设置", 0xC8, ok, resp, msg)


# --------------------------------------------------------------------------
# 汇总
# --------------------------------------------------------------------------
def run_all(hand, joints=DEFAULT_JOINTS):
    results = []
    results.append(test_read_info(hand))
    results.append(test_read_fault(hand))
    results.append(test_read_bus_voltage(hand))
    results.append(test_read_current(hand))
    results.append(test_read_temp(hand))
    results.append(test_read_mode(hand))
    for f in range(5):
        results.append(test_read_pressure(hand, f))
    for j in range(1, min(joints, 4) + 1):
        results.append(test_read_position(hand, j))
        results.append(test_read_speed(hand, j))
        results.append(test_read_torque(hand, j))
    results.append(test_set_position(hand, 1, position=100))
    results.append(test_set_speed(hand, 1, speed=100))
    results.append(test_set_torque(hand, 1, torque=100))
    results.append(test_read_write_param(hand, 0xC0, 50))
    results.append(test_read_write_param(hand, 0xC1, 500))
    results.append(test_read_write_param(hand, 0xC2, 700))
    results.append(test_save_params(hand))

    passed = sum(1 for r in results if r["pass"])
    total = len(results)
    print("\n==================== 汇总 ====================")
    print("通过 {}/{}".format(passed, total))
    print("结果: {}".format("PASS" if passed == total else "FAIL"))
    return passed == total


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(description="吉翼灵巧手 RS485 功能测试 (USB-485)")
    p.add_argument("--port", default=DEFAULT_PORT, help="串口设备(默认/dev/ttyUSB0)")
    p.add_argument("--baud", type=int, default=DEFAULT_BAUD, help="波特率(默认1000000)")
    p.add_argument("--dev-id", type=lambda x: int(x, 0), default=DEFAULT_DEV_ID,
                   help="设备ID(0x10左手/0x20右手)")
    p.add_argument("--read", type=lambda x: int(x, 0), help="读指定寄存器地址,如 0x06")
    p.add_argument("--write", type=lambda x: int(x, 0), help="写指定寄存器地址,如 0x03")
    p.add_argument("--value", type=lambda x: int(x, 0), default=100, help="写指令数值")
    p.add_argument("--joint", type=int, default=1, help="写指令关节号(用于标签)")
    p.add_argument("--list", action="store_true", help="列出协议全部指令")
    p.add_argument("--run-all", action="store_true", help="运行完整功能测试")
    return p


def cmd_list():
    print("{:<6} {:<6} {:<40} {:<6}".format("地址", "长度", "功能", "属性"))
    for addr in sorted(hp.CAPABILITY):
        info = hp.CAPABILITY[addr]
        print("0x{:02X}  {:<4} {:<40} {:<6}".format(addr, info["len"], info["name"], info["attr"]))


def main():
    args = build_parser().parse_args()
    if args.list:
        cmd_list()
        return 0

    try:
        hand = HandRs485(port=args.port, baud=args.baud, dev_id=args.dev_id)
    except SerialException as e:
        print("打开串口失败: {}".format(e))
        return 1
    print("RS485 已打开: port={} baud={} dev_id=0x{:02X}".format(args.port, args.baud, args.dev_id))
    try:
        if args.run_all:
            return 0 if run_all(hand) else 1
        if args.read is not None:
            return 0 if test_read_reg(hand, args.read)["pass"] else 1
        if args.write is not None:
            data = (args.value & 0xFFFF).to_bytes(2, "little")
            return 0 if test_write_reg(hand, args.write, data)["pass"] else 1
        print("未指定操作，使用 --list / --read / --write / --run-all")
        return 2
    finally:
        hand.close()


if __name__ == "__main__":
    sys.exit(main())
