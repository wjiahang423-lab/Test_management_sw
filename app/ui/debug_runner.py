import time

from PyQt5.QtCore import QThread
from PyQt5.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                             QMessageBox, QPushButton, QTextEdit, QVBoxLayout)

from app.core.context import RuntimeContext
from app.core.engine import EngineWorker
from app.ui.pop_dialog import PopDialog


class DebugRunner(QDialog):
    """Run the current plan inside the management page for quick debugging."""

    def __init__(self, plan, context, variables, settings, parent=None):
        super().__init__(parent)
        self.plan = plan
        self.ctx = context
        self.variables = variables
        self.settings = settings
        self.engine = None
        self.setWindowTitle("调试执行 - {}".format(plan.name))
        self.setMinimumSize(720, 480)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("调试模式：在管理页面内直接执行当前测试计划，结果不入生产统计。"))

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet("background:#1e1e1e;color:#d4d4d4;font-family:Consolas,monospace;font-size:12px;")
        layout.addWidget(self.log, 1)

        btns = QHBoxLayout()
        self.btn_stop = QPushButton("停止")
        self.btn_close = QPushButton("关闭")
        btns.addStretch(1)
        btns.addWidget(self.btn_stop)
        btns.addWidget(self.btn_close)
        layout.addLayout(btns)

        self.btn_stop.clicked.connect(self._stop)
        self.btn_close.clicked.connect(self._close)
        self._start()

    def _start(self):
        try:
            self.ctx.set_sn("调试SN")
            self.ctx.reset_display()
            self.engine = EngineWorker(self.plan, self.ctx, self.variables, self.settings)
            self.engine.wait_sn = False
            self.engine.continuous = False
            self.engine.sig_log.connect(self._append_log)
            self.engine.sig_run_finished.connect(self._on_finished)
            self.engine.sig_request_pop.connect(self._on_pop)
            self.engine.sig_case_state.connect(lambda cid, st, dt, el: self._append_log(
                "  - {} -> {}".format(cid, st)))
            self.engine.start()
        except Exception:
            from app.core import syslog
            syslog.exception("调试执行启动失败")
            self._append_log("调试执行启动失败，详情见系统日志（data/logs/）")
            self.btn_stop.setEnabled(False)

    def _append_log(self, msg):
        self.log.append("[{}] {}".format(time.strftime("%H:%M:%S"), msg))
        sb = self.log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_pop(self, config):
        try:
            dlg = PopDialog(config, self)
            result = dlg.exec_() == QDialog.Accepted
        except Exception:
            from app.core import syslog
            syslog.exception("调试弹窗显示异常，按取消处理")
            self._append_log("弹窗显示异常，已按（取消）继续执行")
            result = False
        self.engine.pop_result = result
        self.engine.pop_requested.set()

    def _on_finished(self, ok, msg):
        self._append_log("调试结束：{}".format(msg))

    def _stop(self):
        if self.engine and self.engine.isRunning():
            self.engine.stop()
            self._append_log("停止请求已发送")

    def _close(self):
        if self.engine and self.engine.isRunning():
            self.engine.stop()
            self.engine.wait(3000)
        self.accept()
