# -*- coding: utf-8 -*-
"""意优 RP 系列行星关节 CANopen 纯驱动(无 UI, 无 Qt)。

底层: ZLG USB-CANFD-200U, 经典 CAN 标准帧, 1Mbps。
协议: CANopen CiA 301/402 —— NMT + SDO + 轮廓位置(PP)。

换算关系(厂商手册 3.3/3.4):
    deg   = 脉冲 / 当量 × 360
    脉冲  = deg / 360 × 当量            (当量 = 0x6091:02, 每圈脉冲数)
    速度  = 脉冲/s                      (0x6081/60FF)
软件标0: 上位机记住当前脉冲为零点(0°) —— 左=+90°, 右=-90°, 不写电机。

依赖: pip install python-can zlgcan            (ZLG 库见工程 library/)
"""
from __future__ import annotations

import struct
import time

import can

from zlgcan.zlgcan import ZCanBus, ZCanChlType, ZCANDeviceType

# ---------------------------------------------------------------- 常量 ----
ENC_PER_REV_DEFAULT = 524288.0      # 当量: 每圈脉冲(连接时从 0x6091:02 读, 覆盖)
MIN_DEG = -90.0                     # 左 = 0~+90
MAX_DEG = 90.0                      # 右 = 0~-90

MODE_PP = 1                         # 轮廓位置
MODE_PV = 3                         # 轮廓速度
MODE_CSP = 8                        # 周期同步位置
MODE_NAMES = {1: "轮廓位置PP", 3: "轮廓速度PV", 8: "周期同步CSP"}

OBJ_MODE       = 0x6060
OBJ_MODE_DISP  = 0x6061
OBJ_CTRLWORD   = 0x6040
OBJ_STATWORD   = 0x6041
OBJ_TARGET_POS = 0x607A
OBJ_ACT_POS    = 0x6064
OBJ_ACT_VEL    = 0x606C
OBJ_ACT_TOR    = 0x6077
OBJ_GEAR       = 0x6091
OBJ_PP_VEL     = 0x6081
OBJ_PP_ACC     = 0x6083
OBJ_PP_DEC     = 0x6084
OBJ_TARGET_VEL = 0x60FF
OBJ_ERR_CODE   = 0x603F

CW_FAULT_RESET = 0x80
CW_SHUTDOWN    = 0x06
CW_SWITCH_ON   = 0x07
CW_ENABLE      = 0x0F
CW_NEWSET      = 0x2F     # enable + bit5立即
CW_MOVE        = 0x3F     # enable + bit5立即 + bit4新设定点

ERR_NAMES = {0x3210: "过压", 0x3220: "欠压", 0x3230: "过载",
             0x4210: "温度过高", 0x7121: "堵转", 0x7310: "超速",
             0x8130: "心跳掉线", 0x8500: "速度误差大", 0x8611: "位置误差大"}


def pulses_to_deg(pulses, per_rev=ENC_PER_REV_DEFAULT) -> float:
    return pulses / max(float(per_rev), 1.0) * 360.0


def deg_to_pulses(deg, per_rev=ENC_PER_REV_DEFAULT) -> int:
    deg = max(MIN_DEG, min(MAX_DEG, float(deg)))
    return int(round(deg / 360.0 * per_rev))


def degps_to_pps(deg_s, per_rev=ENC_PER_REV_DEFAULT) -> int:
    return int(round(deg_s * per_rev / 360.0))


def pps_to_rpm(pps, per_rev=ENC_PER_REV_DEFAULT) -> float:
    return pps * 60.0 / max(float(per_rev), 1.0)


# ------------------------------------------------------------ 总线驱动 ----
def open_bus(channel=0, bitrate=1000000, libpath=None,
             device_type=ZCANDeviceType.ZCAN_USBCANFD_200U,
             device_index=0, zlg_channels=2):
    """打开一条经典CAN总线并 NMT 启动所有节点。多节点共用同一个 bus。"""
    cfg = {"bitrate": bitrate, "chl_type": ZCanChlType.CAN, "resistance": 1}
    bus = ZCanBus(libpath=libpath, device_type=device_type,
                  device_index=device_index,
                  configs=[dict(cfg) for _ in range(max(1, zlg_channels))])
    msg = can.Message(arbitration_id=0x000, is_extended_id=False, is_fd=False,
                      data=bytes([0x01, 0x00] + [0] * 6))
    msg.channel = channel
    bus.send(msg, timeout=0.05)
    return bus


class RPJoint:
    """单节点 CANopen 同步驱动(阻塞式一问一答)。支持与其它节点共享同一个 bus。"""

    def __init__(self, node=1, channel=0, bitrate=1000000,
                 libpath=None, device_type=ZCANDeviceType.ZCAN_USBCANFD_200U,
                 device_index=0, zlg_channels=2, bus=None):
        self.node = node
        self.channel = channel
        if bus is not None:
            self.bus = bus
            self._owns_bus = False                       # 共享总线
        else:
            self.bus = open_bus(channel, bitrate, libpath,
                                device_type, device_index, zlg_channels)
            self._owns_bus = True
        time.sleep(0.05)
        self.enc_per_rev = self._read_enc_per_rev() or ENC_PER_REV_DEFAULT

    # ---------------- 底层 SDO ----------------
    def _tx(self, arb_id, data, is_ext=False):
        msg = can.Message(arbitration_id=arb_id, is_extended_id=is_ext,
                          is_fd=False, data=bytes(data))
        msg.channel = self.channel
        self.bus.send(msg, timeout=0.05)

    def sdo_upload(self, idx, sub, timeout=0.15):
        lo, hi = idx & 0xFF, (idx >> 8) & 0xFF
        self._tx(0x600 + self.node, bytes([0x40, lo, hi, sub & 0xFF, 0, 0, 0, 0]))
        t0 = time.time()
        while time.time() - t0 < timeout:
            m = self.bus.recv(timeout=0.05)
            if m is None or m.arbitration_id != 0x580 + self.node or len(m.data) < 8:
                continue
            d = bytes(m.data)
            if d[1] != lo or d[2] != hi or d[3] != sub:
                continue
            if d[0] in (0x43, 0x4B, 0x4F, 0x47):
                n = {0x43: 4, 0x4B: 2, 0x4F: 1, 0x47: 2}.get(d[0], 4)
                return d[4:4 + n]
            if d[0] == 0x80:                       # SDO 中止
                return b"\x80" + bytes(d[4:8])
        return None

    def sdo_download(self, idx, sub, data, timeout=0.15):
        data = bytes(data)
        cmd = {1: 0x2F, 2: 0x2B, 4: 0x23}[len(data)]
        lo, hi = idx & 0xFF, (idx >> 8) & 0xFF
        self._tx(0x600 + self.node,
                 bytes([cmd, lo, hi, sub & 0xFF] + list(data) + [0] * (4 - len(data))))
        t0 = time.time()
        while time.time() - t0 < timeout:
            m = self.bus.recv(timeout=0.05)
            if m is None or m.arbitration_id != 0x580 + self.node or len(m.data) < 4:
                continue
            d = bytes(m.data)
            if d[0] == 0x60 and d[1] == lo and d[2] == hi and d[3] == sub:
                return True
            if d[0] == 0x80:
                return False
        return False

    # ---------------- 读数 ----------------
    def _read_enc_per_rev(self):
        r = self.sdo_upload(OBJ_GEAR, 2)
        if r and not (r[0] == 0x80):
            v = int.from_bytes(r, "little")
            if 1000 <= v <= 100_000_000:
                return float(v)
        return None

    def _i32(self, r):
        if not r or r[0] == 0x80:
            return 0
        return int.from_bytes(r, "little", signed=True)

    def read_pos(self):
        return self._i32(self.sdo_upload(OBJ_ACT_POS, 0))

    def read_vel(self):
        return self._i32(self.sdo_upload(OBJ_ACT_VEL, 0))

    def read_raw(self, idx, sub, timeout=0.4):
        """读任意对象字典, 返回 SDO 应答的原始 8 字节(含命令行), 失败返回 None。
           用于读取 SN/软件版本/型号等非标准长度(字符串)对象。"""
        lo, hi = idx & 0xFF, (idx >> 8) & 0xFF
        self._tx(0x600 + self.node, bytes([0x40, lo, hi, sub & 0xFF, 0, 0, 0, 0]))
        t0 = time.time()
        while time.time() - t0 < timeout:
            m = self.bus.recv(timeout=0.1)
            if m is None or m.arbitration_id != 0x580 + self.node or len(m.data) < 8:
                continue
            d = bytes(m.data)
            if d[1] == lo and d[2] == hi and d[3] == sub:
                return d
        return None

    def read_str(self, idx, sub, timeout=0.6):
        """读 STRING 类型对象(如 SN/硬件版本/MCU版本):
           收集响应窗口内所有帧的 ASCII 数据(支持厂商0x00 与 SDO 分段0x00/0x10)。"""
        lo, hi = idx & 0xFF, (idx >> 8) & 0xFF
        self._tx(0x600 + self.node, bytes([0x40, lo, hi, sub & 0xFF, 0, 0, 0, 0]))
        out = bytearray()
        last = t0 = time.time()
        while time.time() - t0 < timeout:
            m = self.bus.recv(timeout=0.05)
            if m is None:
                if out and time.time() - last > 0.06:      # 空闲>60ms 视为收完
                    break
                continue
            d = bytes(m.data)
            if m.arbitration_id != 0x580 + self.node or len(d) < 8:
                continue
            if d[1] != lo or d[2] != hi or d[3] != sub:
                continue
            last = time.time()
            c = d[0]
            if c in (0x00, 0x10, 0x20, 0x30):              # 分段/厂商ASCII
                out += d[4:]
            elif c in (0x43, 0x4B, 0x4F, 0x47):             # 快速应答(≤4字节)
                n = {0x43: 4, 0x4B: 2, 0x4F: 1, 0x47: 2}.get(c, 4)
                out += d[4:4 + n]
            elif c == 0x41:                                 # 上传开始(长度头), 继续等分段
                continue
            else:
                break
        return out.rstrip(b"\x00").decode("ascii", "ignore")

    @staticmethod
    def fmt_info(d):
        """把 read_raw 结果格式化为可读字段值。"""
        if d is None:
            return "超时"
        c, body = d[0], d[4:]
        if c == 0x00:                       # 厂商字符串应答(ASCII)
            return body.rstrip(b"\x00").decode("ascii", "ignore")
        if c == 0x4F:
            return str(body[0])
        if c == 0x4B:
            return "%d (0x%04X)" % (int.from_bytes(body[:2], "little"), int.from_bytes(body[:2], "little"))
        if c in (0x43, 0x47):
            u = int.from_bytes(body[:4], "little")
            return "%d (0x%08X)" % (u, u)
        return body.hex(" ")

    def read_tor(self):
        return self._i32(self.sdo_upload(OBJ_ACT_TOR, 0))

    def read_statusword(self):
        r = self.sdo_upload(OBJ_STATWORD, 0)
        return int.from_bytes(r, "little") if r and r[0] != 0x80 else 0

    def read_err_code(self):
        r = self.sdo_upload(OBJ_ERR_CODE, 0)
        return int.from_bytes(r[:2], "little") if r and len(r) >= 2 else 0

    # ---------------- 控制 ----------------
    def set_mode(self, mode=MODE_PP, timeout=0.2):
        self.sdo_download(OBJ_MODE, 0, bytes([mode]), timeout=timeout)

    def enable(self):
        """使能: 自动清故障; 先把目标=当前位置避免使能即动; 0x06->0x07->0x0F"""
        if self.read_statusword() & 0x0008:
            self.sdo_download(OBJ_CTRLWORD, 0, bytes([CW_FAULT_RESET, 0, 0, 0]))
            self.sdo_download(OBJ_CTRLWORD, 0, bytes([0, 0, 0, 0]))
            time.sleep(0.02)
        cur = self.read_pos()
        self.sdo_download(OBJ_TARGET_POS, 0, int(cur).to_bytes(4, "little", signed=True))
        self.sdo_download(OBJ_MODE, 0, bytes([MODE_PP]))
        for cw in (CW_SHUTDOWN, CW_SWITCH_ON, CW_ENABLE):
            self.sdo_download(OBJ_CTRLWORD, 0, bytes([cw, 0, 0, 0]))
            time.sleep(0.02)

    def disable(self):
        self.sdo_download(OBJ_CTRLWORD, 0, bytes([0, 0, 0, 0]))

    def set_pp_params(self, deg_s, deg_s2):
        """轮廓速度/加减速: 输入 °/s 与 °/s², 自动转脉冲单位"""
        self.sdo_download(OBJ_PP_VEL, 0, degps_to_pps(deg_s, self.enc_per_rev).to_bytes(4, "little", signed=True))
        self.sdo_download(OBJ_PP_ACC, 0, degps_to_pps(deg_s2, self.enc_per_rev).to_bytes(4, "little", signed=True))
        self.sdo_download(OBJ_PP_DEC, 0, degps_to_pps(deg_s2, self.enc_per_rev).to_bytes(4, "little", signed=True))

    def move_abs(self, hw_target_pulses):
        """下发绝对目标(硬件脉冲), 控制字 0x2f->0x3f(手册4.1.2)"""
        self.sdo_download(OBJ_TARGET_POS, 0,
                          int(hw_target_pulses).to_bytes(4, "little", signed=True))
        self.sdo_download(OBJ_CTRLWORD, 0, bytes([CW_NEWSET, 0, 0, 0]))
        self.sdo_download(OBJ_CTRLWORD, 0, bytes([CW_MOVE, 0, 0, 0]))

    def close(self):
        if self._owns_bus:
            try:
                self.bus.shutdown()
            except Exception:
                pass