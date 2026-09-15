#!/usr/bin/env python3
import datetime
import faulthandler
import os
import signal
import sys
import threading
import traceback

from PyQt5.QtCore import qInstallMessageHandler
from PyQt5.QtWidgets import QApplication, QMessageBox

from app.core import paths as _paths
from app.main_flow import bootstrap
from app.core import syslog

# 无论从何处启动（桌面图标/systemd 等），统一以程序目录为工作目录，
# 使计划里相对路径的脚本、以及 ZLG 驱动的相对库路径都能正确解析。
try:
    os.chdir(_paths.BASE_DIR)
except Exception:
    pass

CRASH_MARKER = os.path.join(_paths.LOGS_DIR, ".prev_crash")


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


def _write_crash_marker():
    """Write a marker so an abnormal exit is visible on next startup."""
    try:
        os.makedirs(_paths.LOGS_DIR, exist_ok=True)
        with open(CRASH_MARKER, "w", encoding="utf-8") as f:
            f.write(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    except Exception:
        pass


def _clear_crash_marker():
    try:
        os.remove(CRASH_MARKER)
    except Exception:
        pass


def _read_crash_marker():
    try:
        with open(CRASH_MARKER, "r", encoding="utf-8") as f:
            text = f.read().strip()
            f.close()
        if text:
            os.remove(CRASH_MARKER)
        return text
    except Exception:
        return ""


def _install_fault_handlers():
    """Native crashes (SIGSEGV/abort/...) still dump the Python stack to a file."""
    try:
        
        os.makedirs(_paths.LOGS_DIR, exist_ok=True)
        path = os.path.join(_paths.LOGS_DIR,
                            "crash_{}.txt".format(datetime.datetime.now().strftime("%Y%m%d_%H%M%S")))
        f = open(path, "w", encoding="utf-8")
        faulthandler.register(signal.SIGSEGV, file=f)
        faulthandler.register(signal.SIGABRT, file=f)
        faulthandler.register(signal.SIGBUS, file=f)
        faulthandler.register(signal.SIGILL, file=f)
        faulthandler.register(signal.SIGFPE, file=f)
    except Exception:
        pass


def _install_hooks():
    """Install global error handlers so a bug never flashes the app to exit."""
    sys.excepthook = syslog.log_exception_hook

    def _thread_hook(args):
        try:
            syslog.exception(
                "线程异常（{}）".format(args.thread.name if args.thread else "?"),
                (args.exc_type, args.exc_value, args.exc_traceback))
        except Exception:
            pass

    try:
        threading.excepthook = _thread_hook
    except AttributeError:
        pass
    try:
        qInstallMessageHandler(_qt_message_handler)
    except Exception:
        pass


def main():
    _install_hooks()
    _install_fault_handlers()
    app = QApplication(sys.argv)
    app.setApplicationName("TestManagement")
    app.setStyle("Fusion")
    # 正常退出(QMessageBox 确认后)清除标记；任何崩溃/异常退出都会留下标记，
    # 下次启动时弹窗告知“上次运行异常退出”，绝不静默闪退。
    prev_crash = _read_crash_marker()
    _write_crash_marker()
    app.aboutToQuit.connect(_clear_crash_marker)
    try:
        win = bootstrap(app)
    except Exception as e:
        syslog.exception("启动失败")
        _clear_crash_marker()
        QMessageBox.critical(None, "错误", "程序启动失败：{}\n\n详情请查看 data/logs/ 下的系统日志".format(e))
        sys.exit(1)
    if win is None:
        sys.exit(0)
    win.show()
    if prev_crash:
        QMessageBox.warning(
            win, "上次运行异常退出",
            "检测到上次程序非正常退出（时间：{}）。\n\n"
            "请检查 data/logs/ 目录下的系统日志与 crash_*.txt 文件，确认原因后再继续使用。".format(prev_crash))
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()