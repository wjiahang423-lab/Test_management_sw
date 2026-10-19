# -*- coding: utf-8 -*-
"""吉翼灵巧手协议公共模块（读取自《吉翼灵巧手协议(3).xlsx》）。

供 CANFD 与 RS485 两套功能测试脚本共用：
  - CAPABILITY：寄存器/指令解析表（地址、功能、编码长度、属性 R/W）
  - Modbus CRC16-IBM（RS485 帧尾）
  - RS485 帧打包/解析（帧头 0xEB 0x90 + 标志 + 设备ID + 地址 + 数据长度 + 数据 + CRC16）
  - CANFD 控制帧：CANID 拼接、数据提帧
"""
from __future__ import annotations

# --------------------------------------------------------------------------
# 指令解析表（来源：xlsx 的 地址/功能/编码长度/数据范围/属性）
# 属性: "R" 只读, "W" 只写, "R/W" 读写
# --------------------------------------------------------------------------
CAPABILITY = {
    0x00: {"name": "产品型号/SN/软硬件版本/手ID",        "len": 25, "attr": "R"},
    0x01: {"name": "关节1-N故障码",                      "len": 2,  "attr": "R"},
    0x02: {"name": "0位标定",                            "len": 1,  "attr": "W"},
    0x03: {"name": "关节目标位置",                       "len": 2,  "attr": "W"},
    0x04: {"name": "目标速度",                           "len": 2,  "attr": "W"},
    0x05: {"name": "目标扭矩",                           "len": 2,  "attr": "W"},
    0x06: {"name": "关节当前位置",                       "len": 2,  "attr": "R"},
    0x07: {"name": "当前速度",                           "len": 2,  "attr": "R"},
    0x08: {"name": "当前扭矩",                           "len": 2,  "attr": "R"},
    0x09: {"name": "手指0压力",                          "len": 2,  "attr": "R"},
    0x0A: {"name": "手指1压力",                          "len": 2,  "attr": "R"},
    0x0B: {"name": "手指2压力",                          "len": 2,  "attr": "R"},
    0x0C: {"name": "手指3压力",                          "len": 2,  "attr": "R"},
    0x0D: {"name": "手指4压力",                          "len": 2,  "attr": "R"},
    0x20: {"name": "关节0位标定数据",                    "len": 2,  "attr": "R/W"},
    0x30: {"name": "直流总线电压",                       "len": 2,  "attr": "R"},
    0x31: {"name": "关节电机电流",                       "len": 2,  "attr": "R"},
    0x32: {"name": "关节温度",                           "len": 2,  "attr": "R"},
    0x33: {"name": "工作模式",                           "len": 2,  "attr": "R"},
    0xC0: {"name": "堵转时间(默认50)",                   "len": 2,  "attr": "R/W"},
    0xC1: {"name": "堵转阈值(默认500)",                  "len": 2,  "attr": "R/W"},
    0xC2: {"name": "堵转扭矩(默认700)",                  "len": 2,  "attr": "R/W"},
    0xC3: {"name": "产品型号",                           "len": 8,  "attr": "R/W"},
    0xC4: {"name": "产品SN",                             "len": 8,  "attr": "W"},
    0xC5: {"name": "软件版本",                           "len": 8,  "attr": "R"},
    0xC6: {"name": "硬件版本",                           "len": 8,  "attr": "R"},
    0xC7: {"name": "手ID(预留)",                         "len": 1,  "attr": "W"},
    0xC8: {"name": "恢复出厂设置",                       "len": 0,  "attr": "W"},
    0xCF: {"name": "保存参数",                           "len": 0,  "attr": "W"},
}

# 设备ID 低位含义（bit1~0）
HAND_ID_BITS = {1: "左手", 2: "右手"}


def capability_get(addr):
    """根据地址返回指令定义；地址不存在返回 None。"""
    return CAPABILITY.get(addr)


def allowed(addr, rw):
    """校验某地址是否允许 读(r) 或 写(w)。"""
    info = CAPABILITY.get(addr)
    if info is None:
        return False
    attr = info["attr"].upper()
    if rw == "r":
        return attr in ("R", "R/W")
    if rw == "w":
        return attr in ("W", "R/W")
    return False


# --------------------------------------------------------------------------
# CANFD 控制帧（来源: 协议 CANID 位定义）
#   Bit 28      : 0=主机发送, 1=从机发送
#   Bit 27      : 0=写, 1=读
#   Bit 24-26   : 预留
#   Bit 23-16   : 设备ID
#   Bit 15-8    : 寄存器地址
#   Bit 7-0     : 数据长度
# --------------------------------------------------------------------------
def build_canid(addr, rw, dev_id, data_len, master_send=True):
    """拼接 29 位扩展帧 CANID。

    addr: 寄存器地址(0~0xFF); rw: 'r'读/'w'写;
    dev_id: 设备ID(见协议表, 如 0x01/0x02 右手等);
    data_len: 数据字节数(0~64); master_send: True主机发送/False从机发送。
    返回 32 位整型（bit28..bit0 有效）。
    """
    bit28 = 0 if master_send else 1
    bit27 = 1 if rw == "r" else 0
    return (
        (bit28 << 28)
        | (bit27 << 27)
        | ((addr & 0xFF) << 8)
        | ((dev_id & 0xFF) << 16)
        | (data_len & 0xFF)
    )


def parse_canid(cid):
    """解析 CANID 各字段，返回 dict。"""
    return {
        "master_send": bool((cid >> 28) & 1),
        "is_read": bool((cid >> 27) & 1),
        "reserved": (cid >> 24) & 0x07,
        "dev_id": (cid >> 16) & 0xFF,
        "addr": (cid >> 8) & 0xFF,
        "data_len": cid & 0xFF,
    }


# --------------------------------------------------------------------------
# RS485 帧（来源: 协议帧格式）
#   BYTE0=0xEB  BYTE1=0x90  标志字节  设备ID  地址  数据长度  数据区[..]  CRC16_L  CRC16_H
#   标志字节: Bit4(0主机/1从机) Bit3(0写/1读) Bit0-2 预留
# --------------------------------------------------------------------------
RS485_HEAD = (0xEB, 0x90)


def crc16_modbus(data: bytes) -> int:
    """Modbus CRC16-IBM：多项式 0x8005, 初值 0xFFFF, 结果小端。"""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def build_rs485_frame(addr, rw, dev_id, data=b"", master_send=True):
    """打包一帧 RS485 发送字节流。

    返回要写入串口的完整字节（含尾部 CRC16，CRC16_L 低字节在前）。
    """
    if isinstance(data, str):
        data = data.encode("latin1")
    flag = (0x00 if master_send else 0x10) | (0x08 if rw == "r" else 0x00) | 0x00
    payload = bytes(RS485_HEAD) + bytes([flag, dev_id & 0xFF, addr & 0xFF, len(data) & 0xFF]) + bytes(data)
    crc = crc16_modbus(payload)
    return payload + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def parse_rs485_frame(frame: bytes):
    """解析/校验一帧 RS485 数据，返回 dict 或 None（帧长不足/校验失败）。"""
    if not frame or len(frame) < 7:
        return None
    if frame[0] != 0xEB or frame[1] != 0x90:
        return None
    flag, dev_id, addr, dlen = frame[2], frame[3], frame[4], frame[5]
    if 6 + dlen + 2 != len(frame):
        return None
    data = frame[6:6 + dlen]
    recv_crc_l, recv_crc_h = frame[-2], frame[-1]
    calc = crc16_modbus(frame[:-2])
    ok = (recv_crc_l == (calc & 0xFF)) and (recv_crc_h == ((calc >> 8) & 0xFF))
    return {
        "ok": ok,
        "master_send": bool(flag & 0x10),
        "is_read": bool(flag & 0x08),
        "dev_id": dev_id,
        "addr": addr,
        "data_len": dlen,
        "data": data,
    }


def u16(data: bytes, offset=0):
    """小端解析 2 字节。"""
    if len(data) < offset + 2:
        return None
    return data[offset] | (data[offset + 1] << 8)
