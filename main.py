#!/usr/bin/env python3
import sys
import threading
import traceback

from PyQt5.QtCore import qInstallMessageHandler
from PyQt5.QtWidgets import QApplication

from app.main_flow import bootstrap
from app.core import syslog


def _qt_message_handler(mode, context, message):
    """Route Qt warnings (e.g. from the C++ runtime) into the system log."""
    try:
        if "unexpected" in str(message).lower() or "error" in str(message).lower() \
                or "invalid" in str(message).lower() or "assert" in str(message).lower():
            syslog.error("[Qt] {}".format(message))
        else:
            syslog.warn("[Qt] {}".format(message))
    except Exception:
        pass


def _install_hooks():
    """Install global error handlers so a bug never flashes the app to exit."""
    sys.excepthook = syslog.log_exception_hook
    qInstallMessageHandler(_qt_message_handler)
    try:
        threading.excepthook = lambda args: syslog.exception(
            "线程异常（{}）".format(args.thread.name if args.thread else "?"),
            (args.exc_type, args.exc_value, args.exc_traceback))
    except AttributeError:
        pass


def main():
    _install_hooks()
    app = QApplication(sys.argv)
    app.setApplicationName("TestManagement")
    app.setStyle("Fusion")
    try:
        win = bootstrap(app)
    except Exception:
        syslog.exception("启动失败")
        import PyQt5.QtWidgets as qtw
        qtw.QMessageBox.critical(None, "错误", "程序启动失败，详情请查看 data/logs/ 下的系统日志")
        sys.exit(1)
    if win is None:
        sys.exit(0)
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
