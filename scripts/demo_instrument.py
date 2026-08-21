# -*- coding: utf-8 -*-
"""示例测试脚本 - 演示内置 API 函数的使用。

本脚本中的函数可被 Action / Measurement / Loop 用例调用。
"""
import test_api


def power_on(voltage=12.0, current=1.5)-> dict:
    """上电：返回功耗数据"""
    import time
    time.sleep(0.2)
    sn = test_api.get_sn()
    test_api.log("power_on: SN=%s" % sn)
    power = voltage * current
    test_api.set_display_info("功耗", "%.2fW" % power)
    return {"power": power, "voltage": voltage, "current": current, "ok": True}


def measure_voltage(channel=1):
    """测量电压：返回测量值与状态"""
    import random
    value = 12.0 + channel * 0.1 + random.uniform(-0.05, 0.05)
    test_api.set_sn_to_Panel(test_api.get_sn())
    return {"channel": channel, "voltage": round(value, 3)}


def check_can(signal_ok=True):
    """检查 CAN 信号状态：根据 YAML 传入的 signal_ok 判定"""
    ok = bool(signal_ok)
    test_api.log("CAN 检查结果: %s" % ok)
    return {"result": "pass" if ok else "fail", "signal_ok": ok}


def parse_can(result):
    """Loop 解析函数：读取每个 session 执行结果并提取判定值"""
    if isinstance(result, dict):
        return {"result": result.get("result", "fail"),
                "signal_ok": result.get("signal_ok", False)}
    return {"result": str(result)}


def do_action_step(msg="action executed"):
    """Action 用例：仅执行，不做判定"""
    test_api.log("do_action_step: %s" % msg)
    test_api.set_display_info("动作", msg)
    return True


def report_robot_status(temperature=25.5, current=1.2, voltage=12.3, battery=87):
    """更新执行页底部机器人状态栏：温度/电流/电压/电池%"""
    test_api.set_robot_temperature(temperature)
    test_api.set_robot_current(current)
    test_api.set_robot_voltage(voltage)
    test_api.set_robot_battery(battery)
    test_api.log("机器人状态已更新：温度{} 电流{} 电压{} 电池{}".format(
        temperature, current, voltage, battery))
    return {"ok": True, "temperature": temperature}

def add_funtion(a:int,b:int)->int:
    """示例函数：两个整数相加"""
    return a+b

def dict_test_(text:str,age:int)->dict:
    """示例函数：返回一个字典"""
    return {"my name is :":text,"age":age}

def list_test_()->list:
    """示例函数：返回一个列表"""
    return [1,2,3,4,5]

def say_hello()->str:
    """示例函数：返回一个字符串"""
    return "Hello!" 



import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def measure(params: dict) -> dict:
    """
    模拟测量并返回结果。
    使用 params 中的 value 字段，并与 min_val/max_val 比较。
    """
    item_name = params.get("name", "unknown")
    # value = float(params.get("value", 0.0))
    if "电压"in item_name:
        value = 12.0  # 使用固定值进行演示
    else:
        value = 21.0  # 使用固定值进行演示
    unit = str(params.get("unit", ""))
    min_val = float(params.get("min_val", float("-inf")))
    max_val = float(params.get("max_val", float("inf")))

    passed = min_val <= value <= max_val
    return {
        "value": value,
        "unit": unit,
        "pass": passed,
        "message": f"{item_name}: {value} {unit} (范围 [{min_val}, {max_val}])",
    }

import requests



def result_to_report(value, expected=None, unit="", lower=None, upper=None,
               message="", passed=None)-> dict:
   
    # if passed is None:
    #     passed = (value == expected)
    if not message:
        message = "期望值: {} | 实际值: {} | 阈值区间: [{}, {}]".format(
            expected, value, lower, upper)
    # 软件写报告的接口
    if value is not None:
        test_api.set_measure_value(value, unit=unit)
    if expected is not None:
        test_api.set_measure_expected(expected) 
    if lower is not None and upper is not None:
        test_api.set_measure_range(lower, upper)
    if message:
        test_api.set_measure_message(message)

    return {
        "value": value,
        "expected": expected,
        "unit": unit,
        "lower": lower,
        "upper": upper,
        "message": message,
        "pass": passed,
    }






# 封装request
def request_api(url, method="GET", params=None, headers=None, timeout=10):
    """
    封装 requests 库的 HTTP 请求，返回响应数据或错误信息。
    :param url: 请求的 URL
    :param method: 请求方法，GET 或 POST
    :param params: 请求参数，字典形式
    :param headers: 请求头，字典形式
    :param timeout: 超时时间，单位秒
    :return: (success, data_or_error)
    """
    try:
        if method.upper() == "POST":
            response = requests.post(url, json=params, headers=headers, timeout=timeout)
        else:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
        response.raise_for_status()  # 非 2xx 视为失败
        response.encoding = "utf-8"
        data = response.json() if response.content else {}
        return True, data
    except Exception as e:
        return False, str(e)

# 阈值对比封装
def compare_thresholds(item_name, num_val, lower=None, upper=None, expected=None):
    """
    对比数值与阈值，返回是否通过及消息。
    :param item_name: 测量项名称
    :param num_val: 实际测量值
    :param lower: 下限
    :param upper: 上限
    :param expected: 期望值
    :return: (passed, message)
    """
    if upper is not None and num_val is not None and num_val > float(upper):
        return False, "{}: 超上限，实际值={}，上限={}".format(item_name, num_val, upper)
    elif lower is not None and num_val is not None and num_val < float(lower):
        return False, "{}: 低于下限，实际值={}，下限={}".format(item_name, num_val, lower)
    elif expected is not None and num_val != expected:
        return False, "{}: 不匹配，实际值={}，期望={}".format(item_name, num_val, expected)
    else:
        return True, "{}: 匹配，实际值={}，期望={}，范围 [{}, {}]".format(
            item_name, num_val, expected, lower, upper)


# 测试函数

def robot_test_001(params: dict) -> dict:
    """Loop 接口测试函数（GET/POST + code/msg 判定）。

    超时/异常/响应非预期 => 返回 pass=False（框架强制 FAIL），message 写明原因。
    YAML 可配 timeout(秒)，默认 5；expected:{code,msg} 可选，缺省按 code==0 视为成功。
    """
    item_name = str(params.get("name", " "))
    url_ = str(params.get("url", "")).strip()
    method = str(params.get("method", "GET")).upper()  # 请求方法，默认 GET
    parameters = params.get("param", {}) or {}  # 可选的请求参数
    headers = {"Content-Type": str(params.get("Content-Type", "application/json"))}
    unit = str(params.get("unit", ""))
    try:
        timeout = float(params.get("timeout", 5))  # 请求超时（秒），可在 YAML 覆盖
    except (TypeError, ValueError):
        timeout = 5.0

    if url_ and not url_.startswith(("http://", "https://")):
        url_ = "http://" + url_  # 地址缺协议时自动补全

    expected_cfg = params.get("expected", {})
    expected = expected_cfg if isinstance(expected_cfg, dict) else {}
    exp_code = expected.get("code")
    exp_msg = expected.get("msg")

    # 1) 发起请求；超时/异常 -> 直接 FAIL（pass=False，框架强制 FAIL）
    success, resp = request_api(url_, method=method, params=parameters,
                                headers=headers, timeout=timeout)
    if not success:
        return _fail_result(item_name, "接口超时/请求失败：{}".format(resp), unit)

    # 2) 响应必须是 JSON 字典
    if not isinstance(resp, dict):
        return _fail_result(item_name, "响应不是 JSON 字典：{!r}".format(resp), unit)

    code = resp.get("code")
    msg = resp.get("msg")

    # 3) 判定 code/msg（页面/YAML 配置的 expected 优先；缺省按特殊接口 code==0 视为成功）
    if exp_code is not None and code != exp_code:
        return _fail_result(item_name, "code 不匹配，期望 {} 实际 {}".format(exp_code, code), unit, code, msg)
    if exp_msg is not None and str(msg) != str(exp_msg):
        return _fail_result(item_name, "msg 不匹配，期望 {} 实际 {}".format(exp_msg, msg), unit, code, msg)
    if exp_code is None and not isinstance(code, (int, float)):
        return _fail_result(item_name, "响应缺少 code 字段：{!r}".format(resp), unit, code, msg)
    if exp_code is None and int(code) != 0:
        return _fail_result(item_name, "code={} 非 0，判定失败".format(code), unit, code, msg)

    # 4) 成功
    message = "{}: 匹配，code={} msg={}".format(item_name, code, msg)
    result = result_to_report(msg, expected=msg, unit=unit, message=message, passed=True)
    result["code"] = code
    result["msg"] = msg
    test_api.set_measure_field("code", code)
    test_api.set_measure_field("msg", msg)
    return result


def _fail_result(item_name, reason, unit="", code=None, msg=None) -> dict:
    """构造失败结果：pass=False 会被框架强制判定 FAIL，message 为失败原因。"""
    test_api.log("{} -> FAIL: {}".format(item_name, reason))
    result = result_to_report(None, expected=None, unit=unit, message=reason, passed=False)
    result["code"] = code
    result["msg"] = msg
    result["reason"] = reason
    test_api.set_measure_field("code", code)
    test_api.set_measure_field("msg", msg)
    return result
