# -*- coding: utf-8 -*-

import sys, os, time, importlib.util

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 直接从文件加载驱动，避免测试机长时间运行时 sys.modules 缓存旧版 eyou_driver。
_DRV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eyou_driver.py")
_spec = importlib.util.spec_from_file_location(
    "eyou_driver_%d" % int(os.path.getmtime(_DRV_PATH)), _DRV_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
driver = _mod.driver
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
    

def test_enable_position(node=1):
    """切换到 PP 位置模式并使能指定节点，成功返回 bool。"""
    try:
        if driver.bus is None and not driver.connect():
            return False
        return bool(driver.enable_position(int(node)))
    except Exception as exc:
        test_api.log("节点%s 位置模式使能失败: %s" % (node, exc))
        return False


def test_move_angle(node=1, angle=30, speed=5, acc=20, timeout=30):
    """先使能，再按角度定位（相对软件零点）；失败抛异常，成功返回 dict。"""
    try:
        if driver.bus is None and not driver.connect():
            raise RuntimeError("CAN 总线未连接")
        node = int(node)
        test_api.log("节点%d 角度控制开始：先使能，再移动到 %+.2f°" % (
            node, float(angle)))
        result = driver.move_angle(
            node,
            float(angle),
            speed_deg_s=float(speed),
            acc_deg_s2=float(acc),
            timeout=float(timeout),
            log=lambda msg: test_api.log(str(msg)),
        )
        test_api.log("节点%d 角度控制完成：实际 %.3f°" % (
            node, result["actual_deg"]))
        return result
    except Exception as exc:
        test_api.log("节点%s 角度控制失败: %s" % (node, exc))
        raise


def test_set_torque(node=1, torque=0):
    """设置指定节点的扭矩，成功返回 bool。"""
    try:
        return bool(driver.set_torque(node, torque))
    except Exception:
        return False



def test_zero_position(node=1):
    """将指定节点当前位置设为机械零点，返回标零后的 0x6064 回读值(float)。

    流程与「意优RP-CANFD调试器」一致：0x2262 写 1 字节 1，0x1010:01
    写 "evas" 保存，再回读 0x6064。未回到 0 时返回实际值，交由计划阈值
    判定；写入或读取通信失败时抛异常，不再回退掩盖故障。
    """
    node = int(node)
    test_api.log("节点%d 开始机械标零（0x2262:00=1，保存 0x1010:01=65 76 61 73）"
                 % node)
    try:
        if driver.bus is None and not driver.connect():
            raise RuntimeError("CAN 总线未连接")
        value = float(driver.zero_position(node))
        if value == 0:
            test_api.log("节点%d 驱动器标零成功，0x6064=0" % node)
        else:
            test_api.log("节点%d 标零后 0x6064=%.0f（未回到 0，交由计划阈值判定）"
                         % (node, value))
        return value
    except Exception as exc:
        test_api.log("节点%s 机械标零失败: %s" % (node, exc))
        raise


def test_disable(node=1):
    """失能指定节点，释放电机输出，便于手动转动关节。"""
    try:
        if driver.bus is None and not driver.connect():
            return False
        node = int(node)
        ok = bool(driver.disable(node))
        test_api.log("节点%d %s" % (node, "已失能，可手动转动" if ok else "失能失败"))
        return ok
    except Exception as exc:
        test_api.log("节点%s 失能失败: %s" % (node, exc))
        return False


def test_return_zero(node=1, speed=5, acc=20, timeout=30):
    """控制指定节点回到机械零点（0°），成功返回 dict，失败抛异常。"""
    try:
        if driver.bus is None and not driver.connect():
            raise RuntimeError("CAN 总线未连接")
        node = int(node)
        test_api.log("节点%d 返回0位开始" % node)
        result = driver.return_zero(
            node,
            speed_deg_s=float(speed),
            acc_deg_s2=float(acc),
            timeout=float(timeout),
            log=lambda msg: test_api.log(str(msg)),
        )
        test_api.log("节点%d 返回0位完成：实际 %.3f°" % (
            node, result["actual_deg"]))
        return result
    except Exception as exc:
        test_api.log("节点%s 返回0位失败: %s" % (node, exc))
        raise


def test_read_angle(node=1):
    """读取指定节点当前角度，单位为度(float)。

    通信失败时必须抛异常，不能返回 None：计划里的「不等于 0」判定会把
    None 当成字符串参与比较，从而误判为 PASS。
    """
    try:
        return float(driver.read_angle(node))
    except Exception as exc:
        test_api.log("节点%s 读取角度失败: %s" % (node, exc))
        raise


def test_read_info(node=1, address="0x1008"):
    """读取指定节点的 SDO 地址，返回字符串；失败抛异常。

    SN/SW/HW 等用例在计划里没有阈值，如果这里返回 None 会被判为 PASS；
    因此通信失败必须抛异常，让用例明确 FAIL。
    """
    try:
        return str(driver.read_info(node, address))
    except Exception as exc:
        test_api.log("节点%s 读取 %s 失败: %s" % (node, address, exc))
        raise

def test_read_sn_(node=None):
    """读取指定节点的序列号，返回字符串。"""
    try:
        sn1 = driver.read_info(1, "0x2082")
        sn2 = driver.read_info(2, "0x2082")
        if sn1 and sn2:
            test_api.testset_sn_to_Panel(str(sn1+sn2)) # 写入测试面板与报告中 
        return str(sn1+sn2) if sn1 and sn2 else None
    except Exception:
        return None 

def test_write_info(node=1, address="0x2304", value="340"):
    """写入指定节点的 SDO 地址，返回 bool。

    数字字符串按整数写入（"340" -> 0x154），并按数值回读比对；
    非数字字符串按 ASCII 写入，按文本回读比对。
    """
    try:
        text = str(value).strip()
        try:
            want = int(text, 0)
        except ValueError:
            if not driver.write_info(node, address, text):
                return False
            time.sleep(0.1)
            return str(driver.read_info(node, address)).strip() == text
        if not driver.write_info(node, address, want):
            return False
        time.sleep(0.1) # 等待写入生效
        return driver.read_value(node, address) == want
    except Exception:
        return False
    
''' 常用的SDO地址：
# 0x2002: MotorName名称
# 0x1009: 制造硬件版本  
# 0x101A: 制造软件版本
#
# 0x2082: 设备序列号
# 0x2084: 设备MCU版本号
# 0x2083: 设备硬件版本号
# 设备身份信息（手册附录 5.1.3 / 5.1.4）

# 0x1008   # 电机名称，界面作为零件号显示
# 0x2082   # 序列号
# 0x2083   # 硬件版本
# 0x101a   # MCU 固件/软件版本

府仰电机
# 0x2304  写入340 # 
# 0x222a  写入4000# 
左右电机
# 0x2304  写入200 #
# 0x222a  写入2000# 
'''