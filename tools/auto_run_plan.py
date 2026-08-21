#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""无界面自动执行测试计划。

用法：
    python3 tools/auto_run_plan.py data/plans/全功能自动测试.plan [--sn SN] [--pop yes|no]

说明：
  - 使用 QCoreApplication（无 GUI），EngineWorker 在线程中执行全部用例
  - Pop 人机交互自动确认（--pop yes）或自动取消（--pop no）
  - 结束时自动生成 HTML 报告并按计划设置上报 JSON 到后端
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PyQt5.QtCore import QCoreApplication, QTimer

from app.core.api import register_api
from app.core.context import RuntimeContext
from app.core.engine import EngineWorker
from app.core.plan_model import TestPlan
from app.core.variable_manager import VariableManager


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan", help="测试计划文件路径（.plan）")
    ap.add_argument("--sn", default="AUTOTEST-SN-001")
    ap.add_argument("--pop", default="yes", choices=["yes", "no"],
                    help="Pop 弹窗自动确认(yes)/自动取消(no)，默认 yes")
    ap.add_argument("--timeout", type=float, default=600, help="整体执行超时(秒)，默认600")
    args = ap.parse_args()

    if not os.path.isfile(args.plan):
        print("计划文件不存在: {}".format(args.plan))
        return 2

    app = QCoreApplication(sys.argv)

    plan = TestPlan.load(args.plan)
    ctx = RuntimeContext()
    variables = VariableManager()
    ctx.set_variables(variables)
    ctx.set_sn(args.sn)
    register_api(ctx.build_api())

    class Settings:
        speed_factor = 1.0

    engine = EngineWorker(plan, ctx, variables, Settings())
    engine.wait_sn = False
    engine.continuous = False
    engine.pop_result = (args.pop.lower() == "yes")

    result = {"ok": None, "msg": ""}

    def on_pop(config):
        # 自动确认/取消弹窗
        engine.pop_result = (args.pop.lower() == "yes")
        engine.pop_requested.set()
        print("[POP] {} -> {}".format(config.get("title", ""),
                                      "确认" if engine.pop_result else "取消"))

    def on_log(msg):
        print(msg)

    def on_overall(text):
        print("== 总体结果：{} ==".format(text))

    def on_finished(ok, msg):
        result["ok"] = ok
        result["msg"] = msg
        app.quit()

    engine.sig_request_pop.connect(on_pop)
    engine.sig_log.connect(on_log)
    engine.sig_overall.connect(on_overall)
    engine.sig_run_finished.connect(on_finished)

    start = time.time()
    engine.start()
    QTimer.singleShot(int(args.timeout * 1000), app.quit)
    app.exec_()

    if engine.isRunning():
        engine.stop()
        engine.wait(5000)

    elapsed = time.time() - start
    print("=" * 60)
    print("执行完成：ok={} msg={} 耗时={:.1f}s".format(result["ok"], result["msg"], elapsed))
    if result["ok"] is None:
        print("结果未知（可能超时被强制退出）")
        return 3
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
