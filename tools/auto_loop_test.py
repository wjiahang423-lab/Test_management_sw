#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量循环执行测试计划（默认 1000 次），用于长时间自动化回归/压测。

用法：
    python3 tools/auto_loop_test.py [--plan data/plans/全功能自动测试.plan]
                                   [--iterations 1000] [--sn AUTOTEST-SN-001]
                                   [--pop yes|no] [--timeout 300]
                                   [--summary data/logs/auto_loop_summary.txt]

每次迭代：全新 RuntimeContext + EngineWorker 执行整份计划，生成报告并上报 JSON；
进度实时写入 stdout 与 summary 文件；Ctrl+C 优雅停止。
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PyQt5.QtCore import QCoreApplication, QEventLoop, QTimer

from app.core.api import register_api
from app.core.context import RuntimeContext
from app.core.engine import EngineWorker
from app.core.plan_model import TestPlan
from app.core.variable_manager import VariableManager

_STOP = False


def _signal_handler(signum, frame):
    global _STOP
    _STOP = True
    print("\n收到停止信号，跑完当前轮后停止...")


def _run_once(plan_path, sn, pop_yes, timeout_s):
    """执行一轮完整计划，返回 (ok, total, passes, fails, elapsed)。"""
    plan = TestPlan.load(plan_path)
    ctx = RuntimeContext()
    variables = VariableManager()
    ctx.set_variables(variables)
    ctx.set_sn(sn)
    register_api(ctx.build_api())

    class Settings:
        speed_factor = 1.0

    engine = EngineWorker(plan, ctx, variables, Settings())
    engine.wait_sn = False
    engine.continuous = False
    engine.pop_result = pop_yes

    loop = QEventLoop()
    state = {"ok": None}

    def on_finished(ok, msg):
        state["ok"] = ok
        loop.quit()

    engine.sig_run_finished.connect(on_finished)

    def on_pop(config):
        engine.pop_result = pop_yes
        engine.pop_requested.set()

    engine.sig_request_pop.connect(on_pop)

    start = time.time()
    engine.start()
    QTimer.singleShot(int(timeout_s * 1000), loop.quit)  # 单轮看门狗
    loop.exec_()

    if engine.isRunning():
        engine.stop()
    engine.wait(5000)

    elapsed = time.time() - start
    passes = sum(1 for r in engine.results if r.get("passed") is True)
    fails = sum(1 for r in engine.results if r.get("passed") is False)
    total = len(engine.results)
    return state["ok"], total, passes, fails, elapsed


def main():
    global _STOP
    import signal

    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default="data/plans/全功能自动测试.plan")
    ap.add_argument("--iterations", type=int, default=1000)
    ap.add_argument("--sn", default="AUTOTEST-SN-001")
    ap.add_argument("--pop", default="yes", choices=["yes", "no"])
    ap.add_argument("--timeout", type=float, default=300, help="单轮看门狗(秒)")
    ap.add_argument("--summary", default="data/logs/auto_loop_summary.txt")
    args = ap.parse_args()

    if not os.path.isfile(args.plan):
        print("计划文件不存在: {}".format(args.plan))
        return 2

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    os.makedirs(os.path.dirname(args.summary), exist_ok=True)
    app = QCoreApplication(sys.argv)
    pop_yes = args.pop.lower() == "yes"

    total_pass = 0
    total_fail = 0
    runs_done = 0
    start_all = time.time()

    print("开始批量循环测试：{} 次，计划 {}，SN={}".format(
        args.iterations, args.plan, args.sn))
    print("进度 -> {}/{}".format(0, args.iterations), flush=True)

    for i in range(1, args.iterations + 1):
        if _STOP:
            break
        ok, total, passes, fails, elapsed = _run_once(
            args.plan, args.sn, pop_yes, args.timeout)
        total_pass += passes
        total_fail += fails
        runs_done += 1

        line = "轮{} ok={} 用例={} 通过={} 失败={} 耗时={:.1f}s".format(
            i, ok, total, passes, fails, elapsed)
        print(line, flush=True)
        try:
            with open(args.summary, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    all_elapsed = time.time() - start_all
    summary = ("\n==== 批量测试结束 ====\n"
               "执行轮数: {}\n"
               "累计用例: {}\n"
               "累计通过: {}\n"
               "累计失败: {}\n"
               "总耗时: {:.1f}s\n").format(runs_done, total_pass + total_fail,
                                          total_pass, total_fail, all_elapsed)
    print(summary)
    try:
        with open(args.summary, "a", encoding="utf-8") as f:
            f.write(summary)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
