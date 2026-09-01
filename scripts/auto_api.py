# -*- coding: utf-8 -*-
"""全功能自动测试脚本：面向树莓派 mock 服务(10.5.35.49:5000)的测量/动作/接口函数。

覆盖动作、延时、人机交互(Pop 自动确认)、测量(各种判定)、Loop(接口/数值/错误/自描述)
等软件的全部用例类型与内置 API。
"""
import os

import test_api
import requests

MOCK_BASE = os.environ.get("EOL_MOCK_BASE", "http://10.5.35.49:5000")


def _req(path, method="GET", body=None, timeout=5):
    try:
        if method.upper() == "POST":
            r = requests.post(MOCK_BASE + path, json=body, timeout=timeout)
        else:
            r = requests.get(MOCK_BASE + path, timeout=timeout)
        r.raise_for_status()
        return True, r.json()
    except Exception as e:
        return False, "{}: {}".format(type(e).__name__, e)


def _result(value, unit="", expected=None, lower=None, upper=None,
            message="", passed=None, code=0, msg="ok"):
    if passed is None:
        passed = (value == expected)
    if not message:
        message = "值={} 期望={} 区间[{}, {}]".format(value, expected, lower, upper)
    test_api.set_measure_value(value, unit=unit)
    if expected is not None:
        test_api.set_measure_expected(expected)
    if lower is not None and upper is not None:
        test_api.set_measure_range(lower, upper)
    test_api.set_measure_message(message)
    test_api.set_measure_field("code", code)
    test_api.set_measure_field("msg", msg)
    return {"value": value, "unit": unit, "expected": expected,
            "lower": lower, "upper": upper, "message": message, "pass": passed}


def _fail(item_name, reason, code=None, msg=None):
    test_api.log("{} -> FAIL: {}".format(item_name, reason))
    test_api.set_measure_message(reason)
    test_api.set_measure_field("code", code)
    test_api.set_measure_field("msg", msg)
    return {"value": None, "expected": None, "lower": None, "upper": None,
            "message": reason, "pass": False, "code": code, "msg": msg, "reason": reason}


# ============================ 动作 ============================
def action_ping(params=None):
    """动作：请求 /status，仅执行不判定。"""
    ok, resp = _req("/status")
    test_api.log("ping -> {}".format("ok" if ok else resp))
    return ok


def action_api_get(params=None):
    """动作：GET 指定路径，仅执行。"""
    params = params or {}
    path = params.get("path", "/api/str")
    ok, resp = _req(path)
    test_api.log("api_get {} -> {}".format(path, "ok" if ok else resp))
    return ok


def scan_sn_auto(params=None):
    """动作：自动 SN（无弹窗），供无界面自动执行使用。"""
    params = params or {}
    sn = params.get("sn", "AUTOTEST-SN-001")
    test_api.set_sn_to_Panel(sn)
    return {"sn": sn, "success": True, "message": "SN: {}".format(sn)}


def fetch_upload_key(params=None):
    """动作：从 mock 服务获取密钥并回填，验证 set_upload_key 关联上报 key。"""
    params = params or {}
    sn = params.get("sn", test_api.get_sn() or "AUTOTEST")
    ok, resp = _req("/api/key?sn={}".format(sn))
    if ok:
        key = resp.get("data", {}).get("key")
        test_api.set_upload_key(key)
        test_api.log("已回填上报密钥: {}".format(key))
        return {"ok": True, "key": key}
    test_api.log("获取密钥失败: {}".format(resp))
    return {"ok": False}


def report_robot(params=None):
    """动作：读取 mock 机器人状态并刷新状态栏。"""
    ok, resp = _req("/api/robot/status")
    if ok and resp.get("code") == 0:
        d = resp["data"]
        test_api.set_robot_temperature(d.get("temperature"))
        test_api.set_robot_current(d.get("current"))
        test_api.set_robot_voltage(d.get("voltage"))
        test_api.set_robot_battery(d.get("battery"))
        return {"ok": True}
    return {"ok": False}


# ============================ 测量 ============================
def measure_json_value(params=None, path=None, field="value"):
    """测量：GET 接口，返回 data 里指定字段。

    兼容两种调用方式：
      - Measurement 关键字传参：measure_json_value(path=..., field=...)
      - Loop 整字典传参：measure_json_value({"path":..., "field":...})
    """
    if params and isinstance(params, dict):
        path = path or params.get("path", "/api/voltage")
        field = field or params.get("field", "value")
    path = path or "/api/voltage"
    ok, resp = _req(path, timeout=float((params or {}).get("timeout", 5)))
    if not ok:
        return _fail("measure", "请求失败: {}".format(resp))
    if resp.get("code") != 0:
        return _fail("measure", "code={} msg={}".format(resp.get("code"), resp.get("msg")))
    data = resp.get("data") or {}
    value = data.get(field) if isinstance(data, dict) else data
    unit = data.get("unit", "") if isinstance(data, dict) else ""
    return {"value": value, "unit": unit, "code": resp.get("code"),
            "msg": resp.get("msg"), "data": data}


def measure_sum(a=0, b=0):
    """测量：/api/sum?a&b 返回和。"""
    ok, resp = _req("/api/sum?a={}&b={}".format(a, b))
    if not ok:
        return 0
    return resp.get("data", {}).get("sum")


def measure_list(params=None):
    """测量：/api/list 返回列表（可判定 长度/包含/等于）。"""
    ok, resp = _req("/api/list")
    return resp.get("data") if ok else []


def measure_dict(params=None):
    """测量：/api/dict 返回字典。"""
    ok, resp = _req("/api/dict")
    return resp.get("data") if ok else {}


def measure_status(params=None):
    """测量：/api/str 返回状态字符串。"""
    ok, resp = _req("/api/str")
    return resp.get("data") if ok else ""


def measure_flag(params=None):
    """测量：/api/bool 返回布尔。"""
    ok, resp = _req("/api/bool")
    return bool(resp.get("data")) if ok else False


def measure_battery(params=None):
    """测量：/api/battery 返回整型电量。"""
    ok, resp = _req("/api/battery")
    return int(resp.get("data", {}).get("value", 0)) if ok else 0


def measure_timeout(params=None):
    """测量：请求 /api/timeout（必然超时，用于超时场景）。"""
    ok, resp = _req("/api/timeout", timeout=2)
    return resp if ok else 0


# ============================ Loop ============================
def loop_cmd(params):
    """Loop：POST /api/request 指令，按 code/msg 判定（expected 为字典时逐键判定）。"""
    item_name = str(params.get("name", " "))
    cmd = params.get("cmd", "list")
    id_ = params.get("id")
    ok, resp = _req("/api/request", method="POST",
                    body={"cmd": cmd, "id": id_} if id_ else {"cmd": cmd},
                    timeout=float(params.get("timeout", 5)))
    if not ok:
        return _fail(item_name, "接口超时/请求失败: {}".format(resp))
    if not isinstance(resp, dict):
        return _fail(item_name, "响应非JSON: {!r}".format(resp))
    code = resp.get("code")
    msg = resp.get("msg")
    expected_cfg = params.get("expected", {})
    expected = expected_cfg if isinstance(expected_cfg, dict) else {}
    exp_code = expected.get("code")
    exp_msg = expected.get("msg")
    if exp_code is not None and code != exp_code:
        return _fail(item_name, "code不匹配 期望{} 实际{}".format(exp_code, code), code, msg)
    if exp_msg is not None and str(msg) != str(exp_msg):
        return _fail(item_name, "msg不匹配 期望{} 实际{}".format(exp_msg, msg), code, msg)
    message = "{}: 匹配 code={} msg={}".format(item_name, code, msg)
    test_api.set_measure_value(msg, unit="cmd")
    test_api.set_measure_expected(msg)
    test_api.set_measure_message(message)
    test_api.set_measure_field("code", code)
    test_api.set_measure_field("msg", msg)
    return {"value": msg, "code": code, "msg": msg, "pass": True, "message": message}


def loop_value(params):
    """Loop：GET 数值接口，按 expected/upper/lower 判定（自描述）。"""
    item_name = str(params.get("name", " "))
    path = params.get("path", "/api/voltage")
    field = params.get("field", "value")
    ok, resp = _req(path, timeout=float(params.get("timeout", 5)))
    if not ok:
        return _fail(item_name, "请求失败: {}".format(resp))
    data = resp.get("data") or {}
    value = data.get(field) if isinstance(data, dict) else data
    if value is None and isinstance(data, dict):
        value = data.get("sum", data.get("value"))  # 兼容 /api/sum 等字段名
    unit = data.get("unit", "") if isinstance(data, dict) else ""
    expected = params.get("expected")
    lower = params.get("lower")
    upper = params.get("upper")
    passed = True
    reason = "{}: 值={} 期望={} 区间[{}, {}]".format(item_name, value, expected, lower, upper)
    try:
        v = float(value)
        if upper is not None and v > float(upper):
            passed = False
            reason += " -> 超上限"
        if lower is not None and v < float(lower):
            passed = False
            reason += " -> 低于下限"
    except (TypeError, ValueError):
        if expected is not None and value != expected:
            passed = False
            reason += " -> 不匹配"
    test_api.set_measure_value(value, unit=unit)
    if expected is not None:
        test_api.set_measure_expected(expected)
    if lower is not None and upper is not None:
        test_api.set_measure_range(lower, upper)
    test_api.set_measure_message(reason)
    test_api.set_measure_field("code", resp.get("code"))
    test_api.set_measure_field("msg", resp.get("msg"))
    return {"value": value, "unit": unit, "expected": expected,
            "lower": lower, "upper": upper, "message": reason, "pass": passed}


def loop_edge(params):
    """Loop：边界/异常场景（慢响应、超时、404、500），按 code==0 判定。"""
    item_name = str(params.get("name", " "))
    path = params.get("path", "/api/str")
    timeout = float(params.get("timeout", 5))
    ok, resp = _req(path, timeout=timeout)
    if not ok:
        return _fail(item_name, "请求失败/超时: {}".format(resp))
    if not isinstance(resp, dict):
        return _fail(item_name, "响应非JSON: {!r}".format(resp))
    code = resp.get("code")
    msg = resp.get("msg")
    if code != 0:
        return _fail(item_name, "code={} 非0 msg={}".format(code, msg), code, msg)
    test_api.set_measure_value(msg, unit="cmd")
    test_api.set_measure_expected("ok")
    test_api.set_measure_message("{}: 匹配 code={} msg={}".format(item_name, code, msg))
    test_api.set_measure_field("code", code)
    test_api.set_measure_field("msg", msg)
    return {"value": msg, "code": code, "msg": msg, "pass": True,
            "message": "{}: 匹配 code={} msg={}".format(item_name, code, msg)}


def loop_self(params):
    """Loop：演示内置 set_measure_result 全字段自描述（value/expected/range/message/passed）。"""
    item_name = str(params.get("name", " "))
    value = params.get("value")
    expected = params.get("expected")
    lower = params.get("lower")
    upper = params.get("upper")
    unit = str(params.get("unit", ""))
    passed = None
    if upper is not None or lower is not None:
        try:
            v = float(value)
            passed = True
            if upper is not None and v > float(upper):
                passed = False
            if lower is not None and v < float(lower):
                passed = False
        except (TypeError, ValueError):
            passed = False
    elif expected is not None:
        passed = (value == expected)
    test_api.set_measure_result(value=value, expected=expected, unit=unit,
                                lower=lower, upper=upper, passed=passed,
                                message="{}: 期望={} 实际={} 区间[{}, {}]".format(
                                    item_name, expected, value, lower, upper))
    return True
