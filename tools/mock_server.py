#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地 mock 服务：离线模拟树莓派测试服务与 CIT 协议服务，用于全功能/稳定性测试。

生产环境对应：
  - auto 模式（默认，端口 5000）：模拟 http://10.5.35.49:5000
      /status  /api/key  /api/robot/status  /api/voltage  /api/current
      /api/temperature  /api/battery  /api/sum  /api/list  /api/dict
      /api/str  /api/bool  /api/range  /api/slow  /api/timeout
      /api/error  /api/missing  /api/request  /submit  /upload
  - cit 模式（端口 8090）：模拟 http://192.168.88.10:8090（CIT 协议 v1.0）
      POST /api/request  {cmd: list|describe|run, id, params}
      GET  /api/sysinfo  /submit

用法：
    python3 tools/mock_server.py --port 5000 --mode auto
    python3 tools/mock_server.py --port 8090 --mode cit
    # 同时跑两套：
    python3 tools/mock_server.py --port 5000 --mode auto &
    python3 tools/mock_server.py --port 8090 --mode cit &

/submit 上报报文按 analyze_submitted.py 的格式追加到 --submit-log 指定的文件。
"""
import argparse
import datetime
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# 默认 mock 数值：与「全功能自动测试.plan」的判定阈值对齐，
# 使正常用例 PASS，标有 (FAIL)/(可能FAIL)/(应FAIL) 的用例按预期 FAIL。
AUTO_VALUES = {
    "voltage": {"value": 12.0, "unit": "V"},
    "current": {"value": 0.8, "unit": "A"},
    "temperature": {"value": 25.0, "unit": "℃"},
    "battery": {"value": 87},
    "list": [0, 42, 20, 30, 5],
    "dict": {"nested": {"a": 1, "b": "x"}, "items": [1, 2, 3]},
    "str": "running",
    "bool": True,
}

CIT_ITEMS = {
    "hw.cpu_cores": {"name": "CPU核心数", "desc": "CPU 核心数量", "result": "PASS", "summary": "8 核心"},
    "hw.memory": {"name": "内存容量", "desc": "内存容量信息", "result": "PASS", "summary": "8192 MB"},
    "temp.soc": {"name": "SoC温度", "desc": "SoC 温度", "result": "PASS", "summary": "45 摄氏度"},
    "net.eth0": {"name": "以太网", "desc": "以太网接口", "result": "PASS", "summary": "link up"},
    "ver.all": {"name": "整机版本", "desc": "整机版本信息", "result": "PASS", "summary": "整机版本 1.0.0"},
    "ver.bsp": {"name": "BSP版本", "desc": "BSP 版本信息", "result": "PASS", "summary": "BSP 2.0.1"},
    "node.cit": {"name": "CIT节点", "desc": "CIT 节点信息", "result": "PASS", "summary": "cit node ok"},
    "temp.fan": {"name": "风扇", "desc": "风扇状态", "result": "FAIL", "summary": "风扇转速异常"},
    "ver.old": {"name": "旧版兼容", "desc": "旧版本兼容", "result": "FAIL", "summary": "旧版本不支持"},
    "module.unregistered": {"name": "未注册模块", "desc": "未注册模块", "result": "ERROR", "summary": "模块未注册"},
}

_submit_lock = threading.Lock()


def _json_response(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _ok(handler, payload):
    _json_response(handler, 200, payload)


def _log_submit(server, payload):
    path = server.submit_log
    if not path:
        return
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "[{}] {}\n".format(ts, json.dumps(payload, ensure_ascii=False))
    try:
        with _submit_lock:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass


class MockHandler(BaseHTTPRequestHandler):
    server_version = "EOLMock/1.0"

    def log_message(self, fmt, *args):
        pass  # 静默访问日志，避免刷屏

    # ---------- 通用 ----------
    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length <= 0:
                return b""
            return self.rfile.read(length)
        except Exception:
            return b""

    def _json_body(self):
        raw = self._read_body()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _query(self):
        return parse_qs(urlparse(self.path).query)

    def _mode(self):
        return getattr(self.server, "mode", "auto")

    # ---------- 路由 ----------
    def do_GET(self):
        mode = self._mode()
        path = urlparse(self.path).path
        q = self._query()
        try:
            if mode == "cit":
                self._get_cit(path, q)
            else:
                self._get_auto(path, q)
        except BrokenPipeError:
            pass
        except Exception:
            _ok(self, {"code": -1, "msg": "mock internal error"})

    def do_POST(self):
        mode = self._mode()
        path = urlparse(self.path).path
        body = self._json_body()
        try:
            if path == "/submit":
                _log_submit(self.server, body)
                _ok(self, {"code": 0, "message": "ok"})
            elif path == "/upload":
                _ok(self, {"code": 0, "message": "ok"})
            elif mode == "cit":
                self._post_cit(path, body)
            else:
                self._post_auto(path, body)
        except BrokenPipeError:
            pass
        except Exception:
            _ok(self, {"code": -1, "msg": "mock internal error"})

    # ---------- auto GET ----------
    def _get_auto(self, path, q):
        if path == "/status":
            _ok(self, {"code": 0, "msg": "ok", "data": {"status": "running"}})
        elif path == "/api/key":
            sn = q.get("sn", [""])[0]
            _ok(self, {"code": 0, "msg": "ok", "data": {"key": "SECRET-KEY-{}".format(sn or "000")}})
        elif path == "/api/robot/status":
            _ok(self, {"code": 0, "msg": "ok",
                       "data": {"temperature": 25.5, "current": 1.2, "voltage": 12.3, "battery": 87}})
        elif path == "/api/voltage":
            _ok(self, {"code": 0, "msg": "ok", "data": AUTO_VALUES["voltage"]})
        elif path == "/api/current":
            _ok(self, {"code": 0, "msg": "ok", "data": AUTO_VALUES["current"]})
        elif path == "/api/temperature":
            _ok(self, {"code": 0, "msg": "ok", "data": AUTO_VALUES["temperature"]})
        elif path == "/api/battery":
            _ok(self, {"code": 0, "msg": "ok", "data": AUTO_VALUES["battery"]})
        elif path == "/api/sum":
            try:
                a = float(q.get("a", ["0"])[0])
                b = float(q.get("b", ["0"])[0])
            except ValueError:
                a = b = 0.0
            _ok(self, {"code": 0, "msg": "ok", "data": {"sum": a + b}})
        elif path == "/api/list":
            _ok(self, {"code": 0, "msg": "ok", "data": AUTO_VALUES["list"]})
        elif path == "/api/dict":
            _ok(self, {"code": 0, "msg": "ok", "data": AUTO_VALUES["dict"]})
        elif path == "/api/str":
            _ok(self, {"code": 0, "msg": "ok", "data": AUTO_VALUES["str"]})
        elif path == "/api/bool":
            _ok(self, {"code": 0, "msg": "ok", "data": AUTO_VALUES["bool"]})
        elif path == "/api/range":
            try:
                lo = float(q.get("lo", ["0"])[0])
                hi = float(q.get("hi", ["100"])[0])
            except ValueError:
                lo, hi = 0.0, 100.0
            _ok(self, {"code": 0, "msg": "ok", "data": {"value": (lo + hi) / 2.0}})
        elif path == "/api/slow":
            try:
                t = float(q.get("t", ["0"])[0])
            except ValueError:
                t = 0.0
            time.sleep(max(0.0, t))
            _ok(self, {"code": 0, "msg": "ok", "data": {"slept": t}})
        elif path == "/api/timeout":
            # 模拟永远不返回的接口，客户端按自身 timeout 超时
            time.sleep(10)
            _ok(self, {"code": 0, "msg": "ok", "data": {}})
        elif path == "/api/error":
            _json_response(self, 500, {"code": 500, "msg": "internal server error"})
        elif path == "/api/missing":
            _json_response(self, 404, {"code": 404, "msg": "not found"})
        else:
            _json_response(self, 404, {"code": 404, "msg": "unknown path {}".format(path)})

    # ---------- auto POST ----------
    def _post_auto(self, path, body):
        if path == "/api/request":
            # loop_cmd：任意 cmd 都返回 code=0 msg=ok（expected 逐键判定）
            _ok(self, {"code": 0, "msg": "ok", "data": {"echo": body.get("cmd", "")}})
        else:
            _json_response(self, 404, {"code": 404, "msg": "unknown path {}".format(path)})

    # ---------- cit ----------
    def _get_cit(self, path, q):
        if path == "/api/sysinfo":
            _ok(self, {"cpu_cores": 8, "mem_total_kb": 8388608, "os": "linux",
                       "hostname": "cit-board", "uptime_s": 123456})
        elif path == "/submit":
            _ok(self, {"code": 0, "message": "ok"})
        else:
            _json_response(self, 404, {"code": 404, "msg": "unknown path {}".format(path)})

    def _post_cit(self, path, body):
        if path != "/api/request":
            _json_response(self, 404, {"code": 404, "msg": "unknown path {}".format(path)})
            return
        cmd = body.get("cmd", "")
        id_ = body.get("id", "")
        if cmd == "list":
            items = [{"id": k, "name": v["name"]} for k, v in CIT_ITEMS.items()]
            _ok(self, {"code": 0, "msg": "ok",
                       "data": {"groups": [{"name": "默认分组", "items": items}]}})
        elif cmd == "describe":
            item = CIT_ITEMS.get(id_)
            if item is None:
                _json_response(self, 404, {"code": 1002, "msg": "未知测试项 {}".format(id_)})
            else:
                _ok(self, {"code": 0, "msg": "ok",
                           "data": {"id": id_, "name": item["name"], "desc": item["desc"]}})
        elif cmd == "run":
            item = CIT_ITEMS.get(id_)
            if item is None:
                _ok(self, {"code": 0, "msg": "ok",
                           "data": {"id": id_, "name": id_, "result": "ERROR",
                                    "summary": "模块未注册", "duration_ms": 1,
                                    "data": None, "logs": [], "error": "unregistered"}})
                return
            result = item["result"]
            params = body.get("params") or {}
            if id_ == "node.cit" and params.get("node") == "/missing":
                result = "FAIL"
            _ok(self, {"code": 0, "msg": "ok",
                       "data": {"id": id_, "name": item["name"], "result": result,
                                "summary": item["summary"], "duration_ms": 5,
                                "data": None, "logs": [], "error": None}})
        else:
            _json_response(self, 400, {"code": 1001, "msg": "非法指令 {}".format(cmd)})


def main():
    ap = argparse.ArgumentParser(description="本地 mock 服务")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--mode", default="auto", choices=["auto", "cit"])
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--submit-log", default="data/logs/mock_submissions.log")
    args = ap.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), MockHandler)
    server.mode = args.mode
    server.submit_log = args.submit_log
    print("mock 服务已启动：mode={} http://{}:{}".format(args.mode, args.host, args.port))
    print("submit 上报日志：{}".format(args.submit_log))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n停止 mock 服务")
        server.shutdown()


if __name__ == "__main__":
    main()
