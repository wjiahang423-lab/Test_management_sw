# -*- coding: utf-8 -*-
"""意优 RP 关节 CANopen 驱动

底层支持两种适配器，默认优先「创芯 USB_CAN_FD」（libcontrolcanfd.so，CAN-FD 模式），
找不到时回退「ZLG USB-CANFD」（zlgcan）。意优 RP 关节是 CAN-FD 节点，
创芯通道必须按 CAN-FD 初始化，否则收发均无应答。

依赖：
    创芯：无需额外 Python 包，只需 libcontrolcanfd.so（随目录或工程提供）；
    ZLG ：pip install python-can zlgcan，并准备 ZLG 动态库。
"""
from __future__ import annotations

import os
import struct
import time
from ctypes import (
    CDLL, Structure, Union, byref, c_ubyte, c_uint, c_ulong, c_ulonglong,
    c_ushort, c_void_p,
)

# ============================================================ 常量 / 对象 ====
# CiA402 工作模式
MODE_POSITION = 1      # 轮廓位置 PP
MODE_VELOCITY = 3      # 轮廓速度 PV
MODE_TORQUE = 4        # 轮廓扭矩 PT
MODE_CSP = 8           # 周期同步位置 CSP
MODE_NAMES = {1: "位置(PP)", 3: "速度(PV)", 4: "转矩(PT)", 8: "周期同步位置(CSP)"}

# 常用 CANopen 对象
OBJ_MODE       = 0x6060
OBJ_MODE_DISP  = 0x6061
OBJ_CTRLWORD   = 0x6040
OBJ_STATWORD   = 0x6041
OBJ_TARGET_POS = 0x607A
OBJ_ACT_POS    = 0x6064
OBJ_ACT_VEL    = 0x606C
OBJ_ACT_TOR    = 0x6077
OBJ_TARGET_VEL = 0x60FF
OBJ_GEAR       = 0x6091   # sub2 = 位置单位/输出圈(当量)
OBJ_ERR_CODE   = 0x603F
OBJ_SAVE       = 0x1010

CW_FAULT_RESET = 0x80
CW_SHUTDOWN    = 0x06
CW_SWITCH_ON   = 0x07
CW_ENABLE      = 0x0F

DEFAULT_NODE = 1
DEFAULT_CHANNEL = 0
DEFAULT_BITRATE = 1_000_000
DEFAULT_DEVICE_INDEX = 0
DEFAULT_ENC_PER_REV = 524288.0


# ============================================================ 角度转换 ======
def pulses_to_deg(pulses, per_rev: float = DEFAULT_ENC_PER_REV) -> float:
    """位置脉冲 -> 角度(度):  deg = 脉冲 / 当量 × 360。"""
    return float(pulses) / max(float(per_rev), 1.0) * 360.0


def deg_to_pulses(deg, per_rev: float = DEFAULT_ENC_PER_REV) -> int:
    """角度(度) -> 位置脉冲:  脉冲 = deg / 360 × 当量。"""
    return int(round(float(deg) / 360.0 * per_rev))


def degps_to_pps(deg_per_s, per_rev: float = DEFAULT_ENC_PER_REV) -> int:
    """角速度(°/s) -> 下发给驱动的速度(脉冲/s, 0x6081/0x60FF)。"""
    return int(round(float(deg_per_s) * per_rev / 360.0))


def pps_to_rpm(pps, per_rev: float = DEFAULT_ENC_PER_REV) -> float:
    """速度脉冲/s -> rpm（显示用）。"""
    return float(pps) * 60.0 / max(float(per_rev), 1.0)


# ======================================================= 创芯 USB_CAN_FD ====
VCI_USBCAN2 = 41
STATUS_OK = 1
_TYPE_CAN = 0
_TYPE_CANFD = 1


class _CxChannelInit(Structure):
    _fields_ = [("acc_code", c_uint), ("acc_mask", c_uint), ("reserved", c_uint),
                ("filter", c_ubyte), ("timing0", c_ubyte), ("timing1", c_ubyte),
                ("mode", c_ubyte)]


class _CxChannelInitFd(Structure):
    _fields_ = [("acc_code", c_uint), ("acc_mask", c_uint), ("abit_timing", c_uint),
                ("dbit_timing", c_uint), ("brp", c_uint), ("filter", c_ubyte),
                ("mode", c_ubyte), ("pad", c_ushort), ("reserved", c_uint)]


class _CxChannelInitUnion(Union):
    _fields_ = [("can", _CxChannelInit), ("canfd", _CxChannelInitFd)]


class _CxInitConfig(Structure):
    _fields_ = [("can_type", c_uint), ("config", _CxChannelInitUnion)]


class _CxCanFrame(Structure):
    _fields_ = [("can_id", c_uint, 29), ("err", c_uint, 1), ("rtr", c_uint, 1),
                ("eff", c_uint, 1), ("can_dlc", c_ubyte), ("__pad", c_ubyte),
                ("__res0", c_ubyte), ("__res1", c_ubyte), ("data", c_ubyte * 8)]


class _CxCanFdFrame(Structure):
    _fields_ = [("can_id", c_uint, 29), ("err", c_uint, 1), ("rtr", c_uint, 1),
                ("eff", c_uint, 1), ("len", c_ubyte), ("brs", c_ubyte, 1),
                ("esi", c_ubyte, 1), ("__res", c_ubyte, 6), ("__res0", c_ubyte),
                ("__res1", c_ubyte), ("data", c_ubyte * 64)]


class _CxTransmit(Structure):
    _fields_ = [("frame", _CxCanFrame), ("transmit_type", c_uint)]


class _CxReceive(Structure):
    _fields_ = [("frame", _CxCanFrame), ("timestamp", c_ulonglong)]


class _CxReceiveFd(Structure):
    _fields_ = [("frame", _CxCanFdFrame), ("timestamp", c_ulonglong)]


def _find_cx_so():
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "USB_CAN_FD_Driver"),
        os.path.normpath(os.path.join(here, "..", "意优关节RP", "USB_CAN_FD_Driver")),
        os.path.normpath(os.path.join(here, "..", "..", "library", "linux", "x86_64")),
        "/home/jyzn/桌面/项目/意优/USB_CAN_FD_Driver",
    ]
    for d in candidates:
        so = os.path.join(d, "libcontrolcanfd.so")
        if os.path.exists(so):
            return so
    return None


class CxTransport:
    """创芯 USB_CAN_FD 传输层（CAN-FD 模式），提供 send/recv/close。"""

    def __init__(self, channel=0, bitrate=DEFAULT_BITRATE, device_index=0,
                 dbit_baud=None, so_path=None):
        self.channel = int(channel) % 2
        self.so_path = so_path or _find_cx_so()
        if not self.so_path:
            raise RuntimeError("未找到 libcontrolcanfd.so")
        self.dll = CDLL(self.so_path)
        self._bind()
        dbit_baud = bitrate if dbit_baud is None else dbit_baud
        self.dev = self.dll.ZCAN_OpenDevice(VCI_USBCAN2, 0, int(device_index))
        if not self.dev:
            raise RuntimeError("打开 USB_CAN_FD 设备失败(检查连接/权限)")
        self._chans = []
        try:
            for ch in (0, 1):
                self.dll.ZCAN_SetAbitBaud(self.dev, ch, bitrate)
                self.dll.ZCAN_SetDbitBaud(self.dev, ch, dbit_baud)
                self.dll.ZCAN_SetCANFDStandard(self.dev, ch, 0)   # ISO
            cfg = _CxInitConfig()
            cfg.can_type = _TYPE_CANFD
            cfg.config.canfd.mode = 0
            for ch in (0, 1):
                h = self.dll.ZCAN_InitCAN(self.dev, ch, byref(cfg))
                if not h:
                    raise RuntimeError("初始化通道%d失败" % ch)
                self.dll.ZCAN_StartCAN(h)
                self._chans.append(h)
        except Exception:
            self.close()
            raise
        self.chan = self._chans[self.channel]

    def _bind(self):
        d = self.dll
        d.ZCAN_OpenDevice.restype = c_void_p
        d.ZCAN_InitCAN.restype = c_void_p
        d.ZCAN_SetAbitBaud.argtypes = (c_void_p, c_ulong, c_ulong)
        d.ZCAN_SetDbitBaud.argtypes = (c_void_p, c_ulong, c_ulong)
        d.ZCAN_SetCANFDStandard.argtypes = (c_void_p, c_ulong, c_ulong)
        d.ZCAN_InitCAN.argtypes = (c_void_p, c_ulong, c_void_p)
        d.ZCAN_StartCAN.argtypes = (c_void_p,)
        d.ZCAN_Transmit.argtypes = (c_void_p, c_void_p, c_ulong)
        d.ZCAN_GetReceiveNum.argtypes = (c_void_p, c_ulong)
        d.ZCAN_Receive.argtypes = (c_void_p, c_void_p, c_ulong, c_ulong)
        d.ZCAN_ReceiveFD.argtypes = (c_void_p, c_void_p, c_ulong, c_ulong)
        d.ZCAN_ResetCAN.argtypes = (c_void_p,)
        d.ZCAN_CloseDevice.argtypes = (c_void_p,)

    def send(self, arb_id, data, extended=False):
        buf = (_CxTransmit * 1)()
        buf[0].frame.can_id = int(arb_id) & 0x1FFFFFFF
        buf[0].frame.eff = 1 if extended else 0
        data = bytes(data)
        buf[0].frame.can_dlc = len(data)
        for i, b in enumerate(data[:8]):
            buf[0].frame.data[i] = b
        buf[0].transmit_type = 0
        return self.dll.ZCAN_Transmit(self.chan, buf, 1)

    def recv(self, timeout=0.05):
        chan = self.chan
        deadline = time.time() + timeout
        while True:
            n = self.dll.ZCAN_GetReceiveNum(chan, _TYPE_CAN)
            if n > 0:
                buf = (_CxReceive * n)()
                got = self.dll.ZCAN_Receive(chan, buf, n, 0)
                if got > 0:
                    f = buf[0].frame
                    return int(f.can_id), bytes(f.data[:min(f.can_dlc, 8)]), bool(f.eff)
            n = self.dll.ZCAN_GetReceiveNum(chan, _TYPE_CANFD)
            if n > 0:
                buf = (_CxReceiveFd * n)()
                got = self.dll.ZCAN_ReceiveFD(chan, buf, n, 0)
                if got > 0:
                    f = buf[0].frame
                    return int(f.can_id), bytes(f.data[:min(f.len, 64)]), bool(f.eff)
            if time.time() >= deadline:
                return None
            time.sleep(0.001)

    def close(self):
        for h in getattr(self, "_chans", []):
            try:
                self.dll.ZCAN_ResetCAN(h)
            except Exception:
                pass
        if getattr(self, "dev", None):
            try:
                self.dll.ZCAN_CloseDevice(self.dev)
            except Exception:
                pass
        self.dev = None
        self._chans = []


# ============================================================ ZLG 传输 ======
def _find_zlg_lib():
    here = os.path.dirname(os.path.abspath(__file__))
    for d in (os.path.normpath(os.path.join(here, "..", "..", "library")),
              os.path.normpath(os.path.join(here, "..", "意优关节RP", "library"))):
        if os.path.exists(os.path.join(d, "linux", "x86_64")):
            return d
    return None


class ZlgTransport:
    """ZLG USB-CANFD 传输层（zlgcan），提供 send/recv/close。"""

    def __init__(self, channel=0, bitrate=DEFAULT_BITRATE, device_index=0, libpath=None):
        import can
        from zlgcan.zlgcan import ZCanBus, ZCanChlType, ZCANDeviceType
        self._can = can
        self.channel = int(channel)
        cfg = {"bitrate": int(bitrate), "chl_type": ZCanChlType.CAN, "resistance": 1}
        self.bus = ZCanBus(libpath=libpath or _find_zlg_lib(),
                           device_type=ZCANDeviceType.ZCAN_USBCANFD_200U,
                           device_index=int(device_index),
                           configs=[dict(cfg), dict(cfg)])

    def send(self, arb_id, data, extended=False):
        msg = self._can.Message(arbitration_id=int(arb_id), is_extended_id=bool(extended),
                                is_fd=False, data=bytes(data))
        msg.channel = self.channel
        self.bus.send(msg, timeout=0.05)

    def recv(self, timeout=0.05):
        msg = self.bus.recv(timeout=timeout)
        if msg is None:
            return None
        return int(msg.arbitration_id), bytes(msg.data), bool(msg.is_extended_id)

    def close(self):
        try:
            self.bus.shutdown()
        except Exception:
            pass


# ============================================================ 驱动主体 ======
class EyouHeadDriver:
    """意优 RP 关节 CANopen 驱动（单条总线，多节点）。"""

    def __init__(self, driver="cx", channel=DEFAULT_CHANNEL, bitrate=DEFAULT_BITRATE,
                 device_index=DEFAULT_DEVICE_INDEX, dbit_baud=None, libpath=None,
                 so_path=None):
        self.driver = driver
        self.channel = channel
        self.bitrate = bitrate
        self.device_index = device_index
        self.dbit_baud = dbit_baud
        self.libpath = libpath
        self.so_path = so_path
        self.bus = None
        self._enc = {}

    # ---------------------------------------------------------- 连接/关闭
    def connect(self) -> bool:
        """打开总线并 NMT 启动所有节点。成功返回 True。"""
        self.close()
        try:
            if self.driver == "zlg":
                self.bus = ZlgTransport(self.channel, self.bitrate, self.device_index, self.libpath)
            else:
                self.bus = CxTransport(self.channel, self.bitrate, self.device_index,
                                       self.dbit_baud, self.so_path)
            self.bus.send(0x000, bytes([0x01, 0x00, 0, 0, 0, 0, 0, 0]))   # NMT start(all)
            time.sleep(0.05)
            return True
        except Exception:
            self.close()
            return False

    def close(self):
        """关闭总线。"""
        if self.bus is not None:
            try:
                self.bus.close()
            except Exception:
                pass
        self.bus = None
        self._enc.clear()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------------------------------------------------------- SDO 读写
    def _require(self):
        if self.bus is None:
            raise RuntimeError("总线未连接，请先 connect()")

    def sdo_read(self, node: int, index: int, sub: int = 0, timeout: float = 0.4):
        """读取对象字典，返回数据字节（失败/超时返回 None）。"""
        self._require()
        node = int(node)
        lo, hi = index & 0xFF, (index >> 8) & 0xFF
        self.bus.send(0x600 + node, bytes([0x40, lo, hi, sub & 0xFF, 0, 0, 0, 0]))
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = self.bus.recv(0.05)
            if r is None:
                continue
            arb, data, _ = r
            if arb != 0x580 + node or len(data) < 8:
                continue
            if data[1] != lo or data[2] != hi or data[3] != sub:
                continue
            if data[0] in (0x43, 0x4B, 0x4F, 0x47):
                n = {0x43: 4, 0x4B: 2, 0x4F: 1, 0x47: 3}[data[0]]
                return bytes(data[4:4 + n])
            if data[0] == 0x80:      # SDO 中止
                return None
        return None

    def sdo_write(self, node: int, index: int, sub: int, data, timeout: float = 0.4) -> bool:
        """写入 1/2/4 字节对象（失败/超时返回 False）。"""
        self._require()
        node = int(node)
        data = bytes(data)
        cmd = {1: 0x2F, 2: 0x2B, 4: 0x23}[len(data)]
        lo, hi = index & 0xFF, (index >> 8) & 0xFF
        self.bus.send(0x600 + node,
                      bytes([cmd, lo, hi, sub & 0xFF] + list(data) + [0] * (4 - len(data))))
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = self.bus.recv(0.05)
            if r is None:
                continue
            arb, resp, _ = r
            if arb != 0x580 + node or len(resp) < 4:
                continue
            if resp[0] == 0x60 and resp[1] == lo and resp[2] == hi and resp[3] == sub:
                return True
            if resp[0] == 0x80:
                return False
        return False

    # ---------------------------------------------------------- 1. 使能
    def enable(self, node: int = DEFAULT_NODE, mode: int = MODE_POSITION) -> bool:
        """按 CiA402 使能：设模式 -> 目标=当前位置(防跳动) -> 0x06->0x07->0x0F。
        返回是否进入 operation-enable(状态字 bit2)。"""
        node = int(node)
        self.set_mode(node, mode)
        if mode in (MODE_POSITION, MODE_CSP):
            cur = self.read_position(node)
            self.sdo_write(node, OBJ_TARGET_POS, 0, int(cur).to_bytes(4, "little", signed=True))
        if self.read_status_word(node) & 0x0008:      # 有故障先复位
            self.sdo_write(node, OBJ_CTRLWORD, 0, bytes([CW_FAULT_RESET, 0, 0, 0]))
            self.sdo_write(node, OBJ_CTRLWORD, 0, bytes([0, 0, 0, 0]))
            time.sleep(0.02)
        for cw in (CW_SHUTDOWN, CW_SWITCH_ON, CW_ENABLE):
            self.sdo_write(node, OBJ_CTRLWORD, 0, bytes([cw, 0, 0, 0]))
            time.sleep(0.02)
        return bool(self.read_status_word(node) & 0x0004)

    def disable(self, node: int = DEFAULT_NODE) -> bool:
        """失能（控制字清零）。"""
        return self.sdo_write(node, OBJ_CTRLWORD, 0, bytes([0, 0, 0, 0]))

    # ---------------------------------------------------------- 2. 模式
    def set_mode(self, node: int, mode: int) -> bool:
        """设置工作模式：1=位置(PP) 3=速度(PV) 4=转矩(PT) 8=周期同步位置(CSP)。"""
        return self.sdo_write(node, OBJ_MODE, 0, bytes([int(mode)]))

    def set_position_mode(self, node: int = DEFAULT_NODE) -> bool:
        return self.set_mode(node, MODE_POSITION)

    def set_velocity_mode(self, node: int = DEFAULT_NODE) -> bool:
        return self.set_mode(node, MODE_VELOCITY)

    def set_torque_mode(self, node: int = DEFAULT_NODE) -> bool:
        return self.set_mode(node, MODE_TORQUE)

    def set_csp_mode(self, node: int = DEFAULT_NODE) -> bool:
        return self.set_mode(node, MODE_CSP)

    def read_mode(self, node: int = DEFAULT_NODE, display: bool = True) -> int:
        """读当前模式：display=True 读 0x6061，False 读目标模式 0x6060。"""
        idx = OBJ_MODE_DISP if display else OBJ_MODE
        d = self.sdo_read(node, idx, 0)
        return int(d[0]) if d else -1

    # ---------------------------------------------------------- 4. 角度读取
    def read_enc_per_rev(self, node: int = DEFAULT_NODE) -> float:
        """读取位置当量(0x6091:02, 每圈脉冲)，失败用默认值并缓存。"""
        node = int(node)
        if node in self._enc:
            return self._enc[node]
        d = self.sdo_read(node, OBJ_GEAR, 2)
        value = int.from_bytes(d, "little") if d else 0
        enc = float(value) if 1000 <= value <= 100_000_000 else DEFAULT_ENC_PER_REV
        self._enc[node] = enc
        return enc

    def read_angle(self, node: int = DEFAULT_NODE, zero: float = 0.0) -> float:
        """读取当前角度(度)：0x6064 脉冲 -> 度，可减去软件零点脉冲 zero。"""
        node = int(node)
        pos = self.read_position(node)
        return pulses_to_deg(pos - zero, self.read_enc_per_rev(node))

    # ---------------------------------------------------------- 5. 数据读取
    def read_position(self, node: int = DEFAULT_NODE) -> int:
        """读实际位置 0x6064（有符号脉冲）。"""
        d = self.sdo_read(node, OBJ_ACT_POS, 0)
        return int.from_bytes(d, "little", signed=True) if d else 0

    def read_velocity(self, node: int = DEFAULT_NODE) -> int:
        """读实际速度 0x606C（有符号脉冲/s）。"""
        d = self.sdo_read(node, OBJ_ACT_VEL, 0)
        return int.from_bytes(d, "little", signed=True) if d else 0

    def read_torque(self, node: int = DEFAULT_NODE) -> int:
        """读实际扭矩 0x6077（有符号）。"""
        d = self.sdo_read(node, OBJ_ACT_TOR, 0)
        return int.from_bytes(d, "little", signed=True) if d else 0

    def read_status_word(self, node: int = DEFAULT_NODE) -> int:
        """读状态字 0x6041。"""
        d = self.sdo_read(node, OBJ_STATWORD, 0)
        return int.from_bytes(d[:2], "little") if d else 0

    def read_error_code(self, node: int = DEFAULT_NODE) -> int:
        """读错误码 0x603F。"""
        d = self.sdo_read(node, OBJ_ERR_CODE, 0)
        return int.from_bytes(d[:2], "little") if d else 0

    def read_data(self, node: int = DEFAULT_NODE, index: int = 0x6064, sub: int = 0):
        """读任意对象字典：
        十进制/十六进制字符串或整数索引；返回 {index, sub, raw, value, text}。
        value 为整数（≤4 字节，按小端有符号/无符号）或字符串/十六进制文本。"""
        node = int(node)
        index, sub = _parse_addr(index, sub)
        raw = self.sdo_read(node, index, sub)
        result = {"node": node, "index": index, "sub": sub, "raw": raw,
                  "value": None, "text": "超时" if raw is None else ""}
        if raw is None:
            return result
        if index in (0x1008, 0x2002, 0x2082, 0x2083, 0x2084):
            text = raw.split(b"\x00", 1)[0].decode("ascii", "ignore").strip()
            result["value"], result["text"] = text, text
        elif 0 < len(raw) <= 4:
            signed = int.from_bytes(raw, "little", signed=True)
            unsigned = int.from_bytes(raw, "little", signed=False)
            result["value"] = signed
            result["text"] = "%d (0x%X)" % (unsigned, unsigned)
        else:
            result["text"] = raw.hex(" ").upper()
        return result

    # ---------------------------------------------------------- 运动下发
    def move_to_pulses(self, node: int, target_pulses: int, mode: int = MODE_POSITION) -> bool:
        """下发绝对目标位置(硬件脉冲)并按模式触发运动。"""
        node = int(node)
        self.set_mode(node, mode)
        ok = self.sdo_write(node, OBJ_TARGET_POS, 0,
                            int(target_pulses).to_bytes(4, "little", signed=True))
        if mode == MODE_POSITION:
            self.sdo_write(node, OBJ_CTRLWORD, 0, bytes([0x2F, 0, 0, 0]))
            self.sdo_write(node, OBJ_CTRLWORD, 0, bytes([0x3F, 0, 0, 0]))
        return ok

    def move_to_angle(self, node: int, deg: float, zero: float = 0.0) -> bool:
        """下发绝对角度(度)，自动换算脉冲。"""
        node = int(node)
        pulses = int(round(zero)) + deg_to_pulses(deg, self.read_enc_per_rev(node))
        return self.move_to_pulses(node, pulses)

    def set_target_velocity(self, node: int, deg_per_s: float) -> bool:
        """速度模式目标速度(°/s)，写入 0x60FF。"""
        node = int(node)
        units = degps_to_pps(deg_per_s, self.read_enc_per_rev(node))
        return self.sdo_write(node, OBJ_TARGET_VEL, 0, int(units).to_bytes(4, "little", signed=True))


def _parse_addr(index, sub=0):
    """支持 int、'0x1018'、'0x1018:01'、'0x1018/01' 等写法。"""
    if isinstance(index, str):
        text = index.strip().replace("/", ":")
        if ":" in text:
            a, b = text.split(":", 1)
            return int(a, 0), int(b, 0)
        return int(text, 0), int(sub)
    return int(index), int(sub)


# 便捷单例（可选）：from driver import driver
driver = EyouHeadDriver()
