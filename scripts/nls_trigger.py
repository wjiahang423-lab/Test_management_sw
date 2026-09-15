"""Newland NLS 扫码枪 软件触发（ctypes 直接调用官方 libnldevicemaster.so）。

依赖库查找顺序：
  NLS_SDK_DIR 环境变量 -> 脚本同目录 -> <app>/library/nls -> ~/bin/nls -> /home/user/bin/nls
"""
import ctypes
import os

_SDK = None


def _find_lib():
    cands = []
    env = os.environ.get("NLS_SDK_DIR")
    if env:
        cands.append(os.path.join(env, "libnldevicemaster.so"))
    d = os.path.dirname(os.path.abspath(__file__))
    for base in (d,
                 os.path.normpath(os.path.join(d, "..", "library", "nls")),
                 os.path.expanduser("~/bin/nls"),
                 "/home/user/bin/nls"):
        cands.append(os.path.join(base, "libnldevicemaster.so"))
    for p in cands:
        if os.path.exists(p):
            return p
    return None


def _lib():
    global _SDK
    if _SDK is not None:
        return _SDK
    path = _find_lib()
    if not path:
        return None
    lib = ctypes.CDLL(path)
    lib.nl_EnumDevices.restype = ctypes.c_void_p
    lib.nl_EnumDevices.argtypes = [ctypes.POINTER(ctypes.c_int)]
    lib.nl_OpenDevice.restype = ctypes.c_void_p
    lib.nl_OpenDevice.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int]
    lib.nl_SendCommand.restype = ctypes.c_int
    lib.nl_SendCommand.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint]
    lib.nl_Read.restype = ctypes.c_uint
    lib.nl_Read.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint]
    lib.nl_CloseDevice.restype = ctypes.c_bool
    lib.nl_CloseDevice.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    lib.nl_ReleaseDevices.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    _SDK = lib
    return lib


def fire(timeout_ms=100):
    """软件触发扫码枪开始一次读码（SCNTRG1）。

    扫码枪以“USB 键盘+POS 复合”模式输出时，识别内容会同时敲入聚焦输入框，
    因此本函数只负责“让扫码枪出光”，条码内容由扫码对话框接收。
    返回 (ok, message/回读数据)。
    """
    lib = _lib()
    if lib is None:
        return False, "未找到 libnldevicemaster.so，请部署 NLS SDK 库（library/nls）"
    cnt = ctypes.c_int(0)
    lst = lib.nl_EnumDevices(ctypes.byref(cnt))
    if not lst or cnt.value <= 0:
        return False, "未发现扫码设备"
    try:
        dev = lib.nl_OpenDevice(lst, 0, 0)  # Nlscan
        if not dev:
            return False, "打开扫码设备失败"
        try:
            r = lib.nl_SendCommand(dev, b"SCNTRG1", 7)
            if r != 1:
                return False, "触发指令被拒（结果 {}".format(r) + "）"
            return True, ""
        finally:
            h = ctypes.c_void_p(dev)
            lib.nl_CloseDevice(ctypes.byref(h))
    finally:
        lib.nl_ReleaseDevices(ctypes.byref(lst))