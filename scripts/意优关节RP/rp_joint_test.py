# -*- coding: utf-8 -*-
"""意优 RP 系列行星关节 CANopen 测试脚本（多节点，运行于 Test_management_sw 软件内）。

流程用例（每个用例都带 node 参数 = 总线上的节点ID）:
    ① CAN硬件连接    action_yrp_connect
    ② 使能+位置标0   action_yrp_enable_zero(node)  (当前脉冲记为零点0°, 纯上位机记忆不写电机)
    ③ 模式内置        内部固定 PP(轮廓位置)
    ④ 角度输入控制    action_yrp_move(node, angle=±°): 左=0~+90, 右=0~-90

多节点：
    - 一条总线上 N 个模组(node=1..N)，共用 1 个 CAN 总线句柄；
    - 每个节点的软件零点独立记录(ZERO dict)，共用管脚表示；
    - 所有动作/测量函数都带 node 参数，在 PLAN 里用 node 指定要测试的电机节点ID，
      不再输入数量/列表测全部。

依赖： python-can + zlgcan（ZLG 库见工程 library/）。

参数约定（同 canfd_test.py）：
    CAN 连接参数(通道/波特率/库路径)在下方「公共全局前置参数设置」修改，
    PLAN 用例只传 node/角度 等参数。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # 可 import 同目录 rp_canopen

import rp_canopen as rp

try:
    import test_api                       # 在 Test_management_sw 软件内可用
except Exception:
    test_api = None


def _mset(name, *a, **k):
    """test_api.set_measure_* 防护封装, 软件外可独立运行。"""
    if test_api is not None:
        getattr(test_api, name)(*a, **k)


def _log(msg):
    if test_api is not None:
        test_api.log(msg)
    else:
        print("[RP关节] " + msg, flush=True)


# ==========================================================================
# 公共全局前置参数设置（CAN 连接参数）
# ==========================================================================
DEFAULT_NODE = 1                     # 默认节点 ID（所有用例可传 node 覆盖）
DEFAULT_CHANNEL = 0                  # ZLG 通道号
DEFAULT_BITRATE = 1_000_000          # 经典 CAN 波特率(1Mbps)
DEFAULT_DEVICE_TYPE = 41             # ZCANDeviceType.ZCAN_USBCANFD_200U
DEFAULT_DEVICE_INDEX = 0
DEFAULT_LIBPATH = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "library"))
if not os.path.exists(os.path.join(DEFAULT_LIBPATH, "linux", "x86_64")):
    _FALLBACK = "/home/jyzn/桌面/项目/意优/意优RP-CANFD调试器/library"
    if os.path.exists(os.path.join(_FALLBACK, "linux", "x86_64")):
        DEFAULT_LIBPATH = _FALLBACK

DEFAULT_PP_DEG_S = 5.0               # 轮廓速度(°/s)
DEFAULT_PP_ACC_DEG_S2 = 20.0         # 加减速度(°/s²)

# 角度行程
MIN_DEG, MAX_DEG = rp.MIN_DEG, rp.MAX_DEG


# ==========================================================================
# 全局状态（本脚本运行期间保持）
# ==========================================================================
_bus = None            # 共享总线句柄(所有节点公用)
_joints = {}           # node -> RPJoint
ZERO = {}              # node -> 软件零点脉冲


def _get_joint(node=DEFAULT_NODE):
    global _bus, _joints
    node = int(node)
    if node in _joints:
        return _joints[node]
    if _bus is None:
        _bus = rp.open_bus(channel=DEFAULT_CHANNEL, bitrate=DEFAULT_BITRATE,
                           libpath=DEFAULT_LIBPATH, device_type=DEFAULT_DEVICE_TYPE,
                           device_index=DEFAULT_DEVICE_INDEX)
        _log("CAN 总线已打开(共享, 全节点共用)")
    j = rp.RPJoint(node=node, bus=_bus, channel=DEFAULT_CHANNEL)
    _joints[node] = j
    _log("连接 node=%d, 当量=%.0f脉冲/圈" % (node, j.enc_per_rev))
    return j


def _ensure_zero(node=DEFAULT_NODE):
    """确保该节点已标0, 返回零点脉冲。"""
    global ZERO
    node = int(node)
    if node not in ZERO:
        j = _get_joint(node)
        j.set_mode(rp.MODE_PP)
        j.enable()
        ZERO[node] = j.read_pos()
        _log("node%d 内部自动: 使能+标0, 零点=%d 脉冲" % (node, ZERO[node]))
    return ZERO[node]


def _close_all():
    global _bus, _joints, ZERO
    if _bus is not None:
        try:
            _bus.shutdown()
        except Exception:
            pass
    _bus, _joints, ZERO = None, {}, {}


def _parse_node(node):
    """把 PLAN 传入的 node 转成 int 节点ID。node 可为 int/数字字符串。"""
    if node is None:
        return int(DEFAULT_NODE)
    if isinstance(node, str):
        if node.strip().lower() in ("all", "*"):
            return int(DEFAULT_NODE)
        return int(node.strip())
    return int(node)


def _angle_up(angle):
    """"""  # 保留（兼容）
    a = float(angle)
    c = max(MIN_DEG, min(MAX_DEG, a))
    return c, abs(c - a) > 1e-6


# ============================ 动作 ============================
def action_yrp_connect(node=DEFAULT_NODE):
    """① CAN 硬件连接: 打开共享总线并连接指定 node 的电机读当量。"""
    try:
        _get_joint(_parse_node(node))
        return True
    except Exception as e:
        _log("连接失败: {}: {}".format(type(e).__name__, e))
        return False


def action_yrp_enable_zero(node=DEFAULT_NODE):
    """② 使能 + 位置标0（单节点）: 设PP→使能→记当前脉冲为0°。"""
    try:
        j = _get_joint(node)
        j.set_mode(rp.MODE_PP)
        j.enable()
        global ZERO
        ZERO[int(node)] = j.read_pos()
        _log("node%d 已使能并标0: 当前脉冲=%d 记为0°(左+右-)" % (node, ZERO[int(node)]))
        return True
    except Exception as e:
        _log("node%d 使能/标0失败: {}: {}".format(node, type(e).__name__, e))
        return False


def action_yrp_enable_zero_all(node=DEFAULT_NODE):
    """②' 使能+标0（指定节点）: node 参数传要测试的电机节点ID, 只处理该节点。"""
    return action_yrp_enable_zero(node)


def action_yrp_set_params(velocity_deg_s=None, acc_deg_s2=None, node=DEFAULT_NODE):
    """可选: 设置 PP 轮廓速度/加减速(°/s, °/s²)。"""
    try:
        j = _get_joint(node)
        v = float(velocity_deg_s) if velocity_deg_s is not None else DEFAULT_PP_DEG_S
        a = float(acc_deg_s2) if acc_deg_s2 is not None else DEFAULT_PP_ACC_DEG_S2
        j.set_pp_params(v, a)
        _log("node%d PP参数: %g°/s, %g°/s²" % (node, v, a))
        return True
    except Exception as e:
        _log("node%d 设置参数失败: {}".format(node, e))
        return False


def action_yrp_move(angle=None, node=DEFAULT_NODE):
    """④ 控制角度输入（单节点）: angle 为角度(°), 左+右-。内部含模式PP与换算。"""
    try:
        if angle is None:
            _log("缺少参数 angle(°)")
            return False
        node = int(node)
        j = _get_joint(node)
        ang, clamped = _angle_up(angle)
        zero = _ensure_zero(node)
        if clamped:
            _log("node%d angle %.1f 钳制到 [%.0f, %.0f]" % (node, float(angle), MIN_DEG, MAX_DEG))
        j.set_mode(rp.MODE_PP)
        target = zero + rp.deg_to_pulses(ang, j.enc_per_rev)
        j.move_abs(target)
        _log("node%d 移动 %+.2f°(左+右-) -> 目标脉冲%d(=0点%d+%d)" % (
            node, ang, target, zero, rp.deg_to_pulses(ang, j.enc_per_rev)))
        return True
    except Exception as e:
        _log("node%d 移动失败: {}: {}".format(node, type(e).__name__, e))
        return False


def action_yrp_disable(node=DEFAULT_NODE):
    """失能输出（单节点）。"""
    try:
        if int(node) in _joints:
            _joints[int(node)].disable()
        _log("node%s 已失能" % node)
        return True
    except Exception as e:
        _log("node%s 失能失败: {}".format(node, e))
        return False


def action_yrp_disable_all(node=DEFAULT_NODE):
    """失能（指定节点）: node 参数传要失能的电机节点ID, 只处理该节点。"""
    return action_yrp_disable(node)


def action_yrp_disconnect(params=None):
    """关闭 CAN 总线(全部节点)。"""
    _close_all()
    _log("已断开 CAN")
    return True


# ============================ 测量 ============================
def measure_yrp_angle(angle=None, node=DEFAULT_NODE):
    """测量(单节点): 当前角度(相对软件零点)。angle 可作期望值。"""
    try:
        node = int(node)
        j = _get_joint(node)
        pos = j.read_pos()
        zero = ZERO.get(node, 0)
        deg = rp.pulses_to_deg(pos - zero, j.enc_per_rev)
        _mset("set_measure_value", round(deg, 3), unit="deg")
        _mset("set_measure_message", "node%d 脉冲=%d 当量=%.0f/圈" % (node, pos, j.enc_per_rev))
        if angle is not None:
            exp, c = _angle_up(angle)
            _mset("set_measure_expected", round(exp, 3))
        _log("node%d 当前角度: %+.3f° (脉冲%d)" % (node, deg, pos))
        return {"node": node, "value": round(deg, 3), "unit": "deg", "pulses": pos}
    except Exception as e:
        _log("node%s 测量角度失败: {}".format(node, e))
        return {"node": node, "value": None, "result": "ERROR", "message": str(e)}


def measure_yrp_feedback(node=DEFAULT_NODE):
    """测量(单节点): 原始编码器位置(脉冲)与换算角度。"""
    try:
        node = int(node)
        j = _get_joint(node)
        pos = j.read_pos()
        zero = ZERO.get(node, 0)
        _mset("set_measure_field", "pulses", pos)
        _mset("set_measure_field", "enc_per_rev", int(j.enc_per_rev))
        _mset("set_measure_value", round(pos, 0), unit="pulse")
        return {"node": node, "pulses": pos, "enc_per_rev": int(j.enc_per_rev)}
    except Exception as e:
        _log("node%s 读编码器失败: {}".format(node, e))
        return {"node": node, "result": "ERROR", "message": str(e)}


def measure_yrp_od_string(address=0x2082, node=DEFAULT_NODE, expected="", label=""):
    """测量(通用): 读一个 STRING 对象并判定等于 expected。
       一个函数复用多个用例: 传 address(对象索引) 与 expected(期望值, 为空则只记录)。
       常用: SN=0x2082, 产品型号=0x1008, 硬件版本=0x2083, MCU固件=0x2084。
       返回 {"value": 读到的字符串}, 供 PLAN 用 returns judge=等于 threshold=<期望> 判定。"""
    try:
        node = int(node)
        j = _get_joint(node)
        s = j.read_str(int(address), 0)
        if label:
            _log("node%d %s(%s): %s" % (node, label, hex(int(address)), s))
        else:
            _log("node%d 0x%04X: %s" % (node, int(address), s))
        _mset("set_measure_value", s, unit="str")
        if expected:
            _mset("set_measure_expected", str(expected))
        return {"node": node, "address": int(address), "value": s, "expected": str(expected)}
    except Exception as e:
        _log("node%s 读 0x%04X 失败: {}: {}".format(node, int(address), type(e).__name__, e))
        _mset("set_measure_message", str(e))
        return {"node": node, "address": int(address), "value": None, "result": "ERROR", "message": str(e)}


def measure_yrp_status(node=DEFAULT_NODE):
    """测量(单节点): 状态字与错误码。"""
    try:
        node = int(node)
        j = _get_joint(node)
        sw = j.read_statusword()
        ec = j.read_err_code()
        _mset("set_measure_value", "0x%04X" % sw, unit="status")
        _mset("set_measure_field", "err_code", "0x%04X %s" % (ec, rp.ERR_NAMES.get(ec, "")))
        return {"node": node, "statusword": sw, "err_code": ec}
    except Exception as e:
        _log("node%s 读状态失败: {}".format(node, e))
        return {"node": node, "result": "ERROR", "message": str(e)}


def _wait_to_arrive(j, target_pulses, deg_s, timeout_s=None):
    """等电机走到目标: 轮询 状态字bit10(目标到达) 或 位置到位, 超时返回(False, 原因)"""
    tol = max(20, rp.deg_to_pulses(0.5, j.enc_per_rev))
    if timeout_s is None:
        travel = abs(target_pulses - j.read_pos()) / j.enc_per_rev * 360.0
        timeout_s = max(10.0, travel / max(deg_s, 0.5) * 1.4 + 5.0)
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        sw = j.read_statusword()
        if sw & 0x0400:                       # bit10 = target reached
            return True, "target_reached"
        if abs(j.read_pos() - target_pulses) <= tol:
            return True, "position_ok"
        time.sleep(0.25)
    return False, "超时(%.0fs), 当前距目标%.0f脉冲" % (timeout_s, abs(j.read_pos() - target_pulses))


def measure_yrp_roundtrip(angle_a=60.0, angle_b=-60.0, velocity_deg_s=None,
                          acc_deg_s2=None, node=DEFAULT_NODE):
    """往返行程测量(单节点):
       自动(连接→PP→使能→标0)→移到angle_a→等到位→读→移到angle_b→等到位→读→回0。"""
    try:
        node = int(node)
        j = _get_joint(node)
        zero = _ensure_zero(node)
        v = float(velocity_deg_s) if velocity_deg_s is not None else DEFAULT_PP_DEG_S
        a = float(acc_deg_s2) if acc_deg_s2 is not None else DEFAULT_PP_ACC_DEG_S2
        j.set_pp_params(v, a)
        ja = _angle_up(angle_a)[0]
        jb = _angle_up(angle_b)[0]

        ta = zero + rp.deg_to_pulses(ja, j.enc_per_rev)
        j.move_abs(ta)
        ok, why = _wait_to_arrive(j, ta, v)
        pa = j.read_pos()
        da = rp.pulses_to_deg(pa - zero, j.enc_per_rev)
        _log("   node%d 到 %+.1f°: 实测%+.2f° (%s)" % (node, ja, da, why))

        tb = zero + rp.deg_to_pulses(jb, j.enc_per_rev)
        j.move_abs(tb)
        ok, why = _wait_to_arrive(j, tb, v)
        pb = j.read_pos()
        db = rp.pulses_to_deg(pb - zero, j.enc_per_rev)
        _log("   node%d 到 %+.1f°: 实测%+.2f° (%s)" % (node, jb, db, why))

        j.move_abs(zero)                                # 回0(收尾)

        value = round(abs(da - db), 3)
        expected = round(abs(ja - jb), 3)
        _log("node%d 往返: %+.1f°→%+.1f° 实测%.2f° 期望%.2f° 脉冲Δ=%d(当量%.0f/圈)" % (
            node, ja, jb, value, expected, abs(pa - pb), j.enc_per_rev))
        _mset("set_measure_value", value, unit="deg")
        _mset("set_measure_expected", expected)
        return {"node": node, "angle_a": ja, "angle_b": jb, "value": value,
                "expected": expected, "pos_deg_a": round(da, 3), "pos_deg_b": round(db, 3),
                "pulses_a": pa, "pulses_b": pb, "travel_pulses": abs(pa - pb),
                "enc_per_rev": int(j.enc_per_rev)}
    except Exception as e:
        _log("node%s 往返测量失败: {}: {}".format(node, type(e).__name__, e))
        _mset("set_measure_message", str(e))
        return {"node": node, "result": "ERROR", "message": str(e)}


def measure_yrp_roundtrip_all(node=DEFAULT_NODE, angle_a=60.0, angle_b=-60.0,
                              velocity_deg_s=None, acc_deg_s2=None):
    """往返行程测量（指定节点）: node 传要测试的电机节点ID, 只测该节点。
       返回与 measure_yrp_roundtrip 一致, PLAN 直接用 item="value" 判定。"""
    return measure_yrp_roundtrip(angle_a=angle_a, angle_b=angle_b,
                                 velocity_deg_s=velocity_deg_s,
                                 acc_deg_s2=acc_deg_s2, node=node)


def measure_yrp_home0(tolerance=1.0, node=DEFAULT_NODE):
    """回0位测量(单节点): 移回零点, 等到位, 校验 |角度|≤tolerance。"""
    try:
        node = int(node)
        j = _get_joint(node)
        zero = _ensure_zero(node)
        j.set_pp_params(10.0, 30.0)
        j.move_abs(zero)
        ok, why = _wait_to_arrive(j, zero, 10.0)
        pos = j.read_pos()
        deg = rp.pulses_to_deg(pos - zero, j.enc_per_rev)
        value = round(deg, 3)
        tol = float(tolerance)
        passed = abs(deg) <= tol
        _log("node%d 回0位: 实测%+.2f°, 容差±%.1f°, %s (%s)" % (node, deg, tol,
                                                             "PASS" if passed else "FAIL", why))
        _mset("set_measure_value", value, unit="deg")
        _mset("set_measure_expected", 0.0)
        return {"node": node, "value": value, "expected": 0.0, "pass": passed,
                "pulses": pos, "zero_pulse": zero, "enc_per_rev": int(j.enc_per_rev)}
    except Exception as e:
        _log("node%s 回0位失败: {}: {}".format(node, type(e).__name__, e))
        _mset("set_measure_message", str(e))
        return {"node": node, "result": "ERROR", "message": str(e)}


def measure_yrp_home0_all(node=DEFAULT_NODE, tolerance=1.0):
    """回0位测量（指定节点）: node 传要测试的电机节点ID, 只测该节点。
       返回与 measure_yrp_home0 一致, PLAN 直接用 item="value" 判定。"""
    return measure_yrp_home0(tolerance=tolerance, node=node)


def measure_yrp_read_all(node=DEFAULT_NODE):
    """角度/编码器回读（指定节点）: node 传要读取的电机节点ID, 只读该节点。
       返回与 measure_yrp_angle 一致, PLAN 直接用 item="value" 判定。"""
    return measure_yrp_angle(node=node)


def action_yrp_home0(params=None, node=DEFAULT_NODE):
    """动作: 单节点回0位并等到位(不判定)。"""
    try:
        measure_yrp_home0(tolerance=99.0, node=node)
        return True
    except Exception as e:
        _log("node%s 回0位出错: {}".format(node, e))
        return False