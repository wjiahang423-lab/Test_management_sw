# -*- coding: utf-8 -*-
"""整机测试 · 头部 / 腰部（ROS 2 运控 action 接口）。

依赖 scripts/arm/arm_driver.py 的 SSH + ROS 环境执行能力，动作全部阻塞等待
`ros2 action send_goal` 返回，完成后读取 `/mc/<part>/joint_states` 校验实际角度。

角度约定：
    运行时会读取 /mc/<part>/joint_states 的 name 自动识别 yaw / pitch 下标；
    读取失败时按运控 controllers_config.yaml 的 [yaw, pitch] 兜底。
    yaw = 左右转动，pitch = 抬头 / 低头。

位值换算：
    腰部 131072 counts/rev × 减速比 101 = 13238272 counts/rev
    头部 524288 counts/rev × 减速比 1   = 524288 counts/rev
角度输入统一为「度」，脚本内部转弧度后下发；腰部测试默认限制在 ±10° 内。
"""

import math
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "arm"))

import arm_driver as _arm_driver  # noqa: E402

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


driver = _arm_driver.driver
DEG2RAD = math.pi / 180.0

HEAD_INDEX = {"yaw": 0, "pitch": 1}
WAIST_INDEX = {"yaw": 0, "pitch": 1}
_INDEX_CACHE = {}

HEAD_LIMIT_RAD = {"yaw": 1.57, "pitch_pos": 0.695, "pitch_neg": -0.52}
HEAD_LIMIT_DEG = {"yaw": (-89.9, 89.9), "pitch": (-29.8, 39.8)}
MAX_HEAD_TEST_DEG = 45.0
MAX_WAIST_TEST_DEG = 10.0

WAIST_ENC_PER_REV = 131072.0
WAIST_RATIO = 101.0
HEAD_ENC_PER_REV = 524288.0
HEAD_RATIO = 1.0

WAIST_COUNTS_PER_RAD = WAIST_ENC_PER_REV * WAIST_RATIO / (2.0 * math.pi)
HEAD_COUNTS_PER_RAD = HEAD_ENC_PER_REV * HEAD_RATIO / (2.0 * math.pi)


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


def _normalise_axis(axis):
    text = str(axis).strip().lower()
    if text in ("yaw", "偏航", "左右", "左转", "右转"):
        return "yaw"
    if text in ("pitch", "俯仰", "抬头", "低头", "上下"):
        return "pitch"
    raise ValueError("未知轴向: %s（可选 yaw / pitch）" % axis)


def _read_joint_index(part):
    if part in _INDEX_CACHE:
        return _INDEX_CACHE[part]
    default = dict(HEAD_INDEX if part == "head" else WAIST_INDEX)
    topic = "/mc/%s/joint_states" % part
    command = ("timeout 8 ros2 topic echo %s --field name "
               "--qos-reliability best_effort --once" % topic)
    try:
        rc, out, err = driver.mc(command, timeout=20)
        text = (out or "") + "\n" + (err or "")
        names = re.findall(r"^\s*-\s*([A-Za-z0-9_]+)\s*$", text, re.M)
        if not names:
            names = re.findall(r"[\"']([A-Za-z0-9_]+)[\"']", text)
        mapping = {}
        for index, name in enumerate(names):
            low = name.lower()
            if "pitch" in low:
                mapping["pitch"] = index
            if "yaw" in low:
                mapping["yaw"] = index
        if "yaw" in mapping and "pitch" in mapping:
            _INDEX_CACHE[part] = mapping
            _log("[%s] 关节顺序: %s" % (part, names))
            return mapping
    except Exception as exc:
        _log("[%s] 读取关节名失败，使用默认顺序 %s：%s" % (part, default, exc))
    _INDEX_CACHE[part] = default
    return default


def _read_positions(part, dof):
    topic = "/mc/%s/joint_states" % part
    command = ("timeout 8 ros2 topic echo %s --field position "
               "--qos-reliability best_effort --once" % topic)
    rc, out, err = driver.mc(command, timeout=20)
    text = (out or "") + "\n" + (err or "")
    try:
        values = driver._array(text)
    except Exception as exc:
        raise _arm_driver.ArmDriverError(
            "读取 %s 关节位置失败：%s\n%s" % (topic, exc, text[-800:]))
    if len(values) < int(dof):
        raise _arm_driver.ArmDriverError(
            "读取 %s 关节位置不足：需要 %d 个，实际 %r" % (topic, dof, values))
    return values[:int(dof)]


def _send_trajectory(mask, joints_key, joints, speed_key, speed, timeout, label):
    values = ", ".join("%.6f" % float(v) for v in joints)
    goal = ("{mode: 1, trajectories: {arm_mask: %d, %s: [%s], %s: %.4f}}"
            % (int(mask), joints_key, values, speed_key, float(speed)))
    command = ('ros2 action send_goal /zioneer/mc/action '
               'mc_task_msgs/action/ArmTask "%s"' % goal)
    _log("[运控] %s: %s" % (label, goal))
    rc, out, err = driver._execute_action_command(command, float(timeout))
    text = (out or "") + "\n" + (err or "")
    driver._require_action_success(rc, text, label)
    return text


def _check_angle_deg(angle_deg, limit, max_test, label):
    angle = float(angle_deg)
    if not math.isfinite(angle):
        raise ValueError("%s 角度必须是有限数值" % label)
    if abs(angle) > abs(float(max_test)):
        raise ValueError("%s 角度 %.2f° 超过测试安全上限 %.2f°"
                         % (label, angle, max_test))
    low, high = limit
    if angle < low or angle > high:
        raise ValueError("%s 角度 %.2f° 超出机械限位 [%.2f, %.2f]"
                         % (label, angle, low, high))
    return angle


def measure_start_master(timeout=90.0):
    driver.start_master_only(timeout=float(timeout))
    _log("[主站] EtherCAT 主站已就绪")
    _display("EtherCAT主站", "已启动")
    return 1


def measure_stop_master(timeout=15.0):
    driver.stop_master_only(timeout=float(timeout))
    _log("[主站] EtherCAT 主站已停止")
    _display("EtherCAT主站", "已停止")
    return 1


def measure_head_position():
    index = _read_joint_index("head")
    values = _read_positions("head", 2)
    yaw, pitch = values[index["yaw"]], values[index["pitch"]]
    result = {
        "yaw_rad": yaw, "pitch_rad": pitch,
        "yaw_deg": yaw / DEG2RAD, "pitch_deg": pitch / DEG2RAD,
    }
    _log("[头部] 当前 yaw=%.4f rad (%.2f°), pitch=%.4f rad (%.2f°)"
         % (yaw, result["yaw_deg"], pitch, result["pitch_deg"]))
    return result


def measure_head_move(axis="pitch", angle_deg=20.0, direction=1, speed=0.5,
                      settle_s=1.0, timeout=60.0):
    axis = _normalise_axis(axis)
    sign = 1.0 if float(direction) >= 0 else -1.0
    angle = _check_angle_deg(sign * float(angle_deg), HEAD_LIMIT_DEG[axis],
                             MAX_HEAD_TEST_DEG, "头部 %s" % axis)
    index = _read_joint_index("head")
    target = angle * DEG2RAD
    joints = [0.0, 0.0]
    joints[index[axis]] = target
    _send_trajectory(1, "head_joints", joints, "head_speed", speed,
                     timeout, "头部 %s 运动" % axis)
    time.sleep(max(0.0, float(settle_s)))
    after = _read_positions("head", 2)
    actual = after[index[axis]]
    error = actual - target
    result = {
        "axis": axis,
        "target_deg": angle,
        "target_rad": target,
        "actual_deg": actual / DEG2RAD,
        "actual_rad": actual,
        "error_rad": error,
        "error_deg": error / DEG2RAD,
        "pulse_equiv": target * HEAD_COUNTS_PER_RAD,
        "joints_after": after,
    }
    _log("[头部] %s 目标 %.2f° (%.4f rad)，实际 %.2f° (%.4f rad)，误差 %.4f rad"
         % (axis, angle, target, result["actual_deg"], actual, error))
    _display("头部%s误差" % axis, "%.2f°" % result["error_deg"])
    return result


def measure_head_limit(axis="yaw", direction=1, speed=0.5,
                      settle_s=2.0, timeout=90.0):
    """头部极限测试：yaw ±1.57 rad；pitch 正向 0.695 rad、负向 -0.52 rad。"""
    axis = _normalise_axis(axis)
    sign = 1.0 if float(direction) >= 0 else -1.0
    if axis == "yaw":
        target = HEAD_LIMIT_RAD["yaw"] * sign
    else:
        target = (HEAD_LIMIT_RAD["pitch_pos"] if sign > 0
                  else HEAD_LIMIT_RAD["pitch_neg"])
    index = _read_joint_index("head")
    joints = [0.0, 0.0]
    joints[index[axis]] = target
    _send_trajectory(1, "head_joints", joints, "head_speed", speed,
                     timeout, "头部 %s 极限运动" % axis)
    time.sleep(max(0.0, float(settle_s)))
    after = _read_positions("head", 2)
    actual = after[index[axis]]
    error = actual - target
    result = {
        "axis": axis,
        "target_rad": target,
        "target_deg": target / DEG2RAD,
        "actual_rad": actual,
        "actual_deg": actual / DEG2RAD,
        "error_rad": error,
        "error_deg": error / DEG2RAD,
        "pulse_equiv": target * HEAD_COUNTS_PER_RAD,
        "joints_after": after,
    }
    _log("[头部] %s 极限目标 %.4f rad (%.2f°)，实际 %.4f rad，误差 %.4f rad"
         % (axis, target, result["target_deg"], actual, error))
    _display("头部%s极限误差" % axis, "%.4f rad" % error)
    return result


def measure_head_home(speed=0.5, settle_s=1.5, timeout=60.0):
    index = _read_joint_index("head")
    _send_trajectory(1, "head_joints", [0.0, 0.0], "head_speed", speed,
                     timeout, "头部回原位")
    time.sleep(max(0.0, float(settle_s)))
    after = _read_positions("head", 2)
    max_abs = max(abs(v) for v in after)
    result = {
        "yaw_rad": after[index["yaw"]], "pitch_rad": after[index["pitch"]],
        "max_abs_rad": max_abs,
        "max_abs_deg": max_abs / DEG2RAD,
    }
    _log("[头部] 回原位后 yaw=%.4f, pitch=%.4f，最大偏差 %.4f rad"
         % (after[index["yaw"]], after[index["pitch"]], max_abs))
    _display("头部回零偏差", "%.4f rad" % max_abs)
    return result


def measure_waist_position():
    index = _read_joint_index("waist")
    values = _read_positions("waist", 2)
    yaw, pitch = values[index["yaw"]], values[index["pitch"]]
    result = {
        "yaw_rad": yaw, "pitch_rad": pitch,
        "yaw_deg": yaw / DEG2RAD, "pitch_deg": pitch / DEG2RAD,
    }
    _log("[腰部] 当前 yaw=%.4f rad (%.2f°), pitch=%.4f rad (%.2f°)"
         % (yaw, result["yaw_deg"], pitch, result["pitch_deg"]))
    return result


def measure_waist_move(axis="yaw", angle_deg=5.0, direction=1, speed=0.3,
                       settle_s=1.0, timeout=120.0):
    axis = _normalise_axis(axis)
    sign = 1.0 if float(direction) >= 0 else -1.0
    angle = _check_angle_deg(sign * float(angle_deg),
                             (-MAX_WAIST_TEST_DEG, MAX_WAIST_TEST_DEG),
                             MAX_WAIST_TEST_DEG, "腰部 %s" % axis)
    index = _read_joint_index("waist")
    target = angle * DEG2RAD
    joints = [0.0, 0.0]
    joints[index[axis]] = target
    _send_trajectory(2, "waist_joints", joints, "waist_speed", speed,
                     timeout, "腰部 %s 运动" % axis)
    time.sleep(max(0.0, float(settle_s)))
    after = _read_positions("waist", 2)
    actual = after[index[axis]]
    error = actual - target
    result = {
        "axis": axis,
        "target_deg": angle,
        "target_rad": target,
        "actual_deg": actual / DEG2RAD,
        "actual_rad": actual,
        "error_rad": error,
        "error_deg": error / DEG2RAD,
        "pulse_equiv": target * WAIST_COUNTS_PER_RAD,
        "joints_after": after,
    }
    _log("[腰部] %s 目标 %.2f° (%.4f rad, 约 %.0f counts)，实际 %.2f°，误差 %.4f rad"
         % (axis, angle, target, result["pulse_equiv"],
            result["actual_deg"], error))
    _display("腰部%s误差" % axis, "%.2f°" % result["error_deg"])
    return result


def measure_waist_home(speed=0.3, settle_s=1.5, timeout=120.0):
    index = _read_joint_index("waist")
    _send_trajectory(2, "waist_joints", [0.0, 0.0], "waist_speed", speed,
                     timeout, "腰部回零")
    time.sleep(max(0.0, float(settle_s)))
    after = _read_positions("waist", 2)
    max_abs = max(abs(v) for v in after)
    result = {
        "yaw_rad": after[index["yaw"]], "pitch_rad": after[index["pitch"]],
        "max_abs_rad": max_abs,
        "max_abs_deg": max_abs / DEG2RAD,
    }
    _log("[腰部] 回零后 yaw=%.4f, pitch=%.4f，最大偏差 %.4f rad"
         % (after[0], after[1], max_abs))
    _display("腰部回零偏差", "%.4f rad" % max_abs)
    return result


def measure_waist_pulse_value(angle_deg=5.0):
    angle = float(angle_deg)
    if abs(angle) > MAX_WAIST_TEST_DEG:
        raise ValueError("腰部换算角度 %.2f° 超过安全上限 %.2f°"
                         % (angle, MAX_WAIST_TEST_DEG))
    counts = angle * DEG2RAD * WAIST_COUNTS_PER_RAD
    _log("[腰部] %.2f° = %.4f rad = %.0f counts（131072×101 counts/rev）"
         % (angle, angle * DEG2RAD, counts))
    return {"angle_deg": angle, "angle_rad": angle * DEG2RAD, "counts": counts}


def measure_head_pulse_value(angle_deg=20.0):
    angle = float(angle_deg)
    if abs(angle) > MAX_HEAD_TEST_DEG:
        raise ValueError("头部换算角度 %.2f° 超过安全上限 %.2f°"
                         % (angle, MAX_HEAD_TEST_DEG))
    counts = angle * DEG2RAD * HEAD_COUNTS_PER_RAD
    _log("[头部] %.2f° = %.4f rad = %.0f counts（524288 counts/rev）"
         % (angle, angle * DEG2RAD, counts))
    return {"angle_deg": angle, "angle_rad": angle * DEG2RAD, "counts": counts}
