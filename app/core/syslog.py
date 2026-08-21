"""System-wide error / diagnostic logging.

Unlike the per-run RunLogger (which captures test-execution detail), this
module records application-level problems — unexpected exceptions, failed
loads/saves, script loading errors — into a persistent daily file under
data/logs/.  It is always safe to call from any thread.
"""
import datetime
import os
import threading
import traceback

from .paths import LOGS_DIR

_lock = threading.RLock()
_file = None


def _today_path():
    stamp = datetime.datetime.now().strftime("%Y%m%d")
    return os.path.join(LOGS_DIR, "system_{}.log".format(stamp))


def _ensure():
    global _file
    if _file is not None:
        return
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
        _file = open(_today_path(), "a", encoding="utf-8")
    except Exception:
        _file = None


def _write(level, msg):
    global _file
    with _lock:
        _ensure()
        if _file is None:
            return
        try:
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for line in str(msg).splitlines() or [""]:
                _file.write("[{}] [{}] {}\n".format(ts, level, line))
            _file.flush()
        except Exception:
            try:
                _file.close()
            except Exception:
                pass
            _file = None


def info(msg):
    _write("INFO", msg)


def warn(msg):
    _write("WARN", msg)


def error(msg):
    _write("ERROR", msg)


def exception(tag="", exc=None):
    """Log an exception with full traceback.  exc may be an exception tuple
    or instance; defaults to the currently handled exception."""
    if exc is None:
        lines = traceback.format_exc()
    elif isinstance(exc, tuple):
        lines = "".join(traceback.format_exception(*exc))
    else:
        lines = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    if tag:
        _write("ERROR", "{}：\n{}".format(tag, lines.rstrip()))
    else:
        _write("ERROR", lines.rstrip())


def log_exception_hook(exc_type, exc_value, exc_tb):
    """sys.excepthook-compatible: never crash, log everything."""
    if issubclass(exc_type, KeyboardInterrupt):
        _write("INFO", "程序被用户中断（Ctrl+C）")
        return
    lines = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    _write("ERROR", "未捕获异常（主线程）：\n{}".format(lines.rstrip()))
