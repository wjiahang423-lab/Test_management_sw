"""机械臂人工点动/标零面板。

窗口在 GUI 主线程创建，SSH 连接、命令下发、停止、标零、角度读取全部走
ThreadPoolExecutor，GUI 线程只负责界面和信号更新，因此点动时不会卡死页面。
"""

import os
import select
import shlex
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import paramiko
from PyQt5.QtCore import (
    QCoreApplication,
    QMetaObject,
    QObject,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
    pyqtSlot,
)
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from arm_driver import INTERFACE
except Exception:
    INTERFACE = {}


_DEFAULT_JOG = {
    "host": "192.168.88.20",
    "port": 22,
    "user": "zioneer",
    "password": "123",
    "connect_timeout": 6.0,
    "sudo_password": "123",
    "enter_dir": "/opt/zioneer_etherkit",
    "cia402_tools": "/opt/zioneer_etherkit/bin/cia402_tools",
    "cia402_csp_tools": "/opt/zioneer_etherkit/bin/cia402_csp_tools",
    "enable_mode": 8,
    "joints": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14],
    "speed_options": [100, 200, 500],
    "default_speed": 200,
    "zero_settle_s": 3.0,
    "commands": {
        "enable": (
            "cd {enter_dir} && echo '{sudo_password}' | sudo -S "
            "{cia402_tools} -s {joint} -m {enable_mode}"
        ),
        "disable": (
            "cd {enter_dir} && echo '{sudo_password}' | sudo -S "
            "{cia402_tools} -s {joint} -d"
        ),
        "disable_all": (
            "cd {enter_dir} && echo '{sudo_password}' | sudo -S "
            "{cia402_tools} -s -1 -d"
        ),
        "jog": (
            "cd {enter_dir} && echo '{sudo_password}' | sudo -S "
            "{cia402_csp_tools} -s {joint} -v {speed}"
        ),
        "zero": (
            "cd {enter_dir} && echo '{sudo_password}' | sudo -S "
            "{cia402_tools} -s {joint} -z"
        ),
    },
}


def _load_jog_config():
    raw = INTERFACE.get("jog") if isinstance(INTERFACE, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    config = dict(_DEFAULT_JOG)
    config.update({k: v for k, v in raw.items() if k != "commands"})
    commands = dict(_DEFAULT_JOG["commands"])
    if isinstance(raw.get("commands"), dict):
        commands.update(raw["commands"])
    config["commands"] = commands
    return config


JOG = _load_jog_config()


class _SshSignals(QObject):
    output = pyqtSignal(str)
    disconnected = pyqtSignal(str)


class _JogSshSession:
    """一条交互式 SSH shell，命令只写不阻塞，输出由后台线程读取。"""

    def __init__(self, config):
        self.config = config
        self.signals = _SshSignals()
        self.client = None
        self.shell = None
        self._lock = threading.RLock()
        self._reader = None
        self._running = False

    @property
    def is_active(self):
        return bool(self._running and self.shell is not None and self.shell.active)

    def connect(self):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        timeout = float(self.config.get("connect_timeout", 6.0))
        client.connect(
            hostname=self.config["host"],
            port=int(self.config["port"]),
            username=self.config["user"],
            password=self.config["password"],
            timeout=timeout,
            banner_timeout=timeout,
            auth_timeout=timeout,
            look_for_keys=False,
            allow_agent=False,
        )
        shell = client.invoke_shell()
        shell.settimeout(0.05)
        self.client = client
        self.shell = shell
        self._running = True
        self._reader = threading.Thread(
            target=self._read_loop, name="jog-ssh-reader", daemon=True
        )
        self._reader.start()
        self.signals.output.emit(
            "[SSH] 已连接 {}@{}".format(self.config["user"], self.config["host"])
        )

    def _read_loop(self):
        while self._running and self.shell is not None:
            try:
                if not select.select([self.shell], [], [], 0.05)[0]:
                    continue
                data = self.shell.recv(65536)
                if not data:
                    break
                self.signals.output.emit(data.decode("utf-8", "replace"))
            except socket.timeout:
                continue
            except Exception as exc:
                if self._running:
                    self.signals.output.emit("[SSH] 读取异常: {}".format(exc))
                break
        self._running = False
        self.signals.disconnected.emit("SSH 连接已断开")

    def send(self, command):
        with self._lock:
            if not self.is_active:
                raise RuntimeError("SSH 未连接")
            self.shell.send((command + "\n").encode("utf-8"))

    def send_if(self, predicate, command):
        with self._lock:
            if not self.is_active:
                raise RuntimeError("SSH 未连接")
            if predicate is not None and not predicate():
                return False
            self.shell.send((command + "\n").encode("utf-8"))
            return True

    def stop_now(self):
        self.stop_now_if(None)

    def stop_now_if(self, predicate, announce=True):
        sent = False
        with self._lock:
            if not self.is_active:
                return False
            if predicate is not None and not predicate():
                return False
            try:
                self.shell.send(b"\x03")
                time.sleep(0.05)
                self.shell.send(b"\x03")
                time.sleep(0.05)
                self.shell.send(b"kill %1 2>/dev/null\n")
                # 点动工具是 root 进程，kill %1 只结束 sudo 包装；再按进程名清理，
                # 防止窗口关闭后 cia402_csp_tools 继续占着 EtherCAT 总线。
                password = str(self.config.get("sudo_password", ""))
                for pattern in ("[c]ia402_csp_tools", "[c]ia402_tools .* -z"):
                    command = "echo %s | sudo -S pkill -INT -f %s 2>/dev/null\n" % (
                        shlex.quote(password), shlex.quote(pattern))
                    self.shell.send(command.encode("utf-8"))
                time.sleep(0.1)
                # 点动工具若忽略 Ctrl+C/INT，强制结束，确保不占用总线。
                command = "echo %s | sudo -S pkill -KILL -f %s 2>/dev/null\n" % (
                    shlex.quote(password), shlex.quote("[c]ia402_csp_tools"))
                self.shell.send(command.encode("utf-8"))
                time.sleep(0.1)
                self.shell.send(b"kill %1 2>/dev/null\n")
                sent = True
            except Exception:
                pass
        if sent and announce:
            self.signals.output.emit("[操作] 已发送停止信号并清理后台点动进程")
        return sent

    def close(self):
        self._running = False
        reader = self._reader
        if reader is not None and reader.is_alive():
            reader.join(0.2)
        try:
            if self.shell is not None:
                self.shell.close()
        except Exception:
            pass
        try:
            if self.client is not None:
                self.client.close()
        except Exception:
            pass
        self.shell = None
        self.client = None


class JogController(QObject):
    """把 SSH 操作全部投递到线程池，GUI 线程只收信号。"""

    output = pyqtSignal(str)
    connected = pyqtSignal(bool, str)
    status = pyqtSignal(str)
    angle = pyqtSignal(float)
    angle_error = pyqtSignal(str)
    zero_done = pyqtSignal(bool, str)
    error = pyqtSignal(str)

    def __init__(self, config=None, parent=None):
        super().__init__(parent)
        self.config = config or JOG
        self._session = _JogSshSession(self.config)
        self._session.signals.output.connect(self.output.emit)
        self._session.signals.disconnected.connect(self._on_disconnected)
        self._cmd_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="jog-cmd")
        self._stop_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jog-stop")
        self._read_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jog-read")
        self._connected = False
        self._connecting = False
        self._shutdown = False
        self._read_angle_cb = None
        self._jog_lock = threading.RLock()
        self._jog_generation = 0
        self._jog_active = False

    def set_read_angle(self, callback):
        self._read_angle_cb = callback

    @property
    def is_connected(self):
        return self._connected

    @property
    def has_read_angle(self):
        return callable(self._read_angle_cb)

    def connect_async(self):
        if self._connected or self._connecting:
            if self._connected:
                self.connected.emit(True, "已连接")
            return
        self._connecting = True
        self.status.emit("连接中...")
        self._cmd_pool.submit(self._do_connect)

    def _do_connect(self):
        try:
            try:
                self._session.close()
            except Exception:
                pass
            self._session.connect()
            if self._shutdown:
                try:
                    self._session.close()
                except Exception:
                    pass
                return
            self._connected = True
            self.connected.emit(True, "已连接")
            self.status.emit("已连接")
        except Exception as exc:
            self._connected = False
            if not self._shutdown:
                self.connected.emit(False, str(exc))
                self.status.emit("连接失败: {}".format(exc))
        finally:
            self._connecting = False

    def _on_disconnected(self, message):
        self._connected = False
        if self._shutdown:
            return
        self.status.emit(message)
        self.connected.emit(False, message)

    def _command(self, name, **values):
        template = self.config["commands"].get(name)
        if not template:
            raise KeyError("缺少 jog.commands.{}".format(name))
        merged = {
            "enter_dir": self.config.get("enter_dir", ""),
            "cia402_tools": self.config.get("cia402_tools", ""),
            "cia402_csp_tools": self.config.get(
                "cia402_csp_tools", "/opt/zioneer_etherkit/bin/cia402_csp_tools"),
            "enable_mode": self.config.get("enable_mode", 8),
            "sudo_password": self.config.get("sudo_password", ""),
        }
        merged.update(values)
        return template.format(**merged)

    def _is_current_jog(self, generation):
        with self._jog_lock:
            return generation == self._jog_generation and self._jog_active

    def _is_current_stop(self, generation):
        with self._jog_lock:
            return generation == self._jog_generation and not self._jog_active

    def start_jog(self, joint, speed):
        if not self._connected:
            self.error.emit("SSH 未连接")
            return
        with self._jog_lock:
            self._jog_generation += 1
            generation = self._jog_generation
            self._jog_active = True
        self.status.emit("点动中...")
        self._cmd_pool.submit(self._do_start_jog, int(joint), int(speed), generation)

    def _do_start_jog(self, joint, speed, generation):
        try:
            if not self._is_current_jog(generation):
                return
            self._session.stop_now_if(
                lambda: self._is_current_jog(generation), announce=False
            )
            command = self._command("jog", joint=joint, speed=speed)
            if self._session.send_if(lambda: self._is_current_jog(generation), command):
                if not self._shutdown:
                    self.status.emit("点动中 J{} speed={}".format(joint, speed))
        except Exception as exc:
            if not self._shutdown:
                self.error.emit("点动失败: {}".format(exc))
                self.status.emit("点动失败")

    def stop_jog(self):
        with self._jog_lock:
            self._jog_generation += 1
            generation = self._jog_generation
            self._jog_active = False
        if not self._connected:
            return
        self.status.emit("停止中...")
        self._stop_pool.submit(self._do_stop, generation)

    def _do_stop(self, generation):
        try:
            if self._session.stop_now_if(lambda: self._is_current_stop(generation)):
                if not self._shutdown:
                    self.status.emit("已停止")
        except Exception as exc:
            if not self._shutdown:
                self.error.emit("停止失败: {}".format(exc))

    def enable_joint(self, joint):
        if not self._connected:
            self.error.emit("SSH 未连接")
            return
        self.status.emit("使能 J{}...".format(int(joint)))
        self._cmd_pool.submit(self._do_enable_joint, int(joint))

    def _do_enable_joint(self, joint):
        try:
            if self._shutdown:
                return
            self._session.send(self._command("enable", joint=joint))
            if not self._shutdown:
                self.status.emit("使能指令已发送 J{}".format(joint))
        except Exception as exc:
            if not self._shutdown:
                self.error.emit("使能失败: {}".format(exc))
                self.status.emit("使能失败")

    def disable_all(self):
        if not self._connected:
            self.error.emit("SSH 未连接")
            return
        self.status.emit("全部失能...")
        self._cmd_pool.submit(self._do_disable_all)

    def _do_disable_all(self):
        try:
            if self._shutdown:
                return
            self._session.send(self._command("disable_all"))
            if not self._shutdown:
                self.status.emit("全部失能指令已发送")
        except Exception as exc:
            if not self._shutdown:
                self.error.emit("全部失能失败: {}".format(exc))
                self.status.emit("全部失能失败")

    def zero_async(self, joint):
        if not self._connected:
            self.error.emit("SSH 未连接")
            return
        self.status.emit("标零 J{}...".format(int(joint)))
        self._cmd_pool.submit(self._do_zero, int(joint))

    def _do_zero(self, joint):
        try:
            if self._shutdown:
                return
            self._session.stop_now()
            self._session.send(self._command("zero", joint=joint))
            if self._shutdown:
                return
            self.status.emit("标零指令已发送 J{}".format(joint))
            self.zero_done.emit(True, "标零指令已发送")
        except Exception as exc:
            if not self._shutdown:
                self.zero_done.emit(False, str(exc))
                self.error.emit("标零失败: {}".format(exc))
                self.status.emit("标零失败")

    def read_angle_async(self):
        if self._shutdown:
            return
        self._read_pool.submit(self._do_read_angle)

    def _do_read_angle(self):
        if self._shutdown:
            return
        if self._read_angle_cb is None:
            self.angle_error.emit("未配置角度读取")
            return
        try:
            value = self._read_angle_cb()
            if not self._shutdown:
                self.angle.emit(float(value))
        except Exception as exc:
            if not self._shutdown:
                self.angle_error.emit(str(exc))

    def shutdown(self):
        if self._shutdown:
            return
        self._shutdown = True
        self._connected = False
        with self._jog_lock:
            self._jog_generation += 1
            self._jog_active = False
        # 先断开信号，避免后台线程在窗口/控制器销毁后继续 emit 导致闪退。
        for sig in (self._session.signals.output, self._session.signals.disconnected,
                    self.output, self.connected, self.status, self.angle,
                    self.angle_error, self.zero_done, self.error):
            try:
                sig.disconnect()
            except Exception:
                pass
        try:
            self._session.stop_now()
        except Exception:
            pass
        self._session.close()
        for pool in (self._cmd_pool, self._stop_pool, self._read_pool):
            try:
                pool.shutdown(wait=False)
            except Exception:
                pass


class JogPanelDialog(QDialog):
    """人工点动与标零窗口，必须在 GUI 主线程创建。"""

    def __init__(self, initial_joint=1, speed=None, title=None, read_angle=None,
                 lock_joint=False, auto_connect=True, show_zero=True, parent=None):
        super().__init__(parent)
        self._lock_joint = bool(lock_joint)
        self._show_zero = bool(show_zero)
        self._zeroed = False
        self._zeroing = False
        self._connected_ui = False
        self._moving = False
        self._last_angle = None
        self._cleaned = False
        self._controller = JogController(JOG, self)
        self._controller.set_read_angle(read_angle)
        self._controller.output.connect(self._append_log)
        self._controller.connected.connect(self._on_connected)
        self._controller.status.connect(self._set_status)
        self._controller.angle.connect(self._on_angle)
        self._controller.angle_error.connect(self._on_angle_error)
        self._controller.zero_done.connect(self._on_zero_done)
        self._controller.error.connect(self._on_error)
        self._build_ui(title or "机械臂零位对齐 - 点动/标零")
        self._select_joint(initial_joint)
        self._select_speed(speed)
        if auto_connect:
            self._controller.connect_async()
        else:
            self._set_status("未连接")

    def _build_ui(self, title):
        self.setWindowTitle(title)
        self.setMinimumSize(1000, 760)
        self.resize(1200, 900)
        self.setStyleSheet(
            "QPushButton { font-size: 15px; font-weight: bold; border-radius: 8px; "
            "padding: 8px; } QLabel { font-size: 14px; }"
        )

        self._joint_combo = QComboBox()
        self._joint_combo.setMinimumWidth(120)
        for joint in JOG.get("joints", list(range(1, 15))):
            self._joint_combo.addItem(
                "J{} ({})".format(int(joint), "左臂" if int(joint) <= 7 else "右臂"),
                int(joint),
            )
        self._joint_combo.setEnabled(not self._lock_joint)
        self._joint_combo.currentIndexChanged.connect(self._on_joint_changed)

        self._speed_combo = QComboBox()
        self._speed_combo.setMinimumWidth(100)
        for speed in JOG.get("speed_options", [100, 200, 500]):
            self._speed_combo.addItem(str(int(speed)), int(speed))
        self._speed_combo.currentIndexChanged.connect(self._on_speed_changed)

        self._status_label = QLabel("未连接")
        self._status_label.setStyleSheet("color: #7f8c8d; font-weight: bold;")

        self._btn_connect = QPushButton("重新连接")
        self._btn_connect.setAutoDefault(False)
        self._btn_connect.clicked.connect(self._controller.connect_async)

        top = QHBoxLayout()
        top.addWidget(QLabel("关节:"))
        top.addWidget(self._joint_combo)
        top.addWidget(QLabel("速度:"))
        top.addWidget(self._speed_combo)
        top.addSpacing(12)
        top.addWidget(self._status_label, 1)
        top.addWidget(self._btn_connect)

        self._btn_forward = QPushButton("▶ 正向")
        self._btn_backward = QPushButton("◀ 反向")
        self._btn_stop = QPushButton("⏹ 停止")
        self._btn_zero = QPushButton("标零")
        self._btn_enable = QPushButton("使能当前关节")
        self._btn_disable_all = QPushButton("全部失能")
        self._btn_read = QPushButton("读取角度")
        for button in (self._btn_forward, self._btn_backward, self._btn_stop,
                       self._btn_zero, self._btn_enable, self._btn_disable_all,
                       self._btn_read):
            button.setAutoDefault(False)

        self._btn_forward.setMinimumHeight(88)
        self._btn_backward.setMinimumHeight(88)
        self._btn_forward.setMinimumWidth(220)
        self._btn_backward.setMinimumWidth(220)
        self._btn_forward.setStyleSheet(
            "QPushButton { background: #27ae60; color: white; font-size: 18px; } "
            "QPushButton:disabled { background: #bdc3c7; }"
        )
        self._btn_backward.setStyleSheet(
            "QPushButton { background: #e67e22; color: white; font-size: 18px; } "
            "QPushButton:disabled { background: #bdc3c7; }"
        )
        self._btn_stop.setStyleSheet(
            "QPushButton { background: #e74c3c; color: white; } "
            "QPushButton:disabled { background: #bdc3c7; }"
        )
        self._btn_zero.setStyleSheet(
            "QPushButton { background: #9b59b6; color: white; } "
            "QPushButton:disabled { background: #bdc3c7; }"
        )

        self._btn_forward.pressed.connect(self._jog_forward)
        self._btn_forward.released.connect(self._stop_jog)
        self._btn_backward.pressed.connect(self._jog_backward)
        self._btn_backward.released.connect(self._stop_jog)
        self._btn_stop.clicked.connect(self._stop_jog)
        self._btn_zero.clicked.connect(self._confirm_zero)
        self._btn_enable.clicked.connect(self._enable_current)
        self._btn_disable_all.clicked.connect(self._confirm_disable_all)
        self._btn_read.clicked.connect(self._controller.read_angle_async)

        grid = QGridLayout()
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(18)
        grid.addWidget(self._btn_backward, 0, 0)
        grid.addWidget(self._btn_stop, 0, 1)
        grid.addWidget(self._btn_forward, 0, 2)
        grid.addWidget(self._btn_enable, 1, 0)
        grid.addWidget(self._btn_zero, 1, 1)
        grid.addWidget(self._btn_disable_all, 1, 2)
        if not self._show_zero:
            # 例如 15050 只做人工点动到零位，写零由下一步 15060 负责。
            self._btn_zero.hide()

        control_group = QGroupBox("点动控制（按住运动，松开停止）")
        control_group.setLayout(grid)

        self._angle_label = QLabel("当前角度: -- rad")
        self._angle_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        angle_row = QHBoxLayout()
        angle_row.addWidget(self._angle_label, 1)
        angle_row.addWidget(self._btn_read)
        if not self._controller.has_read_angle:
            # 例如 15050 只做人工点动，不接 ROS 角度读取。
            self._angle_label.hide()
            self._btn_read.hide()

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(3000)
        self._log.setMinimumHeight(240)
        self._log.setStyleSheet(
            "QPlainTextEdit { font-family: monospace; font-size: 12px; background: #0d1117; "
            "color: #c9d1d9; }"
        )

        self._btn_finish = QPushButton("完成")
        self._btn_cancel = QPushButton("取消")
        self._btn_finish.setAutoDefault(False)
        self._btn_cancel.setAutoDefault(False)
        self._btn_finish.setMinimumSize(180, 66)
        self._btn_cancel.setMinimumSize(180, 66)
        self._btn_finish.setStyleSheet(
            "QPushButton { background: #2980b9; color: white; font-size: 20px; }")
        self._btn_cancel.setStyleSheet(
            "QPushButton { background: #7f8c8d; color: white; font-size: 20px; }")
        self._btn_finish.clicked.connect(self._finish)
        self._btn_cancel.clicked.connect(self.reject)

        hint = QLabel(
            "完成前请确认关节已停止并已点动到零位。"
            if not self._show_zero else
            "完成前请确认关节已停止并已标零。")
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)

        # 取消/完成分置左右两端，中间留出大段空白，避免误触。
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 8, 0, 0)
        bottom.addWidget(self._btn_cancel, 0, Qt.AlignLeft | Qt.AlignVCenter)
        bottom.addStretch(1)
        bottom.addWidget(self._btn_finish, 0, Qt.AlignRight | Qt.AlignVCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        layout.addLayout(top)
        layout.addWidget(control_group)
        layout.addLayout(angle_row)
        layout.addWidget(self._log, 1)
        layout.addWidget(hint)
        layout.addLayout(bottom)

        self._set_controls_enabled(False)

    def _set_controls_enabled(self, enabled):
        self._connected_ui = bool(enabled)
        self._refresh_controls()

    def _refresh_controls(self):
        connected = self._connected_ui
        motion_buttons = (self._btn_forward, self._btn_backward, self._btn_zero,
                          self._btn_enable, self._btn_disable_all)
        for button in motion_buttons:
            button.setEnabled(connected and not self._zeroing)
        self._btn_stop.setEnabled(connected)
        self._btn_read.setEnabled(connected)

    def _end_zeroing(self):
        if self._cleaned:
            return
        self._zeroing = False
        self._refresh_controls()
        if self._controller.has_read_angle:
            self._controller.read_angle_async()

    def _current_joint(self):
        data = self._joint_combo.currentData()
        return int(data) if data is not None else int(self._joint_combo.currentText().split()[0][1:])

    def _current_speed(self):
        data = self._speed_combo.currentData()
        return int(data) if data is not None else int(self._speed_combo.currentText())

    def _select_joint(self, joint):
        target = int(joint)
        for index in range(self._joint_combo.count()):
            if int(self._joint_combo.itemData(index)) == target:
                self._joint_combo.setCurrentIndex(index)
                return
        if self._joint_combo.count():
            self._joint_combo.setCurrentIndex(0)

    def _select_speed(self, speed):
        if speed is None:
            speed = JOG.get("default_speed", 200)
        target = int(speed)
        for index in range(self._speed_combo.count()):
            if int(self._speed_combo.itemData(index)) == target:
                self._speed_combo.setCurrentIndex(index)
                return

    def _jog_forward(self):
        self._start_jog(1)

    def _jog_backward(self):
        self._start_jog(-1)

    def _start_jog(self, direction):
        if self._moving or self._zeroing:
            return
        self._moving = True
        joint = self._current_joint()
        speed = self._current_speed() * int(direction)
        self._set_status("{}点动中 J{}".format("正向" if direction > 0 else "反向", joint))
        self._controller.start_jog(joint, speed)

    def _stop_jog(self):
        if not self._moving:
            return
        self._moving = False
        self._set_status("停止中...")
        self._controller.stop_jog()

    def _on_joint_changed(self):
        if self._moving:
            self._stop_jog()
        self._last_angle = None
        self._angle_label.setText("当前角度: -- rad")
        self._set_status("J{} 已选择".format(self._current_joint()))

    def _on_speed_changed(self):
        if self._moving:
            self._stop_jog()
        self._set_status("速度 {}".format(self._current_speed()))

    def _enable_current(self):
        joint = self._current_joint()
        self._stop_jog()
        self._controller.stop_jog()
        if QMessageBox.question(
            self, "确认使能",
            "确定要使能 J{} 吗？请确认机械臂周围无人、无障碍。".format(joint),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes:
            self._controller.enable_joint(joint)

    def _confirm_disable_all(self):
        self._stop_jog()
        self._controller.stop_jog()
        if QMessageBox.question(
            self, "确认全部失能",
            "确定要全部失能吗？失能后关节会失去保持力矩。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes:
            self._controller.disable_all()

    def _confirm_zero(self):
        joint = self._current_joint()
        self._stop_jog()
        self._controller.stop_jog()
        if QMessageBox.question(
            self, "确认标零",
            "确定要对 J{} 执行标零吗？\n\n标零会修改零位偏置。".format(joint),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes:
            self._zeroing = True
            self._refresh_controls()
            self._controller.zero_async(joint)

    def _on_connected(self, ok, message):
        if self._cleaned:
            return
        self._set_controls_enabled(ok)
        self._set_status("已连接" if ok else "连接失败: {}".format(message))

    def _on_angle(self, value):
        if self._cleaned:
            return
        self._last_angle = float(value)
        color = "#27ae60" if abs(self._last_angle) <= 0.02 else "#e67e22"
        self._angle_label.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: {};".format(color)
        )
        self._angle_label.setText("当前角度: {:.5f} rad".format(self._last_angle))

    def _on_angle_error(self, message):
        if self._cleaned:
            return
        self._angle_label.setStyleSheet(
            "font-size: 16px; font-weight: bold; color: #e74c3c;"
        )
        self._angle_label.setText("读取角度失败: {}".format(message))

    def _on_zero_done(self, ok, message):
        if self._cleaned:
            return
        if ok:
            self._zeroed = True
            settle_ms = int(float(JOG.get("zero_settle_s", 3.0)) * 1000)
            self._set_status(
                "标零指令已发送 J{}，{}s 后可继续点动".format(
                    self._current_joint(), settle_ms // 1000
                )
            )
            QTimer.singleShot(settle_ms, self._end_zeroing)
        else:
            self._zeroing = False
            self._refresh_controls()
            self._set_status("标零失败: {}".format(message))

    def _on_error(self, message):
        if self._cleaned:
            return
        self._append_log("[错误] {}".format(message))
        self._set_status(message)

    def _set_status(self, text):
        if self._cleaned:
            return
        self._status_label.setText(text)
        color = "#27ae60"
        if any(word in text for word in ("失败", "异常", "断开", "错误")):
            color = "#e74c3c"
        elif any(word in text for word in ("中", "连接失败")):
            color = "#e67e22"
        self._status_label.setStyleSheet(
            "color: {}; font-weight: bold;".format(color)
        )

    def _append_log(self, text):
        if self._cleaned:
            return
        self._log.appendPlainText(str(text).rstrip())

    def _finish(self):
        self._stop_jog()
        self.accept()

    def get_result(self):
        return {
            "accepted": self.result() == QDialog.Accepted,
            "joint": self._current_joint(),
            "zeroed": self._zeroed,
            "angle": self._last_angle,
            "status": self._status_label.text(),
        }

    def _cleanup(self):
        if self._cleaned:
            return
        self._cleaned = True
        self._moving = False
        try:
            self._controller.shutdown()
        except Exception:
            pass

    def done(self, result):
        self._cleanup()
        super().done(result)

    def closeEvent(self, event):
        # 窗口管理器关闭（X）也要停掉后台点动并断开 SSH。
        self._cleanup()
        super().closeEvent(event)


# 保留 runner 引用，避免工作线程里 GC 时跨线程析构 QObject 导致闪退。
_PANEL_RUNNERS = []


class _PanelRunner(QObject):
    def __init__(self, kwargs):
        super().__init__()
        self.kwargs = kwargs
        self.result = {"accepted": False}

    @pyqtSlot()
    def run(self):
        dialog = JogPanelDialog(**self.kwargs)
        try:
            dialog.exec_()
        finally:
            # 异常/强制关闭时也确保 SSH 会话和后台点动进程被清理。
            try:
                dialog._cleanup()
            except Exception:
                pass
        self.result = dialog.get_result()


def show_jog_panel(initial_joint=1, speed=None, read_angle=None, lock_joint=False,
                   auto_connect=True, show_zero=True, title=None, parent=None):
    """从测试脚本弹出点动面板；工作线程调用时会自动切到 GUI 主线程。"""
    app = QApplication.instance()
    if app is None:
        if QCoreApplication.instance() is not None:
            raise RuntimeError("当前为无界面运行，无法显示点动面板")
        app = QApplication(sys.argv)
    main_thread = app.thread()
    kwargs = {
        "initial_joint": initial_joint,
        "speed": speed,
        "read_angle": read_angle,
        "lock_joint": lock_joint,
        "auto_connect": auto_connect,
        "show_zero": show_zero,
        "title": title,
        "parent": parent,
    }
    if QThread.currentThread() is main_thread:
        dialog = JogPanelDialog(**kwargs)
        try:
            dialog.exec_()
        finally:
            try:
                dialog._cleanup()
            except Exception:
                pass
        return dialog.get_result()
    runner = _PanelRunner(kwargs)
    runner.moveToThread(main_thread)
    _PANEL_RUNNERS.append(runner)
    QMetaObject.invokeMethod(runner, "run", Qt.BlockingQueuedConnection)
    return runner.result


if __name__ == "__main__":
    application = QApplication(sys.argv)
    panel = JogPanelDialog(initial_joint=1)
    panel.show()
    sys.exit(application.exec_())
