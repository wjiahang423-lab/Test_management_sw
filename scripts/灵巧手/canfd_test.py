#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""吉翼灵巧手 CANFD 功能测试脚本（ZLG USB-CANFD-200U）。

依赖：python-can + ZLG 的 zlgcan 封装包（见 install 说明）。
用法（示例）：
    python canfd_test.py --dev-id 0x02 --chan 0 --list
    python canfd_test.py --dev-id 0x02 --read 0x06
    python canfd_test.py --dev-id 0x02 --write 0x03 100  --joint 1
    python canfd_test.py --dev-id 0x02 --run-all

协议要点（摘自《吉翼灵巧手协议(3).xlsx》CAFD控制协议）：
  - 通信速率 1Mbps(仲裁)/5Mbps(数据)，CANFD 可变速率(BRS)
  - 29 位扩展帧，CANID 定义见 hand_protocol.hp.build_canid
  - 属性 R/W 决定读/写指令

参数约定：
  - CAN 连接参数（通道/波特率/设备ID/设备型号等）统一在下方
    「公共全局前置参数设置」中配置，不通过 PLAN 用例参数传参；
  - PLAN 用例只传「测量/写入数据」参数（joint / position / speed /
    torque / addr / value / finger / joints）。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 保证能 import 同目录 hand_protocol
try:
    import can
except ImportError:
    print("缺少 python-can，请先安装：pip install python-can")
    sys.exit(1)
except Exception as e:                     # 例如导入时缺 ZLG 依赖 DLL
    print("导入 python-can 失败: {}: {}".format(type(e).__name__, e))
    sys.exit(1)

import hand_protocol as hp

# ==========================================================================
# 公共全局前置参数设置（CAN 连接参数）
# --------------------------------------------------------------------------
# 说明：以下参数为所有测试用例共用的 CAN 总线前置配置，统一在此处修改，
#       不再通过 PLAN 用例参数传参；PLAN 中只配置「测量/写入数据」参数。
# ==========================================================================
DEFAULT_CHANNEL = 0                    # 通道号
DEFAULT_INTERFACE = "zlgcan"           # python-can 接口名（ZLG 总线）
DEFAULT_BITRATE = 1_000_000            # 仲裁段 1Mbps
DEFAULT_DATA_BITRATE = 5_000_000       # 数据段 5Mbps（BRS）
DEFAULT_DEV_ID = 0x02                  # 设备ID：0x01 左手 / 0x02 右手（协议 设备ID）
DEFAULT_RECV_TIMEOUT = 1.0             # 应答接收超时（秒）
DEFAULT_JOINTS = 16                    # 关节 1-N，用于拼接多关节数据

# ---- ZLG(zlgcan) 相关：与 zlgcan 库的 ZCANDeviceType / ZCanChlType 枚举值对应 ----
ZCAN_USBCANFD_200U = 41                # ZCANDeviceType.ZCAN_USBCANFD_200U
ZCAN_CHL_CANFD_ISO = 1                 # 通道类型：CANFD(ISO)
DEFAULT_DEVICE_TYPE = ZCAN_USBCANFD_200U
DEFAULT_DEVICE_INDEX = 0
DEFAULT_LIBPATH = os.path.normpath(os.path.join(   # 默认定位到工程根目录下的 library/
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "library"))
DEFAULT_RESISTANCE = 1                 # 终端电阻 120Ω
ZCAN_CHANNELS = 2                      # USB-CANFD-200U 有 2 路 CAN

# 应答严格匹配：False（默认）接收超时前的第一帧当作应答（与原行为一致）；
# True 则只接受「addr 与本次请求一致且为从机发送(bit28=1)」的帧，避免被无关帧干扰。
STRICT_REPLY_MATCH = False


# --------------------------------------------------------------------------
# 总线封装
# --------------------------------------------------------------------------
class HandCanfd:
    """封装 ZLG USB-CANFD-200U 的收发，简化测试代码。

    默认走 python-can 的 ``zlgcan`` 接口（需安装 zlgcan 及库文件）：
      - device_type=ZCAN_USBCANFD_200U(41)
      - configs 为每通道配置列表（200U 有 2 路），含 仲裁bitrate / 数据dbitrate / 终端电阻
    也支持其它接口（socketcan/pcan 等）的常规创建方式。
    """

    def __init__(self, channel=DEFAULT_CHANNEL, interface=DEFAULT_INTERFACE,
                 bitrate=DEFAULT_BITRATE, data_bitrate=DEFAULT_DATA_BITRATE,
                 dev_id=DEFAULT_DEV_ID, device_type=DEFAULT_DEVICE_TYPE,
                 device_index=DEFAULT_DEVICE_INDEX, libpath=DEFAULT_LIBPATH,
                 resistance=DEFAULT_RESISTANCE, zlg_channels=ZCAN_CHANNELS):
        self.dev_id = dev_id
        self.interface = interface
        self.is_zlg = interface == "zlgcan"
        self.zlg_tx_mode = 0               # ZCanTxMode.NORMAL
        try:
            self.bus = can.Bus(**self._bus_kwargs(
                interface, channel, bitrate, data_bitrate,
                device_type, device_index, libpath, resistance, zlg_channels))
        except Exception as e:
            raise RuntimeError("打开 CAN 总线失败: {}: {}（请确认已安装 zlgcan 及库文件，并接入 USB-CANFD-200U）".format(
                type(e).__name__, e))

    @staticmethod
    def _bus_kwargs(interface, channel, bitrate, data_bitrate,
                    device_type, device_index, libpath, resistance, zlg_channels):
        if interface == "zlgcan":
            # zlgcan 接口：device_type 为必填；configs 为每通道配置（200U 为2路）
            cfg = {"bitrate": bitrate,
                   "dbitrate": data_bitrate,       # 数据段波特率，指定后自动 CANFD(ISO)
                   "resistance": resistance}
            return {
                "interface": interface,
                "device_type": device_type,
                "device_index": device_index,
                "libpath": libpath,
                "configs": [dict(cfg) for _ in range(zlg_channels)],
            }
        # 其它接口（socketcan/pcan 等）
        return {
            "interface": interface,
            "channel": channel,
            "bitrate": bitrate,
            "data_bitrate": data_bitrate,
            "fd": True,
        }

    def close(self):
        try:
            self.bus.shutdown()
        except Exception:
            pass

    # ---------------- 发送 ----------------
    def send(self, addr, rw, data=b"", master_send=True, recv=True, timeout=DEFAULT_RECV_TIMEOUT):
        """按协议拼接 CANID 并发送一帧；若 recv 为 True 则等待从机应答帧。

        返回 (ok, response_dict, message)；
        - 读(rw='r')：data 一般为空，等待应答解析数据
        - 写(rw='w')：data 为要写入的数据，等待从机回写应答
        """
        if len(data) > 64:
            return False, None, "数据超长: {}>64".format(len(data))
        canid = hp.build_canid(addr, rw, self.dev_id, len(data), master_send=master_send)
        msg = can.Message(arbitration_id=canid, data=bytearray(data), is_extended_id=True,
                          is_fd=True, is_fd_overloaded=False)
        try:
            if self.is_zlg:
                self.bus.send(msg, tx_mode=self.zlg_tx_mode)
            else:
                self.bus.send(msg)
        except Exception as e:
            return False, None, "发送失败: {}: {}".format(type(e).__name__, e)

        if not recv:
            return True, None, "ok(sent)"

        # 等待应答：读指令由从机返回数据，写指令回写确认
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rx = self._recv_once(timeout=max(0.05, deadline - time.monotonic()))
            if rx is not None:
                if STRICT_REPLY_MATCH and not self._is_reply(addr, rx):
                    continue
                return True, rx, "ok"
        return False, None, "接收超时(无应答)"

    @staticmethod
    def _is_reply(addr, rx):
        """严格模式下判断该帧是否为本次请求的应答：addr 匹配且为从机发送。"""
        if rx.get("addr") != addr:
            return False
        if rx.get("master_send"):
            return False
        return True

    def _recv_once(self, timeout):
        try:
            msg = self.bus.recv(timeout=timeout)
        except Exception as e:
            print("接收出错: {}".format(e))
            return None
        if msg is None:
            return None
        parsed = hp.parse_canid(msg.arbitration_id)
        data = bytes(msg.data) if msg.data is not None else b""
        return {**parsed, "raw_data": data, "data_int": hp.u16(data, 0)}


# --------------------------------------------------------------------------
# 通用辅助
# --------------------------------------------------------------------------
def _to_int(value, base=0):
    """兼容 int 与字符串（如 '0xC0'/'100'）；无法转换时原样返回。"""
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text, base)
        except (TypeError, ValueError):
            pass
    return value


# --------------------------------------------------------------------------
# 测量入口：供 measurement 形式测试 plan 调用。
# 连接参数一律取上方公共全局前置参数（DEFAULT_*），PLAN 只传数据参数。
# 返回值 dict 键：
#   value  -> 期望量化的数值（读传感器/参数）
#   ok     -> 1/0 成功标志（用于写类/无量化值项，plan 判定 ok==1）
#   pass   -> 该步骤整体是否 PASS
# --------------------------------------------------------------------------
def _open_hand():
    """使用公共全局前置参数建立 CANFD 总线连接。"""
    return HandCanfd(channel=DEFAULT_CHANNEL, interface=DEFAULT_INTERFACE,
                     bitrate=DEFAULT_BITRATE, data_bitrate=DEFAULT_DATA_BITRATE,
                     dev_id=DEFAULT_DEV_ID, device_type=DEFAULT_DEVICE_TYPE,
                     device_index=DEFAULT_DEVICE_INDEX, libpath=DEFAULT_LIBPATH,
                     resistance=DEFAULT_RESISTANCE)


def _run(fn, **kw):
    """打开连接执行 fn(hand, **kw)，正常关闭后返回结果 dict。"""
    hand = _open_hand()
    try:
        return fn(hand, **kw)
    finally:
        hand.close()


# ---- 读类（PLAN 只传数据参数） ----
def mea_read_info():
    return _run(test_read_info)


def mea_read_fault(joints=DEFAULT_JOINTS):
    return _run(test_read_fault, joints=joints)


def mea_read_bus_voltage():
    return _run(test_read_bus_voltage)


def mea_read_current():
    return _run(test_read_current)


def mea_read_temp():
    return _run(test_read_temp)


def mea_read_mode():
    return _run(test_read_mode)


def mea_read_pressure(finger=0):
    return _run(test_read_pressure, finger=finger)


def mea_read_position(joint=1):
    return _run(test_read_position, joint=joint)


def mea_read_speed(joint=1):
    return _run(test_read_speed, joint=joint)


def mea_read_torque(joint=1):
    return _run(test_read_torque, joint=joint)


def mea_read_calib():
    return _run(test_read_reg, addr=0x20)


def mea_read_param(addr=0xC0):
    return _run(test_read_reg, addr=_to_int(addr))


# ---- 写类（measurement，判定 ok==1；有回显参数则判定 value） ----
def mea_zero_calib():
    return _run(test_zero_calib)


def mea_set_position(joint=1, position=100):
    return _run(test_set_position, joint=joint, position=position)


def mea_set_speed(joint=1, speed=100):
    return _run(test_set_speed, joint=joint, speed=speed)


def mea_set_torque(joint=1, torque=100):
    return _run(test_set_torque, joint=joint, torque=torque)


def mea_write_param(addr=0xC0, value=50):
    """堵转参数 R/W：Write → 读回校验 value，返回读回值供判定。"""
    return _run(test_read_write_param, addr=_to_int(addr), value=value)


def mea_save_params():
    return _run(test_save_params)


def mea_factory_reset():
    return _run(test_factory_reset)


# --------------------------------------------------------------------------
# 通用测量辅助
# --------------------------------------------------------------------------
def _cap_info(addr):
    """安全读取指令定义：地址不存在时返回占位信息，避免 KeyError。"""
    info = hp.capability_get(addr) or {}
    return info.get("name", "未知(0x{:02X})".format(addr)), info.get("attr", "-")


def _report(procedure, addr, ok, resp, message, value=None, expected=None):
    name, attr = _cap_info(addr)
    print("\n[{}] 地址=0x{:02X} ({}) 属性={}".format(procedure, addr, name, attr))
    print("    判定: {}".format("PASS" if ok else "FAIL"))
    print("    结果: {}".format(message))
    if resp is not None:
        print("    应答: dev_id=0x{:02X} is_read={} data_len={} data={}".format(
            resp.get("dev_id"), resp.get("is_read"), resp.get("data_len"),
            list(resp.get("raw_data", []))))
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
    """数值范围/一致判定。"""
    if value is None:
        return False, "数值解析为空"
    if expect is not None:
        if isinstance(value, (int, float)) and abs(value - expect) <= tol:
            return True, "值={} 符合期望={}".format(value, expect)
        return False, "值={} 不符合期望={}".format(value, expect)
    if lower is not None and upper is not None:
        if lower <= value <= upper:
            return True, "值={} 在区间[{},{}]".format(value, lower, upper)
        return False, "值={} 不在区间[{},{}]".format(value, lower, upper)
    return True, "值={}".format(value)


# --------------------------------------------------------------------------
# 测试项（每个功能对应协议指令）
# --------------------------------------------------------------------------
def test_read_reg(hand, addr, label=None, lower=None, upper=None):
    """通用读寄存器：按属性 R 校验允许读，读取并数值判定。"""
    if not hp.allowed(addr, "r"):
        return _report("READ", addr, False, None, "该地址属性为 {} 不允许读".format(
            hp.capability_get(addr)["attr"]))
    ok, resp, msg = hand.send(addr, "r", b"")
    val = None
    if ok and resp is not None:
        val = resp.get("data_int")
    cok, desc = True, msg
    if ok:
        cok, desc = _check_value(val, lower=lower, upper=upper)
    return _report(label or _cap_info(addr)[0], addr, ok and cok, resp, desc, value=val)


def test_write_reg(hand, addr, data, label=None):
    """通用写寄存器：按属性 W/RW 校验允许写，填入数据并确认应答。"""
    if not hp.allowed(addr, "w"):
        return _report("WRITE", addr, False, None, "该地址属性为 {} 不允许写".format(
            hp.capability_get(addr)["attr"]))
    ok, resp, msg = hand.send(addr, "w", data)
    return _report(label or _cap_info(addr)[0], addr, ok, resp, msg, value=list(data))


def test_read_info(hand):
    """0x00 读产品信息：型号/SN/软硬件版本/手ID（R）。"""
    ok, resp, msg = hand.send(0x00, "r", b"")
    return _report("产品信息", 0x00, ok, resp, msg)


def test_read_fault(hand, joints=DEFAULT_JOINTS):
    """0x01 读关节故障码（R）。"""
    ok, resp, msg = hand.send(0x01, "r", b"")
    return _report("关节故障码", 0x01, ok, resp, msg, value=resp and resp.get("data_int"))


def test_zero_calib(hand):
    """0x02 0位标定（W）：0失能 / 1使能 / 0x03标定。"""
    ok, resp, msg = hand.send(0x02, "w", bytes([0x03]))
    return _report("0位标定(使能)", 0x02, ok, resp, msg)


def test_set_position(hand, joint=1, position=100):
    """0x03 关节目标位置（W）。"""
    data = (int(position) & 0xFFFF).to_bytes(2, "little")
    ok, resp, msg = hand.send(0x03, "w", data)
    return _report("关节{}目标位置".format(joint), 0x03, ok, resp, msg, value=list(data))


def test_set_speed(hand, joint=1, speed=100):
    """0x04 目标速度（W）。"""
    data = (int(speed) & 0xFFFF).to_bytes(2, "little")
    ok, resp, msg = hand.send(0x04, "w", data)
    return _report("关节{}目标速度".format(joint), 0x04, ok, resp, msg, value=list(data))


def test_set_torque(hand, joint=1, torque=100):
    """0x05 目标扭矩（W）。"""
    data = (int(torque) & 0xFFFF).to_bytes(2, "little")
    ok, resp, msg = hand.send(0x05, "w", data)
    return _report("关节{}目标扭矩".format(joint), 0x05, ok, resp, msg, value=list(data))


def test_read_position(hand, joint=1):
    """0x06 关节当前位置（R）。"""
    return test_read_reg(hand, 0x06, label="关节{}当前位置".format(joint))


def test_read_speed(hand, joint=1):
    """0x07 当前速度（R）。"""
    return test_read_reg(hand, 0x07, label="关节{}当前速度".format(joint))


def test_read_torque(hand, joint=1):
    """0x08 当前扭矩（R）。"""
    return test_read_reg(hand, 0x08, label="关节{}当前扭矩".format(joint))


def test_read_pressure(hand, finger=0):
    """0x09~0x0D 手指压力（R）。"""
    return test_read_reg(hand, 0x09 + finger, label="手指{}压力".format(finger))


def test_read_bus_voltage(hand):
    """0x30 直流总线电压（R）。"""
    return test_read_reg(hand, 0x30, label="直流总线电压")


def test_read_current(hand):
    """0x31 关节电流（R）。"""
    return test_read_reg(hand, 0x31, label="关节电流")


def test_read_temp(hand):
    """0x32 关节温度（R）。"""
    return test_read_reg(hand, 0x32, label="关节温度")


def test_read_mode(hand):
    """0x33 工作模式（R）。"""
    return test_read_reg(hand, 0x33, label="工作模式")


def test_read_write_param(hand, addr, value, label=None):
    """0xC0~0xC2 堵转参数 R/W：先读回默认值，再写入设定值并读回确认。"""
    r_ok, r_resp, r_msg = hand.send(addr, "r", b"")
    r_val = r_resp and r_resp.get("data_int")
    w_ok, w_resp, w_msg = hand.send(addr, "w", (int(value) & 0xFFFF).to_bytes(2, "little"))
    rr_ok, rr_resp, rr_msg = hand.send(addr, "r", b"")
    rr_val = rr_resp and rr_resp.get("data_int")
    cok, desc = _check_value(rr_val, expect=int(value), tol=0)
    msg = "读回默认={} 写入={} 读回={} {}（写:{} 读:{}）".format(
        r_val, value, rr_val, desc, "OK" if w_ok else "FAIL", "OK" if rr_ok else "FAIL")
    return _report(label or _cap_info(addr)[0], addr,
                   w_ok and rr_ok and cok, rr_resp, msg, value=rr_val, expected=value)


def test_save_params(hand):
    """0xCF 保存参数（W）。"""
    ok, resp, msg = hand.send(0xCF, "w", b"")
    return _report("保存参数", 0xCF, ok, resp, msg)


def test_factory_reset(hand):
    """0xC8 恢复出厂设置（W）。"""
    ok, resp, msg = hand.send(0xC8, "w", b"")
    return _report("恢复出厂设置", 0xC8, ok, resp, msg)


# --------------------------------------------------------------------------
# 汇总运行
# --------------------------------------------------------------------------
def run_all(hand, joints=DEFAULT_JOINTS):
    """按协议执行一轮代表性功能测试（读类 + 写类 + 参数读写）。"""
    results = [
        test_read_info(hand),
        test_read_fault(hand, joints),
        test_read_bus_voltage(hand),
        test_read_current(hand),
        test_read_temp(hand),
        test_read_mode(hand),
    ] + [test_read_pressure(hand, f) for f in range(5)]
    for j in range(1, min(joints, 4) + 1):
        results += [test_read_position(hand, j), test_read_speed(hand, j), test_read_torque(hand, j)]
    # 写入类（谨慎，避免误动作；如需可注释）
    results += [
        test_set_position(hand, 1, position=100),
        test_set_speed(hand, 1, speed=100),
        test_set_torque(hand, 1, torque=100),
        test_read_write_param(hand, 0xC0, 50),
        test_read_write_param(hand, 0xC1, 500),
        test_read_write_param(hand, 0xC2, 700),
        test_save_params(hand),
    ]

    passed = sum(1 for r in results if r["pass"])
    total = len(results)
    print("\n==================== 汇总 ====================")
    print("通过 {}/{}".format(passed, total))
    print("结果: {}".format("PASS" if passed == total else "FAIL"))
    return passed == total


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _direction(attr):
    """把属性 R/W/RW 转成简洁方向描述。"""
    a = str(attr or "").upper()
    if "R" in a and "W" in a:
        return "读/写"
    if "R" in a:
        return "读"
    return "写"


def build_parser():
    p = argparse.ArgumentParser(description="吉翼灵巧手 CANFD 功能测试 (ZLG USB-CANFD-200U)")
    p.add_argument("--interface", default=DEFAULT_INTERFACE, help="python-can 接口(默认 zlgcan)")
    p.add_argument("--chan", type=int, default=DEFAULT_CHANNEL, help="通道号(默认0)")
    p.add_argument("--bitrate", type=int, default=DEFAULT_BITRATE, help="仲裁段波特率(默认1000000)")
    p.add_argument("--data-bitrate", type=int, default=DEFAULT_DATA_BITRATE,
                   help="数据段波特率(默认5000000)")
    p.add_argument("--device-type", type=int, default=DEFAULT_DEVICE_TYPE,
                   help="ZLG 设备类型枚举值(默认41=USBCANFD_200U)"
                        "；如 42=USBCANFD_100U, 43=USBCANFD_MINI")
    p.add_argument("--device-index", type=int, default=DEFAULT_DEVICE_INDEX,
                   help="ZLG 设备索引(默认0)")
    p.add_argument("--libpath", default=DEFAULT_LIBPATH,
                   help="zlgcan 库文件路径(默认 library/)")
    p.add_argument("--resistance", type=int, default=DEFAULT_RESISTANCE,
                   help="终端电阻 1=120Ω(默认1)")
    p.add_argument("--dev-id", type=lambda x: int(x, 0), default=DEFAULT_DEV_ID,
                   help="设备ID(0x01左手/0x02右手)")
    p.add_argument("--read", type=lambda x: int(x, 0), help="读指定寄存器地址,如 0x06")
    p.add_argument("--write", type=lambda x: int(x, 0), help="写指定寄存器地址,如 0x03")
    p.add_argument("--value", type=lambda x: int(x, 0), default=100, help="写指令数值")
    p.add_argument("--joint", type=int, default=1, help="写指令关节号(用于标签)")
    p.add_argument("--list", action="store_true", help="列出协议全部指令")
    p.add_argument("--run-all", action="store_true", help="运行完整功能测试")
    return p


def cmd_list():
    print("{:<6} {:<6} {:<40} {:<6} {:<6}".format("地址", "长度", "功能", "属性", "方向"))
    for addr in sorted(hp.CAPABILITY):
        info = hp.CAPABILITY[addr]
        print("0x{:02X}  {:<4} {:<40} {:<6} {:<6}".format(
            addr, info["len"], info["name"], info["attr"], _direction(info["attr"])))


def main():
    args = build_parser().parse_args()

    if args.list:
        cmd_list()
        return 0

    try:
        hand = HandCanfd(channel=args.chan, interface=args.interface,
                         bitrate=args.bitrate, data_bitrate=args.data_bitrate,
                         dev_id=args.dev_id, device_type=args.device_type,
                         device_index=args.device_index, libpath=args.libpath,
                         resistance=args.resistance)
    except RuntimeError as e:
        print(e)
        return 1
    print("CANFD 已打开: chan={} bitrate={} data_bitrate={} dev_id=0x{:02X} "
          "device_type={} device_index={} libpath={}".format(
              args.chan, args.bitrate, args.data_bitrate, args.dev_id,
              args.device_type, args.device_index, args.libpath))
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