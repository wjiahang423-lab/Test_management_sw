"""Test execution engine. Runs a TestPlan asynchronously in a QThread.

The engine never touches UI controls directly; it communicates through
signals. Pop dialogs are bridged back to the GUI thread via a signal +
threading.Event handshake.
"""
import concurrent.futures
import datetime
import inspect
import os
import threading
import time

from PyQt5.QtCore import QThread, pyqtSignal

from . import report as report_mod
from . import syslog
from .context import RuntimeContext
from .paths import CASE_TYPES, resolve_root_path
from .script_loader import load_function, invalidate_script_cache, last_load_error

TYPE_CAST = {
    "int": int,
    "str": str,
    "float": float,
    "list": lambda v: v if isinstance(v, list) else [v],
    "dict": lambda v: v if isinstance(v, dict) else dict(v),
}


class EngineWorker(QThread):
    sig_log = pyqtSignal(str)
    sig_case_state = pyqtSignal(str, str, str, float)  # case_id, state, detail, elapsed
    sig_case_sessions = pyqtSignal(str, list)          # case_id, session result dicts
    sig_overall = pyqtSignal(str)                      # 'PASS' | 'FAIL' | text
    sig_progress = pyqtSignal(int, int)                # done, total
    sig_run_finished = pyqtSignal(bool, str)           # ok, message
    sig_run_started = pyqtSignal()
    sig_wait_sn = pyqtSignal()
    sig_request_pop = pyqtSignal(dict)
    sig_reset_tree = pyqtSignal()
    sig_phase = pyqtSignal(str)                        # running / paused / stopped / finished
    sig_round_finished = pyqtSignal(bool, int)         # 连续模式：每轮 (通过?, 轮次)
    sig_round_started = pyqtSignal(int)                # 连续模式：每轮开始（用于清空页面日志）

    def __init__(self, plan, context, variables, settings, parent=None):
        super().__init__(parent)
        self.plan = plan
        self.ctx = context
        self.variables = variables
        self.settings = settings
        self.pause_event = threading.Event()
        self.stop_flag = threading.Event()
        self.sn_event = threading.Event()
        self.pop_requested = threading.Event()
        self.pop_result = False
        self.wait_sn = True
        self.continuous = False
        self._speed = settings.speed_factor if settings else 1.0
        self._pool = None
        self.results = []
        self.current_run_index = 0
        self.stop_requested = False
        self.last_elapsed = 0.0
        self._loop_sessions = {}
        self._last_action_payload = None
        self._logger = None
        self._log_script_cb = None

    # ---------------- control ----------------
    def pause(self):
        self.pause_event.set()
        self.sig_phase.emit("paused")

    def resume(self):
        self.pause_event.clear()
        self.sig_phase.emit("running")

    def stop(self):
        self.stop_requested = True
        self.stop_flag.set()
        self.sn_event.set()
        self.pop_requested.set()
        self.pause_event.clear()
        self.sig_phase.emit("stopped")

    def _speed_ms(self, ms):
        return max(0, ms * self._speed)

    # ---------------- 登录鉴权（上传接口前） ----------------
    def _login_for_token(self):
        """启动测试时，若启用 Token 认证且开启了 JSON 上报，
        先请求登录接口获取 Token 存入运行时上下文，供上传接口使用。

        返回 token（成功）或 ""（未启用/失败）。
        """
        settings = self.plan.settings or {}
        if not settings.get("json_upload_enabled"):
            return ""
        if not settings.get("use_token_auth"):
            return ""
        token, msg = report_mod.login_token(settings)
        if token:
            self.ctx.set_auth_token(token)
            self.sig_log.emit("登录成功，已获取Token并用于数据上报")
            self._logger.info("登录成功，已获取Token（{}）".format(msg))
        else:
            self.ctx.set_auth_token("")
            self.sig_log.emit("Token获取失败：{}（继续执行，上报使用基本认证或待重试）".format(msg))
            self._logger.warn("Token获取失败：{}".format(msg))
        return token

    # ---------------- run loop ----------------
    def run(self):
        self.stop_requested = False
        self.stop_flag.clear()
        self.pause_event.clear()
        self.sn_event.clear()
        self.pop_requested.clear()
        self.results = []
        self.ctx.reset_display()
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)

        self._logger = None
        self._log_script_cb = None
        self._log_routing_active = False
        try:
            self._logger = report_mod.open_run_log(self.plan)
            self.sig_log.connect(self._log_line)
            self._log_script_cb = self.ctx.log_callback
            self.ctx.log_callback = self._route_script_log
            self._log_routing_active = True
            self._log_run_header()
        except Exception:
            syslog.exception("初始化运行日志失败")
            self._logger = report_mod.NullLogger()
            self.sig_log.emit("警告：无法创建运行日志文件，日志不落盘")

        try:
            if self.wait_sn and not self._wait_for_sn():
                self.sig_run_finished.emit(False, "已停止")
                return
            self.sig_run_started.emit()
            self.sig_reset_tree.emit()
            self.sig_overall.emit("执行中")
            self.sig_log.emit("========== 开始执行测试计划：{} ==========".format(self.plan.name))
            self.sig_log.emit("SN：{}".format(self.ctx.get_sn()))
            self._logger.info("测试计划文件：{}".format(self.plan.file_path or "未保存"))
            self._logger.info("总用例数：{}".format(self.plan.total_cases))

            # 启动时先请求登录获取 Token（上传接口前）
            self._login_for_token()

            # 续传功能：检查并上传之前未上报的数据
            self._retry_pending_uploads()

            self._run_plan()

            if self.stop_flag.is_set():
                self.ctx.set_overall(False, "已停止")
                self.sig_overall.emit("已停止")
                self.sig_log.emit("测试已停止")
                self.sig_run_finished.emit(False, "已停止")
                return

            executed = [r for r in self.results if not r.get("skipped")]
            ok = all(r.get("passed") is True for r in executed)
            if executed and not ok:
                self.ctx.set_overall(False, "存在失败项")
            else:
                self.ctx.set_overall(True, "")
            self.sig_overall.emit("PASS" if ok else "FAIL")
            self._do_report(ok)
            self.sig_run_finished.emit(True, "执行完成")
        except Exception as e:
            import traceback
            self.sig_log.emit("执行异常：{}".format(e))
            self.sig_log.emit(traceback.format_exc())
            self._logger.error("执行异常：{}".format(e))
            self._logger.error(traceback.format_exc())
            self.sig_run_finished.emit(False, "执行异常：{}".format(e))
        finally:
            if self._logger is not None:
                if self._log_routing_active:
                    try:
                        self.sig_log.disconnect(self._log_line)
                    except Exception:
                        pass
                    self.ctx.log_callback = self._log_script_cb
                    self._log_routing_active = False
                try:
                    self._logger.close()
                except Exception:
                    syslog.exception("关闭运行日志失败")
                self._logger = None
            if self._pool:
                try:
                    self._pool.shutdown(wait=False)
                except Exception:
                    pass
            self.sig_phase.emit("finished")

    # ---------------- run logging ----------------
    def _log_line(self, msg):
        """Bridge every engine sig_log line into the run log file."""
        if self._logger is not None:
            self._logger.info(msg)

    def _route_script_log(self, msg):
        """Route script test_api.log() output to the run log file AND GUI."""
        if self._logger is not None:
            self._logger.info(msg)
        cb = self._log_script_cb
        if cb is not None:
            try:
                cb(msg)
            except Exception:
                pass

    def _log_run_header(self):
        self._logger.section("运行环境")
        self._logger.info("系统时间：{}".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        self._logger.info("执行模式：{}".format("调试" if getattr(self, "continuous", False) is False and not getattr(self, "wait_sn", True) else "正常"))
        self._logger.info("SN：{}".format(self.ctx.get_sn()))
        self._logger.info("速度系数：{}".format(self._speed))
        robot = self.ctx.get_robot_status()
        self._logger.info("机器人状态：温度={} 电流={} 电压={} 电池={}".format(
            robot.get("temperature"), robot.get("current"),
            robot.get("voltage"), robot.get("battery")))
        self._logger.section("执行进度")

    def _run_plan(self):
        total = self.plan.total_cases
        continuous = bool((self.plan.settings or {}).get("continuous"))
        round_no = 0
        while True:
            round_no += 1
            self.sig_round_started.emit(round_no)
            done = 0
            case_index = 0
            round_start = len(self.results)
            for si, ci, seq, case in self.plan.flatten_cases():
                if self._should_abort():
                    return
                case_index += 1
                if self._is_paused():
                    if self._wait_pause():
                        return
                state = self._execute_case(case, case_index, si, ci)
                if state is None:
                    return  # aborted
                done += 1
                self.sig_progress.emit(done, total)
                self.sig_case_state.emit(case.id, state, self.ctx.get_current_detail(), self.last_elapsed)
                self.sig_log.emit(
                    "[{}/{}] {} -> {}".format(case_index, total, case.name, state))
            if not continuous:
                break
            # 连续模式：每轮结束计入生产统计并生成该轮报告
            round_results = self.results[round_start:]
            executed = [r for r in round_results if not r.get("skipped")]
            round_ok = bool(executed) and all(r.get("passed") is True for r in executed)
            self.sig_round_finished.emit(round_ok, round_no)
            self.sig_log.emit("---------- 连续测试：第 {} 轮完成（{}），自动进入下一轮 ----------".format(
                round_no, "PASS" if round_ok else "FAIL"))
            try:
                self._do_report(round_ok, round_results)
            except Exception:
                syslog.exception("连续轮次报告生成异常")
            # 仅保留最近一轮结果，防止 continuous 长跑导致内存无界增长
            self.results = round_results
            self.sig_overall.emit("执行中")
        if not any(not r.get("skipped") for r in self.results):
            self.sig_log.emit("测试计划中没有实际执行的用例（已全部跳过或无用例）")

    # ---------------- helpers ----------------
    def _should_abort(self):
        if self.stop_flag.is_set():
            self.sig_log.emit("已收到停止指令，测试中断")
            return True
        return False

    def _is_paused(self):
        return self.pause_event.is_set()

    def _wait_pause(self):
        """Block while paused; returns True if aborted by stop."""
        if not self.pause_event.is_set():
            return False
        self.sig_phase.emit("paused")
        self.sig_log.emit("已暂停，等待继续...")
        while self.pause_event.is_set():
            if self.stop_flag.is_set():
                return True
            time.sleep(0.05)
        self.sig_log.emit("继续执行...")
        self.sig_phase.emit("running")
        return False

    def _wait_for_sn(self):
        self.sig_wait_sn.emit()
        while not self.sn_event.is_set():
            if self.stop_flag.is_set():
                return False
            time.sleep(0.05)
        self.sn_event.clear()
        return True

    def _wait_with_control(self, future, timeout_ms, hint="执行"):
        """Wait for future to finish while honoring pause/stop. Returns status str."""
        deadline = None if timeout_ms is None or timeout_ms < 0 else time.time() + timeout_ms / 1000.0
        while True:
            if self.stop_flag.is_set():
                return "stopped"
            if self.pause_event.is_set():
                if self._wait_pause():
                    return "stopped"
                continue
            if future.done():
                return "done"
            if deadline is not None and time.time() > deadline:
                return "timeout"
            time.sleep(0.02)

    def _request_pop(self, config):
        """Bridge a Pop request to the GUI thread and wait for the operator."""
        self.sig_request_pop.emit(config)
        while not self.pop_requested.is_set():
            if self.stop_flag.is_set():
                return False
            time.sleep(0.05)
        self.pop_requested.clear()
        return self.pop_result

    # ---------------- case execution ----------------
    def _execute_case(self, case, case_index, si, ci):
        # 跳过：不执行，仅在报告/树上标记为跳过
        if getattr(case, "skip", False):
            self.last_elapsed = 0.0
            self.ctx.finish_case(None, "该用例已标记为跳过，未执行")
            self.results.append({
                "index": case_index,
                "case_no": getattr(case, "case_no", ""),
                "name": case.name,
                "type": case.type,
                "description": getattr(case, "description", ""),
                "criterion": self._case_criterion(case),
                "state": "跳过",
                "passed": None,
                "skipped": True,
                "detail": "该用例已标记为跳过，未执行",
                "duration": 0.0,
            })
            return "跳过"
        handler = getattr(self, "_handle_{}".format(case.type), None)
        if handler is None:
            self.sig_log.emit("未知用例类型：{}".format(case.type))
            return "跳过"
        attempts = max(1, int(case.retry) + 1)
        last_result = None
        last_detail = ""
        start = time.time()
        self.sig_case_state.emit(case.id, "运行中", "", 0.0)
        self.sig_log.emit("开始执行用例：{}（{}）".format(case.name, case.type))
        self._logger.section("用例 {}：{}".format(case_index, case.name))
        self._logger.info("类型：{}　超时：{}ms　重试次数：{}　失败策略：{}".format(
            case.type, case.timeout_ms, case.retry, case.fail_policy))
        if case.description:
            self._logger.info("描述：{}".format(case.description))
        self._logger.debug("配置：{}".format(case.config))

        for attempt in range(attempts):
            if self._should_abort():
                return None
            if self._is_paused() and self._wait_pause():
                return None
            try:
                passed, detail = handler(case)
                if passed is None:
                    return None
                last_result = passed
                last_detail = detail
                if passed:
                    break
                self.sig_log.emit("第{}次尝试失败：{}".format(attempt + 1, detail))
                self._logger.warn("第{}次尝试失败：{}".format(attempt + 1, detail))
            except Exception as e:
                import traceback
                last_result = False
                last_detail = "异常：{}".format(e)
                self.sig_log.emit("第{}次尝试异常：{}".format(attempt + 1, e))
                self.sig_log.emit(traceback.format_exc())
                self._logger.error("第{}次尝试异常：{}".format(attempt + 1, e))
                self._logger.error(traceback.format_exc())

        elapsed = time.time() - start
        self.last_elapsed = elapsed
        if last_result is True:
            state = "PASS"
        elif last_result is False:
            state = "FAIL"
        else:
            state = "跳过"

        self._logger.info("结果：{}　耗时：{:.3f}s　详情：{}".format(state, elapsed, last_detail))

        self.ctx.finish_case(last_result is True, last_detail)
        result_entry = {
            "index": case_index,
            "case_no": getattr(case, "case_no", ""),
            "name": case.name,
            "type": case.type,
            "description": getattr(case, "description", ""),
            "criterion": self._case_criterion(case),
            "state": state,
            "passed": last_result is True,
            "detail": last_detail,
            "duration": elapsed,
        }
        sessions = self._loop_sessions.pop(case.id, None)
        if sessions:
            result_entry["sessions"] = sessions
        payload = self._last_action_payload
        if payload:
            result_entry["payload"] = payload
            self._last_action_payload = None
        measurements = getattr(self, "_last_measurement_rows", None)
        if measurements:
            result_entry["measurements"] = list(measurements)
            self._last_measurement_rows = None
        loop_measurements = getattr(self, "_last_loop_measurements", None)
        if loop_measurements:
            result_entry["measurements"] = list(loop_measurements)
            self._last_loop_measurements = None
        self.results.append(result_entry)

        if last_result is not True and state == "FAIL":
            if case.fail_policy == "pause":
                self.sig_log.emit("用例失败，按策略停止执行")
                self.stop_requested = True
                self.stop_flag.set()
                self.sn_event.set()
                self.pop_requested.set()
                self.pause_event.clear()
                self.sig_phase.emit("stopped")
        return state

    def _case_criterion(self, case):
        """汇总用例的“判断标准”字符串（来自 config.returns），供报告表格展示。"""
        cfg = case.config or {}
        returns = cfg.get("returns", []) or []
        parts = []
        for r in returns:
            item = str(r.get("item") or "return")
            judge = r.get("judge", "")
            threshold = r.get("threshold", "")
            if judge and threshold != "":
                parts.append("{} {} {}".format(item, judge, threshold))
            elif threshold != "":
                parts.append("{} {}".format(item, threshold))
        return "；".join(parts)

    # ---------------- type handlers ----------------
    def _script_not_loaded(self, func_name, kind):
        """脚本/函数无法加载：把详细原因写入运行日志并从 UI 输出。

        kind: 'Action' / 'Measurement' / 'Loop'。
        """
        detail = last_load_error()
        msg = "无法加载脚本函数：{}（{}）".format(func_name, kind)
        if detail:
            self._logger.error("{}\n{}".format(msg, detail))
            # 取首两行作为 UI 简洁提示，完整堆栈写入运行日志文件
            brief = "\n".join(detail.splitlines()[:2])
            self.sig_log.emit("{}：\n{}".format(msg, brief))
            return False, "{}\n{}".format(msg, brief)
        self._logger.error(msg)
        self.sig_log.emit(msg)
        return False, msg

    def _handle_action(self, case):
        cfg = case.config or {}
        script = cfg.get("script", "")
        func_name = cfg.get("function", "")
        if not script or not func_name:
            return False, "未配置脚本或函数"
        func = load_function(script, func_name)
        if func is None:
            return self._script_not_loaded(func_name, "Action")
        self._logger.info("Action 函数：{}.{}".format(script, func_name))
        self._logger.info("Action 输入参数：无（Action 仅执行）")
        future = self._pool.submit(self._safe_call, func, {}, "Action")
        status = self._wait_with_control(future, case.timeout_ms, "Action")
        if status == "stopped":
            return None, "已停止"
        if status == "timeout":
            self._logger.error("Action 执行超时（{}ms）".format(case.timeout_ms))
            return False, "执行超时（{}ms）".format(case.timeout_ms)
        result, error = future.result()
        if error:
            self._logger.error("{}".format(error))
            return False, error
        self._logger.info("Action 实际输出：{}".format(self._fmt_value(result)))
        # Action 函数返回的 dict 视为该用例的结构化结果（如标定参数），
        # 挂到本用例结果上，随报告展示与逐用例 JSON 上报。
        if isinstance(result, dict):
            self._last_action_payload = result
        return True, "执行函数：{}".format(func_name)

    def _handle_delay(self, case):
        cfg = case.config or {}
        ms = float(cfg.get("delay_ms", 0))
        ms = self._speed_ms(ms)
        desc = cfg.get("description", "")
        self.sig_log.emit("延时 {} ms（{}）".format(int(ms), desc))
        self._logger.info("延时配置：{} ms（原始 {} ms）　说明：{}".format(int(ms), cfg.get("delay_ms", 0), desc))
        deadline = time.time() + ms / 1000.0
        while True:
            if self.stop_flag.is_set():
                return None, "已停止"
            if self.pause_event.is_set() and self._wait_pause():
                return None, "已停止"
            if time.time() >= deadline:
                break
            time.sleep(0.01)
        return True, "延时完成 {} ms".format(int(ms))

    def _handle_pop(self, case):
        cfg = case.config or {}
        config = {
            "title": cfg.get("title", "提示"),
            "content": cfg.get("content", ""),
            "btn_true": cfg.get("btn_true", "确认"),
            "btn_false": cfg.get("btn_false", "取消"),
            "timeout_ms": case.timeout_ms,
        }
        self.sig_log.emit("弹出人机交互窗口：{}".format(config["title"]))
        self._logger.info("Pop 标题：{}　内容：{}".format(config["title"], config["content"]))
        result = self._request_pop(config)
        bind = cfg.get("result_var", "")
        if bind:
            self.variables.set_value(bind, result)
        self._logger.info("Pop 操作员选择：{}（绑定变量 {}={}）".format(
            "确认" if result else "取消", bind, result))
        return result, "操作员选择：{}".format("确认" if result else "取消")

    def _handle_measurement(self, case):
        cfg = case.config or {}
        script = cfg.get("script", "")
        func_name = cfg.get("function", "")
        if not script or not func_name:
            return False, "未配置脚本或函数"
        func = load_function(script, func_name)
        if func is None:
            return self._script_not_loaded(func_name, "Measurement")
        kwargs = self._build_args(cfg.get("params", []))
        self._logger.info("Measurement 函数：{}.{}".format(script, func_name))
        self._logger.info("Measurement 输入参数：{}".format(self._fmt_value(kwargs)))
        future = self._pool.submit(self._safe_call, func, kwargs, "Measurement")
        status = self._wait_with_control(future, case.timeout_ms, "Measurement")
        if status == "stopped":
            return None, "已停止"
        if status == "timeout":
            self._logger.error("Measurement 执行超时（{}ms）".format(case.timeout_ms))
            return False, "测量超时（{}ms）".format(case.timeout_ms)
        ret, error = future.result()
        if error:
            self._logger.error("{}".format(error))
            return False, error
        self._logger.info("Measurement 实际输出：{}".format(self._fmt_value(ret)))
        return self._judge_measurement(cfg, ret)

    def _handle_loop(self, case):
        cfg = case.config or {}
        script = cfg.get("script", "")
        func_name = cfg.get("function", "")
        parser_name = cfg.get("parser", "").strip()
        yaml_path = cfg.get("yaml_path", "")
        session_key = cfg.get("session_key", "").strip() or "sessions"
        if not script or not func_name:
            return False, "未配置脚本或函数"
        sessions = self._load_sessions(yaml_path, session_key)
        if sessions is None:
            return False, "无法加载 YAML 数据：{}".format(yaml_path)
        func = load_function(script, func_name)
        if func is None:
            return self._script_not_loaded(func_name, "Loop")
        self._logger.info("Loop 函数：{}.{}".format(script, func_name))
        if parser_name:
            self._logger.info("Loop 解析函数：{}".format(parser_name))
        self._logger.info("Loop 数据源：{}（共 {} 组）".format(yaml_path, len(sessions)))
        parser = None
        if parser_name and parser_name != func_name:
            parser = load_function(script, parser_name)
        overrides = cfg.get("overrides", [])
        session_results = []
        all_pass = True
        for idx, session in enumerate(sessions):
            if self._should_abort():
                return None, "已停止"
            if self._is_paused() and self._wait_pause():
                return None, "已停止"
            item, expected, name = self._normalize_loop_session(session, idx)
            for ov in overrides:
                key = ov.get("item", "")
                if not key:
                    continue
                value = self._cast_override(ov)
                if key in expected:
                    expected[key] = value
                else:
                    item[key] = value
            self.sig_log.emit("Loop [{}] {} 执行中...".format(idx + 1, name))
            self._logger.section("Loop Session {}：{}".format(idx + 1, name))
            self._logger.info("输入参数：{}".format(self._fmt_value(item)))
            self._logger.info("期望值：{}".format(self._fmt_value(expected)))
            self.ctx.clear_measure_result()  # 每个 session 执行前清空，避免串用
            caller = self._make_loop_caller(func, item)
            future = self._pool.submit(self._safe_call_plain, caller, "Loop")
            status = self._wait_with_control(future, case.timeout_ms, "Loop")
            if status == "stopped":
                return None, "已停止"
            if status == "timeout":
                self._logger.error("Loop [{}] 执行超时（{}ms）".format(idx + 1, case.timeout_ms))
                parsed = {"__timeout__": True}
            else:
                ret, error = future.result()
                if error:
                    self._logger.error("Loop [{}] 执行异常：{}".format(idx + 1, error))
                    parsed = {"__error__": error}
                else:
                    self._logger.info("Loop [{}] 实际输出：{}".format(idx + 1, self._fmt_value(ret)))
                    parsed = self._parse_loop_result(parser, ret)
                    parsed = self._merge_measure_result(parsed)
            passed, detail = self._judge_loop_session(parsed, expected)
            # 会话结束后再清一次：防止超时后被挂起的线程稍后写入的历史测量数据串到下一会话
            self.ctx.clear_measure_result()
            self._logger.info("判定结果：{}　判定详情：{}".format("PASS" if passed else "FAIL", detail))
            session_results.append({
                "name": name,
                "input": item,
                "expected": expected,
                "measured": parsed,
                "detail": detail,
                "passed": passed,
            })
            if not passed:
                all_pass = False
            self.sig_log.emit("Loop [{}] {} -> {}".format(idx + 1, name, "PASS" if passed else "FAIL"))
        self._last_loop_measurements = self._loop_to_measurement_rows(session_results)
        self.sig_case_sessions.emit(case.id, session_results)
        self._loop_sessions[case.id] = session_results
        return all_pass, "共 {} 组 session".format(len(sessions))

    def _loop_to_measurement_rows(self, session_results):
        """把 Loop session 结果转成与 Measurement 相同的上报结构。

        每个 session 的每个 expected 键生成一条记录：
          {name: "<session名>.<键>", value: 实测值, lower: 下限, upper: 上限}
        expected 键值若是范围（如 20~30）自动给上下限；
        单值（等于）视为 下限=上限=该值；无 expected 时整组 session 一条。
        """
        rows = []
        for s in session_results:
            measured = s.get("measured")
            expected = s.get("expected") or {}
            name = str(s.get("name", ""))
            if not expected:
                # 页面/YAML 未配置 expected（无阈值）时：
                # 若函数返回自带 value/expected/lower/upper 的自描述结果，整组一条上报；
                # 该结果已内置页面覆盖配置的阈值（函数从 params 读取）
                if isinstance(measured, dict) and "value" in measured:
                    rows.append({
                        "name": name,
                        "value": measured.get("value"),
                        "lower": measured.get("lower"),
                        "upper": measured.get("upper"),
                        "expected": measured.get("expected"),
                    })
                else:
                    rows.append({
                        "name": name,
                        "value": measured,
                        "lower": None,
                        "upper": None,
                        "expected": None,
                    })
                continue
            for key, thr in expected.items():
                lower, upper = self._threshold_bounds(None, thr)
                rows.append({
                    "name": "{} . {}".format(name, key),
                    "value": measured.get(key) if isinstance(measured, dict) else measured,
                    "lower": lower,
                    "upper": upper,
                    "expected": thr,
                })
        return rows

    # ---------------- loop helpers ----------------
    def _normalize_loop_session(self, session, idx):
        """把一条 YAML session 规范化为 (调用数据dict, 期望dict, 名称)。

        支持两种格式：
          1. 包装式：{name, input: {...}, expected: {...}}
          2. 扁平式：{name, 字段1: ..., 字段2: ...}（整条即数据，函数可自行判定）
        """
        if not isinstance(session, dict):
            return {"data": session}, {}, "Session {}".format(idx + 1)
        name = str(session.get("name", "Session {}".format(idx + 1)))
        expected = {}
        exp = session.get("expected")
        if isinstance(exp, dict):
            expected = dict(exp)
        inp = session.get("input")
        if isinstance(inp, dict):
            item = dict(inp)
        else:
            item = dict(session)
            if isinstance(exp, dict):
                item.pop("expected", None)
        return item, expected, name

    def _make_loop_caller(self, func, item):
        args, kwargs = self._adapt_loop_call(func, item)
        return lambda: func(*args, **kwargs)

    def _adapt_loop_call(self, func, item):
        """根据函数签名自适应传参，兼容各种 Loop 函数：

        - 函数无入参                       -> 不传参  func()
        - 入参键名与函数形参匹配            -> 关键字传参  func(**item)
        - 部分键匹配形参                    -> 只传匹配的形参
        - 函数只有一个形参（如 measure(params)）-> 整体 dict 作为该参数  func(item)
        - 函数带 **kwargs / *args          -> 尽量把 dict 传进去
        """
        try:
            sig = inspect.signature(func)
        except (TypeError, ValueError):
            return (), dict(item)
        params = []
        has_var_kw = False
        has_var_pos = False
        for pname, p in sig.parameters.items():
            if p.kind == p.VAR_KEYWORD:
                has_var_kw = True
            elif p.kind == p.VAR_POSITIONAL:
                has_var_pos = True
            else:
                params.append(pname)
        keys = set(item.keys())
        if not params and not has_var_kw and not has_var_pos:
            return (), {}
        if not keys:
            return (), {}
        if keys <= set(params):
            return (), dict(item)
        overlap = keys & set(params)
        if overlap:
            return (), {k: item[k] for k in params if k in item}
        if has_var_kw:
            return (), dict(item)
        if len(params) == 1:
            return (dict(item),), {}
        if has_var_pos:
            return (dict(item),), {}
        return (), {}

    def _safe_call_plain(self, caller, kind):
        try:
            return caller(), None
        except Exception as e:
            return None, "{} 执行异常：{}".format(kind, e)

    def _parse_loop_result(self, parser, ret):
        """用解析函数处理函数返回值；未配置或解析失败则原样返回。"""
        if parser is not None:
            try:
                return parser(ret)
            except Exception as e:
                return {"__parse_error__": "解析函数异常：{}".format(e)}
        return ret

    def _merge_measure_result(self, parsed):
        """把脚本通过 test_api.set_measure_result(...) 写入的自描述测量字段合并进会话结果。

        规则：脚本返回的 dict 优先；API 显式设置的字段只填充缺失项。
        页面覆盖配置的 expected 逐键判定照常优先（走 per-key 路径）。
        """
        api_measure = self.ctx.get_measure_result()
        if api_measure:
            self.ctx.clear_measure_result()
        if not api_measure:
            return parsed
        if isinstance(parsed, dict):
            merged = dict(parsed)
        else:
            merged = {}
            if parsed is not None:
                merged["raw"] = parsed
        for k, v in api_measure.items():
            merged.setdefault(k, v)
        return merged

    # ---------------- measurement / loop judging ----------------
    def _build_args(self, params):
        kwargs = {}
        for p in params or []:
            name = p.get("name", "")
            if not name:
                continue
            source = p.get("source", "value")
            vtype = p.get("type", "str")
            if source == "variable":
                var_name = p.get("value", "")
                value = self.variables.get_value(var_name) if self.variables else None
                if value is None:
                    value = p.get("value")
                kwargs[name] = value
            else:
                kwargs[name] = self._convert(vtype, p.get("value", ""))
        return kwargs

    def _convert(self, vtype, raw):
        try:
            if vtype == "int":
                return int(float(str(raw).strip()))
            if vtype == "float":
                return float(str(raw).strip())
            if vtype == "str":
                return str(raw)
            if vtype == "list":
                if isinstance(raw, str) and raw.strip():
                    import json
                    return json.loads(raw)
                return list(raw) if raw else []
            if vtype == "dict":
                if isinstance(raw, str) and raw.strip():
                    import json
                    return json.loads(raw)
                return dict(raw) if raw else {}
        except Exception:
            return raw
        return raw

    def _cast_override(self, ov):
        return self._convert(ov.get("type", "str"), ov.get("value", ""))

    def _extract(self, ret, item):
        """从函数返回值中提取一个值。

        item 取值规则：
          - 空 / '*' / 'return'           -> 取整个返回值（标量或整个 list/dict）
          - dict 返回值：item 为键名，支持点号路径如 'a.b.c'
          - list/tuple 返回值：item 为 0 起始的下标
        """
        if ret is None:
            return None
        if isinstance(ret, dict):
            if item in ret:
                return ret[item]
            if item in ("", "*", "return"):
                return ret
            node = ret
            for part in str(item).split("."):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                elif isinstance(node, (list, tuple)) and part.isdigit() and int(part) < len(node):
                    node = node[int(part)]
                else:
                    return None
            return node
        if isinstance(ret, (tuple, list)):
            if item in ("", "*", "return"):
                return ret
            try:
                idx = int(item)
                return ret[idx] if idx < len(ret) else None
            except (ValueError, TypeError):
                return None
        return ret

    def _judge_measurement(self, cfg, ret):
        returns = cfg.get("returns", []) or []
        bind_details = []
        self._last_measurement_rows = []  # {name, value, lower, upper}
        if not returns:
            return True, "返回值：{}".format(ret)
        all_pass = True
        for r in returns:
            item = r.get("item", "return")
            bind_var = r.get("bind_var", "")
            judge = r.get("judge", "")
            threshold = r.get("threshold", "")
            value = self._extract(ret, item)
            lower, upper = self._threshold_bounds(judge, threshold)
            self._last_measurement_rows.append({
                "name": item if item not in ("", "*", "return") else "return",
                "value": value,
                "lower": lower,
                "upper": upper,
                "expected": threshold,
            })
            if bind_var:
                self.variables.set_value(bind_var, value)
                bind_details.append("{}→{}".format(item, bind_var))
            if judge and threshold != "":
                ok, msg = self._compare(judge, value, threshold)
                if not ok:
                    all_pass = False
                bind_details.append("{} {} {} = {}".format(item, judge, threshold, ok))
        detail = "返回值：{}".format(ret)
        if bind_details:
            detail += " ｜ " + "，".join(bind_details)
        return all_pass, detail

    @staticmethod
    def _threshold_bounds(judge, threshold):
        """把判定阈值解析为（下限, 上限），无法解析返回 (None, None)。

        规则（Measurement 与 Loop 一致）：
         - 单值固定值（等于）：如 `5`      -> (5, 5)
         - 范围写法：如 `20~30`、`20,30`   -> (20, 30)
         - 大于：如 `200`（判定=大于）      -> (200, None)
         - 小于：如 `0.5`（判定=小于）      -> (None, 0.5)
         - 等于：如 `100`（判定=等于）      -> (100, 100)
         - 包含/长度/其它或解析失败          -> (None, None)
        """
        thr = str(threshold or "").strip()
        if not thr:
            return None, None
        rng = EngineWorker._split_range(thr)
        if rng is not None:
            return rng[0], rng[1]
        try:
            num = float(thr)
        except (ValueError, TypeError):
            return None, None
        if judge in (">", "大于"):
            return num, None
        if judge in ("<", "小于"):
            return None, num
        return num, num

    def _judge_loop_session(self, parsed, expected):
        """expected: dict of key->threshold（页面覆盖已合并）。无 expected 时自动判定。"""
        expected = expected or {}
        if isinstance(parsed, dict) and parsed.get("__timeout__"):
            return False, "超时"
        if isinstance(parsed, dict) and parsed.get("__error__"):
            return False, str(parsed["__error__"])
        if isinstance(parsed, dict) and parsed.get("__parse_error__"):
            return False, str(parsed["__parse_error__"])
        # 函数显式判定为 False 时以函数为准（防止 timeout/错误被 per-key 重判成 PASS）；
        # 函数判定为 True 不生效，继续走 expected 逐键/自动判定（更严格，防漏判）。
        if isinstance(parsed, dict):
            for key in ("pass", "passed"):
                if key in parsed and isinstance(parsed[key], bool):
                    if parsed[key] is False:
                        return False, str(parsed.get("message") or "函数判定：{}={}".format(key, parsed[key]))
                    break
        if not expected:
            return self._auto_judge(parsed)
        all_pass = True
        parts = []
        for key, threshold in expected.items():
            value = parsed.get(key) if isinstance(parsed, dict) else parsed
            judge = "范围内" if self._looks_like_range(threshold) else "等于"
            ok, msg = self._compare(judge, value, threshold)
            if not ok:
                all_pass = False
            parts.append("{} {} {} = {}".format(key, judge, threshold, ok))
        return all_pass, "；".join(parts)

    def _looks_like_range(self, threshold):
        if not isinstance(threshold, str):
            return False
        rng = self._split_range(threshold)
        return rng is not None and rng[0] < rng[1]

    PASS_TOKENS = ("pass", "ok", "true", "success", "yes", "y")
    FAIL_TOKENS = ("fail", "failed", "ng", "false", "error", "no", "n")

    def _auto_judge(self, parsed):
        """无 expected 配置时，从返回值中自动寻找 pass/ok/result 等状态字段判定。"""
        if isinstance(parsed, bool):
            return parsed, "结果：{}".format(parsed)
        if isinstance(parsed, dict):
            for key in ("pass", "passed", "ok", "success", "result", "status"):
                if key in parsed:
                    val = parsed[key]
                    if isinstance(val, bool):
                        return val, "{}={}".format(key, val)
                    if isinstance(val, str):
                        s = val.strip().lower()
                        if s in self.PASS_TOKENS:
                            return True, "{}={}".format(key, val)
                        if s in self.FAIL_TOKENS:
                            return False, "{}={}".format(key, val)
        if isinstance(parsed, str):
            s = parsed.strip().lower()
            if s in self.PASS_TOKENS:
                return True, "结果：{}".format(parsed)
            if s in self.FAIL_TOKENS:
                return False, "结果：{}".format(parsed)
        return True, "无 expected 且无状态字段，仅记录：{}".format(parsed)

    @staticmethod
    def _split_range(text):
        """Parse a range string like '100~300' / '100,300' / '100，300' -> (lo, hi)."""
        text = str(text).strip()
        for sep in ("~", "，", ","):
            if sep in text:
                parts = [p.strip() for p in text.split(sep)]
                if len(parts) == 2:
                    try:
                        return float(parts[0]), float(parts[1])
                    except (TypeError, ValueError):
                        return None
        return None

    @staticmethod
    def _try_json(text):
        import json as _json
        try:
            return _json.loads(str(text))
        except Exception:
            return None

    @staticmethod
    def _struct_equal(value, threshold):
        """list/dict 等值比较：阈值若是 JSON 文本则结构比较，否则按字符串比较。"""
        parsed = EngineWorker._try_json(threshold)
        if parsed is not None:
            return value == parsed
        return str(value) == str(threshold)

    @staticmethod
    def _len_compare(n, s):
        """长度判定：支持 >3 / >=3 / <5 / <=5 / 3 / 3~8（或 3,8）。"""
        s = str(s).strip()
        rng = EngineWorker._split_range(s)
        if rng is not None:
            return rng[0] <= n <= rng[1]
        for op, fn in ((">=", lambda a, b: a >= b),
                       ("<=", lambda a, b: a <= b),
                       (">", lambda a, b: a > b),
                       ("<", lambda a, b: a < b)):
            if s.startswith(op):
                try:
                    t = float(s[len(op):].strip())
                except (TypeError, ValueError):
                    return False
                return fn(n, t)
        try:
            return n == float(s)
        except (TypeError, ValueError):
            return False

    def _compare(self, judge, value, threshold):
        try:
            s_thr = str(threshold).strip()
            s_val = str(value)

            # ---------- list / tuple / dict 返回值 ----------
            if isinstance(value, (list, tuple, dict)):
                if judge == "等于":
                    return self._struct_equal(value, threshold), value
                if judge == "不等于":
                    return not self._struct_equal(value, threshold), value
                if judge == "包含":
                    if isinstance(value, dict):
                        # dict: 阈值作为“键”判断是否存在
                        return s_thr in value, value
                    lst = list(value)
                    parsed = self._try_json(threshold)
                    if parsed is not None:
                        return parsed in lst, value
                    return s_thr in [str(x) for x in lst], value
                if judge == "长度":
                    try:
                        return self._len_compare(len(value), s_thr), value
                    except TypeError:
                        return False, value
                # 大于/小于/范围内 对 list/dict 无意义，直接失败
                return False, value

            # ---------- 标量返回值 ----------
            if judge == "包含":
                return s_thr in s_val, value

            if judge == "长度":
                return False, value

            try:
                v = float(value)
            except (TypeError, ValueError):
                v = None

            if judge == "范围内":
                rng = self._split_range(s_thr)
                if rng is not None and v is not None:
                    lo, hi = rng
                    return lo <= v <= hi, value
                return False, value

            try:
                t = float(s_thr)
            except (TypeError, ValueError):
                t = None

            if judge == "等于":
                if v is not None and t is not None:
                    return v == t, value
                return s_val == s_thr, value
            if judge == "不等于":
                if v is not None and t is not None:
                    return v != t, value
                return s_val != s_thr, value
            if judge == "大于":
                if v is not None and t is not None:
                    return v > t, value
                return False, value
            if judge == "小于":
                if v is not None and t is not None:
                    return v < t, value
                return False, value
        except Exception:
            return False, value
        return False, value

    def _load_sessions(self, yaml_path, session_key):
        """加载 YAML 中的 session 列表。

        会话键名容错匹配：去除首尾 `-`/`_` 且不区分大小写，
        （如 YAML 键 `-api_board_` 可被页面输入的 `api_board` 命中）；
        找不到时依次回退到 sessions/items/data/tests/cases；
        YAML 顶层本身是列表则直接使用。
        """
        if not yaml_path:
            return []
        yaml_path = resolve_root_path(yaml_path)
        try:
            import yaml
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                def _norm(key):
                    return str(key or "").strip().strip("-").strip("_").strip().lower()
                wanted = _norm(session_key)
                if wanted:
                    for key in data.keys():
                        if _norm(key) == wanted:
                            v = data.get(key)
                            if isinstance(v, list):
                                return v
                for key in ("sessions", "items", "data", "tests", "cases"):
                    v = data.get(key)
                    if isinstance(v, list):
                        return v
            return []
        except Exception as e:
            self.sig_log.emit("加载 YAML 失败：{}".format(e))
            return None

    def _safe_call(self, func, kwargs, kind):
        try:
            result = func(**kwargs) if kwargs else func()
            return result, None
        except Exception as e:
            return None, "{} 执行异常：{}".format(kind, e)

    @staticmethod
    def _fmt_value(value):
        """Format arbitrary return values compactly for the log file."""
        try:
            import json
            text = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            text = str(value)
        return text

    def _retry_pending_uploads(self):
        """续传功能：检查并上传之前未上报的数据。"""
        settings = self.plan.settings or {}
        if not settings.get("json_upload_enabled"):
            return

        pending_list = report_mod.load_pending_data()
        if not pending_list:
            return

        self.sig_log.emit("发现 {} 条待上传数据，尝试续传...".format(len(pending_list)))
        self._logger.info("发现 {} 条待上传数据，开始续传".format(len(pending_list)))

        success_count = 0
        fail_count = 0

        for filepath, pending_info in pending_list:
            data = pending_info.get("data")
            if not data:
                report_mod.delete_pending_data(filepath)
                continue

            # 检查重试次数
            retry_count = pending_info.get("retry_count", 0)
            if retry_count >= 3:
                self.sig_log.emit("警告：数据已重试{}次仍失败，跳过: {}".format(
                    retry_count, os.path.basename(filepath)))
                self._logger.warn("数据已重试{}次仍失败，跳过: {}".format(
                    retry_count, os.path.basename(filepath)))
                fail_count += 1
                continue

            # 尝试上传
            url = settings.get("json_upload_url", "")
            if not url:
                continue

            key = pending_info.get("key") or ""
            log_path = pending_info.get("log_path") or ""
            ok, msg = report_mod._send_with_retry(
                url, data, settings,
                pending_info.get("plan_name", ""),
                pending_info.get("sn", ""),
                key,
                log_path=log_path,
                run_log=(self._logger.info if self._logger else None)
            )

            if ok:
                report_mod.delete_pending_data(filepath)
                success_count += 1
                self._logger.info("续传成功: {}".format(os.path.basename(filepath)))
            else:
                report_mod.update_pending_retry(filepath, pending_info)
                fail_count += 1
                self._logger.warn("续传失败: {} - {}".format(os.path.basename(filepath), msg))

        if success_count > 0 or fail_count > 0:
            self.sig_log.emit("续传完成：成功 {} 条，失败 {} 条".format(success_count, fail_count))
            self._logger.info("续传完成：成功 {} 条，失败 {} 条".format(success_count, fail_count))

    # ---------------- report ----------------
    def _do_report(self, ok, results=None):
        try:
            self._do_report_impl(ok, results)
        except Exception:
            import traceback as _tb
            self.sig_log.emit("生成报告异常：{}".format(_tb.format_exc()))
            if self._logger is not None:
                self._logger.error("生成报告异常：\n{}".format(_tb.format_exc()))
            syslog.exception("生成测试报告异常")

    def _do_report_impl(self, ok, results=None):
        plan = self.plan
        results = results if results is not None else self.results
        sn = self.ctx.get_sn()
        settings = plan.settings or {}
        self.sig_log.emit("生成测试报告...")
        self._logger.section("测试结果汇总")
        self._logger.info("总体结果：{}".format("PASS" if ok else "FAIL"))
        passed = sum(1 for r in results if r.get("passed") is True)
        failed = sum(1 for r in results if r.get("passed") is False)
        skipped = sum(1 for r in results if r.get("skipped"))
        self._logger.info("总用例：{}　通过：{}　失败：{}　跳过：{}".format(
            len(results), passed, failed, skipped))
        robot = self.ctx.get_robot_status()
        self._logger.info("硬件状态：温度={} 电流={} 电压={} 电池={}".format(
            robot.get("temperature"), robot.get("current"),
            robot.get("voltage"), robot.get("battery")))

        # 始终生成本地报告与日志（日志已在运行期间实时写入，此处追加汇总）
        rp, lp = report_mod.save_local_report(plan, self.ctx, results, sn=sn,
                                              log_path=self._logger.path)
        self.sig_log.emit("HTML 报告已保存：{}".format(rp or "失败"))
        self.sig_log.emit("运行日志已保存：{}".format(lp or "失败"))

        # MES 远程数据上报（原有功能）
        if settings.get("storage_mode") == "remote":
            ok_flag, msg = report_mod.send_remote_report(plan, self.ctx, results, sn=sn)
            self.sig_log.emit("远程MES上报：{}（{}）".format("成功" if ok_flag else "失败", msg))

        # 远程文件存储：上传报告/日志到服务器
        if settings.get("remote_storage_enabled"):
            upload_content = settings.get("remote_storage_content", "both")
            upload_strategy = settings.get("remote_storage_strategy", "always")
            
            # 根据上传策略决定是否上传
            should_upload = False
            if upload_strategy == "always":
                should_upload = True
            elif upload_strategy == "on_failure" and not ok:
                should_upload = True
            
            if should_upload:
                # 根据上传内容决定上传哪些文件
                report_to_upload = rp if upload_content in ("both", "report_only") else None
                log_to_upload = lp if upload_content in ("both", "log_only") else None
                
                if report_to_upload or log_to_upload:
                    ok2, msg2 = report_mod.upload_reports(
                        report_to_upload, log_to_upload, plan.name, sn,
                        settings.get("remote_storage_url", ""),
                        auth=report_mod.basic_auth(settings))
                    self.sig_log.emit("远程文件上传：{}（{}）".format("成功" if ok2 else "失败", msg2))
                else:
                    self.sig_log.emit("远程文件上传：无内容需上传（上传内容设置为空）")
            else:
                self.sig_log.emit("远程文件上传：跳过（上传策略：仅失败时上传，本次测试通过）")

        # 逐用例 JSON 数据上报（需求 1）：日志文件用本次测试的 log
        if settings.get("json_upload_enabled"):
            run_log = (self._logger.path if self._logger else None) or None
            ok3, msg3 = report_mod.send_json_cases(plan, self.ctx, results, sn=sn,
                                                   log_path=run_log,
                                                   run_log=(self._logger.info if self._logger else None))
            self.sig_log.emit("逐用例数据上报：{}（{}）".format("成功" if ok3 else "失败", msg3))

        # 本地保留指定天数的报告与日志
        extra_dirs = [settings[k] for k in ("log_dir", "report_dir") if settings.get(k)]
        retention_days = self.settings.report_retention_days if self.settings else 1
        report_mod.cleanup_old_reports(extra_dirs, retention_days)
        # 清理过期的待上报数据
        pending_days = self.settings.pending_retention_days if self.settings else 2
        report_mod.cleanup_old_pending(pending_days)
