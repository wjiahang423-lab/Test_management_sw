#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机械臂运控控制台（PyQt5）。

本地运行，通过 SSH 连接 arm_interface.yaml 里配置的机器人：
- 启动/停止运控栈（默认 ~/mc/install，接口见 motion_interface.yaml）；
- 读取 14 轴位置；
- 复位-回零位、正方向小角度校验、恢复小角度前原位；
- 发送双臂关节目标；
- 执行 CIT 项或任意 ROS 命令。
"""

import os
import sys
import faulthandler
import logging
import math
import json
import threading
import traceback
import importlib.util
from datetime import datetime
from logging.handlers import RotatingFileHandler

def _bundle_root():
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS",
                       os.path.dirname(os.path.abspath(sys.executable)))
    here = os.path.dirname(os.path.abspath(__file__))
    return here if os.path.isdir(os.path.join(here, "scripts", "arm")) else os.path.dirname(here)


ROOT = _bundle_root()
TOOLS_DIR = os.path.join(ROOT, "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)
ARM_DIR = os.path.join(ROOT, "scripts", "arm")
if not os.path.isdir(ARM_DIR):
    _alt = os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                        "scripts", "arm")
    if os.path.isdir(_alt):
        ARM_DIR = _alt
if ARM_DIR not in sys.path:
    sys.path.insert(0, ARM_DIR)

from PyQt5 import QtCore, QtGui, QtWidgets

import arm_driver
import arm_test

pyqtSignal = QtCore.pyqtSignal
Qt = QtCore.Qt
LOG_DIR = os.path.expanduser("~/.local/state/arm_control_gui")
LOGGER = logging.getLogger("arm_control_gui")


def _setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    if not LOGGER.handlers:
        handler = RotatingFileHandler(os.path.join(LOG_DIR, "console.log"),
                                      maxBytes=4 * 1024 * 1024, backupCount=3,
                                      encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        LOGGER.addHandler(handler)
        LOGGER.setLevel(logging.INFO)
        LOGGER.propagate = False


class SafeApplication(QtWidgets.QApplication):
    def notify(self, receiver, event):
        try:
            return super().notify(receiver, event)
        except Exception:
            sys.excepthook(*sys.exc_info())
            return False


class _Emitter(QtCore.QObject):
    text = pyqtSignal(str)


class _Stream(object):
    def __init__(self, emitter, fallback):
        self.emitter = emitter
        self.fallback = fallback

    def write(self, text):
        if text:
            try:
                self.emitter.text.emit(str(text))
            except RuntimeError:
                if self.fallback:
                    self.fallback.write(str(text))

    def flush(self):
        pass


class TaskThread(QtCore.QThread):
    def __init__(self, fn, *args, parent=None, **kwargs):
        super(TaskThread, self).__init__(parent)
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.result = None
        self.error = None
        self.completed = threading.Event()

    def run(self):
        try:
            self.result = self.fn(*self.args, **self.kwargs)
        except BaseException:
            self.error = traceback.format_exc()
        finally:
            self.completed.set()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super(MainWindow, self).__init__()
        self.setWindowTitle("机械臂运控控制台")
        self.resize(1100, 760)
        self.task = None
        self._tasks = []
        self.master_started = False
        self.servo_enabled = False
        self.motion_started = False
        self._busy = False
        self._control_task = None
        self._epoch = 0
        self._closing = False
        self._allow_close = False
        self._shutdown_started = False
        self._remote_touched = False
        _setup_logging()

        self.emitter = _Emitter(self)
        self.emitter.text.connect(self._append_raw, Qt.QueuedConnection)

        self._build_ui()
        self._old_stdout = sys.stdout
        self._old_stderr = sys.stderr
        self._stdout_stream = _Stream(self.emitter, self._old_stdout)
        self._stderr_stream = _Stream(self.emitter, self._old_stderr)
        sys.stdout = self._stdout_stream
        sys.stderr = self._stderr_stream
        self._old_excepthook = sys.excepthook
        self._exception_hook = self._report_exception
        sys.excepthook = self._exception_hook
        arm_driver.driver.output_callback = self.emitter.text.emit

        self._load_cit_items()
        self._load_interface_info()
        self._refresh_ui_state()
        self.log("控制台已就绪；机器人 %s" % self._host)
        self.log("运行日志：%s" % os.path.join(LOG_DIR, "console.log"))

    def _report_exception(self, exc_type, value, tb):
        self.emitter.text.emit("\n!!! 界面异常：" + "".join(
            traceback.format_exception(exc_type, value, tb)))

    def _build_ui(self):
        shell = QtWidgets.QWidget()
        self.setCentralWidget(shell)
        shell_layout = QtWidgets.QVBoxLayout(shell)
        self.tabs = QtWidgets.QTabWidget()
        shell_layout.addWidget(self.tabs)
        arm_page = QtWidgets.QWidget()
        self.tabs.addTab(arm_page, "机械臂运控")
        root = QtWidgets.QVBoxLayout(arm_page)

        top = QtWidgets.QHBoxLayout()
        root.addLayout(top)

        left_host = QtWidgets.QWidget()
        left = QtWidgets.QVBoxLayout(left_host)
        left.setContentsMargins(0, 0, 0, 0)
        left_scroll = QtWidgets.QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setWidget(left_host)
        top.addWidget(left_scroll, 3)

        box_motion = QtWidgets.QGroupBox("运控")
        grid = QtWidgets.QGridLayout(box_motion)
        self.btn_start_master = QtWidgets.QPushButton("启动主站")
        self.btn_enable_servo = QtWidgets.QPushButton("伺服使能")
        self.btn_enable_servo.setToolTip("主站运行且无任务执行时，可重复下发使能命令并确认就绪状态")
        self.btn_start = QtWidgets.QPushButton("启动运控")
        self.btn_stop = QtWidgets.QPushButton("停止运控")
        self.btn_stop_master = QtWidgets.QPushButton("停止主站")
        self.btn_cancel = QtWidgets.QPushButton("取消当前动作")
        self.btn_read = QtWidgets.QPushButton("读取位置")
        grid.addWidget(self.btn_start_master, 0, 0)
        grid.addWidget(self.btn_enable_servo, 0, 1)
        grid.addWidget(self.btn_start, 0, 2)
        grid.addWidget(self.btn_stop, 1, 0)
        grid.addWidget(self.btn_stop_master, 1, 1)
        grid.addWidget(self.btn_cancel, 1, 2)
        grid.addWidget(self.btn_read, 2, 0)
        self.chk_stop_master = QtWidgets.QCheckBox("停止运控时同时停主站")
        grid.addWidget(self.chk_stop_master, 2, 1)
        self.chk_stop_on_exit = QtWidgets.QCheckBox("退出时停止运控")
        self.chk_stop_on_exit.setChecked(True)
        grid.addWidget(self.chk_stop_on_exit, 2, 2)
        left.addWidget(box_motion)

        box_state = QtWidgets.QGroupBox("状态")
        form_state = QtWidgets.QFormLayout(box_state)
        self.lbl_master_state = QtWidgets.QLabel("未启动")
        self.lbl_enable_state = QtWidgets.QLabel("未使能")
        self.lbl_motion_state = QtWidgets.QLabel("未启动")
        form_state.addRow("主站", self.lbl_master_state)
        form_state.addRow("伺服使能", self.lbl_enable_state)
        form_state.addRow("运控软件", self.lbl_motion_state)
        left.addWidget(box_state)

        box_cmd = QtWidgets.QGroupBox("CIT / ROS 指令")
        v = QtWidgets.QVBoxLayout(box_cmd)
        row = QtWidgets.QHBoxLayout()
        self.cmb_cit = QtWidgets.QComboBox()
        self.cmb_cit.setMinimumWidth(260)
        self.btn_cit = QtWidgets.QPushButton("执行 CIT 项")
        row.addWidget(self.cmb_cit, 1)
        row.addWidget(self.btn_cit)
        v.addLayout(row)
        row_cmd = QtWidgets.QHBoxLayout()
        self.ed_cmd = QtWidgets.QPlainTextEdit()
        self.ed_cmd.setPlaceholderText(
            "需先启动运控。示例：\n"
            "ros2 run mc_task_manager ros2_node_action_client jiyi1/greetings\n"
            "ros2 topic echo /mc/robot_status --once")
        self.ed_cmd.setMaximumHeight(76)
        row_cmd.addWidget(self.ed_cmd, 1)
        cmd_right = QtWidgets.QVBoxLayout()
        self.btn_cmd = QtWidgets.QPushButton("发送指令")
        self.btn_cmd.setMinimumHeight(38)
        self.spin_timeout = QtWidgets.QDoubleSpinBox()
        self.spin_timeout.setRange(1, 600)
        self.spin_timeout.setValue(120)
        self.spin_timeout.setSuffix(" s")
        row_time = QtWidgets.QHBoxLayout()
        row_time.addWidget(QtWidgets.QLabel("超时"))
        row_time.addWidget(self.spin_timeout)
        cmd_right.addWidget(self.btn_cmd)
        cmd_right.addLayout(row_time)
        cmd_right.addStretch(1)
        row_cmd.addLayout(cmd_right)
        v.addLayout(row_cmd)
        left.addWidget(box_cmd)

        box_action = QtWidgets.QGroupBox("动作")
        h = QtWidgets.QHBoxLayout(box_action)
        self.btn_home = QtWidgets.QPushButton("复位-回零位")
        self.btn_small = QtWidgets.QPushButton("正方向小角度")
        self.btn_restore = QtWidgets.QPushButton("恢复小角度前原位")
        self.btn_greetings = QtWidgets.QPushButton("问候动作(右手)")
        h.addWidget(self.btn_home)
        h.addWidget(self.btn_small)
        h.addWidget(self.btn_restore)
        h.addWidget(self.btn_greetings)
        left.addWidget(box_action)

        task_row = QtWidgets.QHBoxLayout()
        self.ed_task_name = QtWidgets.QLineEdit("jiyi1/greetings")
        self.ed_task_name.setPlaceholderText("运控任务名，例如 jiyi1/dual_arm_line1")
        self.btn_task = QtWidgets.QPushButton("执行指定任务")
        task_row.addWidget(self.ed_task_name, 1)
        task_row.addWidget(self.btn_task)
        left.addLayout(task_row)

        box_pos = QtWidgets.QGroupBox("当前位置（rad）")
        form = QtWidgets.QFormLayout(box_pos)
        self.ed_left_pos = QtWidgets.QLineEdit()
        self.ed_right_pos = QtWidgets.QLineEdit()
        self.ed_left_pos.setReadOnly(True)
        self.ed_right_pos.setReadOnly(True)
        form.addRow("左臂 J1..J7", self.ed_left_pos)
        form.addRow("右臂 J1..J7", self.ed_right_pos)
        left.addWidget(box_pos)

        left.addStretch(1)

        right = QtWidgets.QVBoxLayout()
        top.addLayout(right, 2)

        box_target = QtWidgets.QGroupBox("双臂关节目标（rad）")
        tv = QtWidgets.QVBoxLayout(box_target)
        self.left_edits = self._joint_row(tv, "左臂")
        self.right_edits = self._joint_row(tv, "右臂")
        row3 = QtWidgets.QHBoxLayout()
        self.spin_speed = QtWidgets.QDoubleSpinBox()
        self.spin_speed.setRange(0.01, 2.0)
        self.spin_speed.setSingleStep(0.01)
        self.spin_speed.setValue(0.05)
        self.btn_fill = QtWidgets.QPushButton("用当前位置填充")
        self.btn_send = QtWidgets.QPushButton("发送双臂目标")
        row3.addWidget(QtWidgets.QLabel("速度"))
        row3.addWidget(self.spin_speed)
        row3.addWidget(self.btn_fill)
        row3.addStretch(1)
        row3.addWidget(self.btn_send)
        tv.addLayout(row3)
        right.addWidget(box_target)

        box_info = QtWidgets.QGroupBox("接口")
        form2 = QtWidgets.QFormLayout(box_info)
        self.lbl_host = QtWidgets.QLabel("-")
        self.lbl_setup = QtWidgets.QLabel("-")
        self.lbl_action = QtWidgets.QLabel("-")
        form2.addRow("机器人", self.lbl_host)
        form2.addRow("运控环境", self.lbl_setup)
        form2.addRow("Action", self.lbl_action)
        right.addWidget(box_info)
        right.addStretch(1)

        box_log = QtWidgets.QGroupBox("日志")
        lv = QtWidgets.QVBoxLayout(box_log)
        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(8000)
        lv.addWidget(self.log_view)
        root.addWidget(box_log, 1)

        # Put the existing capture panel beside the controller in one window.
        capture_file = os.path.join(ROOT, "tools", "handeye_capture_gui.py")
        if os.path.isfile(capture_file):
            # OpenCV can override Qt's platform plugin path during import.
            if "cv2/qt/plugins" in os.environ.get("QT_QPA_PLATFORM_PLUGIN_PATH", ""):
                os.environ.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)
            spec = importlib.util.spec_from_file_location("handeye_capture_gui", capture_file)
            capture = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(capture)
            capture_args = capture.build_parser().parse_args([])

            def make_capture_session(arm):
                return capture.RemoteSession(
                    capture_args.host, "zioneer", "123", "xema_" + arm, arm,
                    capture_args.remote_data,
                    capture_args.remote_data.rstrip("/") + "/" + arm + "/capture",
                    capture_args.pose_topic, os.path.abspath(capture_args.local_dir))

            self.capture_panel = capture.MainWindow(make_capture_session, "left", autoconnect=False)
            self.tabs.addTab(self.capture_panel, "手眼采集")

        self.cmb_instructions = QtWidgets.QComboBox()
        self.cmb_instructions.setMinimumWidth(300)
        self.ed_instruction = QtWidgets.QPlainTextEdit()
        self.ed_instruction.setMaximumHeight(110)
        self.ed_instruction.setPlaceholderText("从接口表选择功能点；执行前请检查命令和机器人状态")
        self.btn_instruction = QtWidgets.QPushButton("启动执行")
        self.instruction_hint = QtWidgets.QLabel("")
        instruction_path = os.path.join(ROOT, "tools", "arm_instruction_set.json")
        try:
            with open(instruction_path, "r", encoding="utf-8") as fp:
                self._instructions = json.load(fp)
        except Exception as exc:
            self._instructions = []
            self.log("读取指令集失败：%s" % exc)
        box_instruction = QtWidgets.QGroupBox("接口表功能点指令集")
        instruction_layout = QtWidgets.QVBoxLayout(box_instruction)
        instruction_row = QtWidgets.QHBoxLayout()
        instruction_row.addWidget(self.cmb_instructions, 1)
        instruction_row.addWidget(self.btn_instruction)
        instruction_layout.addLayout(instruction_row)
        instruction_layout.addWidget(self.instruction_hint)
        instruction_layout.addWidget(self.ed_instruction)
        # The command selector lives on the controller tab beside existing controls.
        root.insertWidget(max(0, root.count() - 2), box_instruction)
        self._populate_instructions()
        self.cmb_instructions.currentIndexChanged.connect(self._select_instruction)
        self.btn_instruction.clicked.connect(self._run_instruction)


        self.btn_start_master.clicked.connect(self._start_master)
        self.btn_enable_servo.clicked.connect(self._enable_servo)
        self.btn_start.clicked.connect(self._start_motion)
        self.btn_stop.clicked.connect(self._stop_motion)
        self.btn_stop_master.clicked.connect(self._stop_master)
        self.btn_cancel.clicked.connect(self._cancel_motion)
        self.btn_read.clicked.connect(self._read_positions)
        self.btn_home.clicked.connect(self._home)
        self.btn_small.clicked.connect(self._small_angle)
        self.btn_restore.clicked.connect(self._restore)
        self.btn_greetings.clicked.connect(self._greetings)
        self.btn_task.clicked.connect(self._run_named_task)
        self.btn_cit.clicked.connect(self._run_cit)
        self.btn_cmd.clicked.connect(self._run_command)
        self.btn_fill.clicked.connect(self._fill_target_from_positions)
        self.btn_send.clicked.connect(self._send_target)

        from robot_workbench.panel import DynamicsPanel
        self.dynamics_panel = DynamicsPanel(self, os.path.join(ROOT, "tools", "robot_workbench"))
        self.tabs.addTab(self.dynamics_panel, "动力学辨识与部署")

    def _joint_row(self, layout, title):
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel(title))
        edits = []
        for i in range(7):
            ed = QtWidgets.QLineEdit("0.0")
            ed.setMaximumWidth(72)
            row.addWidget(QtWidgets.QLabel("J%d" % (i + 1)))
            row.addWidget(ed)
            edits.append(ed)
        layout.addLayout(row)
        return edits

    def _load_interface_info(self):
        dev = arm_driver.DEVICE if isinstance(arm_driver.DEVICE, dict) else {}
        motion = arm_driver.MOTION if isinstance(arm_driver.MOTION, dict) else {}
        self._host = "%s@%s:%s" % (
            dev.get("user", "zioneer"), dev.get("host", "192.168.88.20"),
            dev.get("port", 22))
        self.lbl_host.setText(self._host)
        self.lbl_setup.setText(str(motion.get("setup", "~/mc/install/setup.bash")))
        self.lbl_action.setText(str(motion.get("action_name", "/zioneer/mc/action")))

    def _load_cit_items(self):
        try:
            items = arm_driver.driver.cit_items()
        except Exception as exc:
            self.log("读取 CIT 配置失败：%s" % exc)
            return
        for item_id, item in sorted(items.items()):
            self.cmb_cit.addItem("%s  %s" % (item_id, item.get("name", "")), item_id)

    def _populate_instructions(self):
        for index, item in enumerate(self._instructions):
            self.cmb_instructions.addItem(item["name"], index)
            if not item.get("enabled"):
                model = self.cmb_instructions.model()
                model.item(self.cmb_instructions.count() - 1).setEnabled(False)
        if self._instructions:
            self._select_instruction(0)

    def _select_instruction(self, index):
        if not 0 <= index < len(self._instructions):
            self.ed_instruction.clear()
            self.instruction_hint.clear()
            return
        item = self._instructions[index]
        self.ed_instruction.setPlainText(item["command"])
        self.instruction_hint.setText(
            "可执行指令，执行前请确认安全状态。" if item.get("enabled")
            else "文档标记为 TBD 或没有可直接执行的 ROS 命令。")
        self.btn_instruction.setEnabled(bool(item.get("enabled")) and not (
            self._busy or self._control_task is not None or self._closing))

    def _run_instruction(self):
        command = self.ed_instruction.toPlainText().strip()
        if not command:
            return
        self.ed_cmd.setPlainText(command)
        self._run_command()

    @QtCore.pyqtSlot(str)
    def _append_raw(self, text):
        LOGGER.info(str(text).rstrip())
        self.log_view.moveCursor(QtGui.QTextCursor.End)
        self.log_view.insertPlainText(str(text))
        self.log_view.moveCursor(QtGui.QTextCursor.End)

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        self._append_raw("[%s] %s\n" % (ts, str(message).rstrip()))

    def _refresh_ui_state(self):
        busy = self._busy or self._control_task is not None or self._closing
        if hasattr(self, "dynamics_panel"):
            self.dynamics_panel.set_busy(busy, self._closing)
        if hasattr(self, "btn_instruction"):
            index = self.cmb_instructions.currentIndex()
            valid = 0 <= index < len(self._instructions)
            self.btn_instruction.setEnabled(not busy and valid and self._instructions[index].get("enabled", False))
        self.btn_start_master.setEnabled(not busy and not self.master_started)
        self.btn_enable_servo.setEnabled(
            not busy and self.master_started)
        self.btn_start.setEnabled(
            not busy and self.master_started and self.servo_enabled
            and not self.motion_started)
        control_available = self._control_task is None and not self._closing
        self.btn_stop.setEnabled(control_available and (
            self.motion_started or self.master_started or self._busy))
        self.btn_stop_master.setEnabled(control_available and self.master_started)
        self.btn_read.setEnabled(not busy and self.motion_started)
        self.btn_home.setEnabled(not busy and self.motion_started)
        self.btn_small.setEnabled(not busy and self.motion_started)
        self.btn_restore.setEnabled(not busy and self.motion_started)
        self.btn_greetings.setEnabled(not busy and self.motion_started)
        self.btn_task.setEnabled(not busy and self.motion_started)
        self.btn_cit.setEnabled(not busy and self.cmb_cit.count() > 0)
        self.btn_cmd.setEnabled(not busy)
        self.btn_fill.setEnabled(not busy and self.motion_started)
        self.btn_send.setEnabled(not busy and self.motion_started)
        self.btn_cancel.setEnabled(control_available and (self._busy or self.motion_started))
        self.lbl_master_state.setText("运行中" if self.master_started else "未启动")
        self.lbl_enable_state.setText("已使能" if self.servo_enabled else "未使能")
        self.lbl_motion_state.setText("运行中" if self.motion_started else "未启动")

    def _track_task(self, label, fn, args=(), kwargs=None, kind="primary", on_done=None):
        task = TaskThread(fn, *args, parent=self, **(kwargs or {}))
        task.label = label
        task.kind = kind
        task.on_done = on_done
        task.epoch = self._epoch
        self._tasks.append(task)
        if kind == "primary":
            self.task = task
            self._busy = True
        elif kind == "control":
            self._control_task = task
        task.finished.connect(self._finish_task, Qt.QueuedConnection)
        self._refresh_ui_state()
        task.start()
        return task

    @QtCore.pyqtSlot()
    def _finish_task(self):
        # 只在 QThread.finished 之后释放引用，所有 UI 更新都在主线程中执行。
        task = self.sender()
        if task not in self._tasks:
            return
        self._tasks.remove(task)
        if self.task is task:
            self.task = None
            self._busy = False
        if self._control_task is task:
            self._control_task = None
        try:
            if task.kind == "shutdown":
                if task.error:
                    self.log("!!! 退出清理失败：%s" % task.error)
                    self._closing = False
                    self._shutdown_started = False
                    arm_driver.driver.reset_cancel()
                else:
                    self._allow_close = True
                    QtCore.QTimer.singleShot(0, self.close)
            elif task.kind == "primary" and task.epoch != self._epoch:
                self.log("<<< %s 已中断" % task.label)
            elif task.error:
                self._task_failed(task.label, task.error)
            elif task.kind == "primary":
                self._task_done(task.label, task.result)
                if task.on_done and not self._closing:
                    task.on_done(task.result)
            else:
                self.log("<<< %s 完成：%s" % (task.label, task.result))
                if task.on_done:
                    task.on_done(task.result)
        finally:
            task.deleteLater()
            self._refresh_ui_state()
            if self._closing and not self._allow_close:
                self._advance_shutdown()

    def _run_task(self, label, fn, *args, on_done=None, **kwargs):
        if self.task is not None or self._control_task is not None or self._closing:
            self.log("当前还有任务在执行，请稍候。")
            return
        self.log(">>> %s" % label)
        self._remote_touched = True
        arm_driver.driver.reset_cancel()
        return self._track_task(label, fn, args, kwargs, on_done=on_done)

    def _task_done(self, label, result):
        if label == "启动主站":
            self.master_started = True
            self.servo_enabled = False
            self.motion_started = False
        elif label == "伺服使能":
            self.servo_enabled = True
        elif label == "启动运控":
            self.motion_started = True
        elif label == "停止主站":
            self.master_started = False
            self.servo_enabled = False
            self.motion_started = False
        if result is not None:
            self.log("<<< %s 完成：%s" % (label, result))
        else:
            self.log("<<< %s 完成" % label)
        if label == "读取位置":
            self._show_positions(result)
        elif not self._closing and label in (
                "发送双臂目标", "复位-回零位", "正方向小角度", "恢复小角度前原位"):
            self._read_positions()

    def _task_failed(self, label, err):
        if hasattr(self, "dynamics_panel") and label in (
                "动力学辨识", "读取力传感器", "部署动力学程序", "设备 Home 修正与备份"):
            self.dynamics_panel.status.setText(label + "失败：详见日志")
            self.dynamics_panel.output.emit("!!! %s失败：\n%s\n" % (label, err))
        if label == "启动主站":
            self.master_started = False
        elif label == "伺服使能":
            self.servo_enabled = False
        elif label == "启动运控":
            self.motion_started = False
        self.log("!!! %s 失败：%s" % (label, err))

    def _run_control(self, label, fn, on_done=None):
        if self._control_task is not None:
            self.log("停止/取消任务正在执行，请稍候。")
            return
        self._epoch += 1
        arm_driver.driver.request_cancel()
        self.log(">>> %s" % label)
        primary = self.task

        def control_work():
            # 先让启动/使能任务退出，防止停止之后旧任务又启动节点。
            if primary:
                if primary.label in ("伺服使能", "动力学辨识"):
                    import arm_enable
                    arm_enable.stop_enable_all()
                primary.completed.wait()
            return fn()

        return self._track_task(label, control_work, kind="control", on_done=on_done)

    def _start_master(self):
        self._run_task("启动主站", arm_driver.driver.start_master_only)

    def _enable_servo(self):
        def fn():
            import arm_enable
            arm_enable.stop_enable_all()
            n = arm_test.enable_all_joints()
            ready = arm_test.wait_enable_ready(expected="0x1237", timeout=60)
            return "使能命令 %s 轴，0x1237 就绪 %s 轴" % (n, ready)

        self._run_task("伺服使能", fn)

    def _start_motion(self):
        if not (self.master_started and self.servo_enabled):
            QtWidgets.QMessageBox.warning(
                self, "未就绪", "请先启动主站并完成伺服使能，再启动运控。")
            return
        self._run_task("启动运控", arm_driver.driver.start_motion_control)

    def _new_driver(self):
        current = arm_driver.driver
        return arm_driver.ArmDriver(
            host=current.host, port=current.port, user=current.user,
            password=current.password, connect_timeout=current.connect_timeout,
            cmd_timeout=current.cmd_timeout)

    def _stop_motion(self):
        stop_master = self.chk_stop_master.isChecked()

        def fn():
            d = self._new_driver()
            try:
                return d.stop_motion_control(stop_master=stop_master, force=True)
            finally:
                d.close()

        def on_done(result):
            self.motion_started = False
            if stop_master:
                self.master_started = False
                self.servo_enabled = False
            self._refresh_ui_state()
            self.log("<<< 停止运控完成：%s" % result)

        self._run_control("停止运控", fn, on_done)

    def _stop_master(self):
        def fn():
            d = self._new_driver()
            try:
                return d.stop_master_only()
            finally:
                d.close()

        def on_done(result):
            self.master_started = False
            self.servo_enabled = False
            self.motion_started = False
            self._refresh_ui_state()
            self.log("<<< 停止主站完成：%s" % result)

        self._run_control("停止主站", fn, on_done)

    def _cancel_motion(self):
        def fn():
            if hasattr(self, "dynamics_panel") and self.dynamics_panel.service._pidfile:
                self.dynamics_panel.service.cancel()
                return "辨识取消请求已发送"
            d = self._new_driver()
            try:
                return d.cancel_motion()
            finally:
                d.close()

        self._run_control("取消当前动作", fn)

    def _read_positions(self):
        self._run_task("读取位置", arm_driver.driver.joint_positions_all)

    def _show_positions(self, values):
        if not values:
            return
        left = values[:7]
        right = values[7:14]
        self.ed_left_pos.setText(", ".join("%.5f" % v for v in left))
        self.ed_right_pos.setText(", ".join("%.5f" % v for v in right))

    def _fill_target_from_positions(self):
        self._run_task("填充当前位置", arm_driver.driver.joint_positions_all,
                       on_done=self._fill_positions)

    def _fill_positions(self, values):
        if len(values) != 14:
            self.log("!!! 读取位置数量异常：%r" % (values,))
            return
        for ed, val in zip(self.left_edits, values[:7]):
            ed.setText("%.6f" % val)
        for ed, val in zip(self.right_edits, values[7:14]):
            ed.setText("%.6f" % val)
        self._show_positions(values)
        self.log("已用当前位置填充目标")

    def _parse_targets(self):
        left = [float(ed.text()) for ed in self.left_edits]
        right = [float(ed.text()) for ed in self.right_edits]
        if not all(math.isfinite(v) for v in left + right):
            raise ValueError("关节角必须是有限数值")
        for joint, value in enumerate(left + right, 1):
            limits = arm_driver.JOINT_LIMITS.get(joint)
            if limits and not limits[0] <= value <= limits[1]:
                raise ValueError("J%d 目标 %.4f 超出范围 %.4f～%.4f rad" % (
                    joint, value, limits[0], limits[1]))
        return left, right

    def _send_target(self):
        try:
            left, right = self._parse_targets()
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "参数错误", str(exc))
            return
        speed = float(self.spin_speed.value())
        self._run_action("发送双臂目标",
                       arm_driver.driver.send_dual_arm_goal,
                       left, right, speed, 180.0)

    def _home(self):
        self._run_action("复位-回零位", arm_test.measure_motion_home, 180.0)

    def _small_angle(self):
        self._run_action("正方向小角度", arm_test.measure_positive_direction_small_angle, 120.0)

    def _restore(self):
        self._run_action("恢复小角度前原位", arm_test.measure_motion_restore, 0.05, 180.0)

    def _greetings(self):
        self._run_named_task(task_name="jiyi1/greetings")

    def _run_named_task(self, checked=False, task_name=None):
        task_name = task_name or self.ed_task_name.text().strip()
        if not task_name:
            self.log("请输入运控任务名。")
            return
        timeout = float(self.spin_timeout.value())

        def fn():
            ok, text = arm_driver.driver.run_task(task_name, timeout=timeout)
            if not ok:
                raise arm_driver.ArmDriverError(
                    "任务 %s 未成功：\n%s" % (task_name, (text or "").strip()[-3000:]))
            return (text or "").strip()[-2000:]

        self._run_action("任务 %s" % task_name, fn)

    def _run_action(self, label, fn, *args):
        if not self.motion_started:
            self.log("请先启动运控，再执行动作。")
            return

        def work():
            action = arm_driver.MOTION.get("action_name", arm_driver.ACTION)
            count = arm_driver.driver.action_server_count(action)
            if count != 1:
                raise arm_driver.ArmDriverError(
                    "动作服务 %s 未就绪或存在重复服务端（server=%d），请检查运控状态。"
                    % (action, count))
            try:
                return fn(*args)
            except arm_driver.ArmOperationCancelled:
                raise
            except Exception as exc:
                if arm_driver.driver._cancel_event.is_set():
                    raise arm_driver.ArmOperationCancelled("当前操作已取消") from exc
                # 故障门控/驱动报警通常只在伺服日志里，补到界面错误信息中。
                d = self._new_driver()
                details = []
                try:
                    for name in ("robot_app", "servo"):
                        text = d._read_log(name, lines=100)
                        faults = [line for line in text.splitlines()
                                  if any(k in line.lower() for k in (
                                      "fault", "abort", "error", "reject", "failed"))]
                        if faults:
                            details.append("%s：\n%s" % (name, "\n".join(faults[-8:])))
                except Exception as diagnostic_error:
                    details.append("读取诊断日志失败：%s" % diagnostic_error)
                finally:
                    d.close()
                raise arm_driver.ArmDriverError("%s\n%s" % (
                    exc, "\n".join(details))) from exc

        self._run_task(label, work)

    def _run_cit(self):
        item_id = self.cmb_cit.currentData()
        if not item_id:
            return
        item = arm_driver.driver.cit_item(item_id)
        timeout = max(float(self.spin_timeout.value()),
                      float(item.get("timeout_ms") or 0) / 1000.0)

        def fn():
            text = arm_driver.driver.run_cit_item(item_id, timeout=timeout + 15.0)
            return (text or "").strip()[-800:]

        command = item.get("command") or ""
        is_action = "ros2 action send_goal" in command or "ros2_node_action_client" in command
        runner = self._run_action if is_action else self._run_task
        runner("CIT %s" % item_id, fn)

    def _run_command(self):
        command = self.ed_cmd.toPlainText().strip()
        if not command:
            return
        timeout = float(self.spin_timeout.value())

        def fn():
            is_action = "ros2 action send_goal" in command or "ros2_node_action_client" in command
            if is_action:
                rc, out, err = arm_driver.driver._execute_action_command(command, timeout)
            else:
                rc, out, err = arm_driver.driver.mc(command, timeout=timeout)
            if rc != 0:
                raise arm_driver.ArmDriverError("指令失败 rc=%d\n%s\n%s" % (rc, out, err))
            if is_action:
                arm_driver.driver._require_action_success(rc, out + "\n" + err, "指令动作")
            return "rc=%d\n%s\n%s" % (rc, out.strip(), err.strip())

        is_action = "ros2 action send_goal" in command or "ros2_node_action_client" in command
        runner = self._run_action if is_action else self._run_task
        runner("执行指令", fn)

    def _shutdown(self, stop_motion, stop_master):
        # 此函数始终在后台线程运行，退出时也保持界面响应。
        if "arm_enable" in sys.modules:
            import arm_enable
            arm_enable.stop_enable_all()
        d = self._new_driver()
        try:
            if stop_motion:
                d.stop_motion_control(stop_master=stop_master, force=True)
        finally:
            d.close()
            arm_driver.driver.close()
        return 1

    def _advance_shutdown(self):
        if self._tasks or self._shutdown_started:
            return
        if hasattr(self, "capture_panel") and not self.capture_panel._allow_close:
            self.capture_panel.close()
            QtCore.QTimer.singleShot(100, self._advance_shutdown)
            return
        self._shutdown_started = True
        self._track_task(
            "退出清理", self._shutdown,
            (self.chk_stop_on_exit.isChecked() and self._remote_touched,
             self.chk_stop_master.isChecked()), kind="shutdown")

    def closeEvent(self, event):
        if self._allow_close and not self._tasks:
            arm_driver.driver.output_callback = None
            if sys.stdout is self._stdout_stream:
                sys.stdout = self._old_stdout
            if sys.stderr is self._stderr_stream:
                sys.stderr = self._old_stderr
            if sys.excepthook is self._exception_hook:
                sys.excepthook = self._old_excepthook
            event.accept()
            return
        event.ignore()
        if self._closing:
            return
        self._closing = True
        self.log("正在退出：等待后台任务结束并清理连接…")
        self.statusBar().showMessage("正在退出，等待后台任务结束…")
        if self.task is not None:
            self._cancel_motion()
        self._refresh_ui_state()
        self._advance_shutdown()


def main():
    _setup_logging()
    crash_log = open(os.path.join(LOG_DIR, "crash.log"), "a", encoding="utf-8")
    faulthandler.enable(crash_log, all_threads=True)
    app = SafeApplication(sys.argv)
    win = MainWindow()
    win.show()
    if "--check-gui" in sys.argv:
        # 离线启动自检，既不建立 SSH 也不启动硬件。
        win.chk_stop_on_exit.setChecked(False)
        names = [win.tabs.tabText(i) for i in range(win.tabs.count())]
        sys.__stdout__.write(json.dumps({"tabs": names, "commands": len(win._instructions)}, ensure_ascii=False) + "\n")
        sys.__stdout__.flush()
        QtCore.QTimer.singleShot(0, win.close)
    result = app.exec_()
    # 在 QApplication 仍有效时销毁控件和定时器，避免解释器退出时的 Qt 清理竞态。
    win.deleteLater()
    app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    app.processEvents()
    del win
    faulthandler.disable()
    crash_log.close()
    return result


if __name__ == "__main__":
    sys.exit(main())
