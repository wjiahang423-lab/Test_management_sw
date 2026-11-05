"""动力学辨识独立 Tab，复用主窗口任务队列，避免两个页面同时控制机械臂。"""
import csv
import json
from pathlib import Path
import re

from PyQt5 import QtCore, QtGui, QtWidgets

from .bundle import inspect_archive, pack
from .service import WorkbenchService


class DynamicsPanel(QtWidgets.QWidget):
    output = QtCore.pyqtSignal(str)
    stage = QtCore.pyqtSignal(str)

    def __init__(self, owner, helper_dir):
        super().__init__()
        self.owner = owner
        self.helper_dir = Path(helper_dir)
        self.service = WorkbenchService(owner_driver(), helper_dir, self.output.emit)
        self.last_result = None
        self._operation_epoch = owner._epoch
        self._busy = False
        self.output.connect(self._append, QtCore.Qt.QueuedConnection)
        self.stage.connect(self._stage_done, QtCore.Qt.QueuedConnection)
        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.device = QtWidgets.QLabel("连接参数沿用运控页：" + self.service.driver.user + "@" + self.service.driver.host)
        form.addRow(self.device)
        self.package = QtWidgets.QLineEdit()
        pick = QtWidgets.QPushButton("选择部署包")
        pick.clicked.connect(self._choose_package)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.package, 1)
        row.addWidget(pick)
        form.addRow("install 包", row)
        self.destination = QtWidgets.QLineEdit(str(Path.home() / "动力学标定程序" / "results"))
        form.addRow("本地结果目录", self.destination)
        self.serial = QtWidgets.QLineEdit("ARM_SN001")
        form.addRow("机械臂编号", self.serial)
        self.deploy_first = QtWidgets.QCheckBox("辨识前部署所选包（自动备份旧 install）")
        form.addRow(self.deploy_first)
        self.status = QtWidgets.QLabel("待操作：先检查包或备份设备，再选择左臂/右臂辨识")
        self.status.setWordWrap(True)
        form.addRow("当前步骤", self.status)
        layout.addLayout(form)
        actions = QtWidgets.QHBoxLayout()
        self.inspect_btn = QtWidgets.QPushButton("检查部署包")
        self.pack_btn = QtWidgets.QPushButton("从本地 install 制作包")
        self.backup_btn = QtWidgets.QPushButton("设备点位修正并下载包")
        self.deploy_btn = QtWidgets.QPushButton("一键部署")
        for b in (self.inspect_btn, self.pack_btn, self.backup_btn, self.deploy_btn):
            actions.addWidget(b)
        layout.addLayout(actions)
        controls = QtWidgets.QHBoxLayout()
        self.left_btn = QtWidgets.QPushButton("左臂：一键启动并辨识")
        self.right_btn = QtWidgets.QPushButton("右臂：一键启动并辨识")
        self.cancel_btn = QtWidgets.QPushButton("停止辨识 / 取消运动")
        for b in (self.left_btn, self.right_btn, self.cancel_btn):
            controls.addWidget(b)
        layout.addLayout(controls)
        analog_row = QtWidgets.QHBoxLayout()
        self.analog_left = QtWidgets.QPushButton("读取左臂力传感器（10秒）")
        self.analog_right = QtWidgets.QPushButton("读取右臂力传感器（10秒）")
        analog_row.addWidget(self.analog_left)
        analog_row.addWidget(self.analog_right)
        layout.addLayout(analog_row)
        apply_row = QtWidgets.QHBoxLayout()
        self.apply_btn = QtWidgets.QPushButton("发布本次参数（APPLY）")
        self.skip_btn = QtWidgets.QPushButton("跳过发布，保留结果")
        self.apply_btn.setEnabled(False)
        self.skip_btn.setEnabled(False)
        apply_row.addWidget(QtWidgets.QLabel("仅在原厂程序提示 Type APPLY 时启用："))
        apply_row.addWidget(self.apply_btn)
        apply_row.addWidget(self.skip_btn)
        layout.addLayout(apply_row)
        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.console = QtWidgets.QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(10000)
        split.addWidget(self.console)
        result_box = QtWidgets.QWidget()
        results = QtWidgets.QVBoxLayout(result_box)
        self.files = QtWidgets.QComboBox()
        self.summary = QtWidgets.QLabel("本次结果尚未生成；辨识完成后显示文件和 CSV 数据")
        self.summary.setWordWrap(True)
        self.table = QtWidgets.QTableWidget()
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        results.addWidget(self.summary)
        results.addWidget(self.files)
        results.addWidget(self.table)
        open_btn = QtWidgets.QPushButton("打开本地结果目录")
        open_btn.clicked.connect(self._open_results)
        results.addWidget(open_btn)
        split.addWidget(result_box)
        layout.addWidget(split, 1)
        self.inspect_btn.clicked.connect(self._inspect)
        self.pack_btn.clicked.connect(self._pack)
        self.backup_btn.clicked.connect(self._backup)
        self.deploy_btn.clicked.connect(self._deploy)
        self.left_btn.clicked.connect(lambda: self._identify("left"))
        self.right_btn.clicked.connect(lambda: self._identify("right"))
        self.analog_left.clicked.connect(lambda: self._analog("left"))
        self.analog_right.clicked.connect(lambda: self._analog("right"))
        self.cancel_btn.clicked.connect(self.owner._cancel_motion)
        self.apply_btn.clicked.connect(lambda: self._reply(True))
        self.skip_btn.clicked.connect(lambda: self._reply(False))
        self.files.currentIndexChanged.connect(self._show_file)
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._update_apply)
        self._timer.start(200)

    def _append(self, text):
        self.console.moveCursor(QtGui.QTextCursor.End)
        self.console.insertPlainText(text)
        self.console.moveCursor(QtGui.QTextCursor.End)

    def _stage_done(self, stage):
        if self._operation_epoch != self.owner._epoch:
            return
        self.status.setText(stage)
        if stage == "部署完成":
            self.owner.master_started = self.owner.servo_enabled = self.owner.motion_started = False
        elif stage == "主站已就绪":
            self.owner.master_started = True
        elif stage == "伺服已就绪":
            self.owner.servo_enabled = True
        elif stage == "运控已就绪":
            self.owner.motion_started = True
        self.owner._refresh_ui_state()

    def set_busy(self, busy, closing=False):
        self._busy = busy
        for widget in (self.package, self.destination, self.serial, self.deploy_first,
                       self.inspect_btn, self.pack_btn, self.backup_btn, self.deploy_btn,
                       self.left_btn, self.right_btn, self.analog_left, self.analog_right):
            widget.setEnabled(not busy)
        self.cancel_btn.setEnabled(not closing and self.owner._control_task is None)

    def _choose_package(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择 install 包", "", "部署包 (*.tar.gz *.tgz *.tar.xz)")
        if path:
            self.package.setText(path)

    def _done(self, result):
        self._append(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        self.status.setText("操作完成；请查看下方输出")
        if result.get("local") and str(result["local"]).endswith((".tar.gz", ".tgz", ".tar.xz")):
            self.package.setText(result["local"])

    def _inspect(self):
        path = self.package.text().strip()
        self.owner._run_task("检查动力学部署包", inspect_archive, path, on_done=self._done)

    def _pack(self):
        install = QtWidgets.QFileDialog.getExistingDirectory(self, "选择本地 install 目录")
        if not install:
            return
        vendor, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择原厂 run_arm_dynamics_identification.py（可取消）", "", "Python (*.py)")
        output, _ = QtWidgets.QFileDialog.getSaveFileName(self, "保存动力学标定程序包", str(Path.home() / "动力学标定程序.tar.gz"), "归档 (*.tar.gz)")
        if output:
            self.owner._run_task("制作动力学部署包", pack, install, output,
                "local:" + install, vendor or None,
                str(self.helper_dir / "analog_output_snap.py"), True, on_done=self._done)

    def _backup(self):
        destination = self.destination.text().strip()
        self.owner._run_task("设备 Home 修正与备份", self.service.backup, destination, True, on_done=self._done)

    def _deploy(self):
        path = self.package.text().strip()
        self._operation_epoch = self.owner._epoch

        def work():
            result = self.service.deploy(path)
            self.stage.emit("部署完成")
            return result

        self.owner._run_task("部署动力学程序", work, on_done=self._done)

    def _identify(self, arm):
        serial = self.serial.text().strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", serial):
            QtWidgets.QMessageBox.warning(self, "编号无效", "编号只允许字母、数字、下划线和短横线，最多 80 字符")
            return
        archive, deploy = self.package.text().strip(), self.deploy_first.isChecked()
        destination = self.destination.text().strip()
        self._operation_epoch = self.owner._epoch

        def work():
            import arm_test
            import arm_enable
            d = self.service.driver
            if deploy:
                info = inspect_archive(archive)
                if not info["dynamics_ready"]:
                    raise RuntimeError("包中缺少原厂动力学辨识文件：" + ", ".join(info["missing_dynamics"]))
                self.stage.emit("正在部署…")
                self.service.deploy(archive)
                self.stage.emit("部署完成")
            helper = self.service.prepare_helpers()
            import shlex
            rc, out, err = d.mc("python3 %s %s %s --check" % (
                shlex.quote(helper + "/identification_runner.py"), arm, shlex.quote(serial)), timeout=30)
            if rc:
                raise RuntimeError("辨识文件预检失败，未启动主站或运动：" + out + err)
            self.stage.emit("正在启动主站…")
            d.start_master_only()
            self.stage.emit("主站已就绪")
            if not d.motion_control_running():
                self.stage.emit("正在伺服使能…")
                arm_enable.stop_enable_all()
                arm_test.enable_all_joints()
                arm_test.wait_enable_ready(expected="0x1237", timeout=60)
                self.stage.emit("伺服已就绪")
                self.service._check_cancel()
                self.stage.emit("正在启动运控…")
                d.start_motion_control(start_master=False)
            else:
                # 不在正在运行的伺服层旁启动第二个 cia402_tools 写入会话。
                arm_test.wait_enable_ready(expected="0x1237", timeout=60)
                self.stage.emit("伺服已就绪")
            self.stage.emit("运控已就绪")
            self.service._check_cancel()
            self.stage.emit("正在执行%s臂辨识…" % ("左" if arm == "left" else "右"))
            return self.service.run_identification(arm, serial, destination)

        self.owner._run_task("动力学辨识", work, on_done=self._identified)

    def _analog(self, arm):
        self.owner._run_task("读取力传感器", self.service.capture_analog,
            arm, self.destination.text().strip(), on_done=self._identified)

    def _identified(self, result):
        self.last_result = result
        self._done(result)
        self.files.clear()
        local = Path(result["local"])
        for name in result.get("files", []):
            self.files.addItem(name, str(local / name))
        self.summary.setText("%s臂 %s：%s；%d 个文件已下载到 %s" % (
            "左" if result["arm"] == "left" else "右", result["serial"],
            result["status"], len(result.get("files", [])), local))
        self._show_file(0)

    def _show_file(self, index):
        path = self.files.itemData(index)
        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        if not path or Path(path).suffix.lower() != ".csv":
            return
        try:
            with open(path, newline="", encoding="utf-8-sig") as stream:
                rows = []
                for row in csv.reader(stream):
                    rows.append(row[:64])
                    if len(rows) >= 501:
                        break
            if not rows:
                return
            columns = max(map(len, rows))
            self.table.setColumnCount(columns)
            self.table.setHorizontalHeaderLabels(rows[0] + [""] * (columns - len(rows[0])))
            self.table.setRowCount(len(rows) - 1)
            for r, row in enumerate(rows[1:]):
                for c, value in enumerate(row):
                    self.table.setItem(r, c, QtWidgets.QTableWidgetItem(value))
            self.table.resizeColumnsToContents()
            self.table.setToolTip("预览最多 500 行、64 列；完整数据在本地结果目录")
        except Exception as exc:
            self._append("读取 CSV 失败：%s\n" % exc)

    def _open_results(self):
        path = self.last_result["local"] if self.last_result else self.destination.text()
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(Path(path).absolute())))

    def _update_apply(self):
        waiting = self.service.awaiting_apply and not self.owner._closing
        self.apply_btn.setEnabled(waiting)
        self.skip_btn.setEnabled(waiting)
        if waiting:
            self.status.setText("等待操作员检查原厂验收数据：发布参数或跳过发布")

    def _reply(self, apply):
        try:
            self.service.reply_apply(apply)
            self._append("已发送 APPLY\n" if apply else "已跳过发布\n")
        except Exception as exc:
            self._append(str(exc) + "\n")
        self._update_apply()


def owner_driver():
    from arm_driver import driver
    return driver
