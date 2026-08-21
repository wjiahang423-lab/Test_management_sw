#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 CIT 交互通讯协议 测试计划与 Loop 数据。

产出：
  scripts/cit_loop_run.yaml       run 批量执行 loop 数据（含 PASS/FAIL/ERROR/参数覆盖）
  scripts/cit_loop_describe.yaml  describe 批量 loop 数据
  data/plans/CIT接口测试.plan     覆盖 list/describe/run/sysinfo/错误码的测试计划
服务地址默认 http://10.5.35.49:8090（可在 cit_api.py 或用例 url 字段修改为真板卡）
"""
import json
import os
import uuid

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS = os.path.join(ROOT, "scripts")
PLANS = os.path.join(ROOT, "data", "plans")


def _id():
    return uuid.uuid4().hex[:12]


def _case(name, ctype, config, timeout_ms=10000, retry=0, fail_policy="continue", description=""):
    return {"id": _id(), "name": name, "type": ctype, "timeout_ms": timeout_ms,
            "retry": retry, "fail_policy": fail_policy, "skip": False,
            "description": description, "config": config}


def _action(name, function, params=None):
    cfg = {"script": "scripts/cit_api.py", "function": function}
    if params:
        cfg["params"] = params
    return _case(name, "action", cfg, timeout_ms=6000)


def _delay(name, ms):
    return _case(name, "delay", {"delay_ms": ms, "description": ""}, timeout_ms=ms + 2000)


def _meas(name, function, params, returns, timeout_ms=10000):
    return _case(name, "measurement",
                 {"display": "", "script": "scripts/cit_api.py", "function": function,
                  "params": params, "returns": returns},
                 timeout_ms=timeout_ms)


def _p(name, vtype, value):
    return {"name": name, "type": vtype, "value": str(value), "source": "value"}


def _ret(item, judge, threshold, rtype="str", bind=""):
    return {"item": item, "type": rtype, "bind_var": bind, "judge": judge, "threshold": threshold}


def _loop(name, function, yaml_path, timeout_ms=60000, description=""):
    return _case(name, "loop",
                 {"yaml_path": yaml_path, "script": "scripts/cit_api.py",
                  "function": function, "parser": "",
                  "session_key": "sessions", "overrides": []},
                 timeout_ms=timeout_ms, description=description)


# ================= Loop YAML =================
run_sessions = [
    {"name": "Loop运行_CPU核心数", "id": "hw.cpu_cores", "expected": "PASS"},
    {"name": "Loop运行_内存容量", "id": "hw.memory", "expected": "PASS"},
    {"name": "Loop运行_SoC温度", "id": "temp.soc", "expected": "PASS"},
    {"name": "Loop运行_以太网", "id": "net.eth0", "expected": "PASS"},
    {"name": "Loop运行_整机版本", "id": "ver.all", "expected": "PASS"},
    {"name": "Loop运行_BSP版本", "id": "ver.bsp", "expected": "PASS"},
    {"name": "Loop运行_cit节点", "id": "node.cit", "expected": "PASS"},
    {"name": "Loop运行_风扇(应FAIL)", "id": "temp.fan", "expected": "FAIL"},
    {"name": "Loop运行_旧版兼容(应FAIL)", "id": "ver.old", "expected": "FAIL"},
    {"name": "Loop运行_未注册模块(应ERROR)", "id": "module.unregistered", "expected": "ERROR"},
    {"name": "Loop运行_节点参数覆盖(missing→FAIL)", "id": "node.cit",
     "override": {"node": "/missing"}, "expected": "FAIL"},
]

describe_sessions = [
    {"name": "Loop描述_CPU核心数", "id": "hw.cpu_cores"},
    {"name": "Loop描述_BSP版本", "id": "ver.bsp"},
    {"name": "Loop描述_以太网", "id": "net.eth0"},
    {"name": "Loop描述_cit节点", "id": "node.cit"},
    {"name": "Loop描述_整机版本", "id": "ver.all"},
]


def _dump_yaml(name, sessions):
    lines = ["# CIT 协议 loop 数据", "sessions:"]
    for s in sessions:
        lines.append("  - name: \"{}\"".format(s["name"]))
        for k, v in s.items():
            if k == "name":
                continue
            if isinstance(v, dict):
                lines.append("    {}: {}".format(k, json.dumps(v, ensure_ascii=False)))
            else:
                lines.append("    {}: \"{}\"".format(k, v))
    path = os.path.join(SCRIPTS, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


run_yaml = _dump_yaml("cit_loop_run.yaml", run_sessions)
describe_yaml = _dump_yaml("cit_loop_describe.yaml", describe_sessions)

# ================= 计划 =================
cases = []
# S00 初始化
cases += [
    _action("动作_CIT连通性(list)", "cit_ping"),
    _action("动作_获取板卡系统信息", "cit_sysinfo"),
    _delay("延时_300ms", 300),
]
# S01 列表与描述
cases += [
    _meas("测量_list_测试项数量", "cit_list", [],
          [_ret("count", "范围内", "9~11", "int"), _ret("msg", "等于", "ok", "str")]),
    _meas("测量_describe_ver.bsp", "cit_describe", [_p("id", "str", "ver.bsp")],
          [_ret("id", "等于", "ver.bsp", "str"),
           _ret("name", "包含", "BSP", "str"),
           _ret("desc", "包含", "版本", "str")]),
    _meas("测量_describe_hw.cpu_cores", "cit_describe", [_p("id", "str", "hw.cpu_cores")],
          [_ret("name", "包含", "CPU", "str"), _ret("desc", "包含", "核心", "str")]),
    _meas("测量_describe_node.cit", "cit_describe", [_p("id", "str", "node.cit")],
          [_ret("name", "包含", "cit", "str")]),
    _meas("测量_sysinfo_CPU=8", "cit_sysinfo", [],
          [_ret("data.cpu_cores", "等于", "8", "int"),
           _ret("data.memory_mb", "大于", "1024", "int")]),
]
# S02 运行单项 PASS
cases += [
    _meas("测量_run_cpu_cores", "cit_run", [_p("id", "str", "hw.cpu_cores")],
          [_ret("result", "等于", "PASS", "str"),
           _ret("duration_ms", "范围内", "0~100", "int")]),
    _meas("测量_run_memory", "cit_run", [_p("id", "str", "hw.memory")],
          [_ret("result", "等于", "PASS", "str"), _ret("summary", "包含", "内存", "str")]),
    _meas("测量_run_ver.all", "cit_run", [_p("id", "str", "ver.all")],
          [_ret("result", "等于", "PASS", "str"), _ret("summary", "包含", "版本", "str")]),
    _meas("测量_run_net.eth0", "cit_run", [_p("id", "str", "net.eth0")],
          [_ret("result", "等于", "PASS", "str"), _ret("summary", "包含", "UP", "str")]),
    _meas("测量_run_node.cit_参数覆盖", "cit_run",
          [_p("id", "str", "node.cit"),
           {"name": "override", "type": "dict", "value": '{"node":"/my_cit_node"}', "source": "value"}],
          [_ret("result", "等于", "PASS", "str"), _ret("summary", "包含", "节点存在", "str")]),
]
# S03 运行单项 FAIL / ERROR
cases += [
    _meas("测量_run_fan(应FAIL)", "cit_run", [_p("id", "str", "temp.fan")],
          [_ret("result", "等于", "FAIL", "str"), _ret("summary", "包含", "未找到", "str")]),
    _meas("测量_run_ver.old(应FAIL)", "cit_run", [_p("id", "str", "ver.old")],
          [_ret("result", "等于", "FAIL", "str")]),
    _meas("测量_run_module.unregistered(应ERROR)", "cit_run", [_p("id", "str", "module.unregistered")],
          [_ret("result", "等于", "ERROR", "str"), _ret("error", "包含", "not registered", "str")]),
    _meas("测量_run_node.cit_missing(应FAIL)", "cit_run",
          [_p("id", "str", "node.cit"),
           {"name": "override", "type": "dict", "value": '{"node":"/missing"}', "source": "value"}],
          [_ret("result", "等于", "FAIL", "str"), _ret("summary", "包含", "节点不存在", "str")]),
]
# S04 错误码
cases += [
    _meas("测量_run_未知id(应1002)", "cit_run", [_p("id", "str", "hw.xxx")],
          [_ret("code", "等于", "1002", "int"), _ret("msg", "包含", "item not found", "str")]),
    _meas("测量_未知cmd(应1001)", "cit_unknown_cmd", [_p("cmd", "str", "foo")],
          [_ret("code", "等于", "1001", "int"), _ret("msg", "包含", "unknown cmd", "str")]),
]
# S05 Loop
cases += [
    _loop("Loop_run_批量执行(含FAIL/ERROR/覆盖)", "cit_loop_run", "scripts/cit_loop_run.yaml"),
    _loop("Loop_describe_批量描述", "cit_loop_describe", "scripts/cit_loop_describe.yaml"),
]

sequences = [
    {"id": _id(), "name": "S00_初始化", "cases": cases[:3]},
    {"id": _id(), "name": "S01_列表与描述", "cases": cases[3:8]},
    {"id": _id(), "name": "S02_运行单项PASS", "cases": cases[8:13]},
    {"id": _id(), "name": "S03_运行单项FAIL/ERROR", "cases": cases[13:17]},
    {"id": _id(), "name": "S04_错误码", "cases": cases[17:19]},
    {"id": _id(), "name": "S05_Loop批量", "cases": cases[19:21]},
]

plan = {
    "name": "CIT接口测试",
    "description": "CIT 交互通讯协议 v1.0 测试：list/describe/run/sysinfo/错误码/批量执行。服务 http://10.5.35.49:8090",
    "variables": {},
    "settings": {
        "storage_mode": "local", "batch": "CIT-20260819",
        "log_dir": "", "report_dir": "",
        "remote_storage_enabled": False, "remote_storage_url": "",
        "json_upload_enabled": True,
        "json_upload_url": "http://10.5.35.49:5000/submit",
        "json_upload_key": "",
        "mes_server": "", "mes_interface": "", "mes_template": "",
        "remote_file_server": "", "remote_db_ip": "", "remote_db_table": "",
    },
    "sequences": sequences,
}
plan_path = os.path.join(PLANS, "CIT接口测试.plan")
with open(plan_path, "w", encoding="utf-8") as f:
    json.dump(plan, f, ensure_ascii=False, indent=2)

print("已生成:")
print("  " + run_yaml)
print("  " + describe_yaml)
print("  " + plan_path)
print("计划用例数(扁平): {}  (Loop session: {} + {})".format(
    len(cases), len(run_sessions), len(describe_sessions)))