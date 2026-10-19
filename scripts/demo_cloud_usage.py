# -*- coding: utf-8 -*-
"""
cloud_driver.py 使用示例（仅供脚本编写参考，非正式测试脚本）

将以下内容复制到你的测试脚本文件顶部 import 即可使用：
    from cloud_driver import upload_test_result, test_result, sync_product_series, sync_production_lines
"""

from cloud_driver import (
    upload_test_result,
    test_result,
    sync_product_series,
    sync_production_lines,
)


def my_test_script():
    """示例：完整测试流程 + 上传"""

    # ---- 1. 可选：同步产品型号 / 产线信息 ----
    products = sync_product_series()
    if products["ok"]:
        print("产品型号列表：", [p["seriesCode"] for p in products["data"]])

    lines = sync_production_lines()
    if lines["ok"]:
        print("产线信息：", [(l["lineSn"], l["lineName"]) for l in lines["data"]])

    # ---- 2. 执行你的硬件测试逻辑（此处为模拟） ----
    # 假设你测量了电压、电流等，得到如下结果：

    voltage_rec = test_result(
        product_sn="PRODUCT_SN_001",
        batch_sn="sn11",
        case_id="TC-VOLT-001",
        case_name="主电源电压测量",
        category="电气",
        standard={"type": "range", "unit": "V", "lower": 11.0, "upper": 13.0, "nominal": 12.0},
        actual_input={"value": 12.09, "unit": "V"},
        result=0,            # 0=通过，1=失败
        duration_ms=2000,
        series_id=11,
    )
    print("电压测试上报：", voltage_rec)

    current_rec = test_result(
        product_sn="PRODUCT_SN_001",
        batch_sn="sn11",
        case_id="TC-CURR-001",
        case_name="待机电流测量",
        category="电气",
        standard={"type": "range", "unit": "A", "lower": 0.1, "upper": 0.8, "nominal": 0.5},
        actual_input={"value": 0.45, "unit": "A"},
        result=0,
        duration_ms=1500,
        series_id=11,
    )
    print("电流测试上报：", current_rec)

    # ---- 3. 批量上传（多个用例合并为一次请求，推荐） ----
    # 如果希望一次上传多个用例，用 upload_test_result + 手动构建 records：
    from cloud_driver import _build_record
    records = [
        _build_record("TC-VOLT-001", "主电源电压测量", "电气",
                      {"type": "range", "unit": "V", "lower": 11.0, "upper": 13.0, "nominal": 12.0},
                      {"value": 12.09, "unit": "V"}, result=0, duration_ms=2000),
        _build_record("TC-CURR-001", "待机电流测量", "电气",
                      {"type": "range", "unit": "A", "lower": 0.1, "upper": 0.8, "nominal": 0.5},
                      {"value": 0.45, "unit": "A"}, result=0, duration_ms=1500),
    ]
    batch_result = upload_test_result(
        product_sn="PRODUCT_SN_001",
        batch_sn="sn11",
        records=records,
        series_id=11,
        is_final=True,
        final_bom=[{"subSn": "SNS001", "subLineId": 1}],
    )
    print("批量上报结果：", batch_result)


if __name__ == "__main__":
    my_test_script()
