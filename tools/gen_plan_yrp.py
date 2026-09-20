#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 意优RP关节 测试 PLAN（支持多节点）。

用法:
    python3 tools/gen_plan_yrp.py 1                 # 单节点 node=1
    python3 tools/gen_plan_yrp.py 1,2,3             # 三节点 node=1/2/3
    python3 tools/gen_plan_yrp.py "1..8"            # 连续列 1~8

输出: data/plans/意优RP关节CANopen测试.plan
"""
import json
import os
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAN_OUT = os.path.join(ROOT, "data", "plans", "意优RP关节CANopen测试.plan")
SCRIPT = "scripts/意优关节RP/rp_joint_test.py"


def parse_nodes(arg):
    if not arg:
        return [1]
    if ".." in arg:
        a, b = arg.split("..")
        return list(range(int(a), int(b) + 1))
    parts = arg.replace(";", ",").replace("，", ",").split(",")
    return [int(p.strip()) for p in parts if p.strip()]


def nid():
    return uuid.uuid4().hex[:12]


def param(name, typ, value):
    return {"name": name, "type": typ, "value": str(value), "source": "value"}


def case(name, typ, func, params=None, returns=None, timeout=10000, policy="continue"):
    cfg = {"script": SCRIPT, "function": func}
    if params:
        cfg["params"] = params
    if returns is not None:
        cfg["returns"] = returns
    return {"id": nid(), "name": name, "type": typ, "timeout_ms": timeout,
            "retry": 0, "fail_policy": policy, "skip": False, "description": "",
            "config": cfg}


def node_returns(nodes, key="value", threshold="118~122"):
    return [{"item": "nodes.%s.%s" % (n, key), "type": "float",
             "judge": "范围内", "threshold": threshold} for n in nodes]


def delay(ms=1000, desc="延时1s"):
    return {"id": nid(), "name": "延时_%dms" % ms, "type": "delay",
            "timeout_ms": ms + 1500, "retry": 0, "fail_policy": "continue",
            "skip": False, "description": "", "config": {"delay_ms": ms, "description": desc}}


def info_cases(nodes):
    """设备信息 4 个用例(各读一个值, 判定等于 expected), 共用一个函数 measure_yrp_od_string。
       使用前把每个用例的 returns.threshold(与 expected 参数)填成该机实际期望值。"""
    fields = [
        ("SN序列号", 0x2082),
        ("产品型号", 0x1008),
        ("硬件版本", 0x2083),
        ("MCU固件版本", 0x2084),
    ]
    out = []
    for label, addr in fields:
        c = case("设备信息_%s(0x%04X)" % (label, addr), "measurement", "measure_yrp_od_string",
                 params=[param("address", "int", addr),
                         param("node", "int", 1),
                         param("expected", "str", ""),
                         param("label", "str", label)],
                 returns=[{"item": "value", "type": "str", "judge": "等于", "threshold": ""}],
                 timeout=8000)
        out.append(c)
        out.append(delay(1000))
    return out


def build(nodes):
    nodes = [int(n) for n in nodes]
    ndesc = "、".join("node%d" % n for n in nodes)
    per_node_timeout = max(80000, len(nodes) * 60000)
    cases = []
    cases.append(case("① 连接CAN", "action", "action_yrp_connect",
                      params=[param("nodes", "list", json.dumps(nodes))], timeout=15000))
    cases.append(delay(1000))
    cases += info_cases(nodes)
    cases += [
        case("③ 使能+标0", "action", "action_yrp_enable_zero_all",
             params=[param("nodes", "list", json.dumps(nodes))], timeout=max(15000, 8000 * len(nodes))),
        delay(1000),
        case("④ 往返±60(120°)", "measurement", "measure_yrp_roundtrip_all",
             params=[param("nodes", "list", json.dumps(nodes)),
                     param("angle_a", "float", 60), param("angle_b", "float", -60),
                     param("velocity_deg_s", "float", 10), param("acc_deg_s2", "float", 30)],
             returns=node_returns(nodes, "value", "118~122")
                     + [{"item": "all_pass", "type": "str", "judge": "等于", "threshold": "True"}],
             timeout=per_node_timeout),
        delay(1000),
        case("⑤ 往返±30(60°)", "measurement", "measure_yrp_roundtrip_all",
             params=[param("nodes", "list", json.dumps(nodes)),
                     param("angle_a", "float", 30), param("angle_b", "float", -30),
                     param("velocity_deg_s", "float", 10), param("acc_deg_s2", "float", 30)],
             returns=node_returns(nodes, "value", "58~62")
                     + [{"item": "all_pass", "type": "str", "judge": "等于", "threshold": "True"}],
             timeout=per_node_timeout),
        delay(1000),
        case("⑥ 回0位", "measurement", "measure_yrp_home0_all",
             params=[param("nodes", "list", json.dumps(nodes)), param("tolerance", "float", 1.0)],
             returns=node_returns(nodes, "value", "-1~1")
                     + [{"item": "all_pass", "type": "str", "judge": "等于", "threshold": "True"}],
             timeout=max(60000, 40000 * len(nodes))),
        delay(1000),
        case("⑦ 角度/编码器回读", "measurement", "measure_yrp_read_all",
             params=[param("nodes", "list", json.dumps(nodes))],
             returns=[{"item": "nodes.%s.angle" % n, "type": "float", "judge": "", "threshold": ""} for n in nodes],
             timeout=15000),
        delay(1000),
        case("⑧ 失能", "action", "action_yrp_disable_all",
             params=[param("nodes", "list", json.dumps(nodes))], timeout=8000),
        delay(1000),
        case("⑨ 断开CAN", "action", "action_yrp_disconnect", timeout=8000),
    ]
    return {
        "name": "意优RP关节CANopen测试",
        "description": "单序列: 连接→读设备信息→使能标0→±60/±30往返→回0→回读→失能断开, 步骤间延时1s。全节点(%s)。" % ndesc,
        "variables": {},
        "settings": {
            "storage_mode": "local", "batch": "YRP-" + __import__("datetime").datetime.now().strftime("%Y%m%d"),
            "log_dir": "", "report_dir": "",
            "remote_storage_enabled": False, "remote_storage_url": "",
            "json_upload_enabled": False, "json_upload_url": "", "json_upload_key": "",
            "mes_server": "", "mes_interface": "", "mes_template": "",
            "remote_file_server": "", "remote_db_ip": "", "remote_db_table": "",
        },
        "sequences": [
            {"id": nid(), "name": "RP关节测试流程", "cases": cases},
        ],
    }


def main():
    nodes = parse_nodes(sys.argv[1] if len(sys.argv) > 1 else "")
    plan = build(nodes)
    os.makedirs(os.path.dirname(PLAN_OUT), exist_ok=True)
    with open(PLAN_OUT, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    n = sum(len(s["cases"]) for s in plan["sequences"])
    print("已生成: %s\n节点: %s | 序列: %d | 用例: %d" % (PLAN_OUT, nodes, len(plan["sequences"]), n))


if __name__ == "__main__":
    main()