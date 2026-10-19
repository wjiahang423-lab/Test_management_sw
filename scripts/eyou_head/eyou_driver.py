# -*- coding: utf-8 -*-
"""意优 RP 关节的简单 CANopen 底层驱动。

不依赖 Qt，只保留 CAN 总线、SDO、使能、标零和位置读取功能。
"""

import os
import struct
import time

try:
    import can
    from zlgcan.zlgcan import ZCanBus, ZCanChlType, ZCANDeviceType
except ImportError:
    can = None
    ZCanBus = ZCanChlType = ZCANDeviceType = None


# 常用 CANopen 对象
CONTROL_WORD = 0x6040
STATUS_WORD = 0x6041
POSITION_ACTUAL = 0x6064
TARGET_POSITION = 0x607A
ENCODER_PER_REV = 0x6091
MECHANICAL_ZERO = 0x2262
SAVE_PARAMS = 0x1010

DEFAULT_NODE = 1
DEFAULT_CHANNEL = 0
DEFAULT_BITRATE = 1_000_000
DEFAULT_DEVICE_INDEX = 0
DEFAULT_PRODUCT_ID = 1
DEFAULT_ENC_PER_REV = 524288.0


def _default_library():
    """使用测试管理软件自带的 ZLG 动态库。"""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "library"))


class EyouDriver:
    """共享一条 CAN 总线，按 node 访问多个意优电机。"""

    def __init__(self):
        self.bus = None
        self.joints = {}
        self.zero = {}

    def connect(self, channel=DEFAULT_CHANNEL, bitrate=DEFAULT_BITRATE,
                libpath=None, device_index=DEFAULT_DEVICE_INDEX):
        """打开 ZLG CAN 总线并启动 CANopen 节点。"""
        if can is None or ZCanBus is None:
            return False
        try:
            self.close()
            path = libpath or _default_library()
            cfg = {"bitrate": bitrate, "chl_type": ZCanChlType.CAN, "resistance": 1}
            self.bus = ZCanBus(
                libpath=path,
                device_type=ZCANDeviceType.ZCAN_USBCANFD_200U,
                device_index=device_index,
                configs=[dict(cfg), dict(cfg)],
            )
            message = can.Message(
                arbitration_id=0x000,
                is_extended_id=False,
                is_fd=False,
                data=bytes([0x01, 0x00, 0, 0, 0, 0, 0, 0]),
            )
            message.channel = channel
            self.bus.send(message, timeout=0.05)
            self.channel = channel
            return True
        except Exception:
            self.close()
            return False

    def close(self):
        """关闭 CAN 总线并清空状态。"""
        if self.bus is not None:
            try:
                self.bus.shutdown()
            except Exception:
                pass
        self.bus = None
        self.joints.clear()
        self.zero.clear()

    def _joint(self, node):
        """获取一个节点对象，节点号为 CANopen 1-based ID。"""
        node = int(node)
        if self.bus is None:
            raise RuntimeError("CAN 总线未连接")
        if node not in self.joints:
            self.joints[node] = _Joint(self.bus, self.channel, node)
        return self.joints[node]

    def enable(self, node):
        """按 CiA402 顺序使能电机，并检查状态字。"""
        joint = self._joint(node)
        current = joint.read_position()
        joint.write_sdo(TARGET_POSITION, 0, int(current).to_bytes(4, "little", signed=True))
        for command in (0x06, 0x07, 0x0F):
            joint.write_sdo(CONTROL_WORD, 0, int(command).to_bytes(4, "little"))
            time.sleep(0.02)
        return bool(joint.read_status_word() & 0x0004)

    def set_torque(self, node, torque):
        """按 CiA402 顺序使能电机，并检查状态字。"""
        joint = self._joint(node)
        current = joint.read_position()
        # 0x6060:04=0x00000000 使能扭矩模式
        joint.write_sdo(0x6060, 4, int(current).to_bytes(4, "little", signed=True))
        for command in (0x06, 0x07, 0x0F):
            joint.write_sdo(CONTROL_WORD, 0, int(command).to_bytes(4, "little"))
            # 扭矩设定
            joint.write_sdo(0x6071, 0, int(torque).to_bytes(2, "little", signed=True))
            time.sleep(0.02)
        return bool(joint.read_status_word() & 0x0004)

    def zero_position(self, node):
        """执行机械标零、保存参数，并返回标零后的实际位置。"""
        joint = self._joint(node)
        # 0x2262:00=1 触发机械标零，不能写入当前编码器位置。
        joint.write_sdo(MECHANICAL_ZERO, 0, bytes([1]))
        time.sleep(0.2)
        # 按设备要求写入 ASCII "evas" 保存标零参数。
        joint.write_sdo(SAVE_PARAMS, 1, bytes.fromhex("65 76 61 73"))
        time.sleep(0.2)
        position = None
        for _ in range(20):
            time.sleep(0.1)
            position = joint.read_position()
            if position == 0:
                break
        if position != 0:
            raise RuntimeError("机械标零未生效，0x6064=%s" % position)

        self.zero[int(node)] = position
        return float(position)

    def read_angle(self, node):
        """读取当前位置并换算为相对软件零点的角度。"""
        joint = self._joint(node)
        position = joint.read_position()
        per_rev = joint.encoder_per_rev()
        zero = self.zero.get(int(node), 0.0)
        return (position - zero) * 360.0 / per_rev

    def read_info(self, node, address):
        """读取 SDO 地址并转换为字符串。地址可写成 0x1008 或 0x1018:04。"""
        index, subindex = _parse_address(address)
        joint = self._joint(node)
        if index in (0x1008, 0x2002, 0x2082, 0x2083, 0x2084):
            return joint.read_string(index, subindex)
        data = joint.read_sdo(index, subindex)
        if len(data) <= 4:
            value = int.from_bytes(data, "little", signed=False)
            return "%d (0x%X)" % (value, value)
        return data.hex(" ").upper()


class _Joint:
    """一个节点的最小 CANopen SDO 通信封装。"""

    def __init__(self, bus, channel, node):
        self.bus = bus
        self.channel = channel
        self.node = node

    def _send(self, data):
        message = can.Message(
            arbitration_id=0x600 + self.node,
            is_extended_id=False,
            is_fd=False,
            data=bytes(data),
        )
        message.channel = self.channel
        self.bus.send(message, timeout=0.05)

    def read_sdo(self, index, subindex):
        """读取一个 SDO，支持常用的 1/2/4 字节快速上传。"""
        lo, hi = index & 0xFF, (index >> 8) & 0xFF
        self._send([0x40, lo, hi, subindex, 0, 0, 0, 0])
        end = time.monotonic() + 0.4
        while time.monotonic() < end:
            message = self.bus.recv(timeout=0.05)
            if message is None or message.arbitration_id != 0x580 + self.node:
                continue
            data = bytes(message.data)
            if len(data) < 4 or data[1:4] != bytes([lo, hi, subindex]):
                continue
            if data[0] == 0x80:
                raise RuntimeError("SDO 读取失败")
            sizes = {0x4F: 1, 0x4B: 2, 0x43: 4, 0x47: 3}
            if data[0] in sizes:
                return data[4:4 + sizes[data[0]]]
        raise TimeoutError("SDO 读取超时")

    def write_sdo(self, index, subindex, data):
        """写入一个 1/2/4 字节 SDO。"""
        data = bytes(data)
        command = {1: 0x2F, 2: 0x2B, 4: 0x23}[len(data)]
        lo, hi = index & 0xFF, (index >> 8) & 0xFF
        self._send([command, lo, hi, subindex] + list(data) + [0] * (4 - len(data)))
        end = time.monotonic() + 0.4
        while time.monotonic() < end:
            message = self.bus.recv(timeout=0.05)
            if message is None or message.arbitration_id != 0x580 + self.node:
                continue
            response = bytes(message.data)
            if response[:4] == bytes([0x60, lo, hi, subindex]):
                return
            if response and response[0] == 0x80:
                raise RuntimeError("SDO 写入失败")
        raise TimeoutError("SDO 写入超时")

    def read_string(self, index, subindex):
        """读取快速或分段上传的字符串对象。"""
        lo, hi = index & 0xFF, (index >> 8) & 0xFF
        self._send([0x40, lo, hi, subindex, 0, 0, 0, 0])
        result = bytearray()
        end = time.monotonic() + 0.6
        while time.monotonic() < end:
            message = self.bus.recv(timeout=0.05)
            if message is None or message.arbitration_id != 0x580 + self.node:
                continue
            data = bytes(message.data)
            if len(data) < 4 or data[1:4] != bytes([lo, hi, subindex]):
                continue
            command = data[0]
            if command in (0x4F, 0x4B, 0x43, 0x47):
                sizes = {0x4F: 1, 0x4B: 2, 0x43: 4, 0x47: 3}
                result.extend(data[4:4 + sizes[command]])
                break
            if command == 0x80:
                raise RuntimeError("SDO 读取失败")
            if command == 0x41:
                continue
            if command in (0x00, 0x10, 0x20, 0x30):
                result.extend(data[4:])
                continue
            break
        if not result:
            raise TimeoutError("字符串 SDO 读取超时")
        return bytes(result).rstrip(b"\x00").decode("ascii", errors="ignore")

    def read_position(self):
        data = self.read_sdo(POSITION_ACTUAL, 0)
        return struct.unpack("<i", data[:4])[0]

    def read_status_word(self):
        return int.from_bytes(self.read_sdo(STATUS_WORD, 0)[:2], "little")

    def encoder_per_rev(self):
        try:
            data = self.read_sdo(ENCODER_PER_REV, 2)
            value = int.from_bytes(data, "little")
            return float(value) if value > 0 else DEFAULT_ENC_PER_REV
        except Exception:
            return DEFAULT_ENC_PER_REV


def _parse_address(address):
    text = str(address).strip().replace("/", ":")
    if ":" in text:
        index, subindex = text.split(":", 1)
        return int(index, 0), int(subindex, 0)
    index = int(text, 0)
    return index, 4 if index == 0x1018 else 0


driver = EyouDriver()
