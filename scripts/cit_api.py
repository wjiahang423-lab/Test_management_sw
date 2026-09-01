# -*- coding: utf-8 -*-
"""CIT 交互通讯协议 客户端测试脚本（协议 v1.0，端口 8090）。

端点：
  POST /api/request   {cmd: list|describe|run, id, params}
  GET  /api/sysinfo

响应：{code, msg, data}；run 的 data.result = PASS / FAIL / ERROR。
默认服务地址 http://10.5.35.49:8090（树莓派 CIT 模拟服务），
真板子测试时把默认地址或每个用例的 url 改成板卡 IP:8090。
"""
import os

import test_api
import requests

DEFAULT_URL = os.environ.get("EOL_CIT_URL", "http://192.168.88.10:8090")


def _base(params):
    if isinstance(params, dict) and params.get("url"):
        return str(params["url"]).rstrip("/")
    return DEFAULT_URL


def _post(params, cmd, id_=None, override=None):
    base = _base(params)
    body = {"cmd": cmd}
    if id_:
        body["id"] = id_
    if override:
        body["params"] = override
    timeout = float(params.get("timeout", 10)) if isinstance(params, dict) else 10
    try:
        r = requests.post(base + "/api/request", json=body, timeout=timeout)
        try:
            return True, r.json()
        except Exception:
            r.raise_for_status()
            return False, "响应非JSON: {}".format(r.text[:200])
    except Exception as e:
        return False, "{}: {}".format(type(e).__name__, e)


def _fail(item_name, reason, code=None, msg=None):
    test_api.log("{} -> FAIL: {}".format(item_name, reason))
    test_api.set_measure_message(reason)
    test_api.set_measure_field("code", code)
    test_api.set_measure_field("msg", msg)
    return {"value": None, "result": None, "expected": None, "pass": False,
            "message": reason, "code": code, "msg": msg, "reason": reason}


# ============================ 动作 ============================
def cit_ping(params=None):
    """动作：下发 list 命令验证连通性，仅执行不判定。"""
    ok, resp = _post(params, "list")
    test_api.log("cit_ping -> {}".format("ok" if ok else resp))
    return ok


def cit_sysinfo(params=None):
    """动作/测量：GET /api/sysinfo 返回板卡系统信息。"""
    base = _base(params)
    try:
        r = requests.get(base + "/api/sysinfo", timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        test_api.log("cit_sysinfo 失败: {}".format(e))
        return {"code": -1, "msg": str(e)}


# ============================ 测量 ============================
def cit_list(params=None):
    """测量：list — 返回测试项数量与分组信息。"""
    ok, resp = _post(params, "list")
    if not ok:
        return {"code": -1, "msg": resp, "count": 0, "items": []}
    data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
    groups = data.get("groups", []) or []
    items = [it for g in groups for it in (g.get("items") or [])]
    return {"code": resp.get("code"), "msg": resp.get("msg"),
            "count": len(items), "groups": groups, "items": items}


def cit_describe(params=None, id=None):
    """测量：describe — 查看单个测试项 {id, name, desc}。"""
    ok, resp = _post(params, "describe", id)
    if not ok:
        return {"code": -1, "msg": resp, "id": id, "name": "", "desc": ""}
    data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
    return {"code": resp.get("code"), "msg": resp.get("msg"),
            "id": data.get("id"), "name": data.get("name"), "desc": data.get("desc")}


def cit_run(params=None, id=None, override=None):
    """测量：run — 执行单个测试项，返回 result/summary/duration_ms 等。"""
    ok, resp = _post(params, "run", id, override)
    if not ok:
        return {"code": -1, "msg": resp, "result": "ERROR", "summary": resp,
                "duration_ms": 0, "id": id}
    if resp.get("code") != 0:
        return {"code": resp.get("code"), "msg": resp.get("msg"),
                "result": None, "summary": resp.get("msg"),
                "duration_ms": None, "id": id}
    data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
    return {"code": resp.get("code"), "msg": resp.get("msg"),
            "id": data.get("id"), "name": data.get("name"),
            "result": data.get("result"), "summary": data.get("summary"),
            "duration_ms": data.get("duration_ms"),
            "run_data": data.get("data"), "logs": data.get("logs"),
            "error": data.get("error")}


def cit_unknown_cmd(params=None, cmd="bogus"):
    """测量：非法 cmd，期望返回 1001。"""
    ok, resp = _post(params, cmd)
    if not ok:
        return {"code": -1, "msg": resp}
    return {"code": resp.get("code"), "msg": resp.get("msg")}


# ============================ Loop ============================
def cit_loop_run(params):
    """Loop：run 测试项，result 与期望值(默认PASS)比对判定。"""
    item_name = str(params.get("name", " "))
    id_ = params.get("id", "")
    expected = params.get("expected", "PASS")
    ok, resp = _post(params, "run", id_, params.get("override"))
    if not ok:
        return _fail(item_name, "请求失败/超时: {}".format(resp))
    if resp.get("code") != 0:
        return _fail(item_name, "code={} msg={}".format(resp.get("code"), resp.get("msg")),
                     resp.get("code"), resp.get("msg"))
    data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
    result = data.get("result")
    summary = data.get("summary")
    passed = (result == expected)
    message = "{}: id={} result={} 期望={} summary={}".format(
        item_name, id_, result, expected, summary)
    test_api.set_measure_value(result, unit="")
    test_api.set_measure_expected(expected)
    test_api.set_measure_message(message)
    test_api.set_measure_field("code", resp.get("code"))
    test_api.set_measure_field("msg", resp.get("msg"))
    test_api.set_measure_field("result", result)
    test_api.set_measure_field("summary", summary)
    test_api.set_measure_field("duration_ms", data.get("duration_ms"))
    return {"value": result, "result": result, "expected": expected,
            "summary": summary, "duration_ms": data.get("duration_ms"),
            "code": resp.get("code"), "msg": resp.get("msg"),
            "pass": passed, "message": message}


def cit_loop_describe(params):
    """Loop：describe 测试项，校验 name/desc 非空。"""
    item_name = str(params.get("name", " "))
    id_ = params.get("id", "")
    ok, resp = _post(params, "describe", id_)
    if not ok:
        return _fail(item_name, "请求失败: {}".format(resp))
    if resp.get("code") != 0:
        return _fail(item_name, "code={} msg={}".format(resp.get("code"), resp.get("msg")),
                     resp.get("code"), resp.get("msg"))
    data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
    dname = data.get("name", "")
    desc = data.get("desc", "")
    passed = bool(dname) and bool(desc)
    message = "{}: describe {} -> {} {}".format(item_name, id_, dname, desc)
    test_api.set_measure_value(dname, unit="")
    test_api.set_measure_expected(params.get("expected"))
    test_api.set_measure_message(message)
    test_api.set_measure_field("code", resp.get("code"))
    test_api.set_measure_field("msg", resp.get("msg"))
    return {"value": dname, "pass": passed, "message": message,
            "code": resp.get("code"), "msg": resp.get("msg")}