# -*- coding: utf-8 -*-

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eyou_driver import driver
# from .eyou_driver import driver
import test_api

def test_connect(channel=0):
    """连接 CAN 总线，成功返回 bool。"""
    return bool(driver.connect(channel=channel))
def test_close():
    """关闭 CAN 总线，成功返回 bool。"""
    return bool(driver.close())

def test_scan_nodes():
    """扫描 CAN 总线上的所有节点，返回节点数量。"""
    try:
        return len(list(driver.scan_nodes()))
    except Exception:
        return len([])

def test_enable(node=1):
    """使能指定 CANopen 节点，成功返回 bool。"""
    try:
        return bool(driver.enable(node))
    except Exception:
        return False
    

def test_set_torque(node=1, torque=0):
    """设置指定节点的扭矩，成功返回 bool。"""
    try:
        return bool(driver.set_torque(node, torque))
    except Exception:
        return False



def test_zero_position(node=1):
    """将指定节点当前位置设为机械零点，返回当前位置脉冲值(float)。"""
    try:
        return float(driver.zero_position(node))
    except Exception:
        return None


def test_read_angle(node=1):
    """读取指定节点当前角度，单位为度(float)。"""
    try:
        return float(driver.read_angle(node))
    except Exception:
        return None


def test_read_info(node=1, address="0x1008"):
    """读取指定节点的 SDO 地址，返回字符串。"""
    try:
        return str(driver.read_info(node, address))
    except Exception:
        return None

def test_read_sn_():
    """读取指定节点的序列号，返回字符串。"""
    try:
        sn1 = driver.read_info(1, "0x2082")
        sn2 = driver.read_info(2, "0x2082")
        if sn1 and sn2:
            test_api.testset_sn_to_Panel(str(sn1+sn2)) # 写入测试面板与报告中 
        return str(sn1+sn2) if sn1 and sn2 else None
    except Exception:
        return None 

    
''' 常用的SDO地址：
# 0x2002: MotorName名称
# 0x1009: 制造硬件版本  
# 0x100A: 制造软件版本
#
# 0x2082: 设备序列号
# 0x2084: 设备MCU版本号
# 0x2083: 设备硬件版本号
# 设备身份信息（手册附录 5.1.3 / 5.1.4）

# 0x2002   # 电机名称，界面作为零件号显示
# 0x2082   # 序列号
# 0x2083   # 硬件版本
# 0x2084   # MCU 固件/软件版本
'''