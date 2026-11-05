# -*- coding: utf-8 -*-
"""把《机械臂分装线》计划里的 action 用例批量改成 measurement 用例。

用法：
    python3 tools/plan_arm_measurement.py                 # 就地转换（自动备份 .bak）
    python3 tools/plan_arm_measurement.py --dry-run       # 只打印统计
    python3 tools/plan_arm_measurement.py --plan 某个.plan

转换规则：
    - type: action -> measurement
    - config: {script, function, params[], returns[]}
      script  = scripts/arm/arm_test.py
      returns = 一个数值项 + 判定方式 + 阈值（报告里能看到具体数字）
    - timeout_ms 按动作类型重设（原来的 5s 对运动类完全不够）
    - 每条用例的 description 写明测量内容与单位
"""

import argparse
import json
import os
import re
import shutil
import sys
import time

SCRIPT = "scripts/arm/arm_test.py"
JOINT_LIMITS = {
    1: (-3.04, 3.04), 2: (0.02, 2.95), 3: (-2.95, 2.95), 4: (-2.345, 0.09),
    5: (-3.02, 3.02), 6: (-0.68, 0.68), 7: (-1.56, 1.56),
    8: (-3.04, 3.04), 9: (0.02, 2.95), 10: (-2.95, 2.95), 11: (-0.09, 2.345),
    12: (-3.02, 3.02), 13: (-0.68, 0.68), 14: (-1.56, 1.56),
}

READ_TIMEOUT = 20000
MOVE_TIMEOUT = 180000
LIMIT_TIMEOUT = 240000
REPEAT_TIMEOUT = 600000


def p(name, value, vtype="str"):
    return {"name": name, "type": vtype, "value": str(value), "source": "value"}


def r(rtype="float", judge="", threshold=""):
    return {"item": "return", "type": rtype, "bind_var": "",
            "judge": judge, "threshold": str(threshold)}


def cfg(function, params=None, returns=None, display="", desc=""):
    return {
        "display": display or desc,
        "script": SCRIPT,
        "function": function,
        "params": params or [],
        "returns": returns or [r()],
        "description": desc,
    }


def joint_of_seq(block, base):
    """基本信息/性能测试 序列：block 与关节号的线性关系。"""
    return (int(block) - int(base)) // 20 + 1


def map_case(seq, case):
    """返回 (function, params, returns, timeout_ms, fail_policy, description)。"""
    name = case.get("name", "").strip()
    block = str(seq.get("block_number", ""))
    seq_name = seq.get("name", "")

    m = re.search(r"关节\s*(\d+)", name)

    if name == "安全防护措施到位":
        return ("measure_safety_ready", [], [r("int", "等于", 3)],
                READ_TIMEOUT, "continue", "急停/安全门/障碍停 三项满足数（期望 3）")

    if name == "左机械臂ID码扫描":
        return ("measure_arm_id", [p("arm", "left")], [r("int", "大于", 0)],
                READ_TIMEOUT, "continue", "左臂 J1 CiA301 序列号")
    if name == "右机械臂ID码扫描":
        return ("measure_arm_id", [p("arm", "right")], [r("int", "大于", 0)],
                READ_TIMEOUT, "continue", "右臂 J1 CiA301 序列号")

    if name == "各节点在线检测":
        return ("measure_ethercat_nodes_online", [], [r("int", "大于", 13)],
                READ_TIMEOUT, "continue", "EtherCAT 从站在线数量（sudo ethercat slaves）")

    if m and "零位对齐" in name:
        j = int(m.group(1))
        return ("measure_joint_jog_panel", [p("joint", j), p("speed", 200, "int")],
                [r("int", "等于", 1)],
                -1, "pause", "J%d 人工点动到零位（不写零），下一步 15060 写零" % j)

    if m and "零位写入" in name:
        j = int(m.group(1))
        return ("measure_joint_zero_write", [p("joint", j)],
                [r("int", "范围内", "-200~200")],
                60000, "pause", "J%d 写零后 0x6064 原始位置" % j)

    if m and "力传感器0值" in name:
        j = int(m.group(1))
        return ("measure_force_sensor", [p("joint", j), p("channel", 0)],
                [r("int", "范围内", "2200~2800")],
                READ_TIMEOUT, "continue", "J%d 力传感器0原始值(0x2401)" % j)

    if m and "力传感器1值" in name:
        j = int(m.group(1))
        return ("measure_force_sensor", [p("joint", j), p("channel", 1)],
                [r("int", "范围内", "2200~2800")],
                READ_TIMEOUT, "continue", "J%d 力传感器1原始值(0x2402)" % j)

    if name == "正方向复核":
        return ("measure_positive_direction", [], [r("float", "大于", 0.05)],
                MOVE_TIMEOUT, "pause", "14 轴正向各 0.1rad 的最小实际位移(rad)")

    if name == "读取Part number":
        j = joint_of_seq(block, 15090)
        return ("measure_identity", [p("joint", j), p("field", "part")],
                [r("str")], READ_TIMEOUT, "continue",
                "J%d 产品码 0x1018:2（字符串）" % j)
    if name == "读取SN":
        j = joint_of_seq(block, 15090)
        return ("measure_identity", [p("joint", j), p("field", "sn")],
                [r("str")], READ_TIMEOUT, "continue",
                "J%d 序列号 0x1018:4（字符串）" % j)
    if name == "读取SW":
        j = joint_of_seq(block, 15090)
        return ("measure_identity", [p("joint", j), p("field", "sw")],
                [r("str")], READ_TIMEOUT, "continue",
                "J%d 软件版本 0x100A（字符串）" % j)
    if name == "读取HW":
        j = joint_of_seq(block, 15090)
        return ("measure_identity", [p("joint", j), p("field", "hw")],
                [r("str")], READ_TIMEOUT, "continue",
                "J%d 硬件版本 0x1009（字符串）" % j)

    if m and "运动到正极限" in name:
        j = int(m.group(1))
        hi = JOINT_LIMITS[j][1]
        speed = 1.0 if "最大速度" in name else 0.3
        return ("measure_move_limit",
                [p("joint", j), p("direction", "pos"), p("speed", speed)],
                [r("float", "范围内", "%.2f~%.2f" % (hi - 0.06, hi + 0.03))],
                LIMIT_TIMEOUT, "pause", "J%d 正极限到位角度(rad)" % j)

    if m and "运动到负极限" in name:
        j = int(m.group(1))
        lo = JOINT_LIMITS[j][0]
        return ("measure_move_limit",
                [p("joint", j), p("direction", "neg"), p("speed", 0.3)],
                [r("float", "范围内", "%.2f~%.2f" % (lo - 0.03, lo + 0.06))],
                LIMIT_TIMEOUT, "pause", "J%d 负极限到位角度(rad)" % j)

    if name == "正极限角度":
        j = joint_of_seq(block, 15100)
        hi = JOINT_LIMITS[j][1]
        return ("measure_limit_angle", [p("joint", j)],
                [r("float", "范围内", "%.2f~%.2f" % (hi - 0.06, hi + 0.03))],
                READ_TIMEOUT, "continue", "J%d 正极限角度(rad)" % j)

    if name == "负极限角度":
        j = joint_of_seq(block, 15100)
        lo = JOINT_LIMITS[j][0]
        return ("measure_limit_angle", [p("joint", j)],
                [r("float", "范围内", "%.2f~%.2f" % (lo - 0.03, lo + 0.06))],
                READ_TIMEOUT, "continue", "J%d 负极限角度(rad)" % j)

    if name == "运动速度":
        j = joint_of_seq(block, 15100)
        return ("measure_joint_speed", [p("joint", j)],
                [r("float", "大于", 0.10)],
                180000, "continue", "J%d 运动峰值速度(rad/s)" % j)

    if m and "归零" in name:
        j = int(m.group(1))
        return ("measure_joint_home", [p("joint", j)],
                [r("float", "范围内", "-0.03~0.03")],
                MOVE_TIMEOUT, "pause", "J%d 归零后角度(rad)" % j)

    if m and "温度" in name:
        j = int(m.group(1))
        return ("measure_joint_temperature", [p("joint", j)],
                [r("float", "范围内", "0~75")],
                READ_TIMEOUT, "continue", "J%d 驱动器温度(℃)" % j)

    if m and "正方向运动1弧度" in name:
        j = int(m.group(1))
        return ("measure_move_delta", [p("joint", j), p("delta", 1.0)],
                [r("float", "范围内", "0.95~1.05")],
                MOVE_TIMEOUT, "pause", "J%d 实际位移(rad)" % j)

    if "重复定位精度" in name:
        arm = "left" if "左臂" in seq_name or "左臂" in name else "right"
        return ("measure_repeatability", [p("arm", arm)],
                [r("float", "小于", 0.02)],
                REPEAT_TIMEOUT, "continue", "%s 臂往返 5 次到位峰峰值(rad)" % arm)

    if "示教按钮有效" in name:
        arm = "left" if "左" in seq_name else "right"
        return ("measure_teach_button", [p("arm", arm)],
                [r("int", "等于", 3)],
                60000, "continue", "%s 臂按下示教后的 run_mode(3=TEACH)" % arm)

    if "碰撞急停检测" in name:
        arm = "left" if "左" in seq_name else "right"
        return ("measure_collision_state", [p("arm", arm)],
                [r("int", "等于", 0)],
                READ_TIMEOUT, "continue", "%s 臂 collision_state(0=无碰撞)" % arm)

    raise KeyError("未映射的用例: [%s] %s / %s" % (block, seq_name, name))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default=os.path.expanduser("~/桌面/机械臂分装线.plan"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path = os.path.abspath(os.path.expanduser(args.plan))
    with open(path, "r", encoding="utf-8") as fp:
        plan = json.load(fp)

    counts = {}
    errors = []
    for seq in plan.get("sequences", []):
        for case in seq.get("cases", []):
            try:
                func, params, returns, timeout, policy, desc = map_case(seq, case)
            except KeyError as exc:
                errors.append(str(exc))
                continue
            counts[func] = counts.get(func, 0) + 1
            if args.dry_run:
                continue
            case["type"] = "measurement"
            case["timeout_ms"] = int(timeout)
            case["fail_policy"] = policy
            case["description"] = desc
            case["config"] = cfg(func, params, returns, desc=desc)

    if errors:
        print("以下用例未映射，转换已中止：")
        for e in errors:
            print("  -", e)
        return 2

    print("用例映射统计：")
    for k in sorted(counts):
        print("  %-32s %d" % (k, counts[k]))
    print("  合计 %d" % sum(counts.values()))

    if args.dry_run:
        print("\n[dry-run] 未写回文件")
        return 0

    backup = path + ".bak-" + time.strftime("%Y%m%d_%H%M%S")
    shutil.copy2(path, backup)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(plan, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    print("\n已写回：%s" % path)
    print("备份  ：%s" % backup)
    return 0


if __name__ == "__main__":
    sys.exit(main())
