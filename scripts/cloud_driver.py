# -*- coding: utf-8 -*-
"""
产线检测云平台 - 底层驱动封装
用于测试脚本与后端交互，提供简单易懂的接口
"""

import json
import os
import requests
from datetime import datetime
from typing import Optional, Dict, Any, List


# ============================================================================
# 配置区（在脚本中按需修改）
# ============================================================================
BASE_URL = "http://your-server.com"          # 平台基础地址（替换为实际地址）
UPLOAD_PATH = "/prod-api/eol_api/det/upload" # 检测数据上传接口路径
MES_PRODUCT_PATH = "/prod-api/mes/productSeries"   # 产品型号接口（示例路径，按实际调整）
MES_LINE_PATH = "/prod-api/mes/productionLines"    # 产线信息接口（示例路径，按实际调整）
KEY = "your_key_here"                        # 鉴权密钥（替换为实际密钥）
STATION_ID = 22                              # 工位ID
DEVICE_ID = 100                              # 检测终端ID
LINE_ID = 7                                  # 产线ID
LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "logs")


# ============================================================================
# 内部工具函数
# ============================================================================

def _now_str() -> str:
    """返回当前时间字符串：yyyy-MM-dd HH:mm:ss"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_log_file() -> Optional[str]:
    """获取最新的日志文件路径（仅用于检测失败时上传）"""
    try:
        files = [f for f in os.listdir(LOG_DIR) if f.endswith(".log")]
        if files:
            files.sort(reverse=True)
            return os.path.join(LOG_DIR, files[0])
    except Exception:
        pass
    return None


def _build_det_data(
    product_sn: str,
    batch_sn: str,
    records: List[Dict],
    det_result: int = 0,
    series_id: int = 0,
    batch_id: int = 0,
    is_final: bool = False,
    final_bom: Optional[List[Dict]] = None,
    dtc_code: str = "",
) -> Dict:
    """
    构建 det_data 字典（对应接口文档 4.1 节）。

    参数:
        product_sn:   产品SN
        batch_sn:     批次编号
        records:      用例测试结果数组，每个元素参考 4.2 结构
        det_result:   0=正常，1=异常
        series_id:    产品系列ID
        batch_id:     批次ID
        is_final:     是否总装线
        final_bom:    总装线BOM（isFinal=true 时传入）
        dtc_code:     故障码（det_result=1 时填写）

    返回 det_data 字典，未使用的字段自动补默认值。
    """
    executed = [r for r in records if not r.get("skipped", False)]
    case_num = len(executed)
    pass_num = sum(1 for r in executed if r.get("result") == 0)
    fail_num = sum(1 for r in executed if r.get("result") == 1)
    # 所有执行用例均通过则 detResult=0，否则=1
    det_result = 0 if (case_num == 0 or fail_num == 0) else 1

    return {
        "lineId": LINE_ID,
        "stationId": STATION_ID,
        "productSn": product_sn or "",
        "batchId": batch_id,
        "batchSn": batch_sn or "",
        "detTime": _now_str(),
        "deviceId": DEVICE_ID,
        "seriesId": series_id,
        "detResult": det_result,
        "dtcCode": dtc_code if det_result == 1 else "",
        "caseNum": case_num,
        "passNum": pass_num,
        "failNum": fail_num,
        "records": records,
        "isFinal": is_final,
        "finalBom": final_bom or [],
    }


def _build_record(
    case_id: str,
    case_name: str,
    category: str,
    standard: Dict,
    actual_input: Dict,
    result: int,          # 0=通过，1=失败
    duration_ms: int,
    curve_data: Optional[Dict] = None,
) -> Dict:
    """
    构建单条 records 记录（对应接口文档 4.2 节）。

    参数:
        case_id:       测试用例ID（如 "TC-VOLT-001"）
        case_name:     测试用例名称
        category:      测试类别（如 "电气"/"通信"/"运动"）
        standard:      判定标准，结构参考 4.2，例如：
                       {"type": "range", "unit": "V", "lower": 11.0, "upper": 13.0, "nominal": 12.0}
        actual_input:  实际输入/实测值，例如：{"value": 12.095, "unit": "V"}
        result:        0=通过，1=失败
        duration_ms:   测试耗时（毫秒）
        curve_data:    可选，曲线数据，参考 4.3 结构
    """
    rec = {
        "caseId": case_id,
        "caseName": case_name,
        "category": category,
        "standard": standard,
        "actualInput": actual_input,
        "result": result,
        "durationMs": duration_ms,
        "testTime": _now_str(),
        "deviceId": DEVICE_ID,
    }
    if curve_data:
        rec["curveData"] = curve_data
    return rec


# ============================================================================
# 对外暴露的公开接口（测试脚本直接调用）
# ============================================================================

def upload_test_result(
    product_sn: str,
    batch_sn: str,
    records: List[Dict],
    series_id: int = 0,
    batch_id: int = 0,
    is_final: bool = False,
    final_bom: Optional[List[Dict]] = None,
    dtc_code: str = "",
) -> Dict:
    """
    上传检测结果至云平台（核心接口，对应文档 4.1 节）。

    参数:
        product_sn:   产品SN（必填）
        batch_sn:     批次编号（必填）
        records:      用例结果数组，建议用 build_record() 构造（必填）
        series_id:    产品系列ID（可选，默认0）
        batch_id:     批次ID（可选，默认0）
        is_final:     是否总装线（可选，默认False）
        final_bom:    总装线BOM，格式 [{"subSn": "SN001", "subLineId": 1}]（可选）
        dtc_code:     故障码，检测失败时填写（可选）

    返回:
        {"ok": True/False, "msg": "描述信息", "http_code": HTTP状态码}

    示例:
        >>> rec = build_record("TC-VOLT-001", "电压测量", "电气",
        ...     {"type":"range","unit":"V","lower":11.0,"upper":13.0,"nominal":12.0},
        ...     {"value": 12.09, "unit": "V"}, result=0, duration_ms=2000)
        >>> upload_test_result("SN001", "BATCH01", [rec])
    """
    url = BASE_URL + UPLOAD_PATH
    det_data = _build_det_data(
        product_sn=product_sn,
        batch_sn=batch_sn,
        records=records,
        series_id=series_id,
        batch_id=batch_id,
        is_final=is_final,
        final_bom=final_bom,
        dtc_code=dtc_code,
    )
    det_data["key"] = KEY  # 鉴权密钥

    payload_str = json.dumps(det_data, ensure_ascii=False)
    files = {"det_data": (None, payload_str)}

    # 检测失败时才上传日志文件
    if det_data["detResult"] == 1:
        log_path = _load_log_file()
        if log_path and os.path.isfile(log_path):
            with open(log_path, "rb") as f:
                files["log_file"] = (os.path.basename(log_path), f, "text/plain")

    try:
        resp = requests.post(url, files=files, timeout=15)
        ok = resp.ok
        http_code = resp.status_code
        msg = resp.text[:200] if not ok else "操作成功"
    except requests.exceptions.Timeout:
        ok, http_code, msg = False, 0, "请求超时"
    except requests.exceptions.ConnectionError as e:
        ok, http_code, msg = False, 0, f"连接失败: {str(e)[:100]}"
    except Exception as e:
        ok, http_code, msg = False, 0, str(e)

    return {"ok": ok, "msg": msg, "http_code": http_code}


def sync_product_series(page_num: int = 1, page_size: int = 100) -> Dict:
    """
    同步产品型号列表（对应文档 3.1 节，GET请求）。

    参数:
        page_num:   页码，默认1
        page_size:  每页数量，默认100

    返回:
        {"ok": True/False, "msg": "...", "data": [...]}

    示例:
        >>> ret = sync_product_series()
        >>> if ret["ok"]:
        ...     for item in ret["data"]:
        ...         print(item["seriesCode"], item["seriesName"])
    """
    url = BASE_URL + MES_PRODUCT_PATH
    params = {"pageNum": page_num, "pageSize": page_size}
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.ok:
            body = resp.json()
            if body.get("code") == 200:
                return {"ok": True, "msg": "操作成功", "data": body.get("data", [])}
            return {"ok": False, "msg": body.get("msg", "未知错误"), "data": []}
        return {"ok": False, "msg": f"HTTP {resp.status_code}", "data": []}
    except Exception as e:
        return {"ok": False, "msg": str(e), "data": []}


def sync_production_lines(page_num: int = 1, page_size: int = 100) -> Dict:
    """
    同步产线信息列表（对应文档 3.2 节，GET请求）。

    返回:
        {"ok": True/False, "msg": "...", "data": [...]}

    data 中每项包含: lineId, lineSn, lineName, lineType, nextLineId, order
    """
    url = BASE_URL + MES_LINE_PATH
    params = {"pageNum": page_num, "pageSize": page_size}
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.ok:
            body = resp.json()
            if body.get("code") == 200:
                return {"ok": True, "msg": "操作成功", "data": body.get("data", [])}
            return {"ok": False, "msg": body.get("msg", "未知错误"), "data": []}
        return {"ok": False, "msg": f"HTTP {resp.status_code}", "data": []}
    except Exception as e:
        return {"ok": False, "msg": str(e), "data": []}


# ============================================================================
# 快捷封装：测试脚本专用（一键上传，无需手动构建 records）
# ============================================================================

def test_result(
    product_sn: str,
    batch_sn: str,
    case_id: str,
    case_name: str,
    category: str,
    standard: Dict,
    actual_input: Dict,
    result: int,          # 0=通过，1=失败
    duration_ms: int,
    series_id: int = 0,
    curve_data: Optional[Dict] = None,
    **kwargs,             # 其他 det_data 字段（如 is_final, final_bom, dtc_code 等）
) -> Dict:
    """
    一键测试 & 上传：适用于单个用例的快速测试场景。

    内部自动构建 records 数组并调用 upload_test_result()。

    参数与 build_record() 相同，额外支持 kwargs 传入 det_data 顶层字段。

    返回 upload_test_result() 的结果字典。
    """
    rec = _build_record(
        case_id=case_id,
        case_name=case_name,
        category=category,
        standard=standard,
        actual_input=actual_input,
        result=result,
        duration_ms=duration_ms,
        curve_data=curve_data,
    )
    return upload_test_result(
        product_sn=product_sn,
        batch_sn=batch_sn,
        records=[rec],
        **kwargs,
    )
