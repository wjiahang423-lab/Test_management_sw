# -*- coding: utf-8 -*-
"""意优 RP 关节的简单 CANopen 底层驱动。

不依赖 Qt，只保留 CAN 总线、SDO、使能、标零和位置读取功能。
"""

import os
import struct
import sys
import time

try:
    import can
except ImportError:
    can = None

try:
    from zlgcan.zlgcan import ZCanBus, ZCanChlType, ZCANDeviceType
except Exception:
    ZCanBus = ZCanChlType = ZCANDeviceType = None

# 头部工位使用创芯科技的 USB-CANFD，需要 libcontrolcanfd.so；
# ZLG 的 zlgcan 打不开这块板卡，因此这里优先使用创芯驱动。
_CX_DRIVER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "USB_CAN_FD_Driver")
try:
    if _CX_DRIVER_DIR not in sys.path:
        sys.path.insert(0, _CX_DRIVER_DIR)
    from USB_CAN_FD_Drviver import USB_CAN_FD_Drviver as _CxDevice
    from USB_CAN_FD_Drviver import CanMessage as _CxCanMessage
except Exception:
    _CxDevice = None
    _CxCanMessage = None


# 常用 CANopen 对象
CONTROL_WORD = 0x6040
STATUS_WORD = 0x6041
POSITION_ACTUAL = 0x6064
TARGET_POSITION = 0x607A
ENCODER_PER_REV = 0x6091
MECHANICAL_ZERO = 0x2262
SAVE_PARAMS = 0x1010
MODE = 0x6060
PP_VELOCITY = 0x6081
PP_ACCELERATION = 0x6083
PP_DECELERATION = 0x6084

MODE_PP = 1
MODE_PV = 3
MODE_PT = 4
MODE_CSP = 8

CW_FAULT_RESET = 0x80
CW_SHUTDOWN = 0x06
CW_SWITCH_ON = 0x07
CW_ENABLE = 0x0F
CW_NEW_SETPOINT = 0x2F
CW_MOVE = 0x3F

DEFAULT_NODE = 1
DEFAULT_CHANNEL = 0
DEFAULT_BITRATE = 1_000_000
DEFAULT_DEVICE_INDEX = 0
DEFAULT_PRODUCT_ID = 1
DEFAULT_ENC_PER_REV = 524288.0
DEFAULT_ANGLE_LIMIT_DEG = 90.0
DEFAULT_PP_DEG_S = 5.0
DEFAULT_PP_ACC_DEG_S2 = 20.0

# 意优手册 3.1 操作时序：上电到使能 T1>5s，T2=500ms，T3>500ms。
# 20ms 的连发会让驱动器停在“等待打开伺服使能”（状态字 0x0333）。
ENABLE_STEP_DELAY = 0.6
ENABLE_TIMEOUT = 3.0


def _default_library():
    """使用测试管理软件自带的 ZLG 动态库。"""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "library"))


class _CxMessage:
    """供 _Joint 使用的最小报文对象。"""

    def __init__(self, arbitration_id, is_extended_id, data, channel=0):
        self.arbitration_id = int(arbitration_id)
        self.is_extended_id = bool(is_extended_id)
        self.data = bytes(data)
        self.channel = channel


class _CxBus:
    """把创芯 USB_CAN_FD 驱动包装成 python-can 风格的 send/recv/shutdown。"""

    def __init__(self, channel, bitrate, device_index):
        self.channel = int(channel) % 2
        self._drv = _CxDevice()
        self._drv.open(bitrate=int(bitrate), device_index=int(device_index),
                       channel=self.channel)

    def send(self, message, timeout=None):
        return self._drv.send(_CxCanMessage(
            message.arbitration_id, message.is_extended_id, message.data))

    def recv(self, timeout=0.05):
        message = self._drv.recv(timeout=timeout)
        if message is None:
            return None
        return _CxMessage(message.arbitration_id, message.is_extended_id,
                          message.data, self.channel)

    def shutdown(self):
        self._drv.close()


class EyouDriver:
    """共享一条 CAN 总线，按 node 访问多个意优电机。"""

    def __init__(self):
        self.bus = None
        self.joints = {}
        self.zero = {}

    def connect(self, channel=DEFAULT_CHANNEL, bitrate=DEFAULT_BITRATE,
                libpath=None, device_index=DEFAULT_DEVICE_INDEX, driver="auto"):
        """打开 CAN 总线并启动 CANopen 节点。

        默认优先使用头部工位的创芯 USB-CANFD（libcontrolcanfd.so）；
        找不到设备时再回退到 ZLG（zlgcan）。
        """
        try:
            self.close()
            ch = int(channel)
            if driver in ("auto", "cx") and _CxDevice is not None:
                try:
                    self.bus = _CxBus(ch, bitrate, device_index)
                except Exception:
                    self.bus = None
                    if driver == "cx":
                        raise
            if self.bus is None:
                if can is None or ZCanBus is None:
                    return False
                path = libpath or _default_library()
                cfg = {"bitrate": bitrate, "chl_type": ZCanChlType.CAN, "resistance": 1}
                self.bus = ZCanBus(
                    libpath=path,
                    device_type=ZCANDeviceType.ZCAN_USBCANFD_200U,
                    device_index=device_index,
                    configs=[dict(cfg), dict(cfg)],
                )
            if isinstance(self.bus, _CxBus):
                message = _CxMessage(0x000, False,
                                     bytes([0x01, 0x00, 0, 0, 0, 0, 0, 0]), ch)
            else:
                message = can.Message(
                    arbitration_id=0x000,
                    is_extended_id=False,
                    is_fd=False,
                    data=bytes([0x01, 0x00, 0, 0, 0, 0, 0, 0]),
                )
                message.channel = ch
            self.bus.send(message, timeout=0.05)
            self.channel = ch
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
        return True

    def _joint(self, node):
        """获取一个节点对象，节点号为 CANopen 1-based ID。"""
        node = int(node)
        if self.bus is None:
            raise RuntimeError("CAN 总线未连接")
        if node not in self.joints:
            self.joints[node] = _Joint(self.bus, self.channel, node)
        return self.joints[node]

    def scan_nodes(self, start=1, end=32, timeout=0.05):
        """扫描总线上有 SDO 应答的节点，返回节点号列表。"""
        if self.bus is None:
            raise RuntimeError("CAN 总线未连接")
        found = []
        for node in range(int(start), int(end) + 1):
            if self._joint(node).probe(timeout=timeout):
                found.append(node)
        return found

    def _enable_sequence(self, joint, log=None):
        """按手册时序执行 0x06 -> 0x07 -> 0x0F，并等待 Operation enabled。

        每一步后等待 ENABLE_STEP_DELAY，再轮询状态字，避免命令发得太快
        被驱动器忽略（表现为状态字停在 0x0333）。
        """
        sw = joint.read_status_word()
        if log:
            log("初始状态字 0x%04X" % sw)
        if sw & 0x0008:                      # 有故障先复位
            joint.write_sdo(CONTROL_WORD, 0, bytes([CW_FAULT_RESET, 0, 0, 0]))
            time.sleep(ENABLE_STEP_DELAY)
            joint.write_sdo(CONTROL_WORD, 0, bytes([0, 0, 0, 0]))
            time.sleep(ENABLE_STEP_DELAY)
            sw = joint.read_status_word()
            if log:
                log("故障复位后状态字 0x%04X" % sw)

        for command in (CW_SHUTDOWN, CW_SWITCH_ON, CW_ENABLE):
            joint.write_sdo(CONTROL_WORD, 0, bytes([command, 0, 0, 0]))
            time.sleep(ENABLE_STEP_DELAY)
            sw = joint.read_status_word()
            if log:
                log("控制字 0x%02X 后状态字 0x%04X" % (command, sw))

        deadline = time.time() + ENABLE_TIMEOUT
        while time.time() < deadline:
            sw = joint.read_status_word()
            if sw & 0x0004:
                return sw
            time.sleep(0.05)
        return sw

    def enable(self, node):
        """按 CiA402 顺序使能电机，并检查状态字。"""
        joint = self._joint(node)
        current = joint.read_position()
        joint.write_sdo(TARGET_POSITION, 0, int(current).to_bytes(4, "little", signed=True))
        return bool(self._enable_sequence(joint) & 0x0004)

    def set_mode(self, node, mode):
        """写入目标工作模式 0x6060:00。"""
        joint = self._joint(node)
        return bool(joint.write_sdo(MODE, 0, bytes([int(mode)])))

    def set_pp_params(self, node, velocity_deg_s=DEFAULT_PP_DEG_S,
                      acc_deg_s2=DEFAULT_PP_ACC_DEG_S2, dec_deg_s2=None):
        """写入 PP 轮廓速度/加速度/减速度（角度单位换算成脉冲/s、脉冲/s²）。"""
        joint = self._joint(node)
        per_rev = joint.encoder_per_rev()

        def _pps(value):
            return int(round(float(value) * per_rev / 360.0))

        dec_deg_s2 = acc_deg_s2 if dec_deg_s2 is None else dec_deg_s2
        for index, value in ((PP_VELOCITY, _pps(velocity_deg_s)),
                             (PP_ACCELERATION, _pps(acc_deg_s2)),
                             (PP_DECELERATION, _pps(dec_deg_s2))):
            joint.write_sdo(index, 0,
                            int(value).to_bytes(4, "little", signed=True))
        return True

    def encoder_per_rev(self, node):
        """读取节点每圈脉冲当量（0x6091:02）。"""
        return self._joint(node).encoder_per_rev()

    def read_position(self, node):
        """读取节点实际位置 0x6064（有符号脉冲）。"""
        return self._joint(node).read_position()

    def enable_position(self, node, log=None):
        """切换到 PP 位置模式并按 CiA402 顺序使能，返回是否进入 operation-enabled。"""
        joint = self._joint(node)
        # 手册 3.3：切到位置模式前先把实际位置写给目标位置，避免使能瞬间跳动。
        current = joint.read_position()
        joint.write_sdo(TARGET_POSITION, 0,
                        int(current).to_bytes(4, "little", signed=True))
        joint.write_sdo(MODE, 0, bytes([MODE_PP]))
        time.sleep(0.05)
        return bool(self._enable_sequence(joint, log=log) & 0x0004)

    def move_angle(self, node, angle_deg, speed_deg_s=DEFAULT_PP_DEG_S,
                   acc_deg_s2=DEFAULT_PP_ACC_DEG_S2, timeout=30.0,
                   min_deg=-DEFAULT_ANGLE_LIMIT_DEG,
                   max_deg=DEFAULT_ANGLE_LIMIT_DEG, log=None):
        """使能后按软件零点移动到目标角度（PP 模式），返回到位信息。"""
        node = int(node)
        joint = self._joint(node)
        requested = float(angle_deg)
        low = float(min_deg)
        high = float(max_deg)
        if low > high:
            low, high = high, low
        angle = max(low, min(high, requested))
        clamped = abs(angle - requested) > 1e-9
        if log:
            log("节点%d 目标角度 %.3f°%s" % (
                node, angle,
                "（已从 %.3f° 钳制到允许范围）" % requested if clamped else ""))

        if not self.enable_position(node, log=log):
            words = joint.read_status_word()
            raise RuntimeError("节点%d 使能失败，状态字=0x%04X" % (node, words))
        if log:
            log("节点%d 使能成功（PP 位置模式）" % node)

        try:
            self.set_pp_params(node, speed_deg_s, acc_deg_s2)
            if log:
                log("节点%d PP 参数：速度 %.1f°/s，加速度 %.1f°/s²" % (
                    node, float(speed_deg_s), float(acc_deg_s2)))
        except Exception as exc:
            if log:
                log("节点%d PP 参数写入失败，沿用驱动器默认值：%s" % (node, exc))

        zero = self.zero.get(node)
        if zero is None:
            zero = joint.read_position()
            self.zero[node] = zero
            if log:
                log("节点%d 未记录软件零点，使用当前脉冲 %d 作为零点" % (node, zero))

        per_rev = joint.encoder_per_rev()
        target = int(round(zero + angle * per_rev / 360.0))
        if log:
            log("节点%d 角度换算：%+.3f° × %.0f/360 = %+d 脉冲，零点=%d，目标=%d" % (
                node, angle, per_rev,
                int(round(angle * per_rev / 360.0)), zero, target))

        self.set_mode(node, MODE_PP)
        joint.write_sdo(TARGET_POSITION, 0,
                        int(target).to_bytes(4, "little", signed=True))
        joint.write_sdo(CONTROL_WORD, 0, bytes([CW_NEW_SETPOINT, 0, 0, 0]))
        joint.write_sdo(CONTROL_WORD, 0, bytes([CW_MOVE, 0, 0, 0]))

        tolerance = max(1, int(round(per_rev * 0.5 / 360.0)))
        deadline = time.time() + float(timeout)
        position = joint.read_position()
        status = 0
        while time.time() < deadline:
            try:
                status = joint.read_status_word()
                position = joint.read_position()
            except Exception:
                time.sleep(0.1)
                continue
            if (status & 0x0400) or abs(position - target) <= tolerance:
                actual = (position - zero) * 360.0 / per_rev
                if log:
                    log("节点%d 到位：实际 %.3f°（脉冲 %d，状态字 0x%04X）" % (
                        node, actual, position, status))
                return {
                    "ok": True,
                    "node": node,
                    "requested_deg": requested,
                    "target_deg": angle,
                    "actual_deg": round(actual, 3),
                    "target_pulses": target,
                    "actual_pulses": position,
                    "zero_pulses": zero,
                    "enc_per_rev": per_rev,
                    "status_word": status,
                    "clamped": clamped,
                }
            time.sleep(0.1)

        actual = (position - zero) * 360.0 / per_rev
        raise RuntimeError(
            "节点%d 运动超时（%.1fs）：实际 %.3f°，目标 %.3f°，"
            "脉冲 %d/%d，状态字 0x%04X" % (
                node, float(timeout), actual, angle,
                position, target, status))

    def return_zero(self, node, speed_deg_s=DEFAULT_PP_DEG_S,
                    acc_deg_s2=DEFAULT_PP_ACC_DEG_S2, timeout=30.0, log=None):
        """回到软件 0 位（0x2262 标定后的机械零点）。"""
        node = int(node)
        if node not in self.zero:
            self.zero[node] = 0.0
        if log:
            log("节点%d 返回0位" % node)
        return self.move_angle(node, 0.0, speed_deg_s, acc_deg_s2,
                               timeout=timeout, log=log)

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

    def disable(self, node, timeout=1.0):
        """失能节点，释放电机输出，便于人工手动转动关节。"""
        joint = self._joint(node)
        joint.write_sdo(CONTROL_WORD, 0, bytes([0, 0, 0, 0]))
        deadline = time.time() + float(timeout)
        while time.time() < deadline:
            if not (joint.read_status_word() & 0x0004):
                return True
            time.sleep(0.05)
        return False

    def zero_position(self, node):
        """按意优调试器流程执行机械标零，返回标零后的 0x6064。

        流程与「意优RP-CANFD调试器」app/worker.py::set_mech_home 一致：
        1. 0x2262:00 写 1 字节值 1；
        2. 0x1010:01 写 4 字节 65 76 61 73（设备要求的保存签名 "evas"）；
        3. 每 0.1s 回读 0x6064，最多 20 次，严格等于 0 表示驱动器标零成功。

        无论驱动器是否回到 0，都把回读值记为软件零点并返回，由计划阈值
        判定 PASS/FAIL；只有写入/读取通信失败时才抛异常。
        """
        node = int(node)
        joint = self._joint(node)

        joint.write_sdo(MECHANICAL_ZERO, 0, bytes([1]))
        time.sleep(0.2)
        joint.write_sdo(SAVE_PARAMS, 1, bytes.fromhex("65 76 61 73"))
        time.sleep(0.2)

        position = None
        for _ in range(20):
            time.sleep(0.1)
            try:
                position = joint.read_position()
            except Exception:
                continue
            if position == 0:
                break
        if position is None:
            raise RuntimeError("机械标零失败，节点%d 读取 0x6064 超时" % node)

        self.zero[node] = float(position)
        try:
            self.disable(node)
        except Exception:
            pass
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

        if index == 0x1008:
            return joint.read_string(index, subindex)

        # 0x1009/0x100A 在不同固件上可能是字符串，也可能是 uint32 版本号。
        if index in (0x1009, 0x100A):
            try:
                text = joint.read_string(index, subindex)
                if text and all(0x20 <= ord(ch) < 0x7F for ch in text):
                    return text
            except Exception:
                pass
            data = joint.read_sdo(index, subindex)
            if len(data) <= 4:
                value = int.from_bytes(data, "little", signed=False)
                return "%d" % (value)
            return data.hex(" ").upper()

        if index in (0x2002, 0x2082, 0x2083, 0x2084):
            return joint.read_string(index, subindex)

        data = joint.read_sdo(index, subindex)
        if len(data) <= 4:
            value = int.from_bytes(data, "little", signed=False)
            return "%d" % (value)
        return data.hex(" ").upper()

    def read_value(self, node, address):
        """读取 SDO 并返回整数（用于写入后的数值回读比对）。"""
        index, subindex = _parse_address(address)
        data = self._joint(node).read_sdo(index, subindex)
        return int.from_bytes(data, "little", signed=False)

    def write_info(self, node, address, value):
        """写入 SDO 地址，value 可为数字字符串或整数。

        数字字符串按整数写入（"340" -> 0x154），不会写成 ASCII "340"；
        写入长度优先沿用对象当前长度（读取成功时），否则按数值大小推断。
        """
        try:
            index, subindex = _parse_address(address)
            joint = self._joint(node)
            if isinstance(value, str):
                text = value.strip()
                try:
                    value = int(text, 0)
                except ValueError:
                    value = text
            if isinstance(value, int):
                size = None
                try:
                    size = len(joint.read_sdo(index, subindex))
                except Exception:
                    size = None
                if size not in (1, 2, 4):
                    if -0x80 <= value <= 0xFF:
                        size = 1
                    elif -0x8000 <= value <= 0xFFFF:
                        size = 2
                    else:
                        size = 4
                data = int(value).to_bytes(size, "little",
                                           signed=value < 0)
                return bool(joint.write_sdo(index, subindex, data))
            if isinstance(value, str):
                data = bytes(value.encode("ascii"))
                if len(data) > 4:
                    raise ValueError("字符串长度不能超过 4")
                return bool(joint.write_sdo(index, subindex, data))
            raise TypeError("value 必须是 str 或 int")
        except Exception:
            return False


class _Joint:
    """一个节点的最小 CANopen SDO 通信封装。"""

    def __init__(self, bus, channel, node):
        self.bus = bus
        self.channel = channel
        self.node = node

    def _send(self, data):
        if isinstance(self.bus, _CxBus):
            message = _CxMessage(0x600 + self.node, False, bytes(data), self.channel)
        else:
            message = can.Message(
                arbitration_id=0x600 + self.node,
                is_extended_id=False,
                is_fd=False,
                data=bytes(data),
            )
            message.channel = self.channel
        self.bus.send(message, timeout=0.05)

    def probe(self, index=0x1000, subindex=0, timeout=0.05):
        """向节点发一次 SDO 读取，收到任意应答即认为节点存在。"""
        lo, hi = index & 0xFF, (index >> 8) & 0xFF
        try:
            self._send([0x40, lo, hi, subindex, 0, 0, 0, 0])
        except Exception:
            return False
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            message = self.bus.recv(timeout=min(0.02, timeout))
            if message is None or message.arbitration_id != 0x580 + self.node:
                continue
            data = bytes(message.data)
            if len(data) >= 4 and data[1:4] == bytes([lo, hi, subindex]):
                return True
        return False

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
        command = {1: 0x2F, 2: 0x2B, 3: 0x27, 4: 0x23}[len(data)]
        lo, hi = index & 0xFF, (index >> 8) & 0xFF
        self._send([command, lo, hi, subindex] + list(data) + [0] * (4 - len(data)))
        end = time.monotonic() + 0.4
        while time.monotonic() < end:
            message = self.bus.recv(timeout=0.05)
            if message is None or message.arbitration_id != 0x580 + self.node:
                continue
            response = bytes(message.data)
            if response[:4] == bytes([0x60, lo, hi, subindex]):
                return True
            if response and response[0] == 0x80:
                raise RuntimeError("SDO 写入失败")
        raise TimeoutError("SDO 写入超时")

    def read_string(self, index, subindex):
        """读取字符串对象，支持快速上传和标准 SDO 分段上传。

        0x1008/0x1009/0x100A 等版本/名称对象通常是长字符串，驱动器会先
        返回 0x41（启动分段上传），需要主机继续发 0x60/0x61 分段请求。
        """
        lo, hi = index & 0xFF, (index >> 8) & 0xFF
        self._send([0x40, lo, hi, subindex, 0, 0, 0, 0])
        result = bytearray()
        end = time.monotonic() + 0.8
        segmented = False
        toggle = 0

        while time.monotonic() < end:
            message = self.bus.recv(timeout=0.05)
            if message is None:
                continue
            if message.arbitration_id != 0x580 + self.node:
                continue
            data = bytes(message.data)
            if len(data) < 8:
                continue

            if not segmented:
                if data[1:4] != bytes([lo, hi, subindex]):
                    continue
                command = data[0]
                sizes = {0x4F: 1, 0x4B: 2, 0x43: 4, 0x47: 3}
                if command in sizes:
                    result.extend(data[4:4 + sizes[command]])
                    break
                if command == 0x80:
                    raise RuntimeError("SDO 读取失败")
                if command == 0x41:
                    segmented = True
                    self._send([0x60, 0, 0, 0, 0, 0, 0, 0])
                    end = time.monotonic() + 0.8
                    continue
                if command in (0x00, 0x10, 0x20, 0x30):
                    # 厂商自定义的分段应答：每帧 byte4~7 是数据
                    result.extend(data[4:])
                    end = time.monotonic() + 0.2
                    continue
                continue

            # 标准 SDO 分段上传应答：bit7~5=0，bit4=最后一段，bit3~1=无效字节数
            command = data[0]
            if command & 0xE0:
                continue
            unused = (command >> 1) & 0x07
            last = bool(command & 0x10)
            result.extend(data[1:8 - unused])
            if last:
                break
            toggle ^= 1
            self._send([0x60 | toggle, 0, 0, 0, 0, 0, 0, 0])
            end = time.monotonic() + 0.8

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


def _parse_int(value):
    """解析 0x1018、4、04 等写法；int(x, 0) 不接受前导零十进制。"""
    text = str(value).strip()
    try:
        return int(text, 0)
    except ValueError:
        return int(text, 10)


def _parse_address(address):
    text = str(address).strip().replace("/", ":")
    if ":" in text:
        index, subindex = text.split(":", 1)
        return _parse_int(index), _parse_int(subindex)
    index = _parse_int(text)
    return index, 4 if index == 0x1018 else 0


driver = EyouDriver()
