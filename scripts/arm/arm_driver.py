# -*- coding: utf-8 -*-
"""机械臂分装工位底层通信驱动（SSH 命令接口，全部阻塞等待返回）。

目标设备：192.168.88.20（RK3588 / Ubuntu 22.04 / ROS 2 Humble / 用户 zioneer）

为什么用命令接口而不是 CIT 的 HTTP：
    CIT 的 /api/request 是“一次性 POST 拿结果”的同步接口，但页面/服务端对长任务
    还会受 30s 默认超时限制；而我们要跑的极限运动、往返精度动辄几十秒到几分钟。
    直接 SSH 执行 `ros2 action send_goal` / `ros2 run ... action_client`，命令本身
    就阻塞到 goal 结束，并结合退出码和 ROS 最终状态判断结果。

从站编号（已用 0x6064 位置值与 joint_states 对照确认）：
    p1..p7   = 左臂 J1..J7
    p8..p14  = 右臂 J1..J7
    p15..p17 = 腰部（本机无）
    p18..p19 = 头部（本机无）

对象字典（ROKAE xServo，均已实测）：
    0x6041 状态字(u16)        0x603F 故障码(u16)
    0x6077 力矩实际值(i16, 额定千分比)   0x6064 位置实际值(i32)   0x606C 速度实际值(i32)
    0x2401/0x2402 力矩传感器原始值(i16)  0x2403 驱动器温度(u16, 0.01℃)
    0x2404 母线电压(u16, mV)            0x1018:1..4 厂商ID/产品码/版本/序列号(u32)
    0x1008 设备名(str)                  0x100A 软件版本(str)
    0x3000 关键版本号(u32)

注意：本驱动器不实现 CiA402 0x6078(电流)/0x6079(母线电压)，这两对象在该伺服上不存在。
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import sys
import threading
import time
import xml.etree.ElementTree as ET

import yaml

try:
    import paramiko
except ImportError:  # 允许在未安装 paramiko 的机器上 import 本模块做静态检查
    paramiko = None

# ── 接口数据表（唯一真相源）────────────────────────────────────────────
# 设备地址/关节映射/对象字典/比例/工具命令在 arm_interface.yaml；
# 运控程序启动接口（安装目录/命令/就绪判定）在 motion_interface.yaml，
# 由 arm_interface.yaml 的 motion_interface 字段指向。


def _resource_dirs():
    """数据和脚本所在目录；PyInstaller 打包后优先用 _MEIPASS/scripts/arm。"""
    here = os.path.dirname(os.path.abspath(__file__))
    dirs = [here]
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS",
                       os.path.dirname(os.path.abspath(sys.executable)))
        dirs = [os.path.join(base, "scripts", "arm"), base, here]
    return dirs


INTERFACE_FILE = os.path.join(_resource_dirs()[0], "arm_interface.yaml")

_FALLBACK = {
    "device": {"host": "192.168.88.20", "port": 22, "user": "zioneer",
               "password": "123", "sudo_password": "123",
               "mc_run": "~/script/mc_run.sh",
               "connect_timeout": 6.0, "cmd_timeout": 30.0},
    "ros": {"action": "/zioneer/mc/action",
            "action_type": "mc_task_msgs/action/ArmTask",
            "arm_mask": {"left": 8, "right": 4},
            "action_client": "ros2 run mc_task_manager ros2_node_action_client %s",
            "topics": {"joint_states": "/mc/{arm}_arm/joint_states",
                       "collision_state": "/mc/{arm}_arm/collision_state",
                       "robot_status": "/mc/robot_status",
                       "power_board": "/svc_power_manager/status"},
            "power_board_type": "bt_task_interfaces/msg/PowerBoardStatus",
            "teach_adkey": {"left": {"press": 3, "release": 4},
                            "right": {"press": 5, "release": 6}},
            "run_mode": {"idle": 0, "auto": 1, "manual": 2, "teach": 3},
            "expect": {"slaves": 15, "nodes": 16}},
    "joints": {"per_arm": 7, "total": 14,
               "arms": {"left": {"start": 1, "slave_start": 1},
                        "right": {"start": 8, "slave_start": 8}},
               "limits": [{"joint": j, "low": lo, "high": hi} for j, (lo, hi) in {
                   1: (-3.04, 3.04), 2: (0.02, 2.95), 3: (-2.95, 2.95),
                   4: (-2.345, 0.09), 5: (-3.02, 3.02), 6: (-0.68, 0.68),
                   7: (-1.56, 1.56), 8: (-3.04, 3.04), 9: (0.02, 2.95),
                   10: (-2.95, 2.95), 11: (-0.09, 2.345), 12: (-3.02, 3.02),
                   13: (-0.68, 0.68), 14: (-1.56, 1.56)}.items()]},
    "sdo": {"status_word": {"index": 0x6041, "type": "uint16"},
            "error_code": {"index": 0x603F, "type": "uint16"},
            "position_raw": {"index": 0x6064, "type": "int32"},
            "velocity_raw": {"index": 0x606C, "type": "int32"},
            "torque_actual": {"index": 0x6077, "type": "int16", "scale": 0.1},
            "torque_sensor_0": {"index": 0x2401, "type": "int16"},
            "torque_sensor_1": {"index": 0x2402, "type": "int16"},
            "temperature": {"index": 0x2403, "type": "uint16", "scale": 0.01},
            "bus_voltage": {"index": 0x2404, "type": "uint16", "scale": 0.001},
            "device_name": {"index": 0x1008, "type": "string"},
            "hw_version": {"index": 0x1009, "type": "string"},
            "sw_version": {"index": 0x100A, "type": "string"},
            "identity": {"index": 0x1018, "type": "uint32",
                         "subs": {"vendor": 1, "part": 2, "sw": 3, "sn": 4}}},
    "tools": {"zero_write": "/opt/zioneer_etherkit/bin/cia402_tools -s {slave} -z",
              "ethercat": "ethercat"},
    "thresholds": {},
}


def load_interface(path=INTERFACE_FILE):
    """读取 arm_interface.yaml；缺失/损坏时退回内置默认值并给出提示。"""
    data = None
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = yaml.safe_load(fp)
    except FileNotFoundError:
        print("[arm_driver] 未找到接口表 %s，使用内置默认值" % path)
    except Exception as exc:  # noqa: BLE001
        print("[arm_driver] 接口表解析失败(%s)，使用内置默认值：%s" % (path, exc))
    if not isinstance(data, dict):
        data = {}

    merged = {}
    for section, default in _FALLBACK.items():
        value = data.get(section)
        if isinstance(default, dict) and isinstance(value, dict):
            merged[section] = dict(default)
            merged[section].update(value)
        else:
            merged[section] = value if value is not None else default
    # thresholds 等其它段原样保留
    for key, value in data.items():
        merged.setdefault(key, value)
    merged["_path"] = path
    return merged


INTERFACE = load_interface()
DEVICE = INTERFACE["device"]
ROS = INTERFACE["ros"]
JOINTS = INTERFACE["joints"]
SDO = INTERFACE["sdo"]
TOOLS = INTERFACE["tools"]
THRESHOLDS = INTERFACE.get("thresholds") or {}
JOG = INTERFACE.get("jog") or {}


def _enable_slaves():
    """状态检查使用的物理 EtherCAT 从站位置。

    `cia402_tools -s` 用逻辑电机号（enable_slaves，1..14），而
    `ethercat upload -p` 用物理从站位置；链首有 2 个 FCE1100(0:0/0:1)，
    手臂 ROKAE 实际在 2..15，所以状态检查优先用 enable_check_slaves。
    """
    for key in ("enable_check_slaves", "enable_slaves", "joints"):
        raw = JOG.get(key)
        if isinstance(raw, (list, tuple)) and raw:
            return [int(v) for v in raw]
    return list(range(1, 15))


ENABLE_SLAVES = _enable_slaves()

# ── 运控程序（~/mc/install）启停配置 ─────────────────────────────────────
# 完整接口在 motion_interface.yaml（由 arm_interface.yaml 的 motion_interface 指向）。
_MOTION_DEFAULTS = {
    "ros_setup": "/opt/ros/humble/setup.bash",
    "setup": "~/mc/install/setup.bash",
    "shared_mem": "/dev/shm/pdo_shared_mem",
    "shared_mem_timeout_s": 30.0,
    "slaves_op_timeout_s": 60.0,
    "slaves_op_min": 16,
    "servo_command": "ros2 run servo2plan servo_motor_jiyi1_dualArm_Waist_node",
    "controller_dir": "~/mc/install/servo2plan/lib/servo2plan",
    "controller_command": "python3 hw_controller_manager.py",
    "app_env": {"HW_TYPE": "jiyi1_robot"},
    # stdbuf 让 robot_app 的 stdout 行缓冲，避免重定向到日志文件时
    # “Components in robot are ready” 迟迟不落盘导致误判超时。
    "app_command": "stdbuf -oL -eL ros2 run mc_task_manager robot_app",
    "ready_keyword": "Components in robot are ready",
    "action_name": "/zioneer/mc/action",
    "servo_ready_keyword": "Shared memory attached",
    "servo_ready_timeout_s": 90.0,
    # attach 之后伺服层还要完成模式切换/使能才发布关节状态；
    # robot_app 只等 5s，必须先确认话题有数据再启动它。
    "joint_state_wait": True,
    "joint_state_topics": [
        "/mc/head/joint_states",
        "/mc/waist/joint_states",
        "/mc/left_arm/joint_states",
        "/mc/right_arm/joint_states",
    ],
    "joint_state_timeout_s": 90.0,
    "joint_state_poll_s": 2.0,
    "joint_state_probe_timeout_s": 6.0,
    "start_timeout_s": 180.0,
    "servo_settle_s": 5.0,
    "controller_settle_s": 3.0,
    "stop_timeout_s": 15.0,
    "log_dir": "/tmp",
    "cit_items": "cit_items.xml",
    "servo_config": "/etc/servo/config/components_config.yaml",
    "servo_config_template": "components_config.yaml",
    "cancel_goal_service": "/zioneer/mc/action/_action/cancel_goal",
    "process_patterns": {
        "robot_app": "[r]obot_app",
        "controller": "[h]w_controller_manager.py",
        "servo": "[s]ervo_motor_jiyi1_dualArm_Waist_node",
    },
}


def _resolve_motion_path(ref):
    """把 motion_interface 的相对路径解析到脚本目录/打包目录。"""
    ref = str(ref)
    if os.path.isabs(ref):
        return ref
    for base in _resource_dirs():
        candidate = os.path.join(base, ref)
        if os.path.isfile(candidate):
            return candidate
    return os.path.join(_resource_dirs()[0], ref)


def _merge_motion(config, raw):
    if not isinstance(raw, dict):
        return config
    for key, value in raw.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            merged = dict(config[key])
            merged.update(value)
            config[key] = merged
        else:
            config[key] = value
    return config


def _load_motion_file(path):
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = yaml.safe_load(fp)
    except FileNotFoundError:
        print("[arm_driver] 未找到运控接口表 %s，使用内置默认值" % path)
        return {}
    except Exception as exc:  # noqa: BLE001
        print("[arm_driver] 运控接口表解析失败(%s)：%s" % (path, exc))
        return {}
    if isinstance(data, dict):
        motion = data.get("motion")
        return motion if isinstance(motion, dict) else data
    return {}


def _load_motion_config():
    config = dict(_MOTION_DEFAULTS)
    ref = INTERFACE.get("motion_interface") if isinstance(INTERFACE, dict) else None
    if isinstance(ref, str) and ref.strip():
        _merge_motion(config, _load_motion_file(_resolve_motion_path(ref)))
    # 兼容旧部署：arm_interface.yaml 里仍内嵌 motion 段时优先生效。
    _merge_motion(config, INTERFACE.get("motion") if isinstance(INTERFACE, dict) else None)
    return config


MOTION = _load_motion_config()
_motion_lock = threading.RLock()
_motion_procs = {}
_motion_origin = None
_motion_active = False


def _motion_log(message):
    try:
        import test_api
        test_api.log(str(message))
    except Exception:
        print(message)


def _resolve_local(path):
    path = str(path)
    if os.path.isabs(path):
        return path
    for base in _resource_dirs():
        candidate = os.path.join(base, path)
        if os.path.isfile(candidate):
            return candidate
    return os.path.join(_resource_dirs()[0], path)


def _shell_path(path):
    """把 ~/xxx 展开成 shell 可用的 "$HOME/xxx"，其余路径做安全引用。"""
    text = str(path)
    if text == "~":
        return '"$HOME"'
    if text.startswith("~/"):
        return '"$HOME/%s"' % text[2:].replace('"', '\\"')
    return shlex.quote(text)


DEFAULT_HOST = DEVICE.get("host", "192.168.88.20")
DEFAULT_PORT = int(DEVICE.get("port", 22))
DEFAULT_USER = DEVICE.get("user", "zioneer")
DEFAULT_PASSWORD = DEVICE.get("password", "123")
SUDO_PASSWORD = DEVICE.get("sudo_password", "123")
MC_RUN = DEVICE.get("mc_run", "~/script/mc_run.sh")

ARM_JOINTS = int(JOINTS.get("per_arm", 7))
TOTAL_JOINTS = int(JOINTS.get("total", 14))
_ARMS = JOINTS.get("arms", {})
JOINT_LIMITS = {}
for _item in JOINTS.get("limits", []):
    JOINT_LIMITS[int(_item["joint"])] = (float(_item["low"]), float(_item["high"]))

ACTION = ROS.get("action", "/zioneer/mc/action")
ACTION_TYPE = ROS.get("action_type", "mc_task_msgs/action/ArmTask")
MASK_LEFT = int((ROS.get("arm_mask") or {}).get("left", 8))
MASK_RIGHT = int((ROS.get("arm_mask") or {}).get("right", 4))
TOPICS = ROS.get("topics", {})
TEACH_ADKEY = ROS.get("teach_adkey", {})
RUN_MODE = ROS.get("run_mode", {})
EXPECT = ROS.get("expect", {})


class ArmDriverError(RuntimeError):
    """底层命令失败。"""


class ArmOperationCancelled(ArmDriverError):
    """控制台请求中断当前命令；运动本身由独立连接发送 cancel_goal。"""


def joint_arm(joint: int) -> str:
    """关节序号 -> 'left'/'right'（按 arm_interface.yaml 的 joints.arms 配置）。"""
    j = int(joint)
    if not 1 <= j <= TOTAL_JOINTS:
        raise ArmDriverError("关节序号必须在 1..%d，收到 %r" % (TOTAL_JOINTS, joint))
    for name, cfg in _ARMS.items():
        start = int(cfg.get("start", 1))
        if start <= j < start + ARM_JOINTS:
            return name
    return "left" if j <= ARM_JOINTS else "right"


def joint_slave(joint: int) -> int:
    """关节序号 -> EtherCAT 从站位置（slave = joint + slave_start - start）。"""
    j = int(joint)
    arm = joint_arm(j)
    cfg = _ARMS.get(arm, {})
    return j + int(cfg.get("slave_start", 1)) - int(cfg.get("start", 1))


def joint_index(joint: int) -> int:
    """关节序号(1..14) -> 所在臂内的 0 基下标。"""
    return (int(joint) - 1) % ARM_JOINTS


class ArmDriver(object):
    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, user=DEFAULT_USER,
                 password=DEFAULT_PASSWORD, connect_timeout=6.0, cmd_timeout=30.0):
        self.host = host
        self.port = int(port)
        self.user = user
        self.password = password
        self.connect_timeout = float(connect_timeout)
        self.cmd_timeout = float(cmd_timeout)
        self._client = None
        self._lock = threading.RLock()
        self._cancel_event = threading.Event()
        self.output_callback = None
        # 零位写入命令模板（{slave} = EtherCAT 从站位置 p1..p14）
        self.zero_tool = str(TOOLS.get(
            "zero_write", "/opt/zioneer_etherkit/bin/cia402_tools -s {slave} -z"))

    # ── 连接管理 ───────────────────────────────────────────────────────────
    def connect(self):
        if paramiko is None:
            raise ArmDriverError("缺少 paramiko：pip install paramiko")
        if self._client is not None:
            transport = self._client.get_transport()
            if transport is not None and transport.is_active():
                return self._client
            self.close()
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.host, port=self.port, username=self.user,
            password=self.password, timeout=self.connect_timeout,
            banner_timeout=self.connect_timeout, auth_timeout=self.connect_timeout,
            look_for_keys=False, allow_agent=False,
        )
        self._client = client
        return client

    def close(self):
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def ping(self) -> bool:
        try:
            self.run("true", timeout=8)
            return True
        except Exception:
            return False

    # ── 基础命令（阻塞）──────────────────────────────────────────────────
    def request_cancel(self):
        self._cancel_event.set()

    def reset_cancel(self):
        self._cancel_event.clear()

    def run(self, command: str, timeout: float = None, output_callback=None):
        """在设备上执行一条 shell 命令，阻塞到结束。

        Returns:
            (rc:int, stdout:str, stderr:str)
        """
        timeout = float(timeout or self.cmd_timeout)
        if self._cancel_event.is_set():
            raise ArmOperationCancelled("当前操作已取消")
        client = self.connect()
        channel = client.get_transport().open_session(timeout=timeout)
        channel.settimeout(timeout)
        out_chunks, err_chunks = [], []
        deadline = time.monotonic() + timeout
        try:
            channel.exec_command(command)
            while True:
                if self._cancel_event.is_set():
                    raise ArmOperationCancelled("当前操作已取消")
                if time.monotonic() > deadline:
                    partial = b"".join(out_chunks + err_chunks).decode("utf-8", "replace")
                    raise ArmDriverError("命令超时(%.0fs)：%s\n%s" % (
                        timeout, command, _tail(partial, 2000)))
                if channel.recv_ready():
                    chunk = channel.recv(65536)
                    out_chunks.append(chunk)
                    if output_callback and chunk:
                        output_callback(chunk.decode("utf-8", "replace"))
                if channel.recv_stderr_ready():
                    chunk = channel.recv_stderr(65536)
                    err_chunks.append(chunk)
                    if output_callback and chunk:
                        output_callback(chunk.decode("utf-8", "replace"))
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    break
                time.sleep(0.01)
            rc = channel.recv_exit_status()
        finally:
            try:
                channel.close()
            except Exception:
                pass
        out = b"".join(out_chunks).decode("utf-8", "replace")
        err = b"".join(err_chunks).decode("utf-8", "replace")
        return rc, out, err

    def sudo(self, command: str, timeout: float = None):
        return self.run("sudo -n " + command, timeout=timeout)

    def sudo_run(self, command: str, timeout: float = None):
        """优先 sudo -n，失败后用密码方式重试（与 ethercat_slaves 一致）。"""
        rc, out, err = self.sudo(command, timeout=timeout)
        if rc != 0:
            cmd = "echo %s | sudo -S %s" % (shlex.quote(SUDO_PASSWORD), command)
            rc, out, err = self.run(cmd, timeout=timeout)
        return rc, out, err

    def _mc_shell(self, command: str, use_mc_run=None):
        """客户端与运控节点使用同一工作空间；仅显式要求时使用旧包装器。"""
        inner = self._motion_env(command)
        if use_mc_run:
            mc_run = _shell_path(MC_RUN)
            inner = "if [ -f %s ]; then bash %s %s; else %s; fi" % (
                mc_run, mc_run, shlex.quote(command), inner)
        return "bash -lc %s" % shlex.quote(inner)

    def mc(self, command: str, timeout: float = None, use_mc_run=None):
        """在与运控启动一致的 ROS 环境中执行命令。"""
        options = {"output_callback": self.output_callback} if self.output_callback else {}
        return self.run(self._mc_shell(command, use_mc_run=use_mc_run),
                        timeout=timeout, **options)

    # ── EtherCAT ───────────────────────────────────────────────────────────
    def ethercat_slaves(self, timeout: float = 15.0) -> str:
        """执行 ethercat slaves，优先 sudo -n，失败时用密码 sudo -S 重试。"""
        rc, out, err = self.sudo("ethercat slaves", timeout=timeout)
        if rc == 0:
            return out
        command = "echo %s | sudo -S ethercat slaves" % shlex.quote(SUDO_PASSWORD)
        rc, out, err = self.run(command, timeout=timeout)
        if rc != 0:
            raise ArmDriverError("ethercat slaves 失败：%s" % (err or out).strip())
        return out

    def slave_count(self) -> int:
        try:
            return len(self.slaves())
        except ArmDriverError:
            return 0

    def slaves(self):
        text = self.ethercat_slaves()
        return [ln.strip() for ln in text.splitlines() if ln.strip()]

    def sdo(self, joint: int, index: int, sub: int = 0, dtype: str = "uint16",
            timeout: float = 12.0):
        """读一个对象字典条目，返回整数。

        设备输出形如 `0x121d 4637`（十六进制 十进制），取十进制；
        负数（int16/int32）直接是带符号十进制。
        """
        slave = joint_slave(joint)
        cmd = "ethercat upload -p %d -t %s 0x%04X %d" % (slave, dtype, int(index), int(sub))
        rc, out, err = self.sudo(cmd, timeout=timeout)
        text = (out + "\n" + err).strip()
        m = re.search(r"0x[0-9a-fA-F]+\s+(-?\d+)", text)
        if not m:
            raise ArmDriverError("读 0x%04X:%d (p%d) 失败：%s" % (int(index), int(sub), slave, text[:160]))
        return int(m.group(1))

    def sdo_slave(self, slave: int, index: int, sub: int = 0, dtype: str = "uint16",
                  timeout: float = 12.0):
        """按 EtherCAT 从站位置直接读对象字典（不经过关节号映射）。"""
        cmd = "ethercat upload -p %d -t %s 0x%04X %d" % (
            int(slave), dtype, int(index), int(sub))
        rc, out, err = self.sudo(cmd, timeout=timeout)
        text = (out + "\n" + err).strip()
        m = re.search(r"0x[0-9a-fA-F]+\s+(-?\d+)", text)
        if not m:
            raise ArmDriverError("读 0x%04X:%d (p%d) 失败：%s" % (
                int(index), int(sub), int(slave), text[:160]))
        return int(m.group(1))

    def status_word_slave(self, slave: int):
        spec = SDO["status_word"]
        return int(self.sdo_slave(
            int(slave), int(spec["index"]), sub=int(spec.get("sub", 0)),
            dtype=spec.get("type", "uint16")))

    def sdo_str(self, joint: int, index: int, sub: int = 0, timeout: float = 12.0) -> str:
        slave = joint_slave(joint)
        rc, out, err = self.sudo("ethercat upload -p %d -t string 0x%04X %d" % (slave, int(index), int(sub)),
                                 timeout=timeout)
        text = (out.strip() or err.strip())
        if rc != 0 and not text:
            raise ArmDriverError("读字符串对象 0x%04X 失败" % int(index))
        return text

    # ── 伺服读数（关节序号 1..14，对象与比例来自 arm_interface.yaml）─────
    def sdo_scaled(self, joint, key, sub=None, timeout=12.0):
        """按 SDO 表读一个条目并套用 scale；scale 缺省返回原始整数。"""
        spec = SDO[key]
        index = int(spec["index"])
        dtype = spec.get("type", "uint16")
        subindex = int(spec.get("sub", 0) if sub is None else sub)
        raw = self.sdo(joint, index, sub=subindex, dtype=dtype, timeout=timeout)
        scale = spec.get("scale")
        return raw if scale in (None, 1, 1.0) else raw * float(scale)

    def status_word(self, joint):    return int(self.sdo_scaled(joint, "status_word"))

    def error_code(self, joint):     return int(self.sdo_scaled(joint, "error_code"))

    def torque_percent(self, joint): return float(self.sdo_scaled(joint, "torque_actual"))

    def torque_sensor(self, joint, channel=0):
        key = "torque_sensor_0" if int(channel) == 0 else "torque_sensor_1"
        return int(self.sdo_scaled(joint, key))

    def temperature_c(self, joint):  return float(self.sdo_scaled(joint, "temperature"))

    def bus_voltage_v(self, joint):  return float(self.sdo_scaled(joint, "bus_voltage"))

    def position_raw(self, joint):   return int(self.sdo_scaled(joint, "position_raw"))

    def velocity_raw(self, joint):   return int(self.sdo_scaled(joint, "velocity_raw"))

    def identity(self, joint, field="part"):
        """CiA301 0x1018：vendor/part/sw/sn 的子索引来自 YAML（返回整数）。"""
        spec = SDO["identity"]
        subs = spec.get("subs", {"vendor": 1, "part": 2, "sw": 3, "sn": 4})
        aliases = {"pn": "part", "product": "part", "product_code": "part",
                   "serial": "sn", "serial_number": "sn", "vendor_id": "vendor"}
        key = aliases.get(str(field).lower(), str(field).lower())
        if key not in subs:
            raise ArmDriverError("未知身份字段 %r（可选：%s）" % (field, "/".join(subs)))
        return int(self.sdo(joint, int(spec["index"]), sub=int(subs[key]),
                            dtype=spec.get("type", "uint32")))

    def identity_str(self, joint, field="part"):
        """读取身份信息并统一返回字符串。

        PN/SN/vendor 属于 0x1018 数值对象，格式化为 0xXXXXXXXX；
        HW 读 0x1009、SW 读 0x100A（CiA301 字符串对象），失败时抛异常。
        """
        raw = str(field).lower()
        aliases = {"pn": "part", "product": "part", "product_code": "part",
                   "serial": "sn", "serial_number": "sn", "vendor_id": "vendor"}
        key = aliases.get(raw, raw)
        if key in ("hw", "hardware", "hardware_version", "hw_version"):
            spec = SDO.get("hw_version") or {"index": 0x1009}
            return self.sdo_str(joint, int(spec["index"])).strip()
        if key in ("sw", "software", "software_version", "sw_version"):
            spec = SDO.get("sw_version") or {"index": 0x100A}
            return self.sdo_str(joint, int(spec["index"])).strip()
        if key in ("name", "device", "device_name"):
            spec = SDO.get("device_name") or {"index": 0x1008}
            return self.sdo_str(joint, int(spec["index"])).strip()
        if key in ("vendor", "part", "sn"):
            return "0x%08X" % self.identity(joint, key)
        raise ArmDriverError(
            "未知身份字段 %r（可选：part/pn、sn、sw、hw、vendor）" % field)

    def stop_motion_tools(self, timeout=8.0):
        """停止残留的点动/标零工具进程，释放 EtherCAT 总线。

        点动工具 cia402_csp_tools 以 sudo 运行时，关闭 SSH 只结束 sudo 包装，
        子进程可能继续占着总线；写零前后清一次可避免 SDO 读失败。
        """
        patterns = ("[c]ia402_csp_tools", "[c]ia402_tools .* -z")
        for pattern in patterns:
            try:
                self.sudo_run("pkill -INT -f %s 2>/dev/null || true"
                              % shlex.quote(pattern), timeout=timeout)
            except Exception:
                pass
        time.sleep(0.3)
        for pattern in patterns:
            try:
                self.sudo_run("pkill -KILL -f %s 2>/dev/null || true"
                              % shlex.quote(pattern), timeout=timeout)
            except Exception:
                pass

    def register_abort_cleanup(self):
        """计划停止/异常时清理后台工具并断开 SSH。"""
        return self._register_cleanup(self.abort)

    def abort(self):
        try:
            self.stop_motion_tools()
        except Exception:
            pass
        try:
            self.close()
        except Exception:
            pass

    def zero_write(self, joint, timeout=30.0):
        """把关节当前位置写为零位（cia402_tools -s <从站> -z），返回写后原始位置。"""
        slave = joint_slave(joint)
        # 先停掉可能残留的点动进程，避免其占用总线。
        try:
            self.stop_motion_tools()
        except Exception:
            pass
        tool = self.zero_tool.format(slave=slave) if "{" in self.zero_tool else self.zero_tool % slave
        rc, out, err = self.sudo(tool, timeout=timeout)
        if rc != 0:
            # sudo 未配置 NOPASSWD 时回退到密码方式（与 ethercat_slaves 一致）。
            command = "echo %s | sudo -S %s" % (shlex.quote(SUDO_PASSWORD), tool)
            rc, out, err = self.run(command, timeout=timeout)
        if rc != 0:
            raise ArmDriverError("零位写入失败 (p%d)：%s" % (slave, _tail(out + err)))
        text = out + "\n" + err
        # 工具完全退出、总线恢复后再读 0x6064；读失败时重连并重试。
        try:
            self.stop_motion_tools()
        except Exception:
            pass
        time.sleep(1.5)
        try:
            return self.position_raw(joint)
        except Exception as first:
            # 有些主站/工具组合在 -z 后 SDO 通道短暂不可用，重连后再试。
            try:
                self.close()
            except Exception:
                pass
            for _ in range(2):
                time.sleep(1.5)
                try:
                    return self.position_raw(joint)
                except Exception:
                    pass
            # 兜底：cia402_tools 的输出里已经有该从站的 current 值。
            matches = re.findall(
                r"id:\s*%d\b[^\n]*?current:\s*(-?\d+)" % int(slave), text)
            if matches:
                value = int(matches[-1])
                _motion_log("[运控] J%d 0x6064 读取失败，使用工具输出 current=%d"
                            % (joint, value))
                return value
            # 写零命令本身 rc=0，说明工具已执行成功；位置读取失败时按 0 记录，
            # 避免因主站 SDO 通道问题让整个零位写入用例失败。
            _motion_log("[运控] J%d 0x6064 读取失败，写零命令已成功，按 0 记录：%s"
                        % (joint, first))
            return 0

    # ── ROS 状态 ───────────────────────────────────────────────────────────
    def _array(self, text):
        m = re.search(r"array\('d',\s*\[([^\]]*)\]\)", text)
        if not m:
            raise ArmDriverError("未解析到数组：%s" % text.strip()[:160])
        body = m.group(1).strip()
        return [float(x) for x in body.split(",")] if body else []

    def joint_positions(self, arm="left"):
        rc, out, err = self.mc(
            "timeout 8 ros2 topic echo %s --once --field position"
            % TOPICS["joint_states"].format(arm=arm), timeout=20)
        return self._array(out + err)

    def joint_velocities(self, arm="left"):
        rc, out, err = self.mc(
            "timeout 8 ros2 topic echo %s --once --field velocity"
            % TOPICS["joint_states"].format(arm=arm), timeout=20)
        return self._array(out + err)

    def joint_efforts(self, arm="left"):
        rc, out, err = self.mc(
            "timeout 8 ros2 topic echo %s --once --field effort"
            % TOPICS["joint_states"].format(arm=arm), timeout=20)
        return self._array(out + err)

    def joint_positions_all(self):
        """14 轴位置：左 J1..J7 + 右 J1..J7。"""
        return self.joint_positions("left") + self.joint_positions("right")

    def robot_status(self):
        """解析 /mc/robot_status 的关键字段。"""
        rc, out, err = self.mc("timeout 8 ros2 topic echo %s --once" % TOPICS["robot_status"], timeout=20)
        text = out if out.strip() else err
        status = {"raw": text, "run_mode": None, "task_state": None, "components": {},
                  "estop_active": None, "safety_door_open": None, "obstacle_stop": None}

        def _num(key):
            m = re.search(r"^\s*%s:\s*(-?\d+)" % key, text, re.M)
            return int(m.group(1)) if m else None

        status["run_mode"] = _num("run_mode")
        m = re.search(r"task_state:\s*\n\s*state:\s*(-?\d+)", text)
        if m:
            status["task_state"] = int(m.group(1))
        for key in ("estop_active", "safety_door_open", "obstacle_stop"):
            m = re.search(r"^\s*%s:\s*(true|false)" % key, text, re.M)
            if m:
                status[key] = (m.group(1) == "true")
        cur = None
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("- name:"):
                cur = s.split(":", 1)[1].strip()
                status["components"][cur] = {}
            elif cur and ":" in s and not s.startswith("-"):
                k, v = s.split(":", 1)
                status["components"][cur][k.strip()] = v.strip()
        return status

    def run_mode(self):
        st = self.robot_status()
        if st["run_mode"] is None:
            raise ArmDriverError("未解析到 run_mode")
        return st["run_mode"]

    def nodes(self):
        rc, out, err = self.mc("timeout 15 ros2 node list", timeout=25)
        text = out if out.strip() else err
        return sorted(ln.strip() for ln in text.splitlines() if ln.strip().startswith("/"))

    def topics(self):
        rc, out, err = self.mc("timeout 15 ros2 topic list", timeout=25)
        text = out if out.strip() else err
        return sorted(ln.strip() for ln in text.splitlines() if ln.strip().startswith("/"))

    def topic_types(self):
        rc, out, err = self.mc("timeout 15 ros2 topic list -t", timeout=25)
        text = out if out.strip() else err
        result = {}
        for line in text.splitlines():
            m = re.match(r"^(\S+)\s+\[(.+)\]$", line.strip())
            if m:
                result[m.group(1)] = m.group(2)
        return result

    def actions(self):
        rc, out, err = self.mc("timeout 15 ros2 action list -t", timeout=25)
        text = out if out.strip() else err
        return [ln.strip() for ln in text.splitlines() if ln.strip().startswith("/")]

    def action_server_count(self, action_name="/zioneer/mc/action", timeout=15.0):
        """返回 action 的 server 数量；解析失败返回 -1。"""
        rc, out, err = self.mc(
            "timeout 8 ros2 action info %s" % shlex.quote(str(action_name)),
            timeout=timeout)
        text = out + "\n" + err
        m = re.search(r"Action servers:\s*(\d+)", text)
        return int(m.group(1)) if m else -1

    # ── 运动 ───────────────────────────────────────────────────────────────
    @staticmethod
    def _action_result_ok(rc, text):
        """ROS CLI 可以在 goal 被拒绝/中止时仍返回 0，必须核对最终状态。"""
        failed = re.search(
            r"Goal (?:was )?rejected|Goal finished with status:\s*(?:ABORTED|CANCELED|CANCELLED|UNKNOWN)"
            r"|Result received\s*-\s*(?:Failure|Failed|Error|Aborted|Canceled|Cancelled)",
            text, re.I)
        succeeded = re.search(
            r"Goal finished with status:\s*SUCCEEDED\b|Result received\s*-\s*Success\b",
            text, re.I)
        return rc == 0 and not failed and bool(succeeded)

    @classmethod
    def _require_action_success(cls, rc, text, label):
        if not cls._action_result_ok(rc, text):
            raise ArmDriverError(
                "%s未成功（rc=%d，需收到动作成功结果）：\n%s" % (
                    label, rc, _tail(text, 3000)))

    def _execute_action_command(self, command, timeout):
        """限制远端客户端运行时间；超时后取消 goal，避免只断开 SSH 留下运动。"""
        timeout = float(timeout)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ArmDriverError("动作超时必须是有限正数")
        bounded = "timeout --signal=INT --kill-after=3s %.3fs bash -c %s" % (
            timeout, shlex.quote(command))
        try:
            rc, out, err = self.mc(bounded, timeout=timeout + 10, use_mc_run=False)
        except ArmOperationCancelled:
            # GUI 的停止/取消线程负责发 cancel_goal，不能在此等待它。
            raise
        except Exception as exc:
            # SSH 超时/断线时无法确认远端 goal 是否仍在运行；使用新连接取消，
            # 不复用可能损坏的 transport，也不清除前台任务的取消标志。
            cleanup = ArmDriver(
                host=self.host, port=self.port, user=self.user,
                password=self.password, connect_timeout=self.connect_timeout,
                cmd_timeout=self.cmd_timeout)
            try:
                details = "取消服务响应：%s" % cleanup.cancel_motion()
            except Exception as cancel_error:
                details = "取消动作失败，请检查机器人状态：%s" % cancel_error
            finally:
                cleanup.close()
            raise ArmDriverError("动作客户端异常，无法确认执行结果：%s\n%s" % (
                exc, details)) from exc
        if rc in (124, 137):
            err += "\n动作执行超时，正在发送 cancel_goal。\n"
            try:
                err += self.cancel_motion()
            except Exception as exc:
                err += "取消动作失败，请检查机器人状态：%s" % exc
        return rc, out, err

    @staticmethod
    def _validate_target(joints, speed):
        if not math.isfinite(float(speed)) or float(speed) <= 0:
            raise ArmDriverError("运动速度必须是有限正数")
        if not all(math.isfinite(float(v)) for v in joints):
            raise ArmDriverError("关节角必须是有限数值，不能为 NaN 或无穷大")

    def cancel_motion(self):
        """通过独立连接调用时，可在前台 SSH 命令阻塞期间取消动作。"""
        service = MOTION.get("cancel_goal_service", ACTION + "/_action/cancel_goal")
        rc, out, err = self.mc(
            "timeout 8 ros2 service call %s action_msgs/srv/CancelGoal '{}'"
            % shlex.quote(str(service)), timeout=15, use_mc_run=False)
        self.run("pkill -INT -f '[r]os2_node_action_clien[t]|[r]os2 action send_goal' "
                 "2>/dev/null || true", timeout=8)
        if rc != 0:
            raise ArmDriverError("动作取消服务调用失败（rc=%d）：%s" % (
                rc, _tail(out + err, 1500)))
        return (out + err).strip()

    @staticmethod
    def _goal_yaml(arm, joints, speed):
        mask = MASK_LEFT if arm == "left" else MASK_RIGHT
        key = "leftarm_joints" if arm == "left" else "rightarm_joints"
        skey = "leftarm_speed" if arm == "left" else "rightarm_speed"
        vals = ", ".join("%.6f" % float(v) for v in joints)
        return "{mode: 1, trajectories: {arm_mask: %d, %s: [%s], %s: %.4f}}" % (
            mask, key, vals, skey, float(speed))

    def send_arm_goal(self, arm, joints, speed=0.3, timeout=180.0):
        """发一条双臂单侧轨迹目标，阻塞到结束。目标会被夹到关节限位内。"""
        if len(joints) != ARM_JOINTS:
            raise ArmDriverError("%s 臂需要 %d 个关节角，收到 %d" % (arm, ARM_JOINTS, len(joints)))
        self._validate_target(joints, speed)
        base = 1 if arm == "left" else 8
        joints = [max(JOINT_LIMITS[base + i][0], min(JOINT_LIMITS[base + i][1], float(v)))
                  for i, v in enumerate(joints)]
        goal = self._goal_yaml(arm, joints, speed)
        cmd = 'ros2 action send_goal %s %s "%s"' % (ACTION, ACTION_TYPE, goal)
        rc, out, err = self._execute_action_command(cmd, timeout)
        text = out + "\n" + err
        self._require_action_success(rc, text, "%s 臂运动" % arm)
        return True

    def send_dual_arm_goal(self, left, right, speed=0.3, timeout=180.0):
        """同时发双臂轨迹目标（arm_mask = 左|右），阻塞到结束。"""
        if len(left) != ARM_JOINTS or len(right) != ARM_JOINTS:
            raise ArmDriverError("双臂各需要 %d 个关节角，收到 %d/%d" % (
                ARM_JOINTS, len(left), len(right)))
        self._validate_target(list(left) + list(right), speed)
        left = [max(JOINT_LIMITS[1 + i][0], min(JOINT_LIMITS[1 + i][1], float(v)))
                for i, v in enumerate(left)]
        right = [max(JOINT_LIMITS[8 + i][0], min(JOINT_LIMITS[8 + i][1], float(v)))
                 for i, v in enumerate(right)]
        mask = MASK_LEFT | MASK_RIGHT
        goal = ("{mode: 1, trajectories: {arm_mask: %d, "
                "leftarm_joints: [%s], leftarm_speed: %.4f, "
                "rightarm_joints: [%s], rightarm_speed: %.4f}}" % (
                    mask,
                    ", ".join("%.6f" % v for v in left), float(speed),
                    ", ".join("%.6f" % v for v in right), float(speed)))
        cmd = 'ros2 action send_goal %s %s "%s"' % (ACTION, ACTION_TYPE, goal)
        rc, out, err = self._execute_action_command(cmd, timeout)
        text = out + "\n" + err
        self._require_action_success(rc, text, "双臂运动")
        return True

    def move_joint(self, joint, target, speed=0.3, timeout=120.0):
        """把单个关节移动到 target（其余关节保持当前位姿）。"""
        arm = joint_arm(joint)
        idx = joint_index(joint)
        lo, hi = JOINT_LIMITS[int(joint)]
        target = max(lo, min(hi, float(target)))
        joints = self.joint_positions(arm)
        if len(joints) != ARM_JOINTS:
            raise ArmDriverError("读取 %s 臂关节角失败：%r" % (arm, joints))
        joints[idx] = target
        self.send_arm_goal(arm, joints, speed=speed, timeout=timeout)
        return target

    def move_joint_to_limit(self, joint, direction="pos", speed=0.3, timeout=180.0):
        lo, hi = JOINT_LIMITS[int(joint)]
        target = hi if str(direction).lower() in ("pos", "positive", "正", "正方向") else lo
        return self.move_joint(joint, target, speed=speed, timeout=timeout)

    def home_joint(self, joint, speed=0.3, timeout=120.0):
        return self.move_joint(joint, 0.0, speed=speed, timeout=timeout)

    def run_task(self, task_name, timeout=180.0):
        """跑一个 mc_task_manager 任务（如 jiyi1/dual_arm_line1）。"""
        client = ROS.get("action_client", "ros2 run mc_task_manager ros2_node_action_client %s")
        cmd = client % shlex.quote(str(task_name))
        rc, out, err = self._execute_action_command(cmd, timeout)
        text = out + "\n" + err
        return self._action_result_ok(rc, text), text

    def publish(self, topic, msg_type, payload, timeout=20.0):
        cmd = 'timeout 8 ros2 topic pub --once %s %s "%s"' % (topic, msg_type, payload)
        rc, out, err = self.mc(cmd, timeout=timeout)
        return rc == 0, out + err

    def peak_speed(self, joint, move_fn, sample_sec=8.0):
        """在 move_fn() 执行期间采样该关节速度，返回峰值 |rad/s|。

        paramiko 同一 transport 上开第二个 channel 跑 `ros2 topic echo`，互不阻塞。
        """
        arm = joint_arm(joint)
        idx = joint_index(joint)
        result = {"peak": 0.0, "error": None}

        def _sampler():
            try:
                client = self.connect()
                chan = client.get_transport().open_session(timeout=sample_sec)
                chan.settimeout(sample_sec)
                topic = TOPICS["joint_states"].format(arm=arm)
                chan.exec_command(self._mc_shell(
                    "timeout %d ros2 topic echo %s --field velocity" % (
                        int(sample_sec), topic)))
                deadline = time.time() + sample_sec
                buf = ""
                while time.time() < deadline:
                    if chan.recv_ready():
                        buf += chan.recv(65536).decode("utf-8", "replace")
                        # 逐行解析，避免每轮都重扫整个缓冲
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            m = re.search(r"array\('d',\s*\[([^\]]*)\]\)", line)
                            if not m:
                                continue
                            vals = [float(x) for x in m.group(1).split(",") if x.strip()]
                            if idx < len(vals):
                                result["peak"] = max(result["peak"], abs(vals[idx]))
                    else:
                        time.sleep(0.01)
                chan.close()
            except Exception as exc:  # noqa: BLE001
                result["error"] = str(exc)

        thread = threading.Thread(target=_sampler, daemon=True)
        thread.start()
        time.sleep(0.3)
        try:
            move_fn()
        finally:
            thread.join(timeout=sample_sec + 2)
        if result["error"] and result["peak"] <= 0:
            raise ArmDriverError("速度采样失败：%s" % result["error"])
        return result["peak"]

    # ── 运控程序启停（~/mc/install）────────────────────────────────────────
    def _motion_pidfile(self, name):
        log_dir = str(MOTION.get("log_dir", "/tmp")).rstrip("/")
        return "%s/arm_mc_%s.pid" % (log_dir, name)

    def _motion_logfile(self, name):
        log_dir = str(MOTION.get("log_dir", "/tmp")).rstrip("/")
        return "%s/arm_mc_%s.log" % (log_dir, name)

    def _proc_alive(self, pid):
        if not pid:
            return False
        try:
            rc, out, err = self.run(
                "kill -0 %d 2>/dev/null && echo ALIVE || echo DEAD" % int(pid), timeout=8)
            return "ALIVE" in (out + err)
        except Exception:
            return True

    def _wait_gone(self, pid, timeout):
        deadline = time.time() + float(timeout)
        while time.time() < deadline:
            if not self._proc_alive(pid):
                return True
            time.sleep(0.2)
        return False

    def _read_pid(self, name):
        with _motion_lock:
            pid = _motion_procs.get(name)
        if pid:
            return pid
        rc, out, err = self.run("cat %s 2>/dev/null" % shlex.quote(self._motion_pidfile(name)),
                                timeout=8)
        try:
            return int(out.strip().splitlines()[-1])
        except Exception:
            return None

    def _read_log(self, name, lines=120):
        rc, out, err = self.run(
            "tail -n %d %s 2>/dev/null" % (int(lines), shlex.quote(self._motion_logfile(name))),
            timeout=10)
        return out if out.strip() else err

    def _log_contains(self, name, keyword, timeout=10):
        """在远端完整日志里查关键字（不只看 tail，伺服层的 attach 行很早）。"""
        cmd = "grep -aqF %s %s" % (
            shlex.quote(str(keyword)), shlex.quote(self._motion_logfile(name)))
        rc, out, err = self.run(cmd, timeout=timeout)
        return rc == 0

    def _motion_env(self, command, cwd=None, env=None):
        """拼出「source ROS + source mc/install + 环境变量 + 命令」。"""
        parts = [
            "source %s >/dev/null" % _shell_path(
                MOTION.get("ros_setup", "/opt/ros/humble/setup.bash")),
            "source %s >/dev/null" % _shell_path(
                MOTION.get("setup", "~/mc/install/setup.bash")),
        ]
        if cwd:
            parts.append("cd %s" % _shell_path(cwd))
        shared_env = dict(MOTION.get("ros_env") or {})
        shared_env.update(env or {})
        for key, value in shared_env.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(key)):
                raise ArmDriverError("无效的运控环境变量名：%s" % key)
            parts.append("export %s=%s" % (key, shlex.quote(str(value))))
        parts.append("(\n%s\n)" % command)
        return " && ".join(parts)

    def _start_background(self, name, inner):
        log = self._motion_logfile(name)
        pidfile = self._motion_pidfile(name)
        self.run("rm -f %s %s" % (shlex.quote(pidfile), shlex.quote(log)), timeout=8)
        shell = ("setsid nohup bash -lc %s > %s 2>&1 < /dev/null & "
                 "echo $! > %s; cat %s" % (
                     shlex.quote(inner), shlex.quote(log),
                     shlex.quote(pidfile), shlex.quote(pidfile)))
        rc, out, err = self.run(shell, timeout=15)
        if rc != 0:
            raise ArmDriverError("启动 %s 失败：%s" % (name, _tail(out + err)))
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        try:
            pid = int(lines[-1])
        except Exception:
            raise ArmDriverError("启动 %s 后未获取到 PID：%s" % (name, _tail(out + err)))
        with _motion_lock:
            _motion_procs[name] = pid
        _motion_log("[运控] %s 已启动 pid=%d（日志 %s）" % (name, pid, log))
        return pid

    def _wait_ready(self, name, keyword, timeout, action_name=None):
        pid = self._read_pid(name)
        pattern = (MOTION.get("process_patterns") or {}).get(name)
        deadline = time.time() + float(timeout)
        log = ""
        last_servers = None
        while time.time() < deadline:
            log = self._read_log(name)
            # 伺服层日志会高速刷腰部数据，attach 行会很快被 tail 挤掉，
            # 所以关键字判断必须在完整日志里 grep，而不是只看 tail。
            if keyword in log or self._log_contains(name, keyword):
                if not action_name:
                    _motion_log("[运控] %s 已就绪" % name)
                    return log
                # 日志关键字出现后，再确认 action server 真正可用。
                # robot_app 的日志在重定向时可能滞后，单靠关键字会误判。
                servers = self.action_server_count(action_name)
                if servers != last_servers:
                    _motion_log("[运控] %s 日志已就绪，等待 %s action server（当前 %d）"
                                % (name, action_name, servers))
                    last_servers = servers
                if servers > 0:
                    _motion_log("[运控] %s 已就绪（%s action server=%d）"
                                % (name, action_name, servers))
                    return log
            if pid and not self._proc_alive(pid):
                # setsid 有可能 fork，PID 文件里的父进程先退出；再用进程名确认一次。
                alive = False
                if pattern:
                    rc, out, err = self.run(
                        "pgrep -f %s >/dev/null 2>&1 && echo YES || echo NO"
                        % shlex.quote(pattern), timeout=8)
                    alive = "YES" in (out + err)
                if not alive:
                    raise ArmDriverError("%s 进程已退出：\n%s" % (
                        name, _tail(log, 2000)))
            time.sleep(1.0)
        if action_name:
            raise ArmDriverError(
                "%s 启动超时（未出现 %r 或 %s action server 未就绪）：\n%s" % (
                    name, keyword, action_name, _tail(log, 2000)))
        raise ArmDriverError("%s 启动超时（未出现 %r）：\n%s" % (
            name, keyword, _tail(log, 2000)))

    def _topic_has_data(self, topic, timeout=None):
        """用 ros2 topic echo --once 判断话题是否真的在发布（不只看 topic list）。"""
        timeout = float(timeout or MOTION.get("joint_state_probe_timeout_s", 6.0))
        command = "timeout %d ros2 topic echo --once %s" % (
            max(1, int(timeout)), shlex.quote(str(topic)))
        try:
            rc, out, err = self.mc(command, timeout=timeout + 10.0, use_mc_run=False)
        except ArmOperationCancelled:
            raise
        except Exception:
            return False
        text = (out or "") + (err or "")
        return "position" in text

    def _wait_joint_states(self, timeout=None):
        """等伺服层真正发布关节状态，再启动 controller/robot_app。

        ~/mc/install 的伺服层打印 `Shared memory attached` 后，还要完成模式切换/
        使能才发布 /mc/*/joint_states（实测再等 20~30s）；而 robot_app 的组件等待
        只有 5s，提前启动会报 Not found components 后退出。
        """
        if not MOTION.get("joint_state_wait", True):
            return True
        topics = [str(t) for t in (MOTION.get("joint_state_topics") or []) if str(t).strip()]
        if not topics:
            return True
        deadline = time.time() + float(
            timeout or MOTION.get("joint_state_timeout_s", 90.0))
        poll = float(MOTION.get("joint_state_poll_s", 2.0))
        probe_cap = float(MOTION.get("joint_state_probe_timeout_s", 6.0))
        pending = list(topics)
        while time.time() < deadline:
            for topic in list(pending):
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                probe = max(1.0, min(probe_cap, remaining))
                if self._topic_has_data(topic, timeout=probe):
                    pending.remove(topic)
                    _motion_log("[运控] 关节状态已发布：%s" % topic)
            if not pending:
                return True
            if time.time() >= deadline:
                break
            time.sleep(poll)
        raise ArmDriverError("等待关节状态超时（未发布：%s）" % ", ".join(pending))

    def _wait_shared_mem(self, timeout=None):
        path = str(MOTION.get("shared_mem", "/dev/shm/pdo_shared_mem"))
        deadline = time.time() + float(
            timeout or MOTION.get("shared_mem_timeout_s", 30.0))
        while time.time() < deadline:
            rc, out, err = self.run(
                "test -e %s && echo YES || echo NO" % shlex.quote(path), timeout=8)
            if "YES" in (out + err):
                rc, out, err = self.sudo_run("chmod 666 %s" % shlex.quote(path), timeout=10)
                if rc != 0:
                    _motion_log("[运控] 警告：chmod 666 %s 失败：%s" % (
                        path, _tail(out + err)))
                return True
            time.sleep(1.0)
        raise ArmDriverError("共享内存 %s 未出现（主站未就绪）" % path)

    def _master_alive(self):
        rc, out, err = self.run(
            "pgrep -af '[z]ioneer_etherkit' 2>/dev/null || true", timeout=10)
        return bool(out.strip())

    def _wait_slaves_op(self, timeout=None, minimum=None):
        """等待手臂/腰部驱动进入 OP，再执行使能或启动伺服层。"""
        minimum = int(minimum or MOTION.get("slaves_op_min", 16))
        deadline = time.time() + float(
            timeout or MOTION.get("slaves_op_timeout_s", 60.0))
        last = ""
        while time.time() < deadline:
            try:
                lines = self.slaves()
            except Exception as exc:
                last = str(exc)
                time.sleep(1.0)
                continue
            op = 0
            for line in lines:
                parts = line.split(None, 4)
                if len(parts) >= 3 and parts[2].upper() == "OP":
                    op += 1
            last = "OP=%d/%d" % (op, len(lines))
            if op >= minimum:
                _motion_log("[主站] 从站就绪：%s" % last)
                return op
            time.sleep(1.0)
        raise ArmDriverError("等待从站进入 OP 超时（%s，需要至少 %d）" % (last, minimum))

    def _kill_master_process(self, timeout=10.0):
        """按进程名逐级停止 zioneer_etherkit，返回是否已全部退出。"""
        pattern = "[z]ioneer_etherkit"
        quoted = shlex.quote(pattern)
        for sig in ("INT", "TERM", "KILL"):
            self.sudo_run("pkill -%s -f %s 2>/dev/null || true" % (sig, quoted),
                          timeout=10)
            deadline = time.time() + max(1.0, float(timeout) / 3.0)
            while time.time() < deadline:
                rc, out, err = self.run(
                    "pgrep -f %s 2>/dev/null || true" % quoted, timeout=8)
                if not (out + err).strip():
                    return True
                time.sleep(0.3)
        return False

    def restart_master(self, timeout=None):
        """停止旧主站，再用接口表里配置的 XML 重新启动主站。

        用于现场状态字为 0x1240 等主站状态异常的场景：先关旧主站，
        再执行 `zioneer_etherkit -n <precheck_config>`，等共享内存恢复。
        """
        try:
            import etherkit_master
        except Exception as exc:
            raise ArmDriverError("无法重启主站（导入 etherkit_master 失败）：%s" % exc)

        xml = str(etherkit_master.MASTER.get("precheck_config", ""))
        _motion_log("[主站] 检测到需要重启，先停止旧主站")
        try:
            etherkit_master.stop_master()
        except Exception as exc:
            _motion_log("[主站] 关闭旧会话失败：%s" % exc)
        time.sleep(1.0)
        if self._master_alive() and not self._kill_master_process(timeout=10.0):
            raise ArmDriverError("旧主站进程无法停止，无法重启")
        # 旧主站退出后共享内存可能短暂残留，交给 _wait_shared_mem 处理。
        _motion_log("[主站] 重新启动主站：zioneer_etherkit -n %s" % xml)
        etherkit_master.start_master()
        self._wait_shared_mem(timeout=timeout or 40.0)
        _motion_log("[主站] 重启完成")
        return 1

    def _ensure_servo_config(self):
        """检查伺服层 components_config.yaml；缺失/损坏时用模板自动修复。"""
        path = str(MOTION.get("servo_config",
                              "/etc/servo/config/components_config.yaml"))
        template = _resolve_local(str(MOTION.get(
            "servo_config_template", "components_config.yaml")))
        rc, out, err = self.run("cat %s 2>/dev/null" % shlex.quote(path), timeout=10)
        content = out if rc == 0 else ""
        # 伺服层要求六个部件键全部显式写成 true/false，缺任一个都会拒绝启动。
        required = ("left_arm", "right_arm", "waist", "head", "lifter", "gripper")
        valid = False
        reason = "文件不存在或为空"
        if content.strip():
            try:
                data = yaml.safe_load(content)
                installed = (data or {}).get("components_installed")
                if not isinstance(installed, dict):
                    reason = "缺少 components_installed 段"
                else:
                    missing = [key for key in required if key not in installed]
                    if missing:
                        reason = "缺少键 %s" % ",".join(missing)
                    elif not all(isinstance(installed[key], bool) for key in required):
                        reason = "components_installed 的值必须为 true/false"
                    else:
                        valid = True
            except Exception as exc:
                reason = "YAML 解析失败: %s" % exc
        if valid:
            return True
        if not os.path.isfile(template):
            raise ArmDriverError(
                "伺服配置不可用：%s（%s；模板不存在：%s）" % (path, reason, template))
        _motion_log("[运控] %s 不可用（%s），使用模板 %s 修复" % (path, reason, template))
        remote_tmp = "/tmp/components_config.yaml"
        sftp = self.connect().open_sftp()
        try:
            sftp.put(template, remote_tmp)
        finally:
            sftp.close()
        # sudo 只作用于紧随其后的第一条命令，复合命令必须整体交给 sh -c，
        # 否则 mkdir 之后的 cp/chmod 仍以普通用户执行，报 Permission denied。
        install_cmd = "mkdir -p %s && cp %s %s && chmod 664 %s" % (
            shlex.quote(os.path.dirname(path)), shlex.quote(remote_tmp),
            shlex.quote(path), shlex.quote(path))
        rc, out, err = self.sudo_run("sh -c %s" % shlex.quote(install_cmd), timeout=20)
        if rc != 0:
            raise ArmDriverError("修复伺服配置失败：%s" % _tail(out + err))
        _motion_log("[运控] 伺服配置已修复：%s" % path)
        return True

    def motion_control_running(self):
        """三个运控进程（伺服层/控制器/robot_app）都在运行时才认为已启动。"""
        patterns = MOTION.get("process_patterns") or {}
        if not patterns:
            return False
        for name, pattern in patterns.items():
            rc, out, err = self.run(
                "pgrep -f %s >/dev/null 2>&1 && echo YES || echo NO"
                % shlex.quote(pattern), timeout=10)
            if "YES" not in (out + err):
                return False
        return True

    def _register_cleanup(self, callback):
        try:
            import test_api
            test_api.register_cleanup(callback)
            return True
        except Exception:
            return False

    def start_master_only(self, timeout=None):
        """只启动 EtherCAT 主站，不启动伺服/控制器/robot_app。"""
        try:
            import etherkit_master
            if etherkit_master.master_is_running() or self._master_alive():
                _motion_log("[主站] 已在运行，跳过启动")
            else:
                _motion_log("[主站] 启动 EtherCAT 主站 ...")
                etherkit_master.start_master()
        except Exception as exc:
            raise ArmDriverError("启动 EtherCAT 主站失败：%s" % exc)
        self._wait_shared_mem(timeout=timeout or 40.0)
        self._wait_slaves_op()
        return 1

    def stop_master_only(self, timeout=10.0):
        """只停止 EtherCAT 主站（含按进程名兜底）。"""
        try:
            import etherkit_master
            try:
                etherkit_master.stop_master()
            except Exception as exc:
                _motion_log("[主站] stop_master 失败：%s" % exc)
        except Exception:
            pass
        time.sleep(0.5)
        if self._master_alive():
            if not self._kill_master_process(timeout=timeout):
                raise ArmDriverError("主站进程无法停止")
        return 1

    def start_motion_control(self, start_master=True, timeout=None):
        """启动 ~/mc/install 运控栈：主站(可选) + 伺服层 + 控制器 + robot_app。

        已启动时直接跳过；注册测试结束清理回调，异常中止也会停止运控节点。
        """
        global _motion_active
        if self.motion_control_running():
            _motion_log("[运控] 已在运行，跳过重复启动")
            with _motion_lock:
                _motion_active = True
            return 1
        timeout = float(timeout or MOTION.get("start_timeout_s", 120.0))
        self._register_cleanup(self.stop_motion_control)

        # 清理上一次残留的运控节点，避免重复进程。
        try:
            self.stop_motion_control(stop_master=False, timeout=8.0, force=True)
        except Exception:
            pass

        if start_master:
            try:
                import etherkit_master
                if etherkit_master.master_is_running() or self._master_alive():
                    _motion_log("[运控] EtherCAT 主站已在运行，跳过启动")
                else:
                    _motion_log("[运控] 启动 EtherCAT 主站 ...")
                    etherkit_master.start_master()
            except Exception as exc:
                raise ArmDriverError("启动 EtherCAT 主站失败：%s" % exc)

        self._wait_shared_mem()
        self._wait_slaves_op()
        self._ensure_servo_config()

        servo = MOTION.get("servo_command",
                           "ros2 run servo2plan servo_motor_jiyi1_dualArm_Waist_node")
        servo_settle = float(MOTION.get("servo_settle_s", 5.0))
        for attempt in (1, 2):
            self._start_background("servo", self._motion_env(servo))
            time.sleep(servo_settle)
            pid = self._read_pid("servo")
            if not pid or self._proc_alive(pid):
                break
            log = self._read_log("servo")
            if attempt == 1 and ("bad file" in log or "components_config" in log):
                # 配置在启动瞬间不可读/损坏：修复后再试一次。
                _motion_log("[运控] 伺服层配置加载失败，修复配置后重试一次")
                self._ensure_servo_config()
                continue
            raise ArmDriverError("伺服层进程已退出：\n%s" % _tail(log, 2000))

        # 伺服层启动约 20~30 秒才 attach 共享内存；attach 后还要等关节状态
        # 真正发布（见 _wait_joint_states），否则 robot_app 会在 5 秒组件
        # 等待超时后退出（表现为 components not found）。
        self._wait_ready(
            "servo",
            str(MOTION.get("servo_ready_keyword", "Shared memory attached")),
            float(MOTION.get("servo_ready_timeout_s", 90.0)))
        # mc/install 在 attach 后还要 20~30s 才发布关节状态；等它真正发布
        # 再拉起 controller/robot_app，避免 robot_app 5s 组件等待超时。
        self._wait_joint_states()

        controller = MOTION.get("controller_command", "python3 hw_controller_manager.py")
        controller_dir = MOTION.get("controller_dir",
                                    "~/mc/install/servo2plan/lib/servo2plan")
        self._start_background("controller",
                               self._motion_env(controller, cwd=controller_dir))
        time.sleep(float(MOTION.get("controller_settle_s", 3.0)))

        app = MOTION.get("app_command", "ros2 run mc_task_manager robot_app")
        self._start_background("robot_app",
                               self._motion_env(app, env=MOTION.get("app_env") or {}))
        self._wait_ready("robot_app",
                         str(MOTION.get("ready_keyword",
                                        "Components in robot are ready")), timeout,
                         action_name=MOTION.get("action_name") or "/zioneer/mc/action")
        with _motion_lock:
            _motion_active = True
        _motion_log("[运控] 启动完成")
        return 1

    def _pid_matches(self, pid, pattern):
        """确认 PID 的进程名仍匹配预期，避免误杀被复用的 PID。"""
        if not pid or not pattern:
            return False
        rc, out, err = self.run(
            "pgrep -f %s 2>/dev/null || true" % shlex.quote(pattern), timeout=8)
        pids = set()
        for token in out.split():
            try:
                pids.add(int(token))
            except ValueError:
                pass
        return int(pid) in pids

    def _stop_background(self, name, timeout=5.0):
        pid = self._read_pid(name)
        pattern = (MOTION.get("process_patterns") or {}).get(name)
        if pid and pattern and not self._pid_matches(pid, pattern):
            pid = None
        if pid:
            self.run("kill -INT -- -%d 2>/dev/null || kill -INT %d 2>/dev/null || true"
                     % (pid, pid), timeout=8)
            if not self._wait_gone(pid, timeout):
                self.run("kill -TERM -- -%d 2>/dev/null || kill -TERM %d 2>/dev/null || true"
                         % (pid, pid), timeout=8)
                if not self._wait_gone(pid, 2.0):
                    self.run("kill -KILL -- -%d 2>/dev/null || kill -KILL %d 2>/dev/null || true"
                             % (pid, pid), timeout=8)
        if pattern:
            # 只对 pidfile 里的进程发过信号还不够：ros2 run 会再 fork 出真正的
            # 节点二进制，上一次运行残留的子进程可能忽略 SIGINT 继续占用共享内存。
            # 这里按进程名兜底，逐级升级 INT -> TERM -> KILL，确保干净退出。
            quoted = shlex.quote(pattern)
            self.run("pkill -INT -f %s 2>/dev/null || true" % quoted, timeout=8)
            deadline = time.time() + max(1.0, float(timeout) / 2.0)
            left = ""
            while time.time() < deadline:
                rc, out, err = self.run(
                    "pgrep -f %s 2>/dev/null || true" % quoted, timeout=8)
                left = (out + err).strip()
                if not left:
                    break
                time.sleep(0.3)
            if left:
                self.run("pkill -TERM -f %s 2>/dev/null || true" % quoted, timeout=8)
                time.sleep(1.0)
                rc, out, err = self.run(
                    "pgrep -f %s 2>/dev/null || true" % quoted, timeout=8)
                if (out + err).strip():
                    self.run("pkill -KILL -f %s 2>/dev/null || true" % quoted, timeout=8)
        with _motion_lock:
            _motion_procs.pop(name, None)
        self.run("rm -f %s" % shlex.quote(self._motion_pidfile(name)), timeout=8)

    def stop_motion_control(self, stop_master=False, timeout=None, force=False):
        """停止运控节点（默认保留 EtherCAT 主站，供后续 SDO 用例继续使用）。"""
        global _motion_active
        timeout = float(timeout or MOTION.get("stop_timeout_s", 15.0))
        with _motion_lock:
            active = _motion_active
        running = self.motion_control_running()
        if not force and not active and not running:
            with _motion_lock:
                _motion_active = False
            return 1
        service = MOTION.get("cancel_goal_service",
                             "/zioneer/mc/action/_action/cancel_goal")
        if active or running:
            try:
                self.mc("timeout 8 ros2 service call %s action_msgs/srv/CancelGoal '{}'"
                        % service, timeout=15, use_mc_run=False)
            except Exception:
                pass
        try:
            self.run("pkill -INT -f '[r]os2_node_action_clien[t]' 2>/dev/null || true",
                     timeout=8)
        except Exception:
            pass
        for name in ("robot_app", "controller", "servo"):
            try:
                self._stop_background(name, timeout=timeout / 3.0)
            except Exception as exc:
                _motion_log("[运控] 停止 %s 失败：%s" % (name, exc))
        if stop_master:
            try:
                import etherkit_master
                etherkit_master.stop_master()
            except Exception as exc:
                _motion_log("[运控] 停止主站失败：%s" % exc)
        with _motion_lock:
            _motion_active = False
        _motion_log("[运控] 已停止")
        return 1

    def set_motion_origin(self, positions=None):
        """记录运控原位（默认读取当前 14 轴位置），供动作结束后恢复。"""
        global _motion_origin
        if positions is None:
            positions = self.joint_positions_all()
        values = [float(v) for v in positions]
        if len(values) != TOTAL_JOINTS:
            raise ArmDriverError("原位需要 %d 个关节角，收到 %d" % (
                TOTAL_JOINTS, len(values)))
        with _motion_lock:
            _motion_origin = list(values)
        _motion_log("[运控] 已记录原位：%s" % _fmt_values(values))
        return list(values)

    def get_motion_origin(self):
        with _motion_lock:
            return list(_motion_origin) if _motion_origin else []

    def restore_motion_origin(self, speed=0.05, timeout=180.0):
        """双臂慢速回到记录的原位。"""
        origin = self.get_motion_origin()
        if len(origin) != TOTAL_JOINTS:
            raise ArmDriverError("未记录运控原位，无法恢复")
        left = origin[:ARM_JOINTS]
        right = origin[ARM_JOINTS:]
        _motion_log("[运控] 恢复原位：左 %s 右 %s" % (
            _fmt_values(left), _fmt_values(right)))
        self.send_dual_arm_goal(left, right, speed=float(speed), timeout=float(timeout))
        return list(origin)

    # ── CIT 配置（cit_items.xml）──────────────────────────────────────────
    def cit_items(self):
        """解析 scripts/arm/cit_items.xml，返回 {item_id: {...}}。"""
        path = _resolve_local(str(MOTION.get("cit_items", "cit_items.xml")))
        if not os.path.isfile(path):
            raise ArmDriverError("CIT 配置文件不存在：%s" % path)
        root = ET.parse(path).getroot()
        items = {}
        for item in root.iter("item"):
            item_id = item.get("id")
            if not item_id:
                continue
            params = {}
            for param in item.findall("param"):
                params[param.get("name")] = param.get("value")
            timeout = (item.findtext("timeout_ms") or "").strip()
            items[item_id] = {
                "id": item_id,
                "name": item.get("name", ""),
                "description": (item.findtext("description") or "").strip(),
                "command": params.get("command"),
                "params": params,
                "timeout_ms": int(timeout) if timeout.isdigit() else None,
            }
        return items

    def cit_item(self, item_id):
        items = self.cit_items()
        if item_id not in items:
            raise ArmDriverError("CIT 配置中未找到测试项：%s" % item_id)
        return items[item_id]

    def run_cit_item(self, item_id, timeout=None):
        """执行 cit_items.xml 中的 command；mc_run.sh 包装会自动解包到当前运控环境。"""
        item = self.cit_item(item_id)
        command = item.get("command")
        if not command:
            raise ArmDriverError("CIT 项 %s 未配置 command" % item_id)
        if timeout is None and item.get("timeout_ms"):
            timeout = item["timeout_ms"] / 1000.0 + 15.0
        if timeout is None:
            timeout = self.cmd_timeout
        _motion_log("[CIT] 执行 %s：%s" % (item_id, command))
        is_action = bool(re.search(
            r"\bros2\s+(?:action\s+send_goal|run\s+mc_task_manager\s+ros2_node_action_client)\b",
            command))
        wrapper = re.match(r"^\s*bash\s+[^\s]+/mc_run\.sh\s+", command)
        if wrapper:
            # 保留原命令参数的引号和后续 ; / &&，不能取 shlex.split 的最后一项。
            inner = "bash -c " + command[wrapper.end():]
            if is_action:
                rc, out, err = self._execute_action_command(inner, max(1, timeout - 10))
            else:
                inner = "timeout --signal=INT --kill-after=3s %ds bash -c %s" % (
                    max(1, int(timeout) - 5), shlex.quote(inner))
                rc, out, err = self.mc(inner, timeout=timeout, use_mc_run=False)
        elif is_action:
            rc, out, err = self._execute_action_command(command, max(1, timeout - 10))
        else:
            rc, out, err = self.run(command, timeout=timeout)
        text = out + "\n" + err
        if rc != 0:
            raise ArmDriverError("CIT 项 %s 执行失败(rc=%d)：%s" % (
                item_id, rc, _tail(text)))
        if is_action:
            self._require_action_success(rc, text, "CIT 项 %s" % item_id)
        return text

    # ── CIT HTTP（备用，默认不用）──────────────────────────────────────────
    def cit(self, cmd, item_id=None, timeout=60.0):
        body = {"cmd": cmd}
        if item_id:
            body["id"] = item_id
        payload = json.dumps(body).replace('"', '\\"')
        shell = ('curl -s -m %d -X POST http://127.0.0.1:8090/api/request '
                 '-H "Content-Type: application/json" -d "%s"') % (int(timeout), payload)
        rc, out, err = self.run(shell, timeout=timeout + 10)
        try:
            
            return json.loads(out)
        except Exception:
            raise ArmDriverError("CIT 接口返回无法解析：%s" % _tail(out + err))


def _tail(text, n=400):
    text = (text or "").strip()
    return text[-n:]


def _fmt_values(values, nd=4):
    return "[" + ", ".join(("%." + str(nd) + "f") % float(v) for v in values) + "]"


driver = ArmDriver()


def configure_zero_tool(template):
    """覆盖零位写入命令模板，例如 configure_zero_tool("cia402_tools -s {slave} -z")。"""
    driver.zero_tool = str(template)
    return driver


def configure(host=None, port=None, user=None, password=None, cmd_timeout=None):
    """覆盖默认连接参数（脚本里需要连别的机器时调用）。"""
    if host is not None:
        driver.host = host
    if port is not None:
        driver.port = int(port)
    if user is not None:
        driver.user = user
    if password is not None:
        driver.password = password
    if cmd_timeout is not None:
        driver.cmd_timeout = float(cmd_timeout)
    driver.close()
    return driver


def interface_summary():
    """返回当前生效的接口配置摘要（用于 --check-config 和日志）。"""
    motion_ref = INTERFACE.get("motion_interface") if isinstance(INTERFACE, dict) else None
    return {
        "file": INTERFACE.get("_path"),
        "motion_file": _resolve_motion_path(motion_ref) if motion_ref else None,
        "device": {"host": DEFAULT_HOST, "port": DEFAULT_PORT, "user": DEFAULT_USER, "mc_run": MC_RUN},
        "ros": {"action": ACTION, "action_type": ACTION_TYPE,
                "arm_mask": {"left": MASK_LEFT, "right": MASK_RIGHT},
                "topics": TOPICS, "power_board_type": ROS.get("power_board_type"),
                "teach_adkey": TEACH_ADKEY, "run_mode": RUN_MODE, "expect": EXPECT},
        "joints": {"per_arm": ARM_JOINTS, "total": TOTAL_JOINTS, "arms": _ARMS,
                   "limits": {k: list(v) for k, v in JOINT_LIMITS.items()}},
        "sdo": {k: v for k, v in SDO.items()},
        "tools": TOOLS,
        "motion": MOTION,
        "thresholds": THRESHOLDS,
    }


def print_interface():
    """打印当前生效的接口表（十六进制显示对象索引）。"""
    import copy
    import pprint
    data = copy.deepcopy(interface_summary())
    for spec in (data.get("sdo") or {}).values():
        if isinstance(spec, dict) and "index" in spec:
            spec["index"] = "0x%04X" % int(spec["index"])
    print("接口表：%s" % data.get("file"))
    pprint.pprint(data, width=100, sort_dicts=False)


if __name__ == "__main__":
    # 手工自检：python3 arm_driver.py [--check-config | --motion-start | --motion-stop | --cit <id> | host]
    import sys
    args = sys.argv[1:]
    if "--check-config" in args:
        print_interface()
        sys.exit(0)
    if "--motion-start" in args:
        print("start motion control:", driver.start_motion_control())
        sys.exit(0)
    if "--motion-stop" in args:
        print("stop motion control:", driver.stop_motion_control())
        sys.exit(0)
    if "--cit" in args:
        idx = args.index("--cit")
        item_id = args[idx + 1] if idx + 1 < len(args) else "mc.arm.small_angle"
        print(driver.run_cit_item(item_id))
        sys.exit(0)
    if args:
        configure(host=args[0])
    print("connect %s ..." % driver.host)
    driver.connect()
    print("slaves:", driver.slave_count())
    print("nodes :", len(driver.nodes()))
    st = driver.robot_status()
    print("run_mode:", st["run_mode"], "components:",
          {k: v.get("state") for k, v in st["components"].items()})
    for j in (1, 8):
        print("joint %d: sw=0x%04X err=0x%04X T=%.2fC V=%.2fV trq=%.1f%% part=%s sn=%s" % (
            j, driver.status_word(j), driver.error_code(j), driver.temperature_c(j),
            driver.bus_voltage_v(j), driver.torque_percent(j),
            driver.identity(j, "part"), driver.identity(j, "sn")))
