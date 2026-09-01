#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地离线稳定性测试驱动（7×24 长稳跑批）。

启动本地 mock 服务，把测试脚本与上报地址指向本地，循环执行整份测试计划，
实时记录每轮结果、内存占用（RSS），并校验「失败用例集合」是否始终一致
（软件判定应确定性，网络失败/预期失败用例每轮应完全相同）。

用法：
    python3 tools/run_local_stability.py \
        --plan data/plans/全功能自动测试.plan \
        --iterations 200 --sn AUTOTEST-SN-001 \
        [--cit 同时起 CIT mock(8090)] [--mock-port 5000]

对照 auto_run_plan / auto_loop_test：本工具额外 1) 起 mock 2) 覆盖上报地址
3) 内存监控 4) 预期失败一致性校验。
"""
import argparse
import os
import signal
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PyQt5.QtCore import QCoreApplication, QEventLoop, QTimer

from app.core.api import register_api
from app.core.context import RuntimeContext
from app.core.engine import EngineWorker
from app.core.plan_model import TestPlan
from app.core.variable_manager import VariableManager

from tools import mock_server

_STOP = False


def _signal_handler(signum, frame):
    global _STOP
    _STOP = True
    print("\n收到停止信号，跑完当前轮后停止...")


def _rss_kb():
    """读取当前进程常驻内存（Linux /proc/self/status）。"""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except Exception:
        pass
    return 0


def _start_mock(port, mode):
    srv = mock_server.ThreadingHTTPServer(("127.0.0.1", port), mock_server.MockHandler)
    srv.mode = mode
    srv.submit_log = "data/logs/mock_submissions_{}.log".format(mode)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, t


def _run_once(plan_path, sn, pop_yes, timeout_s):
    """执行一轮完整计划，返回 (ok, fail_names, total, elapsed)。"""
    plan = TestPlan.load(plan_path)
    # 上报地址指向本地 mock
    plan.settings["json_upload_url"] = "http://127.0.0.1:{}/submit".format(os.environ.get("EOL_MOCK_PORT", "5000"))
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

    def on_pop(config):
        engine.pop_result = pop_yes
        engine.pop_requested.set()

    engine.sig_run_finished.connect(on_finished)
    engine.sig_request_pop.connect(on_pop)

    start = time.time()
    engine.start()
    QTimer.singleShot(int(timeout_s * 1000), loop.quit)
    loop.exec_()

    if engine.isRunning():
        engine.stop()
    engine.wait(5000)

    elapsed = time.time() - start
    fail_names = sorted(r.get("name", "") for r in engine.results
                        if r.get("passed") is False and not r.get("skipped"))
    total = len(engine.results)
    return state["ok"], fail_names, total, elapsed


def main():
    global _STOP
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default="data/plans/全功能自动测试.plan")
    ap.add_argument("--iterations", type=int, default=200)
    ap.add_argument("--sn", default="AUTOTEST-SN-001")
    ap.add_argument("--pop", default="yes", choices=["yes", "no"])
    ap.add_argument("--timeout", type=float, default=120, help="单轮看门狗(秒)")
    ap.add_argument("--mock-port", default="5000")
    ap.add_argument("--cit", action="store_true", help="同时启动 CIT mock(8090)")
    ap.add_argument("--summary", default="data/logs/local_stability_summary.txt")
    args = ap.parse_args()

    if not os.path.isfile(args.plan):
        print("计划文件不存在: {}".format(args.plan))
        return 2

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    os.environ["EOL_MOCK_BASE"] = "http://127.0.0.1:{}".format(args.mock_port)
    os.environ["EOL_CIT_URL"] = "http://127.0.0.1:{}".format(args.mock_port)
    os.environ["EOL_MOCK_PORT"] = args.mock_port

    print("启动本地 mock 服务(5000, auto)...")
    _start_mock(int(args.mock_port), "auto")
    if args.cit:
        print("启动本地 mock 服务(8090, cit)...")
        _start_mock(8090, "cit")
    time.sleep(0.3)

    os.makedirs(os.path.dirname(args.summary), exist_ok=True)
    app = QCoreApplication(sys.argv)
    pop_yes = args.pop.lower() == "yes"

    baseline_fail = None
    total_pass = 0
    total_fail = 0
    runs_done = 0
    inconsistent = 0
    rss0 = rss1 = 0
    start_all = time.time()

    print("开始本地稳定性测试：{} 次，计划 {}，SN={}".format(args.iterations, args.plan, args.sn))
    print("进度 -> {}/{}".format(0, args.iterations), flush=True)

    for i in range(1, args.iterations + 1):
        if _STOP:
            break
        ok, fail_names, total, elapsed = _run_once(args.plan, args.sn, pop_yes, args.timeout)
        if ok is None:
            print("轮{} 超时/未知（看门狗触发）".format(i), flush=True)
            continue
        runs_done += 1
        n_pass = total - len(fail_names)
        total_pass += n_pass
        total_fail += len(fail_names)
        if i == 1:
            rss0 = _rss_kb()
        rss1 = _rss_kb()

        if baseline_fail is None:
            baseline_fail = list(fail_names)
        elif list(fail_names) != baseline_fail:
            inconsistent += 1

        line = "轮{} ok={} 用例={} 通过={} 失败={} 耗时={:.1f}s RSS={}KB".format(
            i, ok, total, n_pass, len(fail_names), elapsed, rss1)
        print(line, flush=True)
        try:
            with open(args.summary, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    all_elapsed = time.time() - start_all
    mem_delta = rss1 - rss0
    summary = (
        "\n==== 本地稳定性测试结束 ====\n"
        "执行轮数: {}\n"
        "累计用例: {}\n"
        "累计通过: {}\n"
        "累计失败: {}\n"
        "失败集合不一致轮数: {}\n"
        "内存: 首轮 {}KB -> 末轮 {}KB（Δ {}KB，{}）\n"
        "总耗时: {:.1f}s\n"
        "基线失败用例({}条):\n{}\n"
    ).format(runs_done, total_pass + total_fail, total_pass, total_fail,
             inconsistent, rss0, rss1, mem_delta,
             "增长" if mem_delta > 0 else "下降/持平",
             all_elapsed, len(baseline_fail or []),
             "\n".join("  - {}".format(n) for n in (baseline_fail or [])))
    print(summary)
    try:
        with open(args.summary, "a", encoding="utf-8") as f:
            f.write(summary)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
