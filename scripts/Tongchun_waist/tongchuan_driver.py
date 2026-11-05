# -*- coding: utf-8 -*-
"""同川 TC600E EtherCAT 底层驱动（CiA402 + PDO）。

TC600E 的 0x6040/0x6060/0x6071 默认映射在 RxPDO 0x1601 中。只在 SAFEOP
下用 SDO 写控制字，驱动器不会使能；必须先让主站进入 OP 并持续交换 PDO。
本驱动在 connect() 时进入 OP 并启动后台 PDO 线程，set_torque_mode() 通过
PDO 执行 CiA402 使能序列。

RxPDO 0x1601 映射（17 字节）：
  0x6040 控制字(2) | 0x607A 目标位置(4) | 0x60FF 目标速度(4)
  | 0x6071 目标扭矩(2) | 0x60B1 扭矩偏置(4) | 0x6060 模式(1)

TxPDO 0x1A01 映射（19 字节）：
  0x6041 状态字(2) | 0x6064 实际位置(4) | 0x606C 实际速度(4)
  | 0x6077 实际扭矩(2) | 0x60F4 跟随误差(4) | 0x603F 错误码(2)
  | 0x6061 模式显示(1)
"""

import math
import os
import struct
import subprocess
import sys
import threading
import time

try:
    import pysoem
except ImportError:  # 允许在未安装 pysoem 的电脑上导入测试脚本
    pysoem = None


# 同川电机常用对象
CONTROL_WORD = 0x6040
STATUS_WORD = 0x6041
POSITION_ACTUAL = 0x6064
ZERO_POSITION = 0x3801

# TC600E 电机端编码器分辨率
PULSES_PER_REV = 131072.0
# 减速比：与 position_controller(同川) 的 GEAR_RATIO 一致
# 输出端角度 = 脉冲 * 360 / (PULSES_PER_REV * GEAR_RATIO)
GEAR_RATIO = 101.0

# PDO 偏移（见文件头映射）
OUT_CW = 0
OUT_POS = 2
OUT_VEL = 6
OUT_TORQUE = 10
OUT_TORQUE_OFFSET = 12
OUT_MODE = 16
IN_STATUS = 0
IN_POS = 2
IN_VEL = 6
IN_MODE = 18

PDO_CYCLE_S = 0.005


def _bring_interface_up(interface, sudo_password=None):
    """把网卡置为 UP。

    优先直接执行（进程带 CAP_NET_ADMIN 时无需 sudo），失败后再用 sudo。
    """
    try:
        proc = subprocess.run(["ip", "link", "set", interface, "up"],
                              capture_output=True, timeout=5)
        if proc.returncode == 0:
            return True
    except Exception:
        pass
    if sudo_password is None:
        return False
    try:
        cmd = ["sudo", "-S", "ip", "link", "set", interface, "up"]
        proc = subprocess.run(cmd, input=sudo_password + "\n",
                              capture_output=True, timeout=5)
        return proc.returncode == 0
    except Exception:
        return False


def _has_cap_net_raw():
    """当前进程是否具备 CAP_NET_RAW（root 或已 setcap）。"""
    try:
        if os.geteuid() == 0:
            return True
        with open("/proc/self/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("CapEff:"):
                    return bool(int(line.split()[1], 16) & (1 << 13))
    except Exception:
        pass
    return False


def _log(msg):
    """优先写入测试软件运行日志；独立运行时打印到终端。"""
    try:
        import test_api
        test_api.log(msg)
    except Exception:
        print(msg)


class TongchuanDriver:
    """一个主站连接对象，可访问总线上的多个从站。"""

    def __init__(self):
        self.master = None
        self.interface = "enp2s0"
        self.last_error = ""
        self._lock = threading.RLock()
        self._pdo_stop = threading.Event()
        self._pdo_thread = None
        self._cmd = {}          # node -> {"cw": int, "mode": int, "torque_raw": int, ...}
        self._op = False
        self._last_diag = 0.0

    # ------------------------------------------------------------ 连接/关闭
    def connect(self, interface="enp2s0", sudo_password="zhengjijicheng"):
        """打开网卡、扫描从站并进入 OP，启动后台 PDO 交换。成功返回 True。"""
        if pysoem is None:
            self.last_error = "未安装 pysoem"
            _log("EtherCAT 连接失败：未安装 pysoem")
            return False

        self.close()
        with self._lock:
            try:
                _bring_interface_up(interface, sudo_password)
                self.interface = interface
                self.master = pysoem.Master()
                self.master.open(interface)
                if self.master.config_init() <= 0:
                    self.last_error = "网卡 {} 上未发现 EtherCAT 从站".format(interface)
                    _log("EtherCAT 连接失败：{}（检查网线/从站电源，"
                         "ip -br link show {} 不能是 NO-CARRIER）".format(
                             self.last_error, interface))
                    self.close()
                    return False
                # 同川 TC600E 的 OP 流程：
                # config_map -> config_dc -> 确保 SAFEOP -> 预热 200 帧 -> OP
                self.master.config_map()
                try:
                    self.master.config_dc()
                except Exception:
                    pass

                if self.master.state != pysoem.SAFEOP_STATE:
                    self.master.state = pysoem.SAFEOP_STATE
                    self.master.write_state()
                    self.master.state_check(pysoem.SAFEOP_STATE, 5000)

                # SAFEOP 下先跑 200 帧过程数据，否则从站会拒绝 OP
                for _ in range(200):
                    self.master.send_processdata()
                    wkc = self.master.receive_processdata(100)
                    if wkc > 0:
                        time.sleep(0.001)

                self.master.state = pysoem.OP_STATE
                self.master.write_state()
                self.master.state_check(pysoem.OP_STATE, 5000)
                self._op = (self.master.state == pysoem.OP_STATE)
                if not self._op:
                    self.last_error = "从站未能进入 OP 状态（当前 {}）".format(
                        self.master.state)
                    _log("EtherCAT 连接失败：{}；请检查从站电源/配置，"
                         "必要时给从站断电重启".format(self.last_error))
                    self.close()
                    return False

                # 初始化每个从站的控制命令（先保持禁用）
                self._cmd = {}
                for node in range(len(self.master.slaves)):
                    try:
                        mode = self.read_mode(node)
                    except Exception:
                        mode = 1
                    self._cmd[node] = {"cw": 0x0000, "mode": mode,
                                       "torque_raw": 0, "friction": None}

                self._pdo_stop.clear()
                self._pdo_thread = threading.Thread(target=self._pdo_loop,
                                                    name="EtherCAT-PDO",
                                                    daemon=True)
                self._pdo_thread.start()
                self.last_error = ""
                return True
            except Exception as e:
                self.last_error = "{}: {}".format(type(e).__name__, e)
                hint = ""
                if not _has_cap_net_raw():
                    hint = ("；EtherCAT 需要 root 或 CAP_NET_RAW，请执行："
                            "sudo setcap cap_net_raw,cap_net_admin+ep {}".format(
                                os.path.realpath(sys.executable)))
                _log("EtherCAT 连接失败：{}{}".format(self.last_error, hint))
                self.close()
                return False

    def disconnect(self):
        """断开 EtherCAT 主站连接。"""
        self.close()
        return True

    def close(self):
        """停止 PDO 线程、禁用输出并关闭主站。"""
        self._pdo_stop.set()
        thread = self._pdo_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._pdo_thread = None

        with self._lock:
            if self.master is not None:
                try:
                    for cmd in self._cmd.values():
                        cmd["cw"] = 0x0000      # Disable voltage
                    self._apply_outputs()
                    # 留约 300ms 让驱动器真正进入 Switch on disabled
                    for _ in range(60):
                        self.master.send_processdata()
                        self.master.receive_processdata(2000)
                        time.sleep(0.005)
                except Exception:
                    pass
                try:
                    self.master.close()
                except Exception:
                    pass
            self.master = None
            self._cmd = {}
            self._op = False

    # ------------------------------------------------------------ PDO 线程
    def _pdo_loop(self):
        while not self._pdo_stop.is_set():
            try:
                with self._lock:
                    if self.master is None:
                        break
                    self._apply_outputs()
                    self.master.send_processdata()
                    self.master.receive_processdata(2000)
            except Exception:
                pass
            now = time.time()
            if now - self._last_diag >= 1.0:
                self._last_diag = now
                try:
                    for node, cmd in self._cmd.items():
                        if not cmd.get("friction") or node >= len(self.master.slaves):
                            continue
                        inp = bytes(self.master.slaves[node].input)
                        v = int.from_bytes(inp[IN_VEL:IN_VEL + 4], "little", signed=True) \
                            * 2.0 * math.pi / PULSES_PER_REV
                        tq_act = int.from_bytes(inp[10:12], "little", signed=True)
                        try:
                            brake_sdo = int.from_bytes(self.read_sdo(node, 0x381E)[:2], "little")
                        except Exception:
                            brake_sdo = None
                        _log("[EtherCAT] node%d mode=%d SW=0x%04X v=%.3f rad/s "
                             "tq_cmd=%d tq_act=%d brake_cmd=%s brake_sdo=%s" % (
                                 node, cmd.get("last_mode", inp[IN_MODE]),
                                 cmd.get("last_status", 0), v,
                                 cmd.get("last_torque", 0), tq_act,
                                 cmd.get("brake", 0), brake_sdo))
                except Exception:
                    pass
            time.sleep(PDO_CYCLE_S)

    def _apply_outputs(self):
        """把 _cmd 写入各从站的 RxPDO 缓冲区。"""
        if self.master is None:
            return
        for node, cmd in self._cmd.items():
            if node >= len(self.master.slaves):
                continue
            slave = self.master.slaves[node]
            inp = bytes(slave.input)
            out = bytearray(slave.output)
            if len(inp) < 19 or len(out) < 17:
                continue
            pos = int.from_bytes(inp[IN_POS:IN_POS + 4], "little", signed=True)
            cw = int(cmd.get("cw", 0)) & 0xFFFF
            mode = int(cmd.get("mode", 1)) & 0xFF

            fric = cmd.get("friction")
            if fric:
                # 0x606C 单位 puu/s；v(rad/s) = puu/s * 2π / 131072
                vel = int.from_bytes(inp[IN_VEL:IN_VEL + 4], "little", signed=True)
                v = vel * 2.0 * math.pi / PULSES_PER_REV
                # 速度低通 + 启动阈值 + 渐入，避免噪声/重力触发后自己跑
                v_filt = 0.9 * float(cmd.get("v_filt", 0.0)) + 0.1 * v
                cmd["v_filt"] = v_filt
                if abs(v_filt) < fric["deadband"]:
                    torque_nm = 0.0
                else:
                    v_start = max(float(fric.get("v_start", 0.2)), 1e-6)
                    ramp = min(1.0, abs(v_filt) / v_start)
                    torque_nm = (fric["fc"] * ramp * (1.0 if v_filt > 0 else -1.0)
                                 + fric["b"] * v_filt)
                unit = fric.get("unit", "raw")
                rated_mNm = float(fric.get("rated_mNm") or 0)
                if unit == "nm" and rated_mNm > 0:
                    raw = torque_nm * 1_000_000.0 / rated_mNm
                elif unit == "0.1nm" and rated_mNm > 0:
                    raw = torque_nm * 100_000.0 / rated_mNm
                else:
                    raw = torque_nm
                torque = int(round(raw))
                torque = max(-fric["max"], min(fric["max"], torque))
            else:
                torque = int(round(float(cmd.get("torque_raw", 0))))

            out[OUT_CW:OUT_CW + 2] = cw.to_bytes(2, "little")
            out[OUT_POS:OUT_POS + 4] = int(pos).to_bytes(4, "little", signed=True)
            out[OUT_VEL:OUT_VEL + 4] = (0).to_bytes(4, "little", signed=True)
            out[OUT_TORQUE:OUT_TORQUE + 2] = int(torque).to_bytes(2, "little", signed=True)
            out[OUT_TORQUE_OFFSET:OUT_TORQUE_OFFSET + 4] = (0).to_bytes(4, "little", signed=True)
            out[OUT_MODE] = mode
            slave.output = bytes(out)
            cmd["last_torque"] = int(torque)
            cmd["last_mode"] = int(mode)
            cmd["last_status"] = int.from_bytes(inp[IN_STATUS:IN_STATUS + 2], "little")

    def get_slave_count(self):
        """返回总线上的从站数量。"""
        if self.master is None:
            return 0
        return len(self.master.slaves)

    def _slave(self, node):
        if self.master is None:
            raise RuntimeError("EtherCAT 未连接")
        idx = int(node)
        count = len(self.master.slaves)
        if idx < 0 or idx >= count:
            raise IndexError(
                "EtherCAT 从站下标越界: {}（共 {} 个从站，下标 0..{}）".format(
                    idx, count, count - 1))
        return self.master.slaves[idx]

    # ------------------------------------------------------------ SDO
    def read_sdo(self, node, index, subindex=0):
        """读取 SDO 原始字节。node 是从站下标，index 是对象字典地址。"""
        with self._lock:
            return bytes(self._slave(node).sdo_read(int(index), int(subindex)))

    def write_sdo(self, node, index, value, size=2):
        """写入一个整数 SDO，成功返回 True。"""
        data = int(value).to_bytes(size, byteorder="little", signed=False)
        with self._lock:
            self._slave(node).sdo_write(int(index), 0, data)
        return True

    # ------------------------------------------------------------ 状态/模式
    def read_status(self, node):
        """读取 0x6041 状态字。"""
        return int.from_bytes(self.read_sdo(node, STATUS_WORD)[:2], "little")

    def read_mode(self, node):
        """读取 0x6060 当前模式。"""
        data = self.read_sdo(node, 0x6060)
        return int.from_bytes(data[:1], "little", signed=True)

    # ------------------------------------------------------------ 扭矩/使能
    def set_torque(self, node, torque):
        """设置 0x6071 原始值（单位 0.1% 额定扭矩），实际通过 PDO 下发。"""
        node = int(node)
        with self._lock:
            cmd = self._cmd.setdefault(node, {"cw": 0, "mode": 10, "torque_raw": 0, "friction": None})
            cmd["torque_raw"] = int(round(float(torque)))
            cmd["friction"] = None
        return True

    def set_torque_enable(self, node, enable):
        """通过 PDO 写控制字（0x000F 使能 / 0x0007 禁用运行）。"""
        node = int(node)
        with self._lock:
            cmd = self._cmd.setdefault(node, {"cw": 0, "mode": 4, "torque_raw": 0, "friction": None})
            cmd["cw"] = 0x000F if enable else 0x0000
        return True

    def set_friction_compensation(self, node, enabled=True, fc=5.0, b=10.0,
                                  deadband=0.15, max_raw=150, unit="0.1nm",
                                  v_start=0.3):
        """启用手动拖动摩擦力前馈（通过 PDO 每周期更新 0x6071）。

        力矩 = Fc*sign(v) + B*v，v 由 0x606C 换算为 rad/s。
        unit:
          - "nm"    : Fc/B 按 Nm / (Nm·s/rad)
          - "0.1nm" : Fc/B 按 0.1 Nm 单位（默认）
          - "raw"   : 直接作为 0x6071 原始值（0.1% 额定扭矩）
        默认 0.1nm 是为了兼容现场习惯值 Fc=5、B=10（即 0.5Nm / 1.0Nm·s/rad）。
        """
        node = int(node)
        rated_mNm = 0
        try:
            rated_mNm = int.from_bytes(self.read_sdo(node, 0x6076)[:4], "little")
        except Exception:
            rated_mNm = 0
        with self._lock:
            cmd = self._cmd.setdefault(node, {"cw": 0, "mode": 4, "torque_raw": 0, "friction": None})
            if enabled:
                cmd["friction"] = {
                    "fc": float(fc),
                    "b": float(b),
                    "deadband": max(0.0, float(deadband)),
                    "v_start": max(0.001, float(v_start)),
                    "max": max(1, int(max_raw)),
                    "unit": str(unit),
                    "rated_mNm": rated_mNm,
                }
            else:
                cmd["friction"] = None
        return True

    def release_brake(self, node, enable=True):
        """Pr8.30 / 0x381E：写 128 强制打开抱闸，写 0 恢复。"""
        node = int(node)
        value = 128 if enable else 0
        self.write_sdo(node, 0x381E, value, size=2)
        with self._lock:
            cmd = self._cmd.setdefault(node, {"cw": 0, "mode": 4, "torque_raw": 0, "friction": None})
            cmd["brake"] = value
        return True

    def disable_torque(self, node):
        """关闭扭矩使能、停止摩擦补偿并恢复抱闸。"""
        node = int(node)
        self.set_torque_enable(node, False)      # 0x0000 Disable Voltage
        with self._lock:
            cmd = self._cmd.get(node)
            if cmd:
                cmd["friction"] = None
        for _ in range(20):
            try:
                if not (self.read_status(node) & 0x0004):
                    break
            except Exception:
                pass
            time.sleep(0.05)
        try:
            self.write_sdo(node, 0x381E, 0, size=2)  # 恢复抱闸
        except Exception:
            pass
        with self._lock:
            cmd = self._cmd.get(node)
            if cmd:
                cmd["brake"] = 0
                cmd["friction"] = None
        try:
            return not bool(self.read_status(node) & 0x0004)
        except Exception:
            return True

    def enable(self, node):
        """进入扭矩模式并使能（0 扭矩）。"""
        return self.set_torque_mode(node, 0.0)

    def set_torque_mode(self, node, torque=0):
        """通过 PDO 进入扭矩模式并执行 CiA402 使能序列。

        返回 True 表示状态字 bit2=1（Operation enabled）。
        """
        node = int(node)
        if self.master is None or not self._op:
            self.last_error = "EtherCAT 未进入 OP，无法使能"
            _log(self.last_error)
            return False

        # TC600E 使能序列：0x0000(Disable Voltage) -> 0x0006 -> 0x0007 -> 0x000F
        with self._lock:
            cmd = self._cmd.setdefault(node, {"cw": 0, "mode": 4, "torque_raw": 0, "friction": None})
            cmd["mode"] = 4
            cmd["torque_raw"] = int(round(float(torque)))
            cmd["friction"] = None
            cmd["cw"] = 0x0000
        time.sleep(0.15)

        with self._lock:
            cmd["cw"] = 0x0006
        time.sleep(0.15)

        with self._lock:
            cmd["cw"] = 0x0007
        time.sleep(0.15)

        with self._lock:
            cmd["cw"] = 0x000F

        # 等待 Operation enabled（bit2）
        for _ in range(40):
            try:
                with self._lock:
                    if self.master is None:
                        return False
                    sw = int.from_bytes(
                        bytes(self.master.slaves[node].input)[IN_STATUS:IN_STATUS + 2],
                        "little")
            except Exception:
                return False
            if sw & 0x0004:
                return True
            time.sleep(0.05)
        return False

    def torque_status(self, node):
        """返回扭矩模式诊断信息（供日志/调试）。"""
        node = int(node)
        with self._lock:
            if self.master is None or node >= len(self.master.slaves):
                return {}
            inp = bytes(self.master.slaves[node].input)
            cmd = self._cmd.get(node, {})
            sw = int.from_bytes(inp[IN_STATUS:IN_STATUS + 2], "little")
            v = int.from_bytes(inp[IN_VEL:IN_VEL + 4], "little", signed=True) \
                * 2.0 * math.pi / PULSES_PER_REV

            def sdo_int(idx, size=2, signed=False):
                try:
                    return int.from_bytes(self.read_sdo(node, idx)[:size],
                                          "little", signed=signed)
                except Exception:
                    return None

            return {
                "mode": inp[IN_MODE] if len(inp) > IN_MODE else None,
                "status": "0x%04X" % sw,
                "enabled": bool(sw & 0x0004),
                "velocity_rad_s": round(v, 4),
                "target_torque_raw": cmd.get("last_torque"),
                "brake_cmd": cmd.get("brake", 0),
                "brake_sdo": sdo_int(0x381E),
                "actual_torque_raw": sdo_int(0x6077, 2, True),
                "target_torque_sdo": sdo_int(0x6071, 2, True),
                "rated_torque_mNm": sdo_int(0x6076, 4),
                "max_torque_raw": sdo_int(0x6072),
                "limit_pos_raw": sdo_int(0x60E0),
                "limit_neg_raw": sdo_int(0x60E1),
            }

    # ------------------------------------------------------------ 位置/信息
    def zero_position(self, node):
        """写 5 执行标零，再返回 0x6064 的当前位置脉冲值。"""
        self.write_sdo(node, ZERO_POSITION, 5)
        time.sleep(0.3)  # 等待 100ms，确保标零完成
        data = self.read_sdo(node, POSITION_ACTUAL)
        return float(struct.unpack("<i", data[:4])[0])

    def read_position(self, node):
        """读取 0x6064 当前绝对位置，单位为脉冲。"""
        data = self.read_sdo(node, POSITION_ACTUAL)
        return float(struct.unpack("<i", data[:4])[0])

    def read_angle(self, node):
        """读取当前位置并换算为输出端角度（含减速比）。

        与 position_controller(同川) 一致：
        角度 = 脉冲 * 360 / (131072 * GEAR_RATIO)
        """
        return self.read_position(node) * 360.0 / (PULSES_PER_REV * GEAR_RATIO)

    def read_info(self, node, address):
        """读取 SDO 并返回字符串；字符串对象按 UTF-8 解码，其它返回十六进制。"""
        index = int(str(address).strip(), 0)
        subindex = 4 if index == 0x1018 else 0
        data = self.read_sdo(node, index, subindex)
        if index in (0x1008, 0x3040,0x100A,0x1009,0x100a):
            return data.rstrip(b"\x00").decode("utf-8", errors="replace")
        return "0x" + data.hex().upper()


driver = TongchuanDriver()
