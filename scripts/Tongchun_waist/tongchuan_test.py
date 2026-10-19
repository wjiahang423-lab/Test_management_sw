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
    """设置指定节点的扭矩，成功返回 bool。node 是 EtherCAT 从站下标。torque 是扭矩值(float)。"""
    try:
        return bool(driver.set_torque_mode(node, torque
                                           ))
    except Exception:
        return False


def test_read_sn1_sn2():
    """读取指定节点的 SN1 和 SN2，返回字符串。node 是 EtherCAT 从站下标。"""
    try:
        sn1 = driver.read_info(1, "0x3040")
        sn2 = driver.read_info(2, "0x3040")
        if sn1 and sn2:
            import test_api 
            
            test_api.set_sn_to_Panel(str(sn1 + "&&" + sn2))  # sn1 和 sn2 之间用 && 分隔 写到报告和面板上
        return str(sn1 +"&&"+sn2) if sn1 and sn2 else None
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
    except Exception:
        return None


print("同川腰部电机测试函数已加载。")

print(test_connect())