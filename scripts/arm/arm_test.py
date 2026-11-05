# -*- coding: utf-8 -*-
"""机械臂分装线 · 测试函数集合（Measurement 类型，全部返回数值）。

设计约定
--------
1. 所有对外函数都以 `measure_` 开头，返回值一律是 **数字**（int / float），
   不返回 bool —— 由计划里的「判定方式 + 阈值」决定 PASS/FAIL，报告里也能看到具体数值。
2. 失败分两种：
   - 硬件/通信确实失败 -> 抛异常（引擎直接判 FAIL，并在日志里带原因）；
   - 数据异常但仍可量化 -> 返回数值（例如温度超高、位置偏差大），交由阈值判定。
3. 关节编号统一 1..14：左臂 J1..J7 = 1..7，右臂 J1..J7 = 8..14
   （与 EtherCAT 从站 p1..p14 一一对应）。
4. 全部通过 SSH 命令接口执行并阻塞等待返回，见 arm_driver.py。

依赖：paramiko（requirements.txt 已含）。
"""

import importlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import arm_driver as _arm_driver  # noqa: E402


def _reload_arm_driver_if_changed():
    """软件长时间运行时，脚本文件更新后无需重启 EOL 也能加载新驱动。"""
    try:
        mtime = os.path.getmtime(_arm_driver.__file__)
        if getattr(_arm_driver, "_loaded_mtime", None) != mtime:
            try:
                _arm_driver.driver.close()
            except Exception:
                pass
            module = importlib.reload(_arm_driver)
            module._loaded_mtime = mtime
            return module
    except Exception:
        pass
    return _arm_driver


_arm_driver = _reload_arm_driver_if_changed()
driver = _arm_driver.driver
joint_arm = _arm_driver.joint_arm
joint_index = _arm_driver.joint_index
joint_slave = _arm_driver.joint_slave
JOINT_LIMITS = _arm_driver.JOINT_LIMITS
TOTAL_JOINTS = _arm_driver.TOTAL_JOINTS
ENABLE_SLAVES = _arm_driver.ENABLE_SLAVES
ArmDriverError = _arm_driver.ArmDriverError
INTERFACE = _arm_driver.INTERFACE
MOTION = _arm_driver.MOTION
ROS = _arm_driver.ROS
TOPICS = _arm_driver.TOPICS
TEACH_ADKEY = _arm_driver.TEACH_ADKEY
EXPECT = _arm_driver.EXPECT
THRESHOLDS = _arm_driver.THRESHOLDS

try:
    import test_api
except ImportError:  # 允许脱离 EOL 软件单独调试
    class _StubApi(object):
        @staticmethod
        def log(msg):
            print(msg)

        @staticmethod
        def set_display_info(k, v):
            pass
    test_api = _StubApi()


# ────────────────────────────── 内部工具 ──────────────────────────────
def _log(msg):
    try:
        test_api.log(str(msg))
    except Exception:
        print(msg)


def _fmt(values, nd=4):
    return "[" + ", ".join(("%." + str(nd) + "f") % v for v in values) + "]"


def _require_slaves(minimum=None):
    if minimum is None:
        minimum = int(THRESHOLDS.get("slaves_expect") or EXPECT.get("slaves") or 15)
    n = driver.slave_count()
    _log("EtherCAT 从站数: %d" % n)
    if n < minimum:
        raise ArmDriverError("EtherCAT 从站数异常: %d < %d（检查线缆/供电/主站）" % (n, minimum))
    return n


def _display(key, value, unit=""):
    try:
        test_api.set_display_info(key, ("%s %s" % (value, unit)).strip())
    except Exception:
        pass


# ────────────────────────────── 环境 / 通信 ──────────────────────────────
def measure_safety_ready():
    """前提条件：返回满足的安全条件数量（急停未触发 + 安全门关闭 + 无障碍停 = 3）。"""
    _require_slaves()
    st = driver.robot_status()
    checks = []
    for key, name in (("estop_active", "急停"), ("safety_door_open", "安全门"),
                      ("obstacle_stop", "障碍停")):
        val = st.get(key)
        if val is None:            # 该机型不上报该字段，视为满足
            checks.append(1)
            _log("%s: 未上报，按满足处理" % name)
        else:
            ok = (val is False)
            checks.append(1 if ok else 0)
            _log("%s: %s" % (name, "满足" if ok else "不满足"))
    _display("安全条件", sum(checks))
    return int(sum(checks))


def measure_arm_id(arm="left"):
    """ID 码读取：返回该臂首关节 CiA301 序列号(0x1018:4)；为 0 时回退产品码。"""
    joint = 1 if str(arm).lower().startswith("l") else 8
    sn = driver.identity(joint, "sn")
    if not sn:
        sn = driver.identity(joint, "part")
        _log("0x1018:4 序列号为 0，回退使用产品码(0x1018:2)")
    _log("%s 臂 J1 身份码: %d" % (arm, sn))
    _display("%s臂ID" % arm, sn)
    return int(sn)


def measure_nodes_online(minimum=12):
    """通信检测：返回 ROS 节点数量（字符串型节点名不计）。"""
    nodes = driver.nodes()
    _log("在线节点(%d): %s" % (len(nodes), ", ".join(nodes)))
    _display("节点数", len(nodes))
    return int(len(nodes))


def enable_all_joints():
    """后台使能全部 14 个关节（运动控制前置），返回使能关节数。

    使用独立的交互式 SSH 会话循环下发 cia402_tools 使能命令；命令执行完
    后会话保持，测试用例结束/超时不会打断它，只有计划结束或人工停止时
    才由清理回调关闭。
    """
    import arm_enable
    import importlib
    if not arm_enable.enable_is_running():
        importlib.reload(arm_enable)
    n = arm_enable.start_enable_all()
    _log("已后台使能 %d 个关节" % int(n))
    _display("使能关节数", n)
    return int(n)


def _parse_expected(expected):
    """把 0x1208 / \"0x1208,0x1237\" / [..] 统一成整数列表。"""
    if isinstance(expected, str):
        parts = [p for p in expected.replace("，", ",").replace(" ", ",").split(",") if p]
        return [int(p, 0) for p in parts]
    if isinstance(expected, (list, tuple, set)):
        return [int(v) for v in expected]
    return [int(expected)]


def _fmt_sw(value):
    return ("0x%04X" % value) if isinstance(value, int) else "ERR"


def wait_motors_status_word(expected=0x1208, timeout=120, restart_on="0x1240"):
    """等待 14 个手臂驱动的 0x6041 状态字进入 expected（可传多个值）。

    从站位置来自 arm_interface.yaml 的 jog.enable_slaves（现场为 p2..p15），
    不再用关节号 1..14 直接当从站，避免链首 FCE1100 导致读失败。

    restart_on：现场出现该状态字（默认 0x1240）时，先关闭旧主站，
    再用接口表配置的 XML 重启主站，然后继续等待。只重启一次，避免死循环。
    """
    targets = _parse_expected(expected)
    restart_values = [] if restart_on in (None, "", []) else _parse_expected(restart_on)
    slaves = list(ENABLE_SLAVES)
    timeout = float(timeout)
    deadline = time.time() + timeout
    restarted = False
    last = {}
    while time.time() < deadline:
        ready = 0
        last = {}
        for slave in slaves:
            try:
                value = int(driver.status_word_slave(slave))
            except _arm_driver.ArmOperationCancelled:
                raise
            except Exception as exc:
                value = None
                _log("p%d 状态字读取失败: %s" % (slave, exc))
            last[slave] = value
            if value in targets:
                ready += 1
        _log("状态字 %s 就绪: %d/%d；%s" % (
            "/".join("0x%04X" % v for v in targets), ready, len(slaves),
            ", ".join("p%d=%s" % (s, _fmt_sw(v)) for s, v in sorted(last.items()))))
        trigger = [s for s, v in sorted(last.items()) if v in restart_values]
        if trigger and not restarted:
            _log("检测到 p%s 状态字=%s，关闭旧主站后重启主站 ..." % (
                ",".join(str(s) for s in trigger),
                "/".join("0x%04X" % v for v in restart_values)))
            driver.restart_master()
            restarted = True
            time.sleep(2.0)
            continue
        if ready == len(slaves):
            _display("状态字就绪", len(slaves))
            return int(len(slaves))
        time.sleep(1.0)
    raise ArmDriverError(
        "等待 %d 轴状态字=%s 超时；最后状态: %s" % (
            len(slaves), "/".join("0x%04X" % v for v in targets),
            ", ".join("p%d=%s" % (s, _fmt_sw(v)) for s, v in sorted(last.items()))))


def wait_enable_ready(expected=0x1237, timeout=60):
    """等待全部手臂驱动的 0x6041 状态字等于 expected（默认 0x1237）。

    用于“使能”之后：比固定延时更准确，全部就绪返回轴数（14），
    超时则抛异常并带上每个从站的最后状态字，便于现场定位。
    """
    return wait_motors_status_word(expected=expected, timeout=timeout)


def measure_ethercat_nodes_online():
    """EtherCAT 从站在线检测：返回 `sudo ethercat slaves` 的节点数量。

    输出每行形如 `0  0:0  OP  +  EK1100 ...`：
    第 1 列节点号，第 3 列状态（OP/PREOP/SAFEOP/INIT）。
    """
    _log("执行 sudo ethercat slaves ...")
    lines = driver.slaves()
    count = 0
    state_count = {}
    for line in lines:
        parts = line.split(None, 4)
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        position = parts[0]
        state = parts[2].upper()
        name = parts[4] if len(parts) >= 5 else ""
        state_count[state] = state_count.get(state, 0) + 1
        count += 1
        _log("节点 %s: %s%s" % (position, state, ("  " + name) if name else ""))
    if not count:
        _log("ethercat slaves 原始输出:\n%s" % "\n".join(lines))
    summary = ", ".join("%s=%d" % (k, v) for k, v in sorted(state_count.items()))
    _log("EtherCAT 节点数: %d（%s）" % (count, summary))
    _display("EtherCAT节点数", "%d（%s）" % (count, summary))
    return int(count)


def probe_device():
    """自检：返回 EtherCAT 从站数量，并打印关键寄存器（用于现场核对接口）。"""
    n = driver.slave_count()
    _log("从站数: %d" % n)
    for j in (1, 8):
        _log("J%d: sw=0x%04X err=0x%04X T=%.2fC V=%.2fV trq=%.1f%% part=%d sn=%d" % (
            j, driver.status_word(j), driver.error_code(j), driver.temperature_c(j),
            driver.bus_voltage_v(j), driver.torque_percent(j),
            driver.identity(j, "part"), driver.identity(j, "sn")))
    return int(n)


# ────────────────────────────── 零位 ──────────────────────────────
def measure_joint_zero_align(joint=1, speed=0.15):
    """零位对齐：慢速运动到 0 rad，返回对齐后的关节角度(rad)。"""
    _require_slaves()
    joint = int(joint)
    _log("J%d 零位对齐：运动到 0 rad (speed=%.2f)" % (joint, float(speed)))
    driver.move_joint(joint, 0.0, speed=float(speed), timeout=150)
    time.sleep(0.3)
    arm = joint_arm(joint)
    angle = driver.joint_positions(arm)[joint_index(joint)]
    _log("J%d 对齐后角度: %.5f rad" % (joint, angle))
    _display("J%d角度" % joint, "%.4f rad" % angle)
    return float(angle)


def measure_joint_zero_align_panel(joint=1, speed=200):
    """人工点动+标零：弹出点动面板，操作员完成后返回该关节角度(rad)。"""
    joint = int(joint)
    # 重新加载 jog_panel，避免长时间运行时 sys.modules 缓存旧版面板代码。
    import importlib
    import jog_panel
    importlib.reload(jog_panel)
    show_jog_panel = jog_panel.show_jog_panel

    def _read_angle():
        arm = joint_arm(joint)
        return float(driver.joint_positions(arm)[joint_index(joint)])

    _log("J%d 人工零位对齐：打开点动/标零面板 (speed=%d)" % (joint, int(speed)))
    result = show_jog_panel(
        initial_joint=joint,
        speed=int(speed),
        read_angle=_read_angle,
        lock_joint=True,
        title="J%d 零位对齐 - 点动/标零" % joint,
    )
    if not result.get("accepted"):
        raise ArmDriverError("操作员取消 J%d 零位对齐" % joint)
    try:
        angle = _read_angle()
    except Exception as exc:
        # 只启动了 EtherCAT 主站、未启动运控/伺服节点时 joint_states 不存在。
        # 人工点动已经完成，不应因此判 FAIL；角度留空（nan）交给阈值判定。
        _log("J%d 角度读取失败（%s），跳过角度记录" % (joint, exc))
        return float("nan")
    _log("J%d 人工零位对齐后角度: %.5f rad" % (joint, angle))
    _display("J%d角度" % joint, "%.4f rad" % angle)
    return float(angle)


def measure_joint_jog_panel(joint=1, speed=200):
    """人工点动到零位（不写零）：弹出点动面板，操作员把关节移到零位后点“完成”。

    只做点动，不显示/不执行标零按钮；写零由后续 15060“关节零位写入”负责。
    不依赖 ROS joint_states，完成返回 1（计划按“等于 1”判定）。
    """
    joint = int(joint)
    # 重新加载 jog_panel，避免长时间运行时 sys.modules 缓存旧版面板代码。
    import importlib
    import jog_panel
    importlib.reload(jog_panel)
    show_jog_panel = jog_panel.show_jog_panel

    _log("J%d 人工点动到零位（不写零）：打开点动面板 (speed=%d)" % (joint, int(speed)))
    result = show_jog_panel(
        initial_joint=joint,
        speed=int(speed),
        read_angle=None,
        lock_joint=True,
        show_zero=False,
        title="J%d 点动到零位（不写零）" % joint,
    )
    if not result.get("accepted"):
        raise ArmDriverError("操作员取消 J%d 点动" % joint)
    _log("J%d 点动完成（未写零），下一步 15060 执行写零" % joint)
    _display("J%d点动" % joint, "完成")
    return 1


def measure_joint_zero_write(joint=1):
    """零位写入：把当前位姿写为零点，返回写后 0x6064 原始位置（期望接近 0）。"""
    _require_slaves()
    joint = int(joint)
    _log("J%d 零位写入 (p%d) ..." % (joint, joint_slave(joint)))
    # 计划被停止/异常时，清理后台点动工具并断开 SSH。
    try:
        driver.register_abort_cleanup()
    except Exception:
        pass
    try:
        raw = driver.zero_write(joint)
    except Exception:
        _log("J%d 零位写入失败，停止后台点动/标零工具并关闭通信" % joint)
        try:
            driver.stop_motion_tools()
        except Exception:
            pass
        try:
            driver.close()
        except Exception:
            pass
        raise
    _log("J%d 写后原始位置: %d" % (joint, raw))
    _display("J%d零点" % joint, raw)
    return int(raw)


# ────────────────────────────── 力传感器 ──────────────────────────────
def measure_force_sensor(joint=1, channel=0):
    """力传感器读数：返回关节 J 的 0x2401(channel=0) / 0x2402(channel=1) 原始值。"""
    _require_slaves()
    joint = int(joint)
    value = driver.torque_sensor(joint, int(channel))
    _log("J%d 力传感器%d: %d" % (joint, int(channel), value))
    _display("J%d力%d" % (joint, int(channel)), value)
    return int(value)


def measure_force_change(arm="left", duration=3.0):
    """力传感器变化量：在 duration 秒内采样双臂 14 路，返回最大峰峰值。"""
    _require_slaves()
    start = time.time()
    lows, highs = {}, {}
    while time.time() - start < float(duration):
        for j in range(1, TOTAL_JOINTS + 1):
            v = driver.torque_sensor(j, 0)
            lows[j] = v if j not in lows else min(lows[j], v)
            highs[j] = v if j not in highs else max(highs[j], v)
        time.sleep(0.1)
    span = max(highs[j] - lows[j] for j in highs) if highs else 0
    _log("力传感器峰峰值(%.1fs): %d" % (float(duration), span))
    _display("力变化", span)
    return int(span)


def measure_positive_direction(delta=0.1, speed=0.2):
    """正方向复核：双臂 14 轴各正向运动 delta rad，返回最小实际位移(rad)。"""
    _require_slaves()
    delta = float(delta)
    before = driver.joint_positions_all()
    if len(before) != TOTAL_JOINTS:
        raise ArmDriverError("读取 14 轴位置失败: %r" % (before,))
    for arm in ("left", "right"):
        joints = driver.joint_positions(arm)
        target = [v + delta for v in joints]
        _log("%s 臂正向运动 %.3f rad: %s -> %s" % (arm, delta, _fmt(joints), _fmt(target)))
        driver.send_arm_goal(arm, target, speed=float(speed), timeout=120)
    time.sleep(0.3)
    after = driver.joint_positions_all()
    moved = [after[i] - before[i] for i in range(TOTAL_JOINTS)]
    _log("各轴实际位移: %s" % _fmt(moved))
    _display("最小位移", "%.4f rad" % min(moved))
    return float(min(moved))


# ────────────────────── 运控程序（~/mc/install）启停 / 小角度校验 ──────────────────────
def measure_motion_control_start():
    """启动配置的运控栈（默认 ~/mc/install）；主站已在运行则跳过。返回 1。"""
    setup = MOTION.get("setup") or "~/mc/install/setup.bash"
    _log("启动运控程序（%s）..." % setup)
    driver.start_motion_control()
    _display("运控程序", "已启动")
    return 1


def measure_positive_direction_small_angle(timeout=120):
    """执行 CIT 项「正方向小角度校验」，并记录动作前原位。返回 1。

    原位保存在驱动里，供后续 measure_motion_restore() 恢复。
    """
    _require_slaves()
    origin = driver.joint_positions_all()
    if len(origin) != TOTAL_JOINTS:
        raise ArmDriverError("读取 14 轴原位失败: %r" % (origin,))
    driver.set_motion_origin(origin)
    _log("小角度校验前原位: %s" % _fmt(origin))
    text = driver.run_cit_item("mc.arm.small_angle", timeout=float(timeout) + 15.0)
    _log("小角度校验动作输出: %s" % (text or "").strip()[-600:])
    _display("小角度校验", "完成")
    return 1


def measure_motion_restore(speed=0.05, timeout=180):
    """双臂慢速回到小角度校验前记录的原位，返回最大到位误差(rad)。"""
    origin = driver.get_motion_origin()
    if len(origin) != TOTAL_JOINTS:
        raise ArmDriverError("未记录运控原位，无法恢复；请先执行小角度校验")
    driver.restore_motion_origin(speed=float(speed), timeout=float(timeout))
    time.sleep(0.5)
    after = driver.joint_positions_all()
    if len(after) != TOTAL_JOINTS:
        raise ArmDriverError("恢复后读取 14 轴位置失败: %r" % (after,))
    error = max(abs(after[i] - origin[i]) for i in range(TOTAL_JOINTS))
    _log("恢复原位误差: %.5f rad（原位 %s，当前 %s）" % (
        error, _fmt(origin), _fmt(after)))
    _display("回原位误差", "%.4f rad" % error)
    return float(error)


def measure_motion_home(timeout=180):
    """执行 CIT 项 mc.jiyi1.home（双臂+腰部回 home），返回回零后最大关节角绝对值(rad)。

    参考 cit_items.xml 的“复位-回零位”：
    `ros2 run mc_task_manager ros2_node_action_client move_dual_arm_waist_home`
    """
    _log("执行 CIT 项 mc.jiyi1.home（双臂+腰部回 home）...")
    text = driver.run_cit_item("mc.jiyi1.home", timeout=float(timeout) + 15.0)
    _log("回零动作输出: %s" % (text or "").strip()[-600:])
    time.sleep(0.5)
    after = driver.joint_positions_all()
    if len(after) != TOTAL_JOINTS:
        raise ArmDriverError("回零后读取 14 轴位置失败: %r" % (after,))
    error = max(abs(float(v)) for v in after)
    _log("回零后关节角: %s，最大绝对值 %.5f rad" % (_fmt(after), error))
    _display("回零误差", "%.4f rad" % error)
    return float(error)


def measure_motion_control_stop():
    """停止运控节点（默认保留 EtherCAT 主站）。返回 1。"""
    _log("停止运控程序（保留 EtherCAT 主站）...")
    driver.stop_motion_control()
    _display("运控程序", "已停止")
    return 1


# ────────────────────────────── 基本信息 ──────────────────────────────
def measure_identity(joint=1, field="part"):
    """读取关节身份信息（统一返回字符串）。

    field: part/pn（0x1018:2）、sn（0x1018:4）、vendor（0x1018:1）
           以上格式化为 0xXXXXXXXX；hw 读 0x1009、sw 读 0x100A（字符串）。
    """
    _require_slaves()
    joint = int(joint)
    value = driver.identity_str(joint, field)
    if not value:
        raise ArmDriverError("J%d %s 读取为空" % (joint, field))
    _log("J%d %s = %s" % (joint, field, value))
    _display("J%d %s" % (joint, field), value)
    return value


def measure_identity_record(joint=1, field="part"):
    """只记录关节身份信息：读取失败或空值返回空字符串，不抛异常、不参与判定。"""
    joint = int(joint)
    try:
        _require_slaves()
    except Exception as exc:
        _log("J%d %s 读取前检查失败，记录为空：%s" % (joint, field, exc))
        return ""
    try:
        value = driver.identity_str(joint, field)
    except Exception as exc:
        _log("J%d %s 读取失败，记录为空：%s" % (joint, field, exc))
        return ""
    if not value:
        _log("J%d %s 读取为空" % (joint, field))
        value = ""
    else:
        _log("J%d %s = %s" % (joint, field, value))
    _display("J%d %s" % (joint, field), value)
    return value


def measure_joint_temperature(joint=1):
    """关节温度：返回 0x2403 换算后的摄氏度。"""
    _require_slaves()
    joint = int(joint)
    temp = driver.temperature_c(joint)
    _log("J%d 温度: %.2f ℃" % (joint, temp))
    _display("J%d温度" % joint, "%.2f ℃" % temp)
    return float(temp)


# ────────────────────────────── 运动性能 ──────────────────────────────
def measure_move_limit(joint=1, direction="pos", speed=0.3, timeout=180):
    """运动到限位：返回到位后的角度(rad)。direction=pos/neg。"""
    _require_slaves()
    joint = int(joint)
    lo, hi = JOINT_LIMITS[joint]
    _log("J%d 运动到%s极限，目标 %.3f rad (speed=%.2f)" % (
        joint, "正" if str(direction).lower().startswith("p") else "负",
        hi if str(direction).lower().startswith("p") else lo, float(speed)))
    driver.move_joint_to_limit(joint, direction, speed=float(speed), timeout=float(timeout))
    time.sleep(0.3)
    arm = joint_arm(joint)
    angle = driver.joint_positions(arm)[joint_index(joint)]
    _log("J%d 到位角度: %.4f rad" % (joint, angle))
    _display("J%d角度" % joint, "%.4f rad" % angle)
    return float(angle)


def measure_limit_angle(joint=1):
    """限位角度：返回关节当前角度(rad)（用于“正/负极限角度”用例）。"""
    _require_slaves()
    joint = int(joint)
    arm = joint_arm(joint)
    angle = driver.joint_positions(arm)[joint_index(joint)]
    _log("J%d 当前角度: %.5f rad" % (joint, angle))
    _display("J%d角度" % joint, "%.4f rad" % angle)
    return float(angle)


def measure_joint_speed(joint=1, delta=0.4, speed=1.0):
    """运动速度：让关节正向运动 delta，返回过程中的峰值速度(rad/s)。"""
    _require_slaves()
    joint = int(joint)
    arm = joint_arm(joint)
    idx = joint_index(joint)
    cur = driver.joint_positions(arm)[idx]
    lo, hi = JOINT_LIMITS[joint]
    # 朝空间更大的一侧运动，避免在限位处“动不了”导致峰值速度为 0
    step = float(delta) if (hi - cur) >= (cur - lo) else -float(delta)
    target = max(lo, min(hi, cur + step))

    def _move():
        driver.move_joint(joint, target, speed=float(speed), timeout=120)

    peak = driver.peak_speed(joint, _move, sample_sec=10.0)
    _log("J%d 峰值速度: %.4f rad/s (运动 %.3f -> %.3f)" % (joint, peak, cur, target))
    _display("J%d速度" % joint, "%.4f rad/s" % peak)
    return float(peak)


def measure_joint_home(joint=1, speed=0.3):
    """归零：运动回 0 rad，返回归零后的角度(rad)。"""
    _require_slaves()
    joint = int(joint)
    driver.home_joint(joint, speed=float(speed), timeout=150)
    time.sleep(0.3)
    arm = joint_arm(joint)
    angle = driver.joint_positions(arm)[joint_index(joint)]
    _log("J%d 归零后角度: %.5f rad" % (joint, angle))
    _display("J%d角度" % joint, "%.4f rad" % angle)
    return float(angle)


def measure_move_delta(joint=1, delta=1.0, speed=0.3):
    """正方向运动 delta rad，返回实际位移(rad)。"""
    _require_slaves()
    joint = int(joint)
    arm = joint_arm(joint)
    idx = joint_index(joint)
    before = driver.joint_positions(arm)[idx]
    target = before + float(delta)
    lo, hi = JOINT_LIMITS[joint]
    target = max(lo, min(hi, target))
    driver.move_joint(joint, target, speed=float(speed), timeout=150)
    time.sleep(0.3)
    after = driver.joint_positions(arm)[idx]
    moved = after - before
    _log("J%d 位移: %.4f -> %.4f = %.4f rad" % (joint, before, after, moved))
    _display("J%d位移" % joint, "%.4f rad" % moved)
    return float(moved)


def measure_repeatability(arm="left", cycles=5, target=0.5, speed=0.3):
    """重复定位精度：往返 N 次，返回到达 target 时位置的峰峰值(rad)。"""
    _require_slaves()
    cycles = int(cycles)
    arm = str(arm).lower()
    points = []
    for i in range(cycles):
        joints = driver.joint_positions(arm)
        # 单关节往返：用 J3（双臂均 ±2.95，行程大且远离限位）作为代表
        idx = 2
        stable = list(joints)
        stable[idx] = float(target)
        driver.send_arm_goal(arm, stable, speed=float(speed), timeout=120)
        time.sleep(0.3)
        points.append(driver.joint_positions(arm)[idx])
        _log("%s 臂第 %d 次到位: %.5f rad" % (arm, i + 1, points[-1]))
        back = list(stable)
        back[idx] = 0.0
        driver.send_arm_goal(arm, back, speed=float(speed), timeout=120)
        time.sleep(0.2)
    span = max(points) - min(points)
    _log("%s 臂往返 %d 次，到位峰峰值: %.5f rad" % (arm, cycles, span))
    _display("%s臂重复精度" % arm, "%.5f rad" % span)
    return float(span)


# ────────────────────────────── 交互 / 安全 ──────────────────────────────
def measure_teach_button(arm="left"):
    """示教按钮：模拟按下示教键，返回触发后的 run_mode（3 = TEACH 生效）。"""
    _require_slaves()
    is_left = str(arm).lower().startswith("l")
    keys = TEACH_ADKEY.get("left" if is_left else "right", {})
    press = int(keys.get("press", 3 if is_left else 5))
    release = int(keys.get("release", 4 if is_left else 6))
    topic = TOPICS.get("power_board", "/svc_power_manager/status")
    msg_type = ROS.get("power_board_type", "bt_task_interfaces/msg/PowerBoardStatus")
    driver.publish(topic, msg_type, "{adkey_status: %d}" % press)
    time.sleep(1.0)
    mode = driver.run_mode()
    _log("%s 臂示教按下后 run_mode=%s" % (arm, mode))
    driver.publish(topic, msg_type, "{adkey_status: %d}" % release)
    time.sleep(1.0)
    after = driver.run_mode()
    _log("%s 臂示教松开后 run_mode=%s" % (arm, after))
    _display("%s臂示教" % arm, mode)
    return int(mode)


def measure_collision_state(arm="left"):
    """碰撞急停：读取 /mc/<臂>/collision_state，返回状态数值（0 = 无碰撞）。"""
    _require_slaves()
    arm = "left" if str(arm).lower().startswith("l") else "right"
    rc, out, err = driver.mc(
        "timeout 6 ros2 topic echo %s --once"
        % TOPICS.get("collision_state", "/mc/{arm}_arm/collision_state").format(arm=arm), timeout=20)
    text = out if out.strip() else err
    value = -1          # 解析不到就当失败，避免“静默通过”
    for key in ("state", "collision", "level", "status", "collision_state"):
        m = None
        import re
        m = re.search(r"^\s*%s:\s*(-?\d+)" % key, text, re.M)
        if m:
            value = int(m.group(1))
            break
    if value < 0:
        _log("collision_state 原始内容: %s" % text.strip()[:300])
    _log("%s 臂 collision_state: %d" % (arm, value))
    _display("%s臂碰撞" % arm, value)
    return int(value)


# ────────────────────────────── 整臂动作 ──────────────────────────────
def run_dual_arm_line1(timeout=180):
    """整臂动作：执行「检测线束动作」，返回 1=成功 / 0=失败（数值型）。"""
    ok, text = driver.run_task("jiyi1/dual_arm_line1", timeout=float(timeout))
    _log("dual_arm_line1: %s" % ("成功" if ok else "失败"))
    if not ok:
        _log(text[-500:])
    _display("线束动作", "成功" if ok else "失败")
    return int(1 if ok else 0)
