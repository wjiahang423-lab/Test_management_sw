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
    """安全调用测试平台的测量结果接口。

    脚本既可以在 Test_management_sw 中运行，也可以在普通 Python 环境中
    单独调试。未加载 ``test_api`` 时忽略测量上报，避免影响底层测试逻辑。
    """
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
    """获取指定节点的关节对象，并按需创建共享 CAN 总线连接。

    同一节点重复调用时复用 ``_joints`` 中的对象；首次使用任意节点时
    打开一次共享总线，后续节点共用该总线句柄。
    """
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
    """确保指定节点完成机械标零，并返回软件记录的零点脉冲。

    首次调用会切换到 PP 模式、使能电机、写入对象 ``0x2262``，再读取
    ``0x6064`` 保存当前位置。后续调用直接复用 ``ZERO``，不会重复标零。
    """
    global ZERO
    node = int(node)
    if node not in ZERO:
        j = _get_joint(node)
        j.set_mode(rp.MODE_PP)
        j.enable()
        ok, pos_before, pos_after = j.mech_home()
        ZERO[node] = j.read_pos()
        _log("node%d 机械标0: 标定前=%d, 0x6064读数=%d, ok=%s"
             % (node, pos_before, ZERO[node], ok))
    return ZERO[node]


def _close_all():
    """关闭共享 CAN 总线并清空节点对象和软件零点状态。"""
    global _bus, _joints, ZERO
    if _bus is not None:
        try:
            _bus.shutdown()
        except Exception:
            pass
    _bus, _joints, ZERO = None, {}, {}


def _parse_node(node):
    """将 PLAN 传入的节点参数转换为整数节点 ID。

    支持整数、数字字符串和空值；``all``/``*`` 为兼容旧 PLAN 的写法，
    当前按默认节点处理，而不是遍历所有节点。
    """
    if node is None:
        return int(DEFAULT_NODE)
    if isinstance(node, str):
        if node.strip().lower() in ("all", "*"):
            return int(DEFAULT_NODE)
        return int(node.strip())
    return int(node)


def _angle_up(angle):
    """将输入角度限制在关节允许范围内，并返回是否发生钳制。"""
    a = float(angle)
    c = max(MIN_DEG, min(MAX_DEG, a))
    return c, abs(c - a) > 1e-6


# ============================ 动作 ============================
def action_yrp_connect(node=DEFAULT_NODE):
    """连接指定节点并读取编码器每圈脉冲当量。

    该动作会按需打开共享 CAN 总线，创建节点对象并缓存；连接成功返回
    ``True``，底层异常会被记录并转换为 ``False``。
    """
    try:
        _get_joint(_parse_node(node))
        return True
    except Exception as e:
        _log("连接失败: {}: {}".format(type(e).__name__, e))
        return False


def action_yrp_enable_zero(node=DEFAULT_NODE):
    """使能指定节点，并将当前机械位置标定为电机零点。

    执行顺序为切换 PP 模式、使能、写入 ``0x2262``，然后读取 ``0x6064``
    更新 ``ZERO[node]``。标定成功返回设备结果 ``True``，失败或异常返回
    ``False``。
    """
    try:
        j = _get_joint(node)
        j.set_mode(rp.MODE_PP)
        j.enable()
        ok, pos_before, pos_after = j.mech_home()
        global ZERO
        ZERO[int(node)] = j.read_pos()
        if ok:
            _log("node%d 机械0位标定成功: 标定前脉冲=%d, 现读数=%d(0x6064)"
                 % (node, pos_before, ZERO[int(node)]))
        else:
            _log("node%d 机械0位标定失败: 写0x2262被拒绝" % node)
        return ok
    except Exception as e:
        _log("node%d 使能/机械标0失败: {}: {}".format(node, type(e).__name__, e))
        return False


def action_yrp_enable_zero_all(node=DEFAULT_NODE):
    """兼容旧测试计划的使能标零入口，只处理传入的一个节点。

    函数名中的 ``all`` 不表示遍历全部节点，实际行为与
    :func:`action_yrp_enable_zero` 完全一致。
    """
    return action_yrp_enable_zero(node)



def action_yrp_set_params(velocity_deg_s=None, acc_deg_s2=None, node=DEFAULT_NODE):
    """设置指定节点的 PP 轮廓速度和加减速度。

    参数单位分别为 ``°/s`` 和 ``°/s²``；参数为空时使用脚本默认值。
    设置成功返回 ``True``，参数转换或设备通信异常返回 ``False``。
    """
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
    """命令指定节点移动到相对软件零点的目标角度。

    正角度表示左转、负角度表示右转。函数会自动确保零点存在，切换
    PP 模式，将角度换算为绝对脉冲位置后发送运动指令；超出行程的输入
    会被限制到 ``MIN_DEG`` 和 ``MAX_DEG``。命令发送成功返回 ``True``。
    """
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
    """关闭指定节点的电机输出。

    节点尚未建立连接时只记录状态，不主动创建连接；执行过程无异常时
    返回 ``True``，否则返回 ``False``。
    """
    try:
        if int(node) in _joints:
            _joints[int(node)].disable()
        _log("node%s 已失能" % node)
        return True
    except Exception as e:
        _log("node%s 失能失败: {}".format(node, e))
        return False


def action_yrp_disable_all(node=DEFAULT_NODE):
    """兼容旧测试计划的失能入口，只处理传入节点，不遍历全部节点。"""
    return action_yrp_disable(node)


def action_yrp_disconnect(params=None):
    """关闭共享 CAN 总线并释放全部已缓存节点。

    ``params`` 仅用于兼容测试平台动作函数签名；无论是否传入都会关闭
    当前共享连接，并清空软件零点记录。
    """
    _close_all()
    _log("已断开 CAN")
    return True


# ============================ 测量 ============================
def measure_yrp_angle(angle=None, node=DEFAULT_NODE):
    """读取指定节点相对软件零点的当前角度。

    位置反馈以脉冲读取，再按编码器当量换算为角度并上报；传入
    ``angle`` 时同时将其作为期望值上报，但不在本函数内完成判定。
    成功返回包含节点、角度、单位和原始脉冲的字典，异常返回错误字典。
    """
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
    """读取指定节点的原始编码器位置和每圈脉冲当量。

    该测量面向底层反馈诊断，返回当前位置 ``pulses`` 和
    ``enc_per_rev``，不对位置是否到达目标进行判断。
    """
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
    """读取一个 CANopen STRING 对象，并可选上报期望值。

    ``address`` 是对象索引，``expected`` 为空时只记录实际值，否则交给
    测试平台按期望值判定。常用索引包括序列号 ``0x2082``、产品型号
    ``0x1008``、硬件版本 ``0x2083`` 和 MCU 固件 ``0x2084``。
    """
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
    """读取指定节点的状态字和错误码，并返回原始数值。

    状态字以十六进制测量值上报，错误码同时附带错误名称，便于定位
    驱动器故障；通信异常时返回包含 ``result=ERROR`` 的错误字典。
    """
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
    """轮询关节直到目标到达或超时。

    优先检查状态字 bit10（目标到达），同时用默认 0.5° 的脉冲容差做
    位置兜底判断。未指定超时时间时，根据剩余行程和速度动态估算。
    返回 ``(是否到位, 原因)``。
    """
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
    """测量指定节点在两个角度之间的实际往返行程。

    函数会自动准备连接、PP 模式、使能和零点，然后依次移动到
    ``angle_a``、``angle_b``，等待到位并读取反馈，最后回到零点。返回
    实测角度差、期望角度差、两个位置脉冲值及编码器当量；通信异常返回
    错误字典。角度超出行程时按允许范围钳制。
    """
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
    """兼容旧测试计划的往返测量入口，只测传入节点。

    名称中的 ``all`` 不表示遍历节点，返回值和参数行为与
    :func:`measure_yrp_roundtrip` 一致，可直接用 ``item="value"`` 判定。
    """
    return measure_yrp_roundtrip(angle_a=angle_a, angle_b=angle_b,
                                 velocity_deg_s=velocity_deg_s,
                                 acc_deg_s2=acc_deg_s2, node=node)


def measure_yrp_home0(tolerance=1.0, node=DEFAULT_NODE):
    """将指定节点移回软件零点，并校验回零误差。

    函数使用固定的回零速度和加速度，等待目标到位后读取实际位置，
    以 ``|角度| <= tolerance`` 作为通过条件。返回实测值、判定结果、
    脉冲位置和零点脉冲。
    """
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
    """兼容旧测试计划的回零测量入口，只测传入节点。

    返回值与 :func:`measure_yrp_home0` 一致，可直接使用 ``value`` 字段
    作为测试平台的判定输入。
    """
    return measure_yrp_home0(tolerance=tolerance, node=node)


def measure_yrp_read_all(node=DEFAULT_NODE):
    """兼容旧测试计划的角度回读入口，只读取传入节点。

    返回值与 :func:`measure_yrp_angle` 一致，不执行运动或额外判定。
    """
    return measure_yrp_angle(node=node)


def action_yrp_home0(params=None, node=DEFAULT_NODE):
    """执行指定节点回零动作并等待到位，但不根据误差判定结果。

    通过调用回零测量函数完成移动和等待，使用较宽容差仅确保动作流程
    执行；测试平台若需严格校验，应调用 ``measure_yrp_home0``。
    """
    try:
        measure_yrp_home0(tolerance=99.0, node=node)
        return True
    except Exception as e:
        _log("node%s 回0位出错: {}".format(node, e))
        return False


def measure_yrp_zero_read(node:int)->dict:
    """执行指定节点机械标零，并返回对象 ``0x6064`` 的位置读数。

    函数会切换 PP 模式、使能、写入机械零点并更新 ``ZERO``，同时将
    零点脉冲作为 pulse 类型测量值上报；返回值中的 ``ok`` 表示设备写入
    零点是否成功。
    """
    try:
        node = int(node)
        j = _get_joint(node)
        j.set_mode(rp.MODE_PP)
        j.enable()
        ok, pos_before, pos_after = j.mech_home()
        global ZERO
        ZERO[node] = j.read_pos()
        _mset("set_measure_value", ZERO[node], unit="pulse")
        _mset("set_measure_message", "node%d 标0位完成, 0x6064=%d" % (node, ZERO[node]))
        _log("node%d 标0位: 0x6064读数=%d, ok=%s" % (node, ZERO[node], ok))
        return {"node": node, "value": ZERO[node], "unit": "pulse", "ok": ok}
    except Exception as e:
        _log("node%d 标0位读取失败: {}: {}".format(node, type(e).__name__, e))
        _mset("set_measure_message", str(e))
        return {"node": node, "value": None, "result": "ERROR", "message": str(e)}

def test_case1(a, b):
    """示例测试函数：计算两个数之和并上报测量说明。

    参数 ``a`` 和 ``b`` 应为可使用 ``+`` 运算的数值；函数返回相加结果，
    并将格式化后的计算过程写入测试平台的测量消息。
    """
    _mset("set_measure_message", "%d 加上%d 的结果是 %d" % (a, b, a + b))
    return a + b
