# -*- coding: utf-8 -*-
"""生成整机测试计划：data/plans/整机测试.plan。

整机 = 头部 + 手臂 + 腰部 + 腰部升降柱。
手臂复用 scripts/arm/arm_test.py，头部/腰复用 scripts/whole_machine/whole_machine_test.py，
升降柱使用 scripts/whole_machine/lifter_telnet.py（Telnet 192.168.88.30:23）。
"""

import copy
import json
import os
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAN_PATH = os.path.join(ROOT, "data", "plans", "整机测试.plan")
ARM_PLAN_PATH = os.path.join(ROOT, "data", "plans", "机械臂分装线.plan")
# 这些序列与整机前置/20010 手臂功能测试重复，复制时会跳过：
# 15010/15020/15030 前置与ID扫描、15040 通信检测、15085 正方向复核（含启停运控）。
ARM_SKIP_BLOCKS = {"15010", "15020", "15030", "15040", "15085"}

ARM = "scripts/arm/arm_test.py"
HEAD = "scripts/whole_machine/whole_machine_test.py"
LIFT = "scripts/whole_machine/lifter_telnet.py"
PICTURE = "scripts/set_picture_show.py"
SCAN = "scripts/scan_sn.py"
MASTER = "scripts/arm/etherkit_master.py"


def _id():
    return uuid.uuid4().hex[:12]


def param(name, ptype, value, source="value"):
    return {"name": name, "type": ptype, "value": value, "source": source}


def var_param(name, vtype, var_name):
    return {"name": name, "type": vtype, "value": var_name, "source": "variable"}


def ret(item="return", rtype="float", judge="", threshold="", bind_var=""):
    return {"item": item, "type": rtype, "bind_var": bind_var,
            "judge": judge, "threshold": threshold}


def measurement(name, func, params=None, returns=None, desc="", timeout=60000,
                retry=0, fail_policy="continue", skip=False, case_no="",
                display="", script=HEAD):
    return {
        "id": _id(), "case_no": case_no, "name": name, "type": "measurement",
        "timeout_ms": int(timeout), "retry": int(retry),
        "fail_policy": fail_policy, "skip": bool(skip), "description": desc,
        "config": {
            "display": display or desc,
            "script": script,
            "function": func,
            "params": params or [],
            "returns": returns or [],
        },
    }


def action(name, func, desc="", timeout=120000, retry=0,
           fail_policy="pause", skip=False, case_no="", script=ARM):
    return {
        "id": _id(), "case_no": case_no, "name": name, "type": "action",
        "timeout_ms": int(timeout), "retry": int(retry),
        "fail_policy": fail_policy, "skip": bool(skip), "description": desc,
        "config": {"script": script, "function": func, "description": desc},
    }


def delay(ms, desc=""):
    return {
        "id": _id(), "case_no": "", "name": "延时", "type": "delay",
        "timeout_ms": int(ms) + 5000, "retry": 0, "fail_policy": "pause",
        "skip": False, "description": desc,
        "config": {"delay_ms": int(ms), "description": desc},
    }


def pop(name, title, content, btn_true="是，已到位", btn_false="否，未到位",
        desc="", timeout=-1, fail_policy="pause"):
    return {
        "id": _id(), "case_no": "", "name": name, "type": "pop",
        "timeout_ms": int(timeout), "retry": 0, "fail_policy": fail_policy,
        "skip": False, "description": desc or content,
        "config": {
            "title": title, "content": content,
            "btn_true": btn_true, "btn_false": btn_false,
            "result_var": "", "modal": True,
        },
    }


def sequence(name, block_number, cases, skip=False):
    return {"id": _id(), "name": name, "block_number": str(block_number),
            "skip": bool(skip), "cases": cases}


def _head_move_case(name, axis, direction, case_no):
    return measurement(
        name, "measure_head_move",
        params=[param("axis", "str", axis),
                var_param("angle_deg", "float", "head_angle_deg"),
                param("direction", "float", str(direction)),
                var_param("speed", "float", "head_speed"),
                param("settle_s", "float", "1.0")],
        returns=[ret("error_rad", "float", "范围内", "-0.05~0.05")],
        desc="%s：%s 轴 %s，角度取变量 head_angle_deg" % (
            name, axis, "正方向" if direction > 0 else "负方向"),
        timeout=90000, fail_policy="pause", case_no=case_no)


def _waist_move_case(name, axis, direction, case_no):
    return measurement(
        name, "measure_waist_move",
        params=[param("axis", "str", axis),
                var_param("angle_deg", "float", "waist_angle_deg"),
                param("direction", "float", str(direction)),
                var_param("speed", "float", "waist_speed"),
                param("settle_s", "float", "1.0")],
        returns=[ret("error_rad", "float", "范围内", "-0.03~0.03")],
        desc="%s：%s 轴 %s，角度取变量 waist_angle_deg（安全上限 10°）" % (
            name, axis, "正方向" if direction > 0 else "负方向"),
        timeout=150000, fail_policy="pause", case_no=case_no)


def _regen_ids(node):
    """复制外部计划时重新生成 sequence/case id，避免与整机计划已有 id 冲突。"""
    if isinstance(node, dict):
        if "id" in node:
            node["id"] = _id()
        for value in node.values():
            _regen_ids(value)
    elif isinstance(node, list):
        for value in node:
            _regen_ids(value)
    return node


def load_arm_sequences():
    """从机械臂分装线计划复制手臂测试项（保留原 block_number、skip 标记）。"""
    if not os.path.isfile(ARM_PLAN_PATH):
        raise FileNotFoundError("未找到手臂计划：%s" % ARM_PLAN_PATH)
    with open(ARM_PLAN_PATH, "r", encoding="utf-8") as fp:
        arm_plan = json.load(fp)
    sequences = []
    for sequence_data in arm_plan.get("sequences", []):
        block = str(sequence_data.get("block_number", "") or "")
        if block in ARM_SKIP_BLOCKS:
            continue
        if not sequence_data.get("cases"):
            continue
        sequence = _regen_ids(copy.deepcopy(sequence_data))
        if sequence.get("name", "").endswith("基本信息记录"):
            for case in sequence.get("cases", []):
                config = case.setdefault("config", {})
                if config.get("function") == "measure_identity":
                    config["function"] = "measure_identity_record"
                    config["returns"] = []
                    note = "（只记录，不判断，空值也通过）"
                    config["description"] = config.get("description", "") + note
                    case["description"] = case.get("description", "") + note
                case["fail_policy"] = "continue"
        sequences.append(sequence)
    return sequences


def build_plan():
    variables = {
        "head_speed": {"type": "float", "value": 0.5,
                       "description": "头部运控速度 0~1"},
        "waist_angle_deg": {"type": "float", "value": 5.0,
                            "description": "腰部测试角度（度），默认 5°，脚本硬限制 ≤10°"},
        "waist_speed": {"type": "float", "value": 0.3,
                        "description": "腰部运控速度 0~1"},
        "lift_up_position": {"type": "int", "value": 80,
                             "description": "升降柱上升位值（0~2000）"},
        "lift_down_position": {"type": "int", "value": 0,
                               "description": "升降柱回零位值"},
        "lift_speed": {"type": "int", "value": 80,
                       "description": "升降柱速度（0~200）"},
        "lift_wait_s": {"type": "float", "value": 12.0,
                        "description": "升降柱每条指令后的等待时间（秒）"},
    }

    seq_prereq = sequence("整机测试前提（主站/使能/运控）", "20001", [
        measurement("扫码读取SN", "scan_sn",
                    params=[param("params", "dict", "")],
                    returns=[ret("success", "bool", "等于", "True")],
                    desc="扫描整机 SN", timeout=300000, script=SCAN),
        measurement("启动EtherCAT主站", "measure_start_master",
                    params=[param("timeout", "float", "90")],
                    returns=[ret("return", "int", "等于", "1")],
                    desc="启动/复用 EtherCAT 主站并等待从站 OP", timeout=240000,
                    fail_policy="pause", script=HEAD),
        delay(15000, "等待主站稳定"),
        measurement("安全防护措施到位", "measure_safety_ready",
                    returns=[ret("return", "int", "等于", "3")],
                    desc="急停/安全门/障碍停 三项满足数（期望 3）", timeout=60000, script=ARM),
        measurement("等待手臂驱动初始化", "wait_motors_status_word",
                    params=[param("expected", "str", "0x1208"),
                            param("timeout", "float", "120")],
                    returns=[ret("return", "int", "等于", "14")],
                    desc="等待 14 轴状态字进入 0x1208", timeout=150000,
                    fail_policy="pause", script=ARM),
        action("使能全部关节", "enable_all_joints",
               "运动控制前置：后台使能 14 个手臂关节", timeout=180000),
        measurement("等待使能完成", "wait_enable_ready",
                    params=[param("expected", "str", "0x1237"),
                            param("timeout", "float", "60")],
                    returns=[ret("return", "int", "等于", "14")],
                    desc="等待 14 轴状态字 = 0x1237", timeout=90000,
                    fail_policy="pause", script=ARM),
        measurement("EtherCAT节点在线检测", "measure_ethercat_nodes_online",
                    returns=[ret("return", "int", "大于", "13")],
                    desc="EtherCAT 从站节点数", timeout=30000, script=ARM),
        measurement("左臂ID码扫描", "measure_arm_id",
                    params=[param("arm", "str", "left")],
                    returns=[ret("return", "int", "大于", "0")],
                    desc="左臂 J1 CiA301 序列号", timeout=30000, script=ARM),
        measurement("右臂ID码扫描", "measure_arm_id",
                    params=[param("arm", "str", "right")],
                    returns=[ret("return", "int", "大于", "0")],
                    desc="右臂 J1 CiA301 序列号", timeout=30000, script=ARM),
        action("启动mc/install运控程序", "measure_motion_control_start",
               "启动 ~/mc/install 运控栈（主站已在运行则跳过）", timeout=240000),
        delay(3000, "等待运控 action 服务就绪"),
    ])

    seq_arm = sequence("手臂功能测试", "20010", [
        measurement("正方向小角度校验", "measure_positive_direction_small_angle",
                    params=[param("timeout", "float", "120")],
                    returns=[ret("return", "int", "等于", "1")],
                    desc="执行 CIT 项 mc.arm.small_angle，记录动作前原位",
                    timeout=180000, fail_policy="pause", script=ARM),
        measurement("确认动作与图片一致", "test_set_picture_show",
                    params=[param("window_width", "int", "950"),
                            param("window_height", "int", "900"),
                            param("font_size", "int", "22"),
                            param("image_width", "int", "900"),
                            param("image_height", "int", "750"),
                            param("text_content", "str",
                                  "请确认，双臂姿势如图片所示！"),
                            param("image_name", "str",
                                  "arm_test_right_place.jpg")],
                    returns=[ret("result", "str", "等于", "Pass")],
                    desc="人工确认双臂小角度动作", timeout=180000,
                    script=PICTURE),
        measurement("恢复动作前原位", "measure_motion_restore",
                    params=[param("speed", "float", "0.05"),
                            param("timeout", "float", "180")],
                    returns=[ret("return", "float", "小于", "0.05")],
                    desc="双臂慢速回到小角度校验前原位", timeout=240000,
                    fail_policy="pause", script=ARM),
    ])

    seq_head = sequence("头部功能测试", "20020", [
        pop("头部动作安全确认", "头部测试安全确认",
            "请确认头部运动范围内无人、无遮挡。\n\n确认后点击“是”开始头部测试。",
            btn_true="是，开始测试", btn_false="否，取消测试",
            desc="头部运动前人工安全确认", fail_policy="pause"),
        measurement("头部当前位置", "measure_head_position",
                    desc="读取 /mc/head/joint_states 位置", timeout=30000),
        measurement("头部左转极限", "measure_head_limit",
                    params=[param("axis", "str", "yaw"),
                            param("direction", "float", "1"),
                            var_param("speed", "float", "head_speed"),
                            param("settle_s", "float", "2.0")],
                    returns=[ret("error_rad", "float", "范围内", "-0.05~0.05")],
                    desc="yaw 极限 +1.57 rad（约 +90°）",
                    timeout=120000, fail_policy="pause", case_no="2002001"),
        measurement("头部右转极限", "measure_head_limit",
                    params=[param("axis", "str", "yaw"),
                            param("direction", "float", "-1"),
                            var_param("speed", "float", "head_speed"),
                            param("settle_s", "float", "2.0")],
                    returns=[ret("error_rad", "float", "范围内", "-0.05~0.05")],
                    desc="yaw 极限 -1.57 rad（约 -90°）",
                    timeout=120000, fail_policy="pause", case_no="2002002"),
        measurement("头部抬头极限", "measure_head_limit",
                    params=[param("axis", "str", "pitch"),
                            param("direction", "float", "1"),
                            var_param("speed", "float", "head_speed"),
                            param("settle_s", "float", "2.0")],
                    returns=[ret("error_rad", "float", "范围内", "-0.05~0.05")],
                    desc="pitch 正向极限 +0.695 rad（约 +39.8°）",
                    timeout=120000, fail_policy="pause", case_no="2002003"),
        measurement("头部低头极限", "measure_head_limit",
                    params=[param("axis", "str", "pitch"),
                            param("direction", "float", "-1"),
                            var_param("speed", "float", "head_speed"),
                            param("settle_s", "float", "2.0")],
                    returns=[ret("error_rad", "float", "范围内", "-0.05~0.05")],
                    desc="pitch 负向极限 -0.52 rad（约 -29.8°）",
                    timeout=120000, fail_policy="pause", case_no="2002004"),
        measurement("头部恢复原位", "measure_head_home",
                    params=[var_param("speed", "float", "head_speed")],
                    returns=[ret("max_abs_rad", "float", "小于", "0.03")],
                    desc="头部 yaw/pitch 回 0", timeout=90000,
                    fail_policy="pause"),
        measurement("头部位置确认", "measure_head_position",
                    returns=[ret("yaw_deg", "float", "范围内", "-2~2"),
                             ret("pitch_deg", "float", "范围内", "-2~2")],
                    desc="回原位后位置复核（度）", timeout=30000),
    ])

    seq_waist = sequence("腰部功能测试", "20030", [
        pop("腰部动作安全确认", "腰部测试安全确认",
            "腰部测试角度限制在 10° 以内。\n\n请确认腰部运动范围内无人、无遮挡。",
            btn_true="是，开始测试", btn_false="否，取消测试",
            desc="腰部运动前人工安全确认", fail_policy="pause"),
        measurement("腰部当前位置", "measure_waist_position",
                    desc="读取 /mc/waist/joint_states 位置", timeout=30000),
        measurement("腰部角度位值换算", "measure_waist_pulse_value",
                    params=[var_param("angle_deg", "float", "waist_angle_deg")],
                    desc="角度转弧度并换算 counts（131072×101 counts/rev）",
                    timeout=15000),
        _waist_move_case("腰部偏航正转", "yaw", 1, "2003001"),
        _waist_move_case("腰部偏航反转", "yaw", -1, "2003002"),
        _waist_move_case("腰部俯仰正转", "pitch", 1, "2003003"),
        _waist_move_case("腰部俯仰反转", "pitch", -1, "2003004"),
        measurement("腰部回零", "measure_waist_home",
                    params=[var_param("speed", "float", "waist_speed")],
                    returns=[ret("max_abs_rad", "float", "小于", "0.03")],
                    desc="腰部 yaw/pitch 回 0", timeout=150000,
                    fail_policy="pause"),
        measurement("腰部位置确认", "measure_waist_position",
                    returns=[ret("yaw_deg", "float", "范围内", "-2~2"),
                             ret("pitch_deg", "float", "范围内", "-2~2")],
                    desc="回零后位置复核（度）", timeout=30000),
    ])

    seq_lift = sequence("腰部升降柱测试", "20040", [
        pop("升降柱动作安全确认", "腰部升降柱测试安全确认",
            "即将下发 bollardmove 80 80 上升到 80，再回 0。\n\n"
            "请确认升降柱周围无人、无遮挡、无异物。",
            btn_true="是，开始测试", btn_false="否，取消测试",
            desc="升降柱运动前人工安全确认", fail_policy="pause"),
        measurement("升降柱上升至设定位值", "lifter_move",
                    params=[var_param("position", "int", "lift_up_position"),
                            var_param("speed", "int", "lift_speed"),
                            var_param("wait_s", "float", "lift_wait_s")],
                    returns=[ret("sent", "int", "等于", "1")],
                    desc="Telnet 192.168.88.30:23 下发 bollardmove <位值> <速度>",
                    timeout=90000, fail_policy="pause", script=LIFT),
        delay(2000, "等待上升动作稳定"),
        measurement("升降柱回零", "lifter_move",
                    params=[var_param("position", "int", "lift_down_position"),
                            var_param("speed", "int", "lift_speed"),
                            var_param("wait_s", "float", "lift_wait_s")],
                    returns=[ret("sent", "int", "等于", "1")],
                    desc="下发 bollardmove 0 <速度>，回到 0 位",
                    timeout=90000, fail_policy="pause", script=LIFT),
        delay(2000, "等待回零动作稳定"),
        pop("升降柱回零到位确认", "升降柱回零确认",
            "请确认升降柱是否已回到 0 位（回零到位）。\n\n"
            "到位点“是”，未到位点“否”。",
            btn_true="是，已回零到位", btn_false="否，未回零到位",
            desc="人工判断回零是否到位", fail_policy="continue"),
    ])

    seq_stop_before_arm = sequence("停止运控（手臂静态测试前）", "20045", [
        action("停止运控程序", "measure_motion_control_stop",
               "手臂基本信息/静态读取前停止运控节点，保留 EtherCAT 主站",
               timeout=120000, fail_policy="continue"),
        delay(2000, "等待运控节点退出"),
    ])

    arm_test_sequences = load_arm_sequences()

    seq_finish = sequence("测试收尾", "20050", [
        action("停止运控程序", "measure_motion_control_stop",
               "停止运控节点（保留 EtherCAT 主站）", timeout=120000,
               fail_policy="continue"),
        delay(2000, "等待运控节点退出"),
        measurement("停止EtherCAT主站", "measure_stop_master",
                    params=[param("timeout", "float", "15")],
                    returns=[ret("return", "int", "等于", "1")],
                    desc="关闭 EtherCAT 主站", timeout=60000,
                    fail_policy="continue", script=HEAD),
    ])

    return {
        "name": "整机测试",
        "description": "整机测试：主站/使能/运控启动 → 手臂小角度 → 头部 → 腰部 → 腰部升降柱 → "
                       "手臂基本信息/测试项（从机械臂分装线计划复制 15050~15440）→ 收尾。"
                       "手臂复制项中 15010/15020/15030/15040/15085 与整机前置或 20010 重复，未重复复制。"
                       "头部左右/上下使用极限值 (yaw ±1.57 rad, pitch +0.695/-0.52 rad)，最后归零；"
                       "腰部角度以「度」输入，脚本转弧度，硬限制 10°。"
                       "脚本会读取 joint_states 的 name 自动识别 yaw/pitch 下标，"
                       "不依赖 head_joints/waist_joints 的固定顺序；"
                       "默认按 controllers_config.yaml 的 [yaw, pitch] 兜底。",
        "variables": variables,
        "settings": {
            "storage_mode": "local", "batch": "", "log_dir": "", "report_dir": "",
            "continuous": False,
            "remote_storage_enabled": False, "remote_storage_url": "",
            "remote_storage_content": "both", "remote_storage_strategy": "always",
            "json_upload_enabled": False, "json_upload_url": "", "json_upload_key": "",
            "series_id": "", "is_final": False,
            "server_username": "root", "server_password": "root",
            "mes_server": "", "mes_interface": "", "mes_template": "",
            "remote_file_server": "", "remote_db_ip": "", "remote_db_table": "",
            "login_url": "", "login_username": "", "login_password": "",
            "token_header": "Authorization", "token_prefix": "Bearer ",
        },
        "sequences": ([seq_prereq, seq_arm, seq_head, seq_waist, seq_lift,
                       seq_stop_before_arm] + arm_test_sequences + [seq_finish]),
    }


def main():
    plan = build_plan()
    os.makedirs(os.path.dirname(PLAN_PATH), exist_ok=True)
    with open(PLAN_PATH, "w", encoding="utf-8") as fp:
        json.dump(plan, fp, ensure_ascii=False, indent=2)
    print("已生成：%s" % PLAN_PATH)
    print("序列数：%d，用例数：%d" % (
        len(plan["sequences"]), sum(len(s["cases"]) for s in plan["sequences"])))


if __name__ == "__main__":
    main()
