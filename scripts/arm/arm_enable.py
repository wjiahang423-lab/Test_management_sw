# -*- coding: utf-8 -*-
"""机械臂全部关节使能（后台 SSH 会话）。

启动一条独立的交互式 SSH shell，在主站预检完成后循环下发
``cia402_tools -s <joint> -m <enable_mode>`` 到 J1..J14。命令完成后
shell 会话保持打开，测试过程中不会被测试用例结束/超时打断；测试结束
或人工中断时由引擎清理回调 ``stop_enable_all`` 发送 Ctrl+C 并关闭。
"""

import atexit
import os
import select
import socket
import sys
import threading
import time
import uuid

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


def _log(message):
    try:
        test_api.log(str(message))
    except Exception:
        print(message)


def _cfg():
    jog = INTERFACE.get("jog") if isinstance(INTERFACE, dict) else None
    jog = jog or {}
    dev = INTERFACE.get("device") if isinstance(INTERFACE, dict) else None
    dev = dev or {}
    # 优先使用实际 EtherCAT 从站位置列表；现场链首有 2 个 FCE1100，
    # 不能直接用关节编号 1..14 当从站位置。
    joints = (jog.get("enable_slaves") or jog.get("joints")
              or list(range(1, 15)))
    return {
        "host": dev.get("host", "192.168.88.20"),
        "port": int(dev.get("port", 22)),
        "user": dev.get("user", "zioneer"),
        "password": str(dev.get("password", "123")),
        "connect_timeout": float(dev.get("connect_timeout", 6.0)),
        "enter_dir": jog.get("enter_dir", "/opt/zioneer_etherkit"),
        "cia402_tools": jog.get("cia402_tools", "/opt/zioneer_etherkit/bin/cia402_tools"),
        "sudo_password": str(jog.get("sudo_password", dev.get("sudo_password", "123"))),
        "enable_mode": int(jog.get("enable_mode", 8)),
        "enable_template": str((jog.get("commands") or {}).get("enable", "")),
        "joints": [int(j) for j in joints],
        "timeout": float(jog.get("enable_timeout_s", 120.0)),
    }


_lock = threading.RLock()
_session = None
_atexit_registered = False


class _EnableSession(object):
    def __init__(self, config):
        self.config = config
        self.client = None
        self.shell = None
        self._running = False
        self._reader = None
        self._lock = threading.RLock()
        self._output = []
        self._done = threading.Event()

    @property
    def is_running(self):
        return bool(self._running and self.shell is not None and self.shell.active)

    def _text(self):
        with self._lock:
            return "".join(self._output)

    def start(self):
        cfg = self.config
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=cfg["host"], port=cfg["port"], username=cfg["user"],
            password=cfg["password"], timeout=cfg["connect_timeout"],
            banner_timeout=cfg["connect_timeout"], auth_timeout=cfg["connect_timeout"],
            look_for_keys=False, allow_agent=False,
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
        self._reader = threading.Thread(
            target=self._read_loop, name="arm-enable-reader", daemon=True
        )
        self._reader.start()

        # 关闭终端回显，避免命令和 sudo 密码回显到日志；完成标记用动态 MARK 变量判断。
        with self._lock:
            self.shell.send(b"stty -echo\n")
        time.sleep(0.3)
        token = "ENABLE_" + uuid.uuid4().hex[:12]
        with self._lock:
            self._output.clear()
            self.shell.send(("MARK=%s\n" % token).encode("utf-8"))
            self.shell.send(("cd %s\n" % cfg["enter_dir"]).encode("utf-8"))
        time.sleep(0.3)

        command = self._build_command()
        _log("[使能] 下发全部关节使能命令")
        with self._lock:
            self.shell.send((command + "\n").encode("utf-8"))

        done_marker = token + "_DONE"
        fail_marker = token + "_FAIL"
        deadline = time.time() + float(cfg["timeout"])
        while time.time() < deadline:
            text = self._text()
            if done_marker in text:
                _log("[使能] 14 轴使能命令执行完成，后台会话保持")
                return
            if fail_marker in text:
                raise RuntimeError("部分关节使能失败：\n%s" % self._tail())
            if not self.is_running:
                raise RuntimeError("使能 SSH 会话已断开：\n%s" % self._tail())
            time.sleep(0.1)
        raise TimeoutError("使能命令超时（%.0fs）\n%s" % (cfg["timeout"], self._tail()))

    def _build_command(self):
        cfg = self.config
        joints = " ".join(str(j) for j in cfg["joints"])
        template = cfg.get("enable_template") or ""
        if template:
            one = template.format(
                enter_dir=cfg["enter_dir"],
                sudo_password=cfg["sudo_password"],
                cia402_tools=cfg["cia402_tools"],
                joint="$j",
                enable_mode=cfg["enable_mode"],
            )
        else:
            pw = cfg["sudo_password"].replace("'", "'\\''")
            one = ("echo '{pw}' | sudo -S {tool} -s $j -m {mode}").format(
                pw=pw, tool=cfg["cia402_tools"], mode=cfg["enable_mode"])
        return (
            "for j in {js}; do {one} || echo ${{MARK}}_FAIL_$j; "
            "done; echo ${{MARK}}_DONE"
        ).format(js=joints, one=one)

    def _read_loop(self):
        while self._running and self.shell is not None:
            try:
                if not select.select([self.shell], [], [], 0.05)[0]:
                    continue
                data = self.shell.recv(65536)
                if not data:
                    break
                text = data.decode("utf-8", "replace")
                with self._lock:
                    self._output.append(text)
                    if len(self._output) > 500:
                        del self._output[:-500]
                _log(text.replace(str(self.config.get("sudo_password", "")), "***").rstrip())
            except socket.timeout:
                continue
            except Exception:
                break
        self._running = False

    def _tail(self, n=1200):
        return self._text()[-n:]

    def stop(self):
        self._running = False
        with self._lock:
            shell = self.shell
        if shell is not None:
            try:
                shell.send(b"\x03\x03")
                time.sleep(0.05)
                shell.send(b"exit\n")
            except Exception:
                pass
        reader = self._reader
        if reader is not None and reader.is_alive():
            reader.join(0.3)
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
        _log("[使能] 后台会话已关闭")


def start_enable_all():
    """后台使能全部关节；重复调用时复用已有会话。返回使能关节数。"""
    global _session, _atexit_registered
    with _lock:
        if _session is not None and _session.is_running:
            _log("[使能] 后台会话已在运行，跳过重复使能")
            return len(_cfg()["joints"])
        old = _session
        _session = None
    if old is not None:
        try:
            old.stop()
        except Exception:
            pass
    session = _EnableSession(_cfg())
    with _lock:
        _session = session
        if not _atexit_registered:
            atexit.register(stop_enable_all)
            _atexit_registered = True
    # 先注册清理回调，确保使能过程中按停止/异常也能关闭后台会话。
    try:
        test_api.register_cleanup(stop_enable_all)
    except Exception:
        pass
    try:
        session.start()
    except Exception:
        stop_enable_all()
        raise
    return len(_cfg()["joints"])


def stop_enable_all():
    """关闭后台使能会话；可重复调用。"""
    global _session
    with _lock:
        session = _session
        _session = None
    if session is not None:
        session.stop()


def enable_is_running():
    with _lock:
        return bool(_session is not None and _session.is_running)
