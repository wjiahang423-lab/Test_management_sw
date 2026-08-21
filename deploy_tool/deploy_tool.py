#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""deploy_tool.py — 远程部署上位机(PyQt5)

功能:
    1. 选择要部署的软件文件夹(也可直接选择 .tar.gz 安装包)
    2. 输入远程主机 IP/用户名/密码(点击“+”可添加多台设备)
    3. 一键部署到所有远程 Linux/Ubuntu 主机
    4. 可选“开机启动”(systemd 服务开机自启)
"""
import os
import sys
import threading

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout,
    QLabel, QLineEdit, QPushButton, QFileDialog, QCheckBox, QPlainTextEdit,
    QListWidget, QListWidgetItem, QMessageBox, QSpinBox
)

from remote_deployer import DeployCoordinator, DeployError, RemoteHost


# ---------------- 后台线程 ----------------
class DeployWorker(QThread):
    """在子线程中执行部署。用 QThread 子类 + run(), 避免 moveToThread/slot 的
    跨线程信号丢失与“线程已结束仍被销毁”问题。"""
    log = pyqtSignal(str)
    finished = pyqtSignal(dict)

    def __init__(self, folder, hosts, target, autostart, parent=None):
        super().__init__(parent)
        self.folder = folder
        self.hosts = hosts
        self.target = target
        self.autostart = autostart

    def run(self):
        coord = DeployCoordinator(log=self.log.emit)
        try:
            results = coord.deploy(self.folder, self.hosts, self.target, self.autostart)
        except DeployError as e:
            self.log.emit("[ERROR] {}".format(e))
            results = {}
        except Exception as e:  # noqa
            self.log.emit("[ERROR] 未预期异常: {}".format(e))
            results = {}
        self.finished.emit(results)


# ---------------- 主窗口 ----------------
class DeployToolWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("远程部署上位机 - Test Management Deploy")
        self.resize(780, 620)
        self._worker = None
        self._hosts = []
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)

        # ---- 1. 部署源 ----
        src_group = QGroupBox("1. 部署源（选择本机软件文件夹 或 .tar.gz 安装包）")
        src_layout = QHBoxLayout(src_group)
        self.src_edit = QLineEdit()
        self.src_edit.setPlaceholderText("点击右侧“浏览”选择软件文件夹 / 安装包")
        browse_btn = QPushButton("浏览…")
        browse_btn.clicked.connect(self._browse)
        src_layout.addWidget(self.src_edit)
        src_layout.addWidget(browse_btn)
        root.addWidget(src_group)

        # ---- 2. 远程设备 ----
        device_group = QGroupBox("2. 远程设备（Linux/Ubuntu，点击“+”添加多台）")
        dev_layout = QVBoxLayout(device_group)
        form = QFormLayout()
        self.ip_edit = QLineEdit()
        self.ip_edit.setPlaceholderText("192.168.1.100")
        self.user_edit = QLineEdit()
        self.user_edit.setText("pi")
        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.Password)
        self.pass_edit.setText("raspberry")
        self.sudo_edit = QLineEdit()
        self.sudo_edit.setEchoMode(QLineEdit.Password)
        self.sudo_edit.setPlaceholderText("留空则使用密码")
        form.addRow("IP 地址:", self.ip_edit)
        form.addRow("用户名:", self.user_edit)
        form.addRow("密码:", self.pass_edit)
        form.addRow("sudo 密码:", self.sudo_edit)
        row = QHBoxLayout()
        add_btn = QPushButton("+ 添加设备")
        add_btn.clicked.connect(self._add_host)
        row.addWidget(add_btn)
        row.addStretch()
        form.addRow("", row)
        dev_layout.addLayout(form)

        self.device_list = QListWidget()
        self.device_list.setMaximumHeight(140)
        dev_layout.addWidget(self.device_list)
        dev_rm = QHBoxLayout()
        rm_btn = QPushButton("移除选中")
        rm_btn.clicked.connect(self._remove_selected)
        clear_btn = QPushButton("清空设备")
        clear_btn.clicked.connect(self.device_list.clear)
        dev_rm.addWidget(rm_btn)
        dev_rm.addWidget(clear_btn)
        dev_rm.addStretch()
        dev_layout.addLayout(dev_rm)
        root.addWidget(device_group)

        # ---- 3. 部署选项 ----
        opt_group = QGroupBox("3. 部署选项")
        opt_layout = QFormLayout(opt_group)
        self.target_edit = QLineEdit()
        self.target_edit.setText("~/Test_Management")
        opt_layout.addRow("远程目标目录:", self.target_edit)
        self.autostart_check = QCheckBox("开启开机启动（systemd 服务，开机自动运行）")
        self.autostart_check.setChecked(True)
        opt_layout.addRow("开机启动:", self.autostart_check)
        root.addWidget(opt_group)

        # ---- 4. 操作 ----
        act_layout = QHBoxLayout()
        self.deploy_btn = QPushButton("一键部署")
        self.deploy_btn.setMinimumHeight(40)
        self.deploy_btn.clicked.connect(self._start_deploy)
        self.quit_btn = QPushButton("退出")
        self.quit_btn.clicked.connect(self.close)
        act_layout.addWidget(self.deploy_btn, 1)
        act_layout.addWidget(self.quit_btn)
        root.addLayout(act_layout)

        # ---- 5. 日志 ----
        log_group = QGroupBox("部署日志")
        log_layout = QVBoxLayout(log_group)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        log_layout.addWidget(self.log_view)
        root.addWidget(log_group, 1)

    # ---------- 交互 ----------
    def _browse(self):
        # 让用户选择: 软件文件夹 或 .tar.gz 安装包
        from PyQt5.QtWidgets import QInputDialog
        choice, ok = QInputDialog.getItem(
            self, "选择部署源", "请选择部署源类型:",
            ["软件文件夹", ".tar.gz / .tgz 安装包"], 0, False)
        if not ok:
            return
        if choice == ".tar.gz / .tgz 安装包":
            path, _ = QFileDialog.getOpenFileName(
                self, "选择安装包", "", "安装包 (*.tar.gz *.tgz)")
        else:
            path = QFileDialog.getExistingDirectory(self, "选择软件文件夹")
        if path:
            self.src_edit.setText(path)
            self._log("[INFO] 选择部署源: {}".format(path))
            self._log("[HINT] 提示: onedir 单文件夹可选用 deploy/dist/EOL_CIT, 目标机免装 Python")

    def _add_host(self):
        ip = self.ip_edit.text().strip()
        user = self.user_edit.text().strip()
        password = self.pass_edit.text()
        sudo = self.sudo_edit.text() or password
        if not ip:
            QMessageBox.warning(self, "提示", "请输入远程 IP 地址")
            return
        if not user:
            user = "pi"
        host = RemoteHost(ip, user, password, sudo)
        self._hosts.append(host)
        item = QListWidgetItem("{}@{}".format(user, ip))
        item.setToolTip("IP: {}  用户: {}  密码: {}".format(ip, user, password))
        self.device_list.addItem(item)
        self.ip_edit.clear()
        self._log("[INFO] 已添加设备 {}@{}".format(user, ip))

    def _remove_selected(self):
        row = self.device_list.currentRow()
        if row >= 0:
            self.device_list.takeItem(row)
            if row < len(self._hosts):
                self._hosts.pop(row)

    def _append_host_list(self):
        # 同步列表与主机
        items = []
        for i in range(self.device_list.count()):
            items.append(self.device_list.item(i).text())
        return items

    # ---------- 部署 ----------
    def _validate_source(self, path):
        """校验部署源是否像可用的软件(app 或 onedir 可执行程序)。返回 (ok, 提示)。"""
        if not os.path.exists(path):
            return False, "路径不存在: {}".format(path)
        if os.path.isfile(path):
            if path.lower().endswith((".tar.gz", ".tgz")):
                return True, ""
            return False, "安装包需为 .tar.gz / .tgz 格式"
        if not os.path.isdir(path):
            return False, "非文件夹/文件"
        # 目录: 应包含 main.py(源码) 或可执行程序(onedir)
        if os.path.isfile(os.path.join(path, "main.py")):
            return True, ""
        if os.path.isfile(os.path.join(path, "EOL_CIT")) and not os.path.isdir(os.path.join(path, "EOL_CIT")):
            return True, ""
        # 顶层为空
        try:
            if not os.listdir(path):
                return False, "所选文件夹为空，请选择软件目录或 onedir 文件夹(如 deploy/dist/EOL_CIT)"
        except OSError:
            pass
        return False, ("所选文件夹未找到 main.py 或可执行程序。\n"
                       "请选择: 1) 软件源码目录  2) onedir 文件夹 deploy/dist/EOL_CIT")

    def _start_deploy(self):
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "提示", "部署正在进行中，请稍候…")
            return
        folder = self.src_edit.text().strip()
        ok, msg = self._validate_source(folder)
        if not ok:
            QMessageBox.warning(self, "提示", msg)
            self._log("[ERROR] " + msg)
            return
        if not self._hosts:
            QMessageBox.warning(self, "提示", "请至少添加一台远程设备")
            return
        target = self.target_edit.text().strip() or "~/Test_Management"
        autostart = self.autostart_check.isChecked()

        self._log("")
        self._log("===== 开始一键部署（{} 台设备，开机启动={}）=====".format(
            len(self._hosts), "开" if autostart else "关"))
        self.deploy_btn.setEnabled(False)

        # DeployWorker 本身是 QThread, start() 后 run() 在子线程执行。
        # 用实例属性持有引用, 防止线程对象被提前销毁。
        self._worker = DeployWorker(folder, list(self._hosts), target, autostart, self)
        self._worker.log.connect(self._log)
        self._worker.finished.connect(self._on_deploy_done)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_deploy_done(self, results):
        self.deploy_btn.setEnabled(True)
        ok = sum(1 for v in results.values() if v == "OK")
        self._log("")
        self._log("===== 部署汇总：成功 {} / 共 {} 台 =====".format(ok, len(results)))
        for k, v in results.items():
            self._log("  {} : {}".format(k, v))
        self._log("===== 全部完成 =====")
        QMessageBox.information(self, "部署完成",
                                "部署完成：成功 {} / 共 {} 台".format(ok, len(results)))

    def _log(self, msg):
        self.log_view.appendPlainText(msg)
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())


def main():
    app = QApplication(sys.argv)
    win = DeployToolWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
