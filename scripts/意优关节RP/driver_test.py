# -*- coding: utf-8 -*-
"""eyou_head/driver.py 的简单测试脚本（不使用 Qt）。

可直接运行：  python3 driver_test.py
也可被 Test_management_sw 当作模块导入，复用下面的 test_* 函数。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from driver import (
    DEFAULT_NODE, MODE_POSITION, MODE_TORQUE, MODE_VELOCITY,
    EyouHeadDriver, deg_to_pulses, pulses_to_deg, pps_to_rpm,
)

_drv = EyouHeadDriver()


# 信息（手册附录 5.1.3 / 5.1.4）
OBJ_PART_NUMBER = 0x2002   # 电机名称，界面作为零件号显示
OBJ_SERIAL_NUM  = 0x2082   # 序列号
OBJ_HW_VERSION  = 0x2083   # 硬件版本
OBJ_SW_VERSION  = 0x2084   # MCU 固件/软件版本

def test_connect(node=DEFAULT_NODE):
    """连接 CAN 总线，返回 (ok, 当量 或 None)。"""
    if not _drv.connect():
        return False, None
    try:
        return True, _drv.read_enc_per_rev(node)
    except Exception:
        return False, None


def test_set_mode(mode=MODE_POSITION, node=DEFAULT_NODE):
    """设置工作模式：1=位置 3=速度 4=转矩 8=CSP，返回 bool。"""
    try:
        return bool(_drv.set_mode(node, mode))
    except Exception:
        return False


def test_enable(node=DEFAULT_NODE, mode=MODE_POSITION):
    """按模式使能电机，返回 bool。"""
    try:
        return bool(_drv.enable(node, mode))
    except Exception:
        return False


def test_disable(node=DEFAULT_NODE):
    """失能电机，返回 bool。"""
    try:
        return bool(_drv.disable(node))
    except Exception:
        return False


def test_read_angle(node=DEFAULT_NODE, zero=0.0):
    """读取当前角度(度)，失败返回 None。"""
    try:
        return float(_drv.read_angle(node, zero))
    except Exception:
        return None


def test_read_data(node=DEFAULT_NODE, index=0x6064, sub=0):
    """读取任意对象字典，返回 (value, text)。"""
    try:
        info = _drv.read_data(node, index, sub)
        return info["value"], info["text"]
    except Exception:
        return None, None


def test_angle_convert(deg=90.0, per_rev=524288.0):
    """角度转换自检：deg -> 脉冲 -> deg，返回 (脉冲, 回读角度)。"""
    pulses = deg_to_pulses(deg, per_rev)
    return pulses, round(pulses_to_deg(pulses, per_rev), 6)


def _demo():
    node = DEFAULT_NODE
    print("=== 角度转换 ===")
    p, back = test_angle_convert(90.0)
    print("90° -> %d 脉冲 -> %.4f°" % (p, back))

    print("=== 连接 ===")
    ok, enc = test_connect(node)
    print("connect:", ok, "当量(脉冲/圈):", enc)
    if not ok:
        print("连接失败：检查适配器/接线/权限，或程序占用设备。")
        return

    try:
        print("当前模式:", _drv.read_mode(node), "目标模式:", _drv.read_mode(node, display=False))
        print("使能(位置模式):", _drv.enable(node, MODE_POSITION))
        print("状态字: 0x%04X" % _drv.read_status_word(node))
        print("角度: %.3f°" % _drv.read_angle(node))
        print("位置脉冲: %d" % _drv.read_position(node))
        print("速度脉冲/s: %d (%.3f rpm)" % (
            _drv.read_velocity(node), pps_to_rpm(_drv.read_velocity(node), enc or 524288.0)))
        print("扭矩: %d" % _drv.read_torque(node))
        print("读 0x1018:01 (厂商码):", _drv.read_data(node, 0x1018, 1)["text"])
        print("失能:", _drv.disable(node))
    finally:
        _drv.close()
        print("=== 已断开 ===")


if __name__ == "__main__":
    # _demo()
    ok, enc = test_connect(2)
    print("connect:", ok, "当量(脉冲/圈):", enc)
    if not ok:
            print("连接失败：检查适配器/接线/权限，或程序占用设备。")
    print("当前角度:%.3f° " % test_read_angle(2))
    # 设置摸索
    print("设置模式:", test_set_mode(MODE_TORQUE, 2))
    # print("使能:", test_enable(2, MODE_POSITION))
