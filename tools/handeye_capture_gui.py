#!/usr/bin/env python3
"""Local PyQt5 GUI that drives hand-eye capture on the robot over SSH.

This computer needs no ROS: everything ROS-related runs on the robot.

  * first "连接" starts getdata.py on the robot behind a FIFO (if not running)
  * "拍照" writes a capture command into that FIFO
  * the new PNG is copied back with scp and shown here
  * every sample already on the robot can be browsed and downloaded

Usage:
    python3 gui_capture.py
    python3 gui_capture.py --host 192.168.88.10 --user zioneer --password 123 \
        --cam xema_left --arm left

Note: the password is passed to sshpass on the command line, so it is visible
to other local users through `ps`.
"""

from __future__ import annotations

import argparse
import base64
import os
import shlex
import subprocess
import sys
import time

import cv2
import numpy as np

from PyQt5.QtCore import Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import (
    QDesktopServices,
    QFont,
    QImage,
    QKeySequence,
    QPixmap,
)
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QShortcut,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

SSH_OPTS = [
    "-o",
    "StrictHostKeyChecking=accept-new",
    "-o",
    "ConnectTimeout=8",
    "-o",
    "ServerAliveInterval=5",
]

REMOTE_SETUP = """#!/bin/bash
echo $$ > {pidfile}
source /opt/ros/humble/setup.bash
P=/opt/zioneer/versions/MechaWorkbase/svc_robot_cit/mc_task_msgs
export PYTHONPATH="$P/local/lib/python3.10/dist-packages:$PYTHONPATH"
export LD_LIBRARY_PATH="$P/lib:$LD_LIBRARY_PATH"
exec python3 -u {getdata} --cam {cam} --arm {arm} --out {out} --pose-topic {pose} < {fifo}
"""


def bgr_to_qimage(arr: np.ndarray) -> QImage:
    if arr is None:
        return QImage()
    arr = np.ascontiguousarray(arr)
    if arr.ndim == 2:
        h, w = arr.shape
        return QImage(arr.data, w, h, int(arr.strides[0]), QImage.Format_Grayscale8).copy()
    rgb = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
    h, w, _ = rgb.shape
    return QImage(rgb.data, w, h, int(rgb.strides[0]), QImage.Format_RGB888).copy()


class ImageView(QLabel):
    def __init__(self, placeholder: str = "暂无图像", parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(360, 260)
        self.setStyleSheet("background:#202124;color:#bbb;border:1px solid #333;")
        self._pix: QPixmap | None = None
        self.setText(placeholder)

    def set_bgr(self, arr: np.ndarray):
        img = bgr_to_qimage(arr)
        if img.isNull():
            return
        self._pix = QPixmap.fromImage(img)
        self._rescale()

    def clear_image(self, text: str = "暂无图像"):
        self._pix = None
        self.setText(text)

    def _rescale(self):
        if self._pix is None or self._pix.isNull():
            return
        self.setPixmap(
            self._pix.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale()


class RemoteSession:
    """All robot interaction happens here (ssh / scp / a FIFO)."""

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        cam: str,
        arm: str,
        remote_data: str,
        remote_out: str,
        pose_topic: str,
        local_dir: str,
    ):
        self.host = host
        self.user = user
        self.password = password
        self.cam = cam
        self.arm = arm
        self.remote_data = remote_data.rstrip("/")
        self.remote_out = remote_out.rstrip("/")
        self.pose_topic = pose_topic
        self.local_dir = local_dir

        tag = f"{cam}_{arm}"
        self.fifo = f"/tmp/handeye_{tag}.fifo"
        self.log = f"/tmp/handeye_{tag}.log"
        self.pidfile = f"/tmp/handeye_{tag}.pid"
        self.writer_pidfile = f"/tmp/handeye_{tag}.writer.pid"
        self.script = f"/tmp/handeye_{tag}.sh"
        self.cache_dir = os.path.join(local_dir, tag)
        self.target = f"{user}@{host}"

    # ---- plumbing ----------------------------------------------------
    def _ssh_cmd(self, cmd: str) -> list[str]:
        base = ["ssh", *SSH_OPTS, self.target, cmd]
        if self.password:
            return ["sshpass", "-p", self.password, *base]
        return base

    def ssh(self, cmd: str, timeout: float = 30.0) -> tuple[int, str, str]:
        p = subprocess.run(
            self._ssh_cmd(cmd), capture_output=True, text=True, timeout=timeout
        )
        return p.returncode, p.stdout, p.stderr

    def _scp_cmd(self, remote: str, local: str) -> list[str]:
        base = ["scp", "-q", *SSH_OPTS, f"{self.target}:{remote}", local]
        if self.password:
            return ["sshpass", "-p", self.password, *base]
        return base

    # ---- service lifecycle -------------------------------------------
    def is_alive(self) -> bool:
        cmd = (
            f"p=$(cat {self.pidfile} 2>/dev/null); "
            f'if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then echo ALIVE; else echo DEAD; fi'
        )
        rc, out, _ = self.ssh(cmd, timeout=15)
        return out.strip() == "ALIVE"

    def start_service(self):
        script = REMOTE_SETUP.format(
            pidfile=self.pidfile,
            getdata=f"{self.remote_data}/getdata.py",
            cam=self.cam,
            arm=self.arm,
            out=self.remote_out,
            pose=self.pose_topic,
            fifo=self.fifo,
        )
        b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
        cmd = (
            f"mkdir -p {self.remote_out}\n"
            f"printf %s {b64} | base64 -d > {self.script}\n"
            f"chmod +x {self.script}\n"
            f"rm -f {self.fifo} {self.log} {self.pidfile} {self.writer_pidfile}\n"
            f"mkfifo {self.fifo}\n"
            f"nohup setsid bash -c 'echo $$ > {self.writer_pidfile}; "
            f"exec sleep 100000000 3> {self.fifo}' </dev/null >/dev/null 2>&1 &\n"
            f"nohup setsid {self.script} > {self.log} 2>&1 < /dev/null &\n"
            f"echo started"
        )
        rc, out, err = self.ssh(cmd, timeout=25)
        if "started" not in out:
            raise RuntimeError(err.strip() or "无法在机器人上启动采集服务")

    def stop_service(self):
        cmd = (
            f"p=$(cat {self.pidfile} 2>/dev/null); "
            f'[ -n "$p" ] && kill "$p" 2>/dev/null; '
            f"w=$(cat {self.writer_pidfile} 2>/dev/null); "
            f'[ -n "$w" ] && kill "$w" 2>/dev/null; '
            f"rm -f {self.fifo} {self.pidfile} {self.writer_pidfile}"
        )
        try:
            self.ssh(cmd, timeout=10)
        except Exception:  # noqa: BLE001
            pass

    def log_tail(self, lines: int = 30) -> str:
        rc, out, _ = self.ssh(f"tail -n {lines} {self.log} 2>/dev/null", timeout=15)
        return out

    def service_state(self) -> str:
        """READY / ERROR / WAIT, looking at the whole log (not just the tail)."""
        cmd = (
            f"if grep -q 'ready\\.' {self.log} 2>/dev/null; then echo READY; "
            f"elif grep -qE 'Traceback|未就绪' {self.log} 2>/dev/null; then echo ERROR; "
            f"else echo WAIT; fi"
        )
        rc, out, _ = self.ssh(cmd, timeout=15)
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        return lines[-1] if lines else "WAIT"

    def wait_ready(self, timeout: float) -> tuple[bool, str]:
        t0 = time.time()
        state = "WAIT"
        while time.time() - t0 < timeout:
            state = self.service_state()
            if state == "READY":
                return True, self.log_tail(8)
            if state == "ERROR":
                return False, self.log_tail(30)
            time.sleep(1.0)
        return False, self.log_tail(30)

    def ensure_service(self, timeout: float = 90.0) -> tuple[bool, str]:
        if self.is_alive():
            ok, tail = self.wait_ready(min(20.0, timeout))
            if ok:
                return True, "已复用机器人上运行的采集服务"
            self.stop_service()
        self.start_service()
        return self.wait_ready(timeout)

    # ---- data --------------------------------------------------------
    def list_images(self) -> list[str]:
        rc, out, _ = self.ssh(f"ls -1 {self.remote_out} 2>/dev/null", timeout=20)
        names = [
            ln.strip()
            for ln in out.splitlines()
            if ln.strip().endswith("_color_brightness.png")
        ]
        return sorted(names, reverse=True)

    def capture(self, timeout: float = 90.0):
        before = set(self.list_images())
        rc, out, err = self.ssh(f"printf 'p\\n' > {self.fifo}", timeout=15)
        if rc != 0:
            return False, f"发送拍照指令失败：{err.strip() or out.strip()}"

        t0 = time.time()
        while time.time() - t0 < timeout:
            names = self.list_images()
            new = [n for n in names if n not in before]
            if new:
                name = new[0]
                local = self.fetch(name)
                pose = self.pose_for(name)
                return True, (name, local, pose)
            time.sleep(0.7)

        tail = self.log_tail(8)
        return False, f"等待新图像超时\n{tail.strip()[-400:]}"

    def pose_for(self, name: str, timeout: float = 10.0) -> str:
        """getdata.py writes poses.txt after the images; wait for this name."""
        t0 = time.time()
        last = ""
        while time.time() - t0 < timeout:
            rc, out, _ = self.ssh(
                f"tail -n 1 {self.remote_out}/poses.txt 2>/dev/null", timeout=15
            )
            last = out.strip()
            if last.startswith(name):
                return last
            time.sleep(0.4)
        return last if last.startswith(name) else ""

    def fetch(self, name: str) -> str:
        os.makedirs(self.cache_dir, exist_ok=True)
        local = os.path.join(self.cache_dir, name)
        if os.path.exists(local) and os.path.getsize(local) > 0:
            return local
        remote = f"{self.remote_out}/{name}"
        p = subprocess.run(
            self._scp_cmd(remote, local), capture_output=True, text=True, timeout=180
        )
        if p.returncode != 0:
            raise RuntimeError(p.stderr.strip() or "scp 下载失败")
        return local


class ConnectThread(QThread):
    done = pyqtSignal(bool, str)

    def __init__(self, session: RemoteSession, parent=None):
        super().__init__(parent)
        self.session = session

    def run(self):
        try:
            ok, msg = self.session.ensure_service()
        except Exception as e:  # noqa: BLE001
            self.done.emit(False, str(e))
        else:
            self.done.emit(ok, msg)


class CaptureThread(QThread):
    done = pyqtSignal(bool, object)

    def __init__(self, session: RemoteSession, parent=None):
        super().__init__(parent)
        self.session = session

    def run(self):
        try:
            ok, info = self.session.capture()
        except Exception as e:  # noqa: BLE001
            ok, info = False, str(e)
        self.done.emit(ok, info)


class ListThread(QThread):
    done = pyqtSignal(bool, object)

    def __init__(self, session: RemoteSession, parent=None):
        super().__init__(parent)
        self.session = session

    def run(self):
        try:
            self.done.emit(True, self.session.list_images())
        except Exception as e:  # noqa: BLE001
            self.done.emit(False, str(e))


class FetchThread(QThread):
    done = pyqtSignal(bool, object)

    def __init__(self, session: RemoteSession, name: str, parent=None):
        super().__init__(parent)
        self.session = session
        self.name = name

    def run(self):
        try:
            self.done.emit(True, self.session.fetch(self.name))
        except Exception as e:  # noqa: BLE001
            self.done.emit(False, str(e))


class StopServicesThread(QThread):
    def __init__(self, sessions, parent=None):
        super().__init__(parent)
        self.sessions = list(sessions)

    def run(self):
        for session in self.sessions:
            session.stop_service()


class MainWindow(QWidget):
    def __init__(self, make_session, initial_arm: str, parent=None, autoconnect=True):
        super().__init__(parent)
        self.make_session = make_session
        self.sessions: dict[str, RemoteSession] = {}
        self.arm = initial_arm
        self.session = self._get_session(initial_arm)
        self.ready = False
        self._closing = False
        self._allow_close = False
        self._cleanup_thread = None
        self._touched_sessions = set()
        self.connect_thread: ConnectThread | None = None
        self.capture_thread: CaptureThread | None = None
        self.list_thread: ListThread | None = None
        self.fetch_thread: FetchThread | None = None

        self._build()
        if autoconnect:
            self._connect()
        else:
            self.view.clear_image("点击“重连”连接采集设备")
            self._set_status("未连接；点击“重连”启动采集服务")

    def _get_session(self, arm: str) -> RemoteSession:
        if arm not in self.sessions:
            self.sessions[arm] = self.make_session(arm)
        return self.sessions[arm]

    # ------------------------------------------------------------------
    def _build(self):
        self.resize(1380, 860)
        self.setStyleSheet(
            "QGroupBox{font-weight:bold;} QLabel{font-size:13px;}"
            "QPushButton{padding:6px 14px;}"
        )

        title = QLabel("手眼标定采集")
        f = QFont()
        f.setPointSize(17)
        f.setBold(True)
        title.setFont(f)

        self.info_label = QLabel()
        self.info_label.setStyleSheet("color:#666;")
        self._update_info()

        arm_label = QLabel("手臂")
        self.arm_combo = QComboBox()
        self.arm_combo.addItems(["left", "right"])
        self.arm_combo.setCurrentText(self.arm)
        self.arm_combo.setMinimumWidth(96)
        self.arm_combo.currentTextChanged.connect(self._on_arm_changed)

        self.btn_capture = QPushButton("拍照  (空格)")
        self.btn_capture.setMinimumSize(200, 52)
        self.btn_capture.setStyleSheet(
            "QPushButton{background:#c62828;color:#fff;font-size:17px;"
            "font-weight:bold;border-radius:6px;}"
            "QPushButton:hover{background:#d32f2f;}"
            "QPushButton:pressed{background:#8e0000;}"
            "QPushButton:disabled{background:#9e9e9e;}"
        )
        self.btn_capture.clicked.connect(self.on_capture)
        self.btn_capture.setEnabled(False)

        self.btn_reconnect = QPushButton("重连")
        self.btn_reconnect.clicked.connect(self._connect)

        top = QHBoxLayout()
        top.addWidget(title)
        top.addSpacing(18)
        top.addWidget(self.info_label, 1)
        top.addWidget(arm_label)
        top.addWidget(self.arm_combo)
        top.addWidget(self.btn_reconnect)
        top.addWidget(self.btn_capture)

        self.view = ImageView("正在连接机器人…")
        view_box = QGroupBox("图像预览")
        view_lay = QVBoxLayout(view_box)
        view_lay.addWidget(self.view, 1)
        self.file_label = QLabel("—")
        self.file_label.setStyleSheet(
            "color:#444;font-family:monospace;font-size:12px;"
        )
        self.file_label.setWordWrap(True)
        view_lay.addWidget(self.file_label)

        self.count_label = QLabel("远程 0 张")
        self.count_label.setStyleSheet("color:#666;")
        self.list = QListWidget()
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.ElideMiddle)
        self.list.currentItemChanged.connect(self._on_select)
        self.btn_refresh = QPushButton("刷新列表")
        self.btn_refresh.clicked.connect(lambda: self.refresh_list())
        self.btn_folder = QPushButton("打开本地目录")
        self.btn_folder.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.session.cache_dir))
        )

        list_box = QGroupBox("机器人上的样本")
        list_lay = QVBoxLayout(list_box)
        list_lay.addWidget(self.count_label)
        list_lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        row.addWidget(self.btn_refresh)
        row.addWidget(self.btn_folder)
        list_lay.addLayout(row)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(view_box)
        split.addWidget(list_box)
        split.setSizes([940, 420])

        self.status = QLabel("正在连接机器人…")
        self.status.setWordWrap(True)
        self._set_status("正在连接机器人…")

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(split, 1)
        lay.addWidget(self.status)

        for key in ("Space", "P"):
            shortcut = QShortcut(QKeySequence(key), self, activated=self.on_capture)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)

    def _set_status(self, text: str, error: bool = False):
        color = "#b71c1c" if error else "#333"
        self.status.setText(text)
        self.status.setStyleSheet(
            f"color:{color};border-top:1px solid #ddd;padding-top:4px;"
        )

    def _update_info(self):
        s = self.session
        self.setWindowTitle(f"手眼标定采集（远程） — {s.target} · {s.cam} · {s.arm}")
        self.info_label.setText(
            f"机器人 {s.target}　　相机 /{s.cam}　　手臂 {s.arm}\n"
            f"远程输出 {s.remote_out}\n本地缓存 {s.cache_dir}"
        )

    def _update_buttons(self):
        busy = any(
            t is not None and t.isRunning()
            for t in (
                self.connect_thread,
                self.capture_thread,
                self.list_thread,
                self.fetch_thread,
            )
        )
        self.arm_combo.setEnabled(not busy)
        self.btn_reconnect.setEnabled(not busy)
        self.btn_refresh.setEnabled(not busy)
        self.btn_capture.setEnabled(self.ready and not busy)

    def _on_arm_changed(self, arm: str):
        if not arm or arm == self.arm:
            return
        self.arm = arm
        self.session = self._get_session(arm)
        self.ready = False
        self.list.blockSignals(True)
        self.list.clear()
        self.list.blockSignals(False)
        self.count_label.setText("远程 0 张")
        self.view.clear_image("正在连接机器人…")
        self.file_label.setText("—")
        self.btn_capture.setText("拍照  (空格)")
        self._update_info()
        self._set_status(
            f"已切换到 {arm} 手臂（相机 /{self.session.cam}），正在连接…"
        )
        self._connect()

    # ---- connect -----------------------------------------------------
    def _connect(self):
        if self._closing:
            return
        if self.connect_thread is not None and self.connect_thread.isRunning():
            return
        self.ready = False
        self._set_status(
            f"正在启动/连接机器人上的 {self.arm} 采集服务…"
        )
        session = self.session
        self._touched_sessions.add(session)
        self.connect_thread = ConnectThread(session, self)
        self.connect_thread.done.connect(
            lambda ok, msg, s=session: self._on_connected(s, ok, msg)
        )
        self.connect_thread.finished.connect(self._update_buttons)
        self.connect_thread.start()
        self._update_buttons()

    def _on_connected(self, session: RemoteSession, ok: bool, msg: str):
        if self._closing:
            return
        if session is not self.session:
            return
        self.ready = ok
        if ok:
            self._set_status("已就绪：按“拍照”或空格执行远程采集。")
            self.refresh_list()
        else:
            self.view.clear_image("未连接")
            self._set_status(f"连接失败：{msg.strip()[-500:]}", error=True)

    # ---- capture -----------------------------------------------------
    def on_capture(self):
        if self._closing:
            return
        if not self.ready:
            self._set_status("尚未连接机器人，请先重连。", error=True)
            return
        if self.capture_thread is not None and self.capture_thread.isRunning():
            return
        self.btn_capture.setText("采集中…")
        self._set_status("已发送拍照指令，等待机器人返回图像…")
        session = self.session
        self.capture_thread = CaptureThread(session, self)
        self.capture_thread.done.connect(
            lambda ok, info, s=session: self._on_captured(s, ok, info)
        )
        self.capture_thread.finished.connect(self._update_buttons)
        self.capture_thread.start()
        self._update_buttons()

    def _on_captured(self, session: RemoteSession, ok: bool, info):
        if session is not self.session:
            return
        self.btn_capture.setText("拍照  (空格)")
        if not ok:
            self._set_status(f"采集失败：{info}", error=True)
            return
        name, local, pose = info
        self.file_label.setText(name + (f"\n{pose}" if pose else ""))
        img = cv2.imread(local, cv2.IMREAD_COLOR)
        if img is not None:
            self.view.set_bgr(img)
        self._set_status(f"已采集并下载：{name}")
        self.refresh_list(select=name)

    # ---- list / viewer -----------------------------------------------
    def refresh_list(self, select: str | None = None):
        if self._closing:
            return
        if not self.ready:
            return
        if self.list_thread is not None and self.list_thread.isRunning():
            return
        session = self.session
        self.list_thread = ListThread(session, self)
        self.list_thread.done.connect(
            lambda ok, r, s=session: self._on_list(s, ok, r, select)
        )
        self.list_thread.finished.connect(self._update_buttons)
        self.list_thread.start()
        self._update_buttons()

    def _on_list(self, session: RemoteSession, ok: bool, names, select: str | None):
        if self._closing:
            return
        if session is not self.session:
            return
        if not ok:
            self._set_status(f"获取列表失败：{names}", error=True)
            return
        cur = self.list.currentItem()
        keep = select or (cur.text() if cur is not None else None)

        self.list.blockSignals(True)
        self.list.clear()
        for n in names:
            item = QListWidgetItem(n)
            item.setData(Qt.UserRole, n)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self.count_label.setText(f"远程 {len(names)} 张")

        target = keep if keep in names else (names[0] if names else None)
        if target:
            hits = self.list.findItems(target, Qt.MatchExactly)
            if hits:
                self.list.setCurrentItem(hits[0])
        else:
            self.view.clear_image("暂无图像，按“拍照”采集")
            self.file_label.setText("—")

    def _on_select(self, item: QListWidgetItem | None, _prev=None):
        if self._closing:
            return
        if item is None or not self.ready:
            return
        if self.fetch_thread is not None and self.fetch_thread.isRunning():
            return
        name = item.data(Qt.UserRole)
        self._set_status(f"正在下载 {name} …")
        session = self.session
        self.fetch_thread = FetchThread(session, name, self)
        self.fetch_thread.done.connect(
            lambda ok, r, s=session: self._on_fetched(s, ok, r, name)
        )
        self.fetch_thread.finished.connect(self._update_buttons)
        self.fetch_thread.start()
        self._update_buttons()

    def _on_fetched(self, session: RemoteSession, ok: bool, result, name: str):
        if session is not self.session:
            return
        if not ok:
            self._set_status(f"下载失败：{result}", error=True)
            return
        img = cv2.imread(result, cv2.IMREAD_COLOR)
        if img is None:
            self.view.clear_image("无法读取该图片")
        else:
            self.view.set_bgr(img)
        self.file_label.setText(name)
        self._set_status(f"查看：{name}")

    def closeEvent(self, event):
        if self._allow_close:
            event.accept()
            return
        event.ignore()
        if not self._closing:
            self._closing = True
            self.setEnabled(False)
            self._set_status("正在等待采集任务退出…")
            self._advance_close()

    def _advance_close(self):
        if any(t is not None and t.isRunning() for t in (
                self.connect_thread, self.capture_thread, self.list_thread, self.fetch_thread)):
            QTimer.singleShot(100, self._advance_close)
            return
        if self._cleanup_thread is None:
            self._cleanup_thread = StopServicesThread(self._touched_sessions, self)
            self._cleanup_thread.finished.connect(self._finish_close)
            self._cleanup_thread.start()

    def _finish_close(self):
        self._allow_close = True
        self.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Remote hand-eye capture GUI (SSH)")
    p.add_argument("--host", default="192.168.88.10")
    p.add_argument("--user", default="zioneer")
    p.add_argument("--password", default="123", help="empty string to use ssh keys")
    p.add_argument(
        "--arm", choices=("left", "right"), default="left", help="initial arm"
    )
    p.add_argument("--remote-data", default="/home/zioneer/vision/eye-hand/data")
    p.add_argument("--pose-topic", default="/zioneer/mc/current_poses")
    p.add_argument(
        "--local-dir",
        default=os.path.join(os.path.expanduser("~"), "eye-hand_captures"),
    )
    return p


def main() -> int:
    args = build_parser().parse_args()

    def make_session(arm: str) -> RemoteSession:
        return RemoteSession(
            args.host,
            args.user,
            args.password,
            f"xema_{arm}",
            arm,
            args.remote_data,
            f"{args.remote_data.rstrip('/')}/{arm}/capture",
            args.pose_topic,
            os.path.abspath(args.local_dir),
        )

    # opencv-python ships its own Qt plugins and points Qt at them on import,
    # which makes PyQt5 fail to load the xcb platform plugin.
    if "cv2/qt/plugins" in os.environ.get("QT_QPA_PLATFORM_PLUGIN_PATH", ""):
        del os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"]

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    win = MainWindow(make_session, args.arm)
    win.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
