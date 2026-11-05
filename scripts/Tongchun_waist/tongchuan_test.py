# -*- coding: utf-8 -*-

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tongchuan_driver import driver





def test_connect(interface="enp2s0", sudo_password="zhengjijicheng"):
    """网络连接，成功返回 bool。"""
    return bool(driver.connect(interface, sudo_password))

def test_disconnect():
    """断开网络连接，成功返回 bool。"""
    return bool(driver.disconnect())

def test_read_nodes()->int:
    """读取网络中所有节点，返回节点数量。"""
    try:
        return driver.get_slave_count()
    except Exception:
        return 0

def test_enable(node):
    """使能指定节点，成功返回 bool。node 是 EtherCAT 从站下标。"""
    try:
        return bool(driver.enable(node))
    except Exception:
        return False


def test_zero_position(node):
    """标零指定节点，返回标零后的当前位置脉冲值(float)。"""
    try:
        return float(driver.zero_position(node))
    except Exception:
        return None

def test_set_torque_mode(node, torque):
    """设置指定节点的扭矩模式并写入扭矩，返回 bool。

    返回 True 表示驱动器已进入 Operation enabled（0x6041 bit2=1）。
    """
    try:
        return bool(driver.set_torque_mode(node, torque))
    except Exception:
        return False

def test_set_friction_compensation(node, fc=5, b=10, deadband=0.15,
                                   max_raw=150, unit="0.1nm", v_start=0.3):
    """启用手动拖动摩擦力前馈：0x6071 = Fc*sign(v) + B*v。

    unit="0.1nm"（默认）时 Fc=5/B=10 表示 0.5Nm / 1.0Nm·s/rad；
    unit="nm" 表示 Nm；unit="raw" 表示 0x6071 原始值（0.1% 额定扭矩）。
    """
    try:
        return bool(driver.set_friction_compensation(
            node, True, fc, b, deadband, max_raw, unit, v_start))
    except Exception:
        return False


def test_disable_friction_compensation(node):
    """关闭摩擦力前馈。"""
    try:
        return bool(driver.set_friction_compensation(node, False))
    except Exception:
        return False


def test_release_brake(node, enable=True):
    """强制打开/关闭抱闸（Pr8.30 / 0x381E，写 128 打开）。"""
    try:
        return bool(driver.release_brake(node, enable))
    except Exception:
        return False


def test_torque_status(node):
    """返回扭矩模式诊断：mode/status/enabled/velocity/target_torque/brake。"""
    try:
        return driver.torque_status(node)
    except Exception as e:
        return {"error": "{}: {}".format(type(e).__name__, e)}


def test_disable_torque(node):
    """关闭扭矩使能、停止摩擦补偿并恢复抱闸。"""
    try:
        return bool(driver.disable_torque(node))
    except Exception:
        return False


def test_read_sn1_sn2():
    """读取两个从站的 SN1 和 SN2，返回字符串。"""
    try:
        sn1 = driver.read_info(0, "0x3040")
        sn2 = driver.read_info(1, "0x3040")
        if sn1 and sn2:
            import test_api
            test_api.set_sn_to_Panel(str(sn1 + "&&" + sn2))  # sn1 和 sn2 之间用 && 分隔 写到报告和面板上
        return str(sn1 + "&&" + sn2) if sn1 and sn2 else None
    except Exception:
        return None


def test_read_angle(node):
    """读取指定节点当前角度，单位为度(float)。"""
    try:
        return float(driver.read_angle(node))
    except Exception:
        return None


def test_read_info(node, address):
    """读取指定节点的 SDO 字典地址，返回字符串。"""
    try:
        return str(driver.read_info(node, address))
    except Exception as e:
        try:
            import test_api
            test_api.log("读取 node={} {} 失败：{}: {}".format(
                node, address, type(e).__name__, e))
        except Exception:
            pass
        return None
