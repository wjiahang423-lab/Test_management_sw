# -*- coding: utf-8 -*-
"""生成全功能自动测试数据：YAML loop 数据 + 测试计划(≥100 用例)。

产出：
  scripts/auto_loop_cmd.yaml    接口指令 loop 数据（dict expected，逐键判定）
  scripts/auto_loop_value.yaml  数值 loop 数据（标量 expected/upper/lower，自描述）
  scripts/auto_loop_self.yaml   内置 API 自描述 loop 数据
  data/plans/全功能自动测试.plan 覆盖全部用例类型的测试计划
"""
import json
import os
import uuid

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS = os.path.join(ROOT, "scripts")
PLANS = os.path.join(ROOT, "data", "plans")


def _id():
    return uuid.uuid4().hex[:12]


def _case(name, ctype, config, timeout_ms=8000, retry=0, fail_policy="continue", description=""):
    return {
        "id": _id(), "name": name, "type": ctype, "timeout_ms": timeout_ms,
        "retry": retry, "fail_policy": fail_policy, "skip": False,
        "description": description, "config": config,
    }


def _action(name, function, params=None, script="scripts/auto_api.py", **kw):
    cfg = {"script": script, "function": function}
    if params:
        cfg["params"] = params
    cfg.update({k: v for k, v in kw.items()})
    return _case(name, "action", cfg, timeout_ms=6000)


def _delay(name, ms, description=""):
    return _case(name, "delay", {"delay_ms": ms, "description": description}, timeout_ms=ms + 2000)


def _pop(name, title, content, btn_true="确认", btn_false="取消", result_var="", retry=0):
    return _case(name, "pop",
                 {"title": title, "content": content, "btn_true": btn_true,
                  "btn_false": btn_false, "result_var": result_var},
                 timeout_ms=15000, retry=retry)


def _meas(name, function, params, returns, display="", timeout_ms=10000):
    cfg = {"display": display, "script": "scripts/auto_api.py", "function": function,
           "params": params, "returns": returns}
    return _case(name, "measurement", cfg, timeout_ms=timeout_ms)


def _p(name, vtype, value):
    return {"name": name, "type": vtype, "value": str(value), "source": "value"}


def _ret(item, judge, threshold, rtype="float", bind=""):
    return {"item": item, "type": rtype, "bind_var": bind, "judge": judge, "threshold": threshold}


def _loop(name, function, yaml_path, session_key, parser="", timeout_ms=60000, description=""):
    return _case(name, "loop",
                 {"yaml_path": yaml_path, "script": "scripts/auto_api.py",
                  "function": function, "parser": parser,
                  "session_key": session_key, "overrides": []},
                 timeout_ms=timeout_ms, description=description)


# ================= 动作 =================
actions = [
    _action("动作_扫描SN(自动)", "scan_sn_auto", [{"name": "sn", "type": "str", "value": "AUTOTEST-SN-001", "source": "value"}]),
    _action("动作_获取上报密钥并回填", "fetch_upload_key", [{"name": "sn", "type": "str", "value": "AUTOTEST-SN-001", "source": "value"}]),
    _action("动作_刷新机器人状态栏", "report_robot"),
    _action("动作_上电_12V", "power_on", [{"name": "voltage", "type": "float", "value": "12", "source": "value"},
                                         {"name": "current", "type": "float", "value": "1.5", "source": "value"}],
            script="scripts/demo_instrument.py"),
    _action("动作_上电_5V", "power_on", [{"name": "voltage", "type": "float", "value": "5", "source": "value"},
                                        {"name": "current", "type": "float", "value": "2", "source": "value"}],
            script="scripts/demo_instrument.py"),
    _action("动作_执行步骤1", "do_action_step", [{"name": "msg", "type": "str", "value": "第1步-初始化", "source": "value"}],
            script="scripts/demo_instrument.py"),
    _action("动作_执行步骤2", "do_action_step", [{"name": "msg", "type": "str", "value": "第2步-加载", "source": "value"}],
            script="scripts/demo_instrument.py"),
    _action("动作_Ping服务", "action_ping"),
    _action("动作_GET_字符串接口", "action_api_get", [{"name": "path", "type": "str", "value": "/api/str", "source": "value"}]),
    _action("动作_GET_字典接口", "action_api_get", [{"name": "path", "type": "str", "value": "/api/dict", "source": "value"}]),
    _action("动作_GET_错误接口", "action_api_get", [{"name": "path", "type": "str", "value": "/api/error", "source": "value"}]),
    _action("动作_返回字符串", "say_hello", [], script="scripts/demo_instrument.py"),
    _action("动作_返回列表", "list_test_", [], script="scripts/demo_instrument.py"),
]

# ================= 延时 =================
delays = [
    _delay("延时_100ms", 100, "短延时"),
    _delay("延时_300ms", 300, ""),
    _delay("延时_500ms", 500, ""),
    _delay("延时_1000ms", 1000, "等待稳定"),
    _delay("延时_1500ms", 1500, ""),
    _delay("延时_2000ms", 2000, "较长等待"),
]

# ================= Pop（自动执行时由 runner 自动确认/取消） =================
pops = [
    _pop("弹窗_确认继续", "操作确认", "这是自动测试弹窗，请确认", result_var="pop_a"),
    _pop("弹窗_确认完成", "完成确认", "请确认当前步骤完成", result_var="pop_b"),
    _pop("弹窗_可取消", "选择", "点确认继续，点取消则本用例失败", result_var="pop_c"),
]

# ================= 测量 =================
measurements = []
# 电压
for i, (judge, thr, dsc) in enumerate([
    ("范围内", "11~13", "正常范围"),
    ("范围内", "12~12.5", "窄范围(可能FAIL)"),
    ("大于", "11", "大于下限"),
    ("小于", "13", "小于上限"),
    ("等于", "12", "约等于(可能FAIL)"),
    ("不等于", "99", "不等于"),
]):
    measurements.append(_meas("测量_电压_{}".format(dsc), "measure_json_value",
                              [_p("path", "str", "/api/voltage"), _p("field", "str", "value")],
                              [_ret("value", judge, thr, "float")], display="电压"))
# 电流
for i, (judge, thr, dsc) in enumerate([
    ("范围内", "0.6~1.0", "正常"),
    ("大于", "0.5", "大于0.5"),
    ("小于", "1.5", "小于1.5"),
]):
    measurements.append(_meas("测量_电流_{}".format(dsc), "measure_json_value",
                              [_p("path", "str", "/api/current"), _p("field", "str", "value")],
                              [_ret("value", judge, thr, "float")], display="电流"))
# 温度
for i, (judge, thr, dsc) in enumerate([
    ("范围内", "19~29", "正常(可能FAIL)"),
    ("大于", "18", "大于18"),
    ("小于", "32", "小于32"),
]):
    measurements.append(_meas("测量_温度_{}".format(dsc), "measure_json_value",
                              [_p("path", "str", "/api/temperature"), _p("field", "str", "value")],
                              [_ret("value", judge, thr, "float")], display="温度"))
# 电量(整型)
for i, (judge, thr, dsc) in enumerate([
    ("范围内", "50~100", "正常"),
    ("大于", "60", "大于60"),
    ("小于", "100", "小于100"),
]):
    measurements.append(_meas("测量_电量_{}".format(dsc), "measure_battery", [],
                              [_ret("return", judge, thr, "int")], display="电量"))
# 求和
for i, (a, b, judge, thr, dsc) in enumerate([
    ("1", "2", "等于", "3", "1+2=3"),
    ("10", "5", "大于", "10", "10+5>10"),
    ("3", "4", "范围内", "6~8", "3+4∈[6,8]"),
    ("0", "0", "等于", "0", "0+0=0"),
    ("-5", "8", "等于", "3", "-5+8=3"),
]):
    measurements.append(_meas("测量_求和_{}".format(dsc), "measure_sum",
                              [_p("a", "float", a), _p("b", "float", b)],
                              [_ret("return", judge, thr, "float")], display="求和"))
# 列表
measurements += [
    _meas("测量_列表_长度≥5", "measure_list", [], [_ret("return", "长度", ">=5", "list")], display="列表"),
    _meas("测量_列表_包含0~100元素", "measure_list", [], [_ret("return", "包含", "42", "list")], display="列表"),
    _meas("测量_列表_元素2", "measure_list", [], [_ret("0", "小于", "60", "int")], display="列表"),
    _meas("测量_列表_元素4", "measure_list", [], [_ret("4", "大于", "0", "int")], display="列表"),
]
# 字典
measurements += [
    _meas("测量_字典_含键nested", "measure_dict", [], [_ret("return", "包含", "nested", "dict")], display="字典"),
    _meas("测量_字典_字段a=1", "measure_dict", [], [_ret("nested.a", "等于", "1", "int")], display="字典"),
    _meas("测量_字典_字段b=x", "measure_dict", [], [_ret("nested.b", "等于", "x", "str")], display="字典"),
    _meas("测量_字典_items长度3", "measure_dict", [], [_ret("items", "长度", "3", "list")], display="字典"),
]
# 字符串/布尔
measurements += [
    _meas("测量_状态串_包含run", "measure_status", [], [_ret("return", "包含", "run", "str")], display="状态"),
    _meas("测量_状态串_等于running", "measure_status", [], [_ret("return", "等于", "running", "str")], display="状态"),
    _meas("测量_布尔_flag", "measure_flag", [], [_ret("return", "等于", "True", "str")], display="布尔"),
]
# 接口字段
measurements += [
    _meas("测量_超时场景", "measure_timeout", [], [_ret("return", "等于", "0", "int")], display="超时", timeout_ms=4000),
]

# ---- 扩充：更多判定/更多数据类型（凑足 ≥100 条用例） ----
for i, (path, field, judge, thr, dsc) in enumerate([
    ("/api/voltage", "value", "范围内", "11~13", "v1"),
    ("/api/voltage", "value", "范围内", "12.5~13.5", "v2(可能FAIL)"),
    ("/api/voltage", "value", "大于", "11.5", "v3"),
    ("/api/voltage", "value", "小于", "12.6", "v4"),
    ("/api/voltage", "value", "不等于", "0", "v5"),
    ("/api/current", "value", "范围内", "0.5~1.3", "c1"),
    ("/api/current", "value", "大于", "0.6", "c2"),
    ("/api/current", "value", "小于", "1.6", "c3"),
    ("/api/temperature", "value", "大于", "18", "t1"),
    ("/api/temperature", "value", "小于", "34", "t2"),
    ("/api/battery", "value", "范围内", "50~100", "b1"),
    ("/api/battery", "value", "大于", "55", "b2"),
    ("/api/battery", "value", "小于", "101", "b3"),
    ("/api/range?lo=0&hi=50", "value", "范围内", "0~50", "r1"),
    ("/api/range?lo=10&hi=20", "value", "范围内", "10~20", "r2"),
    ("/api/range?lo=10&hi=20", "value", "大于", "9", "r3"),
    ("/api/sum?a=10&b=20", "sum", "等于", "30", "s1"),
    ("/api/sum?a=2&b=2", "sum", "等于", "4", "s2"),
    ("/api/sum?a=1.5&b=1.5", "sum", "等于", "3.0", "s3"),
    ("/api/sum?a=-1&b=5", "sum", "等于", "4", "s4"),
]):
    measurements.append(_meas("测量_接口字段_{}_{}".format(path.split("?")[0].lstrip("/"), dsc),
                              "measure_json_value",
                              [_p("path", "str", path), _p("field", "str", field)],
                              [_ret("value", judge, thr, "float")], display="接口字段"))

# 列表/字典/字符串/布尔 更多判定
for i, (judge, thr, dsc) in enumerate([
    ("长度", ">=1", "非空"),
    ("长度", "<10", "少于10"),
    ("长度", "5", "恰好5个"),
    ("包含", "0", "含0"),
    ("包含", "99", "可能不含(FAIL)"),
    ("等于", "[1, 2, 3]", "结构比较(FAIL)"),
]):
    measurements.append(_meas("测量_列表_{}".format(dsc), "measure_list", [],
                              [_ret("return", judge, thr, "list")], display="列表"))
for i, (item, judge, thr, dsc) in enumerate([
    ("return", "包含", "nested", "含nested键"),
    ("return", "包含", "items", "含items键"),
    ("nested.a", "等于", "1", "a==1"),
    ("nested.b", "等于", "x", "b==x"),
    ("items", "长度", "3", "items长度3"),
    ("items.0", "等于", "1", "items[0]==1"),
    ("items.2", "等于", "3", "items[2]==3"),
    ("return", "等于", '{"nested": {"a": 1, "b": "x"}, "items": [1, 2, 3]}', "整字典相等"),
]):
    measurements.append(_meas("测量_字典_{}".format(dsc), "measure_dict", [],
                              [_ret(item, judge, thr, "dict" if item == "return" else "str")], display="字典"))
for i, (judge, thr, dsc) in enumerate([
    ("包含", "run", "含run"),
    ("包含", "ning", "含ning"),
    ("等于", "running", "==running"),
    ("包含", "STOP", "含STOP(FAIL)"),
]):
    measurements.append(_meas("测量_状态串_{}".format(dsc), "measure_status", [],
                              [_ret("return", judge, thr, "str")], display="状态"))
for i, (judge, thr, dsc) in enumerate([
    ("等于", "True", "==True"),
    ("等于", "False", "==False(FAIL)"),
]):
    measurements.append(_meas("测量_布尔_{}".format(dsc), "measure_flag", [],
                              [_ret("return", judge, thr, "str")], display="布尔"))

# 绑定变量 + 复用上一步结果
measurements.append(_meas("测量_求和_绑定变量", "measure_sum",
                          [_p("a", "float", "2"), _p("b", "float", "3")],
                          [_ret("return", "等于", "5", "float", bind="last_sum")], display="求和"))
measurements.append(_meas("测量_读取全局变量", "measure_sum",
                          [_p("a", "float", "1"), _p("b", "float", "1")],
                          [_ret("return", "等于", "2", "float")], display="求和"))
measurements.append(_meas("测量_延时慢接口(1s)", "measure_json_value",
                          [_p("path", "str", "/api/slow?t=1"), _p("field", "str", "slept")],
                          [_ret("value", "等于", "1.0", "float")], display="慢接口", timeout_ms=5000))

# ================= Loop YAML 数据 =================
cmd_sessions = [
    {"name": "接口_list指令", "cmd": "list", "timeout": 3, "expected": {"code": 0, "msg": "ok"}},
    {"name": "接口_describe_ver.bsp", "cmd": "describe", "id": "ver.bsp", "timeout": 3,
     "expected": {"code": 0, "msg": "ok"}},
    {"name": "接口_run指令", "cmd": "run", "timeout": 5, "expected": {"code": 0, "msg": "ok"}},
    {"name": "接口_未知指令", "cmd": "frobnicate", "timeout": 3, "expected": {"code": 0, "msg": "ok"}},
]

value_sessions = [
    {"name": "Loop电压_正常", "path": "/api/voltage", "expected": 12, "lower": 11, "upper": 13, "timeout": 3},
    {"name": "Loop电压_超上限(应FAIL)", "path": "/api/voltage", "expected": 12, "lower": 11, "upper": 11.1, "timeout": 3},
    {"name": "Loop电流_正常", "path": "/api/current", "expected": 1, "lower": 0.6, "upper": 1.2, "timeout": 3},
    {"name": "Loop温度_正常", "path": "/api/temperature", "expected": 25, "lower": 15, "upper": 35, "timeout": 3},
    {"name": "Loop电量_范围", "path": "/api/battery", "expected": 80, "lower": 50, "upper": 100, "timeout": 3},
    {"name": "Loop求和_1+2=3", "path": "/api/sum?a=1&b=2", "expected": 3, "timeout": 3},
]

self_sessions = [
    {"name": "自描述_固定值匹配", "value": 25, "expected": 25, "unit": "°C", "msg": "温度25度"},
    {"name": "自描述_区间内", "value": 12.2, "expected": 12, "lower": 11, "upper": 13, "unit": "V"},
    {"name": "自描述_超上限(应FAIL)", "value": 14.5, "expected": 12, "lower": 11, "upper": 13, "unit": "V"},
    {"name": "自描述_低于下限(应FAIL)", "value": 10.2, "expected": 12, "lower": 11, "upper": 13, "unit": "V"},
    {"name": "自描述_字符串匹配", "value": "ok", "expected": "ok", "unit": "cmd"},
    {"name": "自描述_字符串不匹配(应FAIL)", "value": "ng", "expected": "ok", "unit": "cmd"},
]

edge_sessions = [
    {"name": "边界_正常接口", "path": "/api/str", "timeout": 3},
    {"name": "边界_慢响应1s", "path": "/api/slow?t=1", "timeout": 3},
    {"name": "边界_慢响应2s", "path": "/api/slow?t=2", "timeout": 5},
    {"name": "边界_404", "path": "/api/missing", "timeout": 3},
    {"name": "边界_500错误", "path": "/api/error", "timeout": 3},
    {"name": "边界_超时(应FAIL)", "path": "/api/timeout", "timeout": 2},
    {"name": "边界_求和接口", "path": "/api/sum?a=1&b=2", "timeout": 3},
    {"name": "边界_字典接口", "path": "/api/dict", "timeout": 3},
]


def _dump_yaml(name, sessions):
    lines = ["# 全功能自动测试 loop 数据", "sessions:"]
    for s in sessions:
        lines.append("  - name: \"{}\"".format(s["name"]))
        for k, v in s.items():
            if k == "name":
                continue
            if isinstance(v, dict):
                lines.append("    {}: {}".format(k, json.dumps(v, ensure_ascii=False)))
            elif isinstance(v, str):
                lines.append("    {}: \"{}\"".format(k, v))
            elif isinstance(v, bool):
                lines.append("    {}: {}".format(k, "true" if v else "false"))
            else:
                lines.append("    {}: {}".format(k, v))
    path = os.path.join(SCRIPTS, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


cmd_yaml = _dump_yaml("auto_loop_cmd.yaml", cmd_sessions)
value_yaml = _dump_yaml("auto_loop_value.yaml", value_sessions)
self_yaml = _dump_yaml("auto_loop_self.yaml", self_sessions)
edge_yaml = _dump_yaml("auto_loop_edge.yaml", edge_sessions)

# ================= 组装计划 =================
loop_cases = [
    _loop("Loop_接口指令(list/describe/run)", "loop_cmd", "scripts/auto_loop_cmd.yaml", "sessions",
          timeout_ms=60000, description="dict expected 逐键判定 + 错误场景"),
    _loop("Loop_数值区间(电压/电流/温度/电量/求和)", "loop_value", "scripts/auto_loop_value.yaml", "sessions",
          timeout_ms=60000, description="标量 expected/upper/lower 自描述"),
    _loop("Loop_自描述测量(内置API全字段)", "loop_self", "scripts/auto_loop_self.yaml", "sessions",
          timeout_ms=30000, description="set_measure_result 全字段自描述"),
    _loop("Loop_边界与超时(404/500/慢响应/超时)", "loop_edge", "scripts/auto_loop_edge.yaml", "sessions",
          timeout_ms=60000, description="边界/异常场景"),
]

cases_flat = []
# 01 初始化
seq0 = [actions[0], actions[1], actions[2]]
# 02 动作
seq1 = actions[3:]
# 03 延时
seq2 = delays
# 04 弹窗
seq3 = pops
# 05 测量-模拟量
seq4 = measurements[:14]
# 06 测量-数据类型
seq5 = measurements[14:]
# 07 Loop
seq6 = loop_cases

sequences = [
    {"id": _id(), "name": "S00_初始化与密钥", "cases": [c for c in seq0]},
    {"id": _id(), "name": "S01_动作类", "cases": [c for c in seq1]},
    {"id": _id(), "name": "S02_延时类", "cases": [c for c in seq2]},
    {"id": _id(), "name": "S03_人机交互Pop", "cases": [c for c in seq3]},
    {"id": _id(), "name": "S04_测量_模拟量", "cases": [c for c in seq4]},
    {"id": _id(), "name": "S05_测量_数据类型", "cases": [c for c in seq5]},
    {"id": _id(), "name": "S06_Loop", "cases": [c for c in seq6]},
]

all_cases = [c for s in sequences for c in s["cases"]]
session_count = len(cmd_sessions) + len(value_sessions) + len(self_sessions) + len(edge_sessions)

plan = {
    "name": "全功能自动测试",
    "description": "覆盖全部用例类型与场景的自动测试计划（动作/延时/Pop/测量/Loop，含错误/超时/自描述）。"
                   "mock 服务：http://10.5.35.49:5000",
    "variables": {
        "pop_a": {"type": "str", "value": True, "description": "弹窗结果A"},
        "pop_b": {"type": "str", "value": True, "description": "弹窗结果B"},
        "pop_c": {"type": "str", "value": True, "description": "弹窗结果C"},
    },
    "settings": {
        "storage_mode": "local",
        "batch": "AUTO-20260818",
        "log_dir": "",
        "report_dir": "",
        "remote_storage_enabled": False,
        "remote_storage_url": "",
        "json_upload_enabled": True,
        "json_upload_url": "http://10.5.35.49:5000/submit",
        "json_upload_key": "",
        "mes_server": "",
        "mes_interface": "",
        "mes_template": "",
        "remote_file_server": "",
        "remote_db_ip": "",
        "remote_db_table": "",
    },
    "sequences": sequences,
}

plan_path = os.path.join(PLANS, "全功能自动测试.plan")
with open(plan_path, "w", encoding="utf-8") as f:
    json.dump(plan, f, ensure_ascii=False, indent=2)

print("已生成：")
print("  " + cmd_yaml)
print("  " + value_yaml)
print("  " + self_yaml)
print("  " + plan_path)
print("计划用例数(扁平): {}（含 Loop {} 例，其 session 共 {} 条）".format(
    len(all_cases), len(loop_cases), session_count))
print("合计测试项: {}".format(len(all_cases) + session_count))
