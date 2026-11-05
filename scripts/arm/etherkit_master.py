"""EtherCAT 主站启停。

start_master() 作为 Action 用例调用：在后台 SSH 交互 shell 中启动
`zioneer_etherkit -n QUALITY_INSPECTION.xml`，并注册测试结束清理回调；
stop_master() 在整个测试计划结束时发送 Ctrl+C 并关闭 SSH。
"""

import atexit
import os
import sys
import threading
import time
from string import Template

import paramiko

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from arm_driver import INTERFACE
except Exception:
    INTERFACE = {}

try:
    import test_api
except ImportError:
    class _StubApi(object):
        @staticmethod
        def log(msg):
            print(msg)

        @staticmethod
        def register_cleanup(callback):
            return False

    test_api = _StubApi()


_DEFAULT_MASTER = {
    "host": "192.168.88.20",
    "port": 22,
    "user": "zioneer",
    "password": "123",
    "connect_timeout": 6.0,
    "sudo_password": "123",
    "etherkit_bin": "/opt/zioneer_etherkit/bin/zioneer_etherkit",
    "precheck_config": "/opt/zioneer_etherkit/etc/etherkit/QUALITY_INSPECTION.xml",
    "command": "sudo ${etherkit_bin} -n ${precheck_config}",
    "password_keywords": ["password", "[sudo]"],
    "error_keywords": [
        "command not found",
        "no such file",
        "permission denied",
        "incorrect password",
        "sorry, try again",
        "sudo:",
    ],
    "start_timeout_s": 20.0,
    "settle_s": 3.0,
    "check_command": "pgrep -af '[z]ioneer_etherkit'",
}


def _load_master_config():
    raw = INTERFACE.get("master") if isinstance(INTERFACE, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    config = dict(_DEFAULT_MASTER)
    config.update(raw)
    for key in ("password_keywords", "error_keywords"):
        if isinstance(raw.get(key), list):
            config[key] = list(raw[key])
    return config


MASTER = _load_master_config()
_lock = threading.RLock()
_session = None
_stop_event = threading.Event()
_atexit_registered = False


def _log(message):
    try:
        test_api.log(str(message))
    except Exception:
        print(message)


class _MasterSession(object):
    def __init__(self, config):
        self.config = config
        self.client = None
        self.shell = None
        self._reader = None
        self._running = False
        self._lock = threading.RLock()
        self._output = []

    @property
    def is_running(self):
        """会话标记为运行还不够，必须确认远端 zioneer_etherkit 进程仍在。"""
        if not self._running or self.shell is None:
            return False
        try:
            if not self.shell.active:
                return False
        except Exception:
            return False
        return self._process_alive()

    def start(self):
        timeout = float(self.config.get("connect_timeout", 6.0))
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
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
        try:
            shell = client.invoke_shell()
        except Exception:
            client.close()
            raise
        shell.settimeout(0.05)
        self.client = client
        self.shell = shell
        self._running = True
        command = Template(str(self.config.get("command", ""))).safe_substitute(self.config)
        _log("[主站/预检] 下发命令：{}".format(command))
        shell.send((command + "\n").encode("utf-8"))
        try:
            self._wait_started()
        except Exception:
            self._close()
            raise
        self._reader = threading.Thread(
            target=self._read_loop, name="etherkit-master-reader", daemon=True
        )
        self._reader.start()
        _log("[主站] 启动完成，后台运行中")

    def _wait_started(self):
        start = time.time()
        settle = float(self.config.get("settle_s", 3.0))
        timeout = float(self.config.get("start_timeout_s", 20.0))
        keywords = [str(k).lower() for k in self.config.get("password_keywords", [])]
        errors = [str(k).lower() for k in self.config.get("error_keywords", [])]
        password = str(self.config.get("sudo_password", ""))
        password_sent = False
        buffer = ""
        last_check = 0.0
        while time.time() - start < timeout:
            now = time.time()
            text = self._drain()
            if text:
                buffer += text
                lower = buffer.lower()
                if not password_sent and any(k in lower for k in keywords):
                    _log("[主站] 检测到密码提示，发送 sudo 密码")
                    with self._lock:
                        self.shell.send((password + "\n").encode("utf-8"))
                    password_sent = True
                    buffer = ""
                for keyword in errors:
                    if keyword in lower:
                        raise RuntimeError(
                            "主站启动失败：{}".format(buffer[-500:].strip())
                        )
            if now - start >= settle and now - last_check >= 1.0:
                last_check = now
                if self._process_alive():
                    return
            time.sleep(0.2)
        if self._process_alive():
            return
        raise RuntimeError(
            "主站进程未启动：{}".format(buffer[-500:].strip() or "无输出")
        )

    def _drain(self):
        chunks = []
        with self._lock:
            if self.shell is None:
                return ""
            while self.shell.recv_ready():
                data = self.shell.recv(65536)
                if not data:
                    break
                chunks.append(data)
        text = b"".join(chunks).decode("utf-8", "replace")
        if text:
            with self._lock:
                self._output.append(text)
                if len(self._output) > 200:
                    del self._output[:-200]
            _log(text.replace(str(self.config.get("sudo_password", "")), "***").rstrip())
        return text

    def _process_alive(self):
        command = str(self.config.get("check_command", "")).strip()
        if not command or self.client is None:
            return True
        try:
            _, stdout, stderr = self.client.exec_command(command, timeout=8)
            out = stdout.read().decode("utf-8", "replace").strip()
            err = stderr.read().decode("utf-8", "replace").strip()
            if out:
                _log("[主站] 进程检查：{}".format(out.splitlines()[0]))
                return True
            if err:
                _log("[主站] 进程检查 stderr：{}".format(err))
                if "not found" in err.lower():
                    return True
            return False
        except Exception as exc:
            _log("[主站] 进程检查异常：{}".format(exc))
            return False

    def _read_loop(self):
        while self._running:
            try:
                if not self._drain():
                    time.sleep(0.05)
            except Exception:
                break

    def stop(self):
        with self._lock:
            if not self._running and self.shell is None:
                return
            self._running = False
            shell = self.shell
        if shell is not None:
            try:
                shell.send(b"\x03")
                time.sleep(0.2)
                shell.send(b"\x03")
                time.sleep(0.2)
                shell.send(b"kill %1 2>/dev/null\n")
                time.sleep(0.2)
                shell.send(b"exit\n")
            except Exception:
                pass
        self._close()
        _log("[主站] 已发送 Ctrl+C 并关闭 SSH")

    def _close(self):
        self._running = False
        shell = self.shell
        client = self.client
        self.shell = None
        self.client = None
        try:
            if shell is not None:
                shell.close()
        except Exception:
            pass
        try:
            if client is not None:
                client.close()
        except Exception:
            pass


def start_master():
    """启动主站并保持后台运行；测试结束时由引擎清理回调关闭。"""
    global _session, _atexit_registered
    with _lock:
        if _session is not None and _session.is_running:
            _log("[主站] 已在运行，跳过重复启动")
            return 1
        if _session is not None:
            _log("[主站] 原会话已失效（进程不存在或 SSH 已断），重新启动")
        old_session = _session
        _session = None
        _stop_event.clear()
    if old_session is not None:
        try:
            old_session.stop()
        except Exception:
            pass
    session = _MasterSession(MASTER)
    with _lock:
        if not _atexit_registered:
            atexit.register(stop_master)
            _atexit_registered = True
    # 先注册清理回调，启动过程中按停止也能关闭后台会话。
    try:
        test_api.register_cleanup(stop_master)
    except Exception:
        pass
    session.start()
    with _lock:
        if _stop_event.is_set():
            session.stop()
            raise RuntimeError("测试已停止，取消启动主站")
        _session = session
    return 1


def stop_master():
    """发送 Ctrl+C 并关闭主站 SSH；可重复调用。"""
    global _session
    _stop_event.set()
    with _lock:
        session = _session
        _session = None
    if session is not None:
        session.stop()


def master_is_running():
    with _lock:
        return bool(_session is not None and _session.is_running)
