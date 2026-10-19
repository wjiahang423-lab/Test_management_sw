#
# USB_CAN_FD 设备驱动封装 (Linux .so)
#
# 说明:
#   * 每次导入只加载动态库, 不在导入时占用设备; 设备在调用 open() 时才打开。
#   * 适配 python-can 风格的 send/recv/close 接口, 供 yiou_can_debug.py 使用。
#   * 意优 RP 关节是 CAN-FD 节点, 通道必须按 CANFD 模式初始化(TYPE_CANFD),
#     否则经典 CAN 模式下收发均无应答。数据帧仍用 8 字节经典帧(不置 BRS)。
#   * 发送与接收使用同一通道(单通道接总线)。
#
from ctypes import *
import os
import time

VCI_USBCAN2 = 41
STATUS_OK = 1
INVALID_DEVICE_HANDLE = None
INVALID_CHANNEL_HANDLE = None
TYPE_CAN = 0
TYPE_CANFD = 1

USBCANFD_MAX_NUM = 8


class VCI_INIT_CONFIG(Structure):
    _fields_ = [("AccCode", c_uint),
                ("AccMask", c_uint),
                ("Reserved", c_uint),
                ("Filter", c_ubyte),
                ("Timing0", c_ubyte),
                ("Timing1", c_ubyte),
                ("Mode", c_ubyte)]


class VCI_CAN_OBJ(Structure):
    _fields_ = [("ID", c_uint),
                ("TimeStamp", c_uint),
                ("TimeFlag", c_ubyte),
                ("SendType", c_ubyte),
                ("RemoteFlag", c_ubyte),
                ("ExternFlag", c_ubyte),
                ("DataLen", c_ubyte),
                ("Data", c_ubyte * 8),
                ("Reserved", c_ubyte * 3)]


class _ZCAN_CHANNEL_CAN_INIT_CONFIG(Structure):
    _fields_ = [("acc_code", c_uint),
                ("acc_mask", c_uint),
                ("reserved", c_uint),
                ("filter", c_ubyte),
                ("timing0", c_ubyte),
                ("timing1", c_ubyte),
                ("mode", c_ubyte)]


class _ZCAN_CHANNEL_CANFD_INIT_CONFIG(Structure):
    _fields_ = [("acc_code", c_uint),
                ("acc_mask", c_uint),
                ("abit_timing", c_uint),
                ("dbit_timing", c_uint),
                ("brp", c_uint),
                ("filter", c_ubyte),
                ("mode", c_ubyte),
                ("pad", c_ushort),
                ("reserved", c_uint)]


class _ZCAN_CHANNEL_INIT_CONFIG(Union):
    _fields_ = [("can", _ZCAN_CHANNEL_CAN_INIT_CONFIG),
                ("canfd", _ZCAN_CHANNEL_CANFD_INIT_CONFIG)]


class ZCAN_CHANNEL_INIT_CONFIG(Structure):
    _fields_ = [("can_type", c_uint),
                ("config", _ZCAN_CHANNEL_INIT_CONFIG)]


class ZCAN_CAN_FRAME(Structure):
    _fields_ = [("can_id", c_uint, 29),
                ("err", c_uint, 1),
                ("rtr", c_uint, 1),
                ("eff", c_uint, 1),
                ("can_dlc", c_ubyte),
                ("__pad", c_ubyte),
                ("__res0", c_ubyte),
                ("__res1", c_ubyte),
                ("data", c_ubyte * 8)]


class ZCAN_CANFD_FRAME(Structure):
    _fields_ = [("can_id", c_uint, 29),
                ("err", c_uint, 1),
                ("rtr", c_uint, 1),
                ("eff", c_uint, 1),
                ("len", c_ubyte),
                ("brs", c_ubyte, 1),
                ("esi", c_ubyte, 1),
                ("__res", c_ubyte, 6),
                ("__res0", c_ubyte),
                ("__res1", c_ubyte),
                ("data", c_ubyte * 64)]


class ZCAN_Transmit_Data(Structure):
    _fields_ = [("frame", ZCAN_CAN_FRAME), ("transmit_type", c_uint)]


class ZCAN_Receive_Data(Structure):
    _fields_ = [("frame", ZCAN_CAN_FRAME), ("timestamp", c_ulonglong)]


class ZCAN_TransmitFD_Data(Structure):
    _fields_ = [("frame", ZCAN_CANFD_FRAME), ("transmit_type", c_uint)]


class ZCAN_ReceiveFD_Data(Structure):
    _fields_ = [("frame", ZCAN_CANFD_FRAME), ("timestamp", c_ulonglong)]


class ZCAN_DEVICE_INFO(Structure):
    _fields_ = [("hw_Version", c_ushort),
                ("fw_Version", c_ushort),
                ("dr_Version", c_ushort),
                ("in_Version", c_ushort),
                ("irq_Num", c_ushort),
                ("can_Num", c_ubyte),
                ("str_Serial_Num", c_ubyte * 21),
                ("str_hw_Type", c_ubyte * 40),
                ("reserved", c_ushort * 5)]


# 加载动态库(只加载, 不打开设备)
_dir = os.path.dirname(os.path.realpath(__file__))
canDLL = cdll.LoadLibrary(os.path.join(_dir, 'libcontrolcanfd.so'))

canDLL.ZCAN_OpenDevice.restype = c_void_p
canDLL.ZCAN_SetAbitBaud.argtypes = (c_void_p, c_ulong, c_ulong)
canDLL.ZCAN_SetDbitBaud.argtypes = (c_void_p, c_ulong, c_ulong)
canDLL.ZCAN_SetCANFDStandard.argtypes = (c_void_p, c_ulong, c_ulong)
canDLL.ZCAN_InitCAN.argtypes = (c_void_p, c_ulong, c_void_p)
canDLL.ZCAN_InitCAN.restype = c_void_p
canDLL.ZCAN_StartCAN.argtypes = (c_void_p,)
canDLL.ZCAN_Transmit.argtypes = (c_void_p, c_void_p, c_ulong)
canDLL.ZCAN_TransmitFD.argtypes = (c_void_p, c_void_p, c_ulong)
canDLL.ZCAN_GetReceiveNum.argtypes = (c_void_p, c_ulong)
canDLL.ZCAN_Receive.argtypes = (c_void_p, c_void_p, c_ulong, c_long)
canDLL.ZCAN_ReceiveFD.argtypes = (c_void_p, c_void_p, c_ulong, c_long)
canDLL.ZCAN_ResetCAN.argtypes = (c_void_p,)
canDLL.ZCAN_CloseDevice.argtypes = (c_void_p,)


class CanMessage:
    """统一的 CAN 报文对象, 替换 python-can 的 can.Message。"""

    __slots__ = ("arbitration_id", "is_extended_id", "data")

    def __init__(self, arbitration_id=0, is_extended_id=False, data=b""):
        self.arbitration_id = int(arbitration_id)
        self.is_extended_id = bool(is_extended_id)
        self.data = bytes(data)


class USB_CAN_FD_Drviver:
    def __init__(self):
        self.canDLL = canDLL
        self.m_dev = None    # 设备句柄
        self.dev_ch1 = None  # 通道0
        self.dev_ch2 = None  # 通道1
        self._chans = []     # [通道0句柄, 通道1句柄]
        self._chan = None    # 当前收发使用的通道

    def open(self, bitrate=1000000, device_index=0, channel=0, dbit_baud=None):
        if self.m_dev is not None:
            return
        m_dev = self.canDLL.ZCAN_OpenDevice(VCI_USBCAN2, 0, device_index)
        if not m_dev:
            raise RuntimeError(
                "打开 USB_CAN_FD 设备失败: 请检查设备连接与权限(可能需要 sudo)")
        self.m_dev = m_dev
        self._chans = []
        if dbit_baud is None:
            dbit_baud = bitrate
        try:
            # 关节为 CAN-FD 节点: 必须设置仲裁域+数据域波特率, 并按 CANFD 初始化。
            for ch in (0, 1):
                if self.canDLL.ZCAN_SetAbitBaud(m_dev, ch, bitrate) != STATUS_OK:
                    raise RuntimeError(f"设置通道{ch}仲裁域波特率 {bitrate} 失败")
                self.canDLL.ZCAN_SetDbitBaud(m_dev, ch, dbit_baud)
                self.canDLL.ZCAN_SetCANFDStandard(m_dev, ch, 0)  # 0 = ISO
            init_config = ZCAN_CHANNEL_INIT_CONFIG()
            init_config.can_type = TYPE_CANFD
            init_config.config.canfd.mode = 0
            for ch in (0, 1):
                handle = self.canDLL.ZCAN_InitCAN(m_dev, ch, byref(init_config))
                if not handle:
                    raise RuntimeError(f"初始化通道{ch}失败")
                if self.canDLL.ZCAN_StartCAN(handle) != STATUS_OK:
                    raise RuntimeError(f"启动通道{ch}失败")
                self._chans.append(handle)
            self.dev_ch1 = self._chans[0]
            self.dev_ch2 = self._chans[1]
            self._chan = self._chans[channel % len(self._chans)]
        except Exception:
            self.close()
            raise

    def send(self, msg, tx_mode=0):
        if self._chan is None:
            raise RuntimeError("设备未打开")
        buf = (ZCAN_Transmit_Data * 1)()
        f = buf[0].frame
        f.can_id = msg.arbitration_id & 0x1FFFFFFF
        f.eff = 1 if msg.is_extended_id else 0
        f.rtr = 0
        data = bytes(msg.data)
        n = min(len(data), 8)
        f.can_dlc = n
        for i in range(8):
            f.data[i] = data[i] if i < n else 0
        buf[0].transmit_type = int(tx_mode)   # 0正常 1单次 2自发自收 3单次自发自收
        return self.canDLL.ZCAN_Transmit(self._chan, buf, 1)

    def recv(self, timeout=0.05):
        if self._chan is None:
            raise RuntimeError("设备未打开")
        chan = self._chan
        deadline = time.time() + timeout
        while True:
            count = self.canDLL.ZCAN_GetReceiveNum(chan, TYPE_CAN)
            if count > 0:
                buf = (ZCAN_Receive_Data * count)()
                num = self.canDLL.ZCAN_Receive(chan, buf, count, 0)
                if num > 0:
                    f = buf[0].frame
                    dlc = min(f.can_dlc, 8)
                    return CanMessage(
                        arbitration_id=f.can_id,
                        is_extended_id=bool(f.eff),
                        data=bytes(f.data[:dlc]),
                    )
            count = self.canDLL.ZCAN_GetReceiveNum(chan, TYPE_CANFD)
            if count > 0:
                buf = (ZCAN_ReceiveFD_Data * count)()
                num = self.canDLL.ZCAN_ReceiveFD(chan, buf, count, 0)
                if num > 0:
                    f = buf[0].frame
                    dlc = min(f.len, 64)
                    return CanMessage(
                        arbitration_id=f.can_id,
                        is_extended_id=bool(f.eff),
                        data=bytes(f.data[:dlc]),
                    )
            if time.time() >= deadline:
                return None
            time.sleep(0.001)

    def close(self):
        try:
            for h in self._chans:
                if h:
                    self.canDLL.ZCAN_ResetCAN(h)
        except Exception:
            pass
        if self.m_dev:
            try:
                self.canDLL.ZCAN_CloseDevice(self.m_dev)
            except Exception:
                pass
        self._chans = []
        self.dev_ch1 = None
        self.dev_ch2 = None
        self._chan = None
        self.m_dev = None