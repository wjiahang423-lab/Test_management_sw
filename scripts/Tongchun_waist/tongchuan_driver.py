# -*- coding: utf-8 -*-
"""同川 TC600E EtherCAT 底层驱动。

这里只是测试函数需要的通信功能，不输出调试日志。
"""

import struct
import threading
import subprocess
import sys, os
# sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import pysoem
except ImportError:  # 允许在未安装 pysoem 的电脑上导入测试脚本
    pysoem = None


# 同川电机常用对象
CONTROL_WORD = 0x6040
STATUS_WORD = 0x6041
POSITION_ACTUAL = 0x6064
ZERO_POSITION = 0x3801

# TC600E 电机端编码器分辨率，角度 = 脉冲 / 分辨率 * 360
PULSES_PER_REV = 131072.0


def _bring_interface_up(interface, sudo_password=None):
    """通过 sudo 激活网卡接口（设置 IFF_UP）。

    返回 True 表示成功，False 表示失败。
    """
    if sudo_password is None:
        return False
    try:
        cmd = ["sudo", "-S", "ip", "link", "set", interface, "up"]
        proc = subprocess.run(
            cmd,
            input=sudo_password + "\n",
            capture_output=True,
            timeout=5
        )
        return proc.returncode == 0
    except Exception:
        return False

class TongchuanDriver:
    """一个主站连接对象，可访问总线上的多个从站。"""

    def __init__(self):
        self.master = None
        self.interface = "enp2s0"
        self._lock = threading.Lock()

    def connect(self, interface="enp2s0",sudo_password="zhengjijicheng"):
        """打开网卡并扫描 EtherCAT 从站。成功返回 True。"""
        if pysoem is None:
            return False

        with self._lock:
            self.close()
            try:
                # 需要输入sudo密码才能访问网卡
                if sudo_password:
                      _bring_interface_up(interface, sudo_password)
                self.interface = interface
                self.master = pysoem.Master()
                self.master.open(interface)
                if self.master.config_init() <= 0:
                    self.close()
                    return False
                self.master.config_map()
                return True
            except Exception:
                self.close()
                return False

    def get_slave_count(self)->int:
        """返回总线上的从站数量。"""
        if self.master is None:
            return 0
        return len(self.master.slaves)
            

    def close(self):
        """关闭主站连接。"""
        if self.master is None:
            return
        try:
            self.master.close()
        except Exception:
            pass
        self.master = None

    def _slave(self, node):
        if self.master is None:
            raise RuntimeError("EtherCAT 未连接")
        
        return self.master.slaves[int(node)]

    def read_sdo(self, node, index, subindex=0):
        """读取 SDO 原始字节。node 是从站下标，index 是对象字典地址。"""
        with self._lock:
            return bytes(self._slave(node).sdo_read(int(index), int(subindex)))

    def write_sdo(self, node, index, value, size=2):
        """写入一个整数 SDO。"""
        data = int(value).to_bytes(size, byteorder="little", signed=False)
        with self._lock:
            self._slave(node).sdo_write(int(index), 0, data)

    def enable(self, node):
        """按 CiA402 顺序发送使能控制字，并检查状态字。"""
        # 0x0006 -> 0x0007 -> 0x000F 是标准 CiA402 使能顺序。
        for command in (0x0006, 0x0007, 0x000F):
            self.write_sdo(node, CONTROL_WORD, command)
        status = int.from_bytes(self.read_sdo(node, STATUS_WORD)[:2], "little")
        return bool(status & 0x0004)

    def set_torque(self, node, torque):
        """写入扭矩值到 0x6071，单位为 0.001 Nm。"""
        torque_value = int(torque * 1000)
        self.write_sdo(node, 0x6071, torque_value, size=2)
        return True

    def set_torque_enable(self, node, enable):
        """写入扭矩使能位到 0x6040。"""
        control_word = 0x000F if enable else 0x0007
        self.write_sdo(node, CONTROL_WORD, control_word)
        return True

    def set_torque_mode(self, node):
        """写入模式选择 0x6060 = 0x0004（扭矩模式）。"""
        self.write_sdo(node, 0x6060, 0x0004)
        enable = self.set_torque_enable(node, True)
        return enable

    def zero_position(self, node):
        """写 5 执行标零，再返回 0x6064 的当前位置脉冲值。"""
        self.write_sdo(node, ZERO_POSITION, 5)
        data = self.read_sdo(node, POSITION_ACTUAL)
        return float(struct.unpack("<i", data[:4])[0])

    def read_position(self, node):
        """读取 0x6064 当前绝对位置，单位为脉冲。"""
        data = self.read_sdo(node, POSITION_ACTUAL)
        return float(struct.unpack("<i", data[:4])[0])

    def read_angle(self, node):
        """读取当前位置并换算为角度。"""
        return self.read_position(node) * 360.0 / PULSES_PER_REV

    def read_info(self, node, address):
        """读取 SDO 并返回字符串；字符串对象按 UTF-8 解码，其它返回十六进制。"""
        index = int(str(address), 0)
        subindex = 4 if index == 0x1018 else 0
        data = self.read_sdo(node, index, subindex)
        if index in (0x1008, 0x1009, 0x100A, 0x3040):
            return data.rstrip(b"\x00").decode("utf-8", errors="replace")
        return "0x" + data.hex().upper()


driver = TongchuanDriver()
