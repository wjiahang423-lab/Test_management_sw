# -*- coding: utf-8 -*-
"""腰部升降柱 · Telnet 控制（192.168.88.30:23）。

指令格式：bollardmove <位值> <速度>
位值 0~2000，速度 0~200。上升测试用 bollardmove 80 80，回零用
bollardmove 0 80；结束后由计划中的 Pop 页面人工确认是否回零到位。
"""

import os
import socket
import time

try:
    import test_api
except ImportError:
    class _StubApi(object):
        @staticmethod
        def log(msg):
            print(msg)

        @staticmethod
        def set_display_info(key, value):
            pass

    test_api = _StubApi()


HOST = os.environ.get("LIFTER_HOST", "192.168.88.30")
PORT = int(os.environ.get("LIFTER_PORT", "23"))
POSITION_RANGE = (0, 2000)
SPEED_RANGE = (0, 200)


def _log(message):
    try:
        test_api.log(str(message))
    except Exception:
        print(message)


def _display(key, value):
    try:
        test_api.set_display_info(key, str(value))
    except Exception:
        pass


def _check_range(name, value, low, high):
    number = int(value)
    if number < low or number > high:
        raise ValueError("%s=%d 超出允许范围 %d~%d" % (name, number, low, high))
    return number


def _send_command(command, connect_timeout=5.0, wait_s=10.0):
    sock = None
    received = ""
    try:
        sock = socket.create_connection((HOST, PORT), timeout=connect_timeout)
        sock.settimeout(0.2)
        try:
            sock.recv(4096)
        except socket.timeout:
            pass
        sock.sendall((command + "\n").encode("utf-8"))
        deadline = time.time() + max(0.0, float(wait_s))
        while time.time() < deadline:
            try:
                data = sock.recv(4096)
            except socket.timeout:
                continue
            if not data:
                break
            received += data.decode("utf-8", "replace")
        return received
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


def lifter_move(position=80, speed=80, wait_s=10.0, move_timeout=60.0):
    position = _check_range("位值", position, *POSITION_RANGE)
    speed = _check_range("速度", speed, *SPEED_RANGE)
    if speed == 0:
        raise ValueError("速度不能为 0")
    command = "bollardmove %d %d" % (position, speed)
    wait = min(max(0.0, float(wait_s)), max(0.0, float(move_timeout)))
    _log("[升降柱] 连接 %s:%d，下发：%s，保持连接 %.1fs" % (HOST, PORT, command, wait))
    received = _send_command(command, wait_s=wait)
    if received.strip():
        _log("[升降柱] 返回：%s" % received.strip()[:500])
    _log("[升降柱] 指令完成，等待 %.1fs" % wait)
    _display("升降柱位值", position)
    return {"sent": 1, "position": position, "speed": speed}


def lifter_up(position=80, speed=80, wait_s=10.0, move_timeout=60.0):
    return lifter_move(position=position, speed=speed,
                       wait_s=wait_s, move_timeout=move_timeout)


def lifter_home(speed=80, wait_s=10.0, move_timeout=60.0):
    return lifter_move(position=0, speed=speed,
                       wait_s=wait_s, move_timeout=move_timeout)
