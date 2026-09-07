"""HTML report generation and result persistence (local or remote)."""
import datetime
import html
import json
import os
import threading

from . import syslog
from .paths import LOGS_DIR, REPORTS_DIR


class RunLogger:
    """Thread-safe file logger for a single test run.

    Captures the full system execution trail: engine progress, script logs,
    warnings/errors with tracebacks, detailed case info, function input/output
    parameters and actual values, and hardware usage.  Each line is prefixed
    with timestamp and level so the log file is useful on its own.
    """

    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        self._fh = None
        self._open()

    def _open(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            self._fh = open(self.path, "w", encoding="utf-8")
            self._fh.write(self._line("RUN", "===== 测试执行日志开始 ====="))
        except Exception:
            self._fh = None

    @staticmethod
    def _line(level, msg):
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return "[{}] [{}] {}\n".format(ts, level, msg)

    def write(self, level, msg):
        """Write one or more lines at the given level (thread-safe)."""
        with self._lock:
            if self._fh is None:
                return
            try:
                for ln in str(msg).splitlines() or [""]:
                    self._fh.write(self._line(level, ln))
                self._fh.flush()
            except Exception:
                pass

    def info(self, msg):
        self.write("INFO", msg)

    def warn(self, msg):
        self.write("WARN", msg)

    def error(self, msg):
        self.write("ERROR", msg)

    def debug(self, msg):
        self.write("DEBUG", msg)

    def section(self, title):
        self.write("INFO", "")
        self.write("INFO", "========== {} ==========".format(title))
        self.write("INFO", "")

    def close(self):
        with self._lock:
            if self._fh is None:
                return
            try:
                self._fh.write(self._line("RUN", "===== 测试执行日志结束 ====="))
                self._fh.close()
            except Exception:
                pass
            self._fh = None


def _today_start():
    now = datetime.datetime.now()
    return datetime.datetime(now.year, now.month, now.day).timestamp()


def _cleanup_dir(directory):
    """删除目录中修改时间早于今天凌晨的文件（保留当天报告/日志）。"""
    if not directory or not os.path.isdir(directory):
        return
    cutoff = _today_start()
    try:
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            try:
                if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except Exception:
                pass
    except Exception:
        pass


def cleanup_old_reports(dirs=None):
    """本地只保留当天报告与日志，清理之前日期的文件。"""
    targets = list(dirs or [])
    targets.extend([LOGS_DIR, REPORTS_DIR])
    seen = set()
    for d in targets:
        key = os.path.abspath(d)
        if key in seen:
            continue
        seen.add(key)
        _cleanup_dir(d)


def _now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _now_ms_str():
    """带毫秒的时间戳：YYYY-MM-DD HH:MM:SS.mmm"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + ".{:03d}".format(
        datetime.datetime.now().microsecond // 1000)


def _stamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def ensure_dirs():
    for d in (LOGS_DIR, REPORTS_DIR):
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            syslog.exception("创建目录失败：{}".format(d))


def _log_file_path(plan_settings, plan_name):
    ensure_dirs()
    base = plan_settings.get("log_dir") or LOGS_DIR
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        syslog.exception("日志目录不可用，回退默认目录：{}".format(base))
        base = LOGS_DIR
    return os.path.join(base, "{}_{}.log".format(plan_name or "plan", _stamp()))


def open_run_log(plan):
    """Open a RunLogger for a plan run, creating the target log file."""
    return RunLogger(_log_file_path(plan.settings, plan.name))


class NullLogger:
    """No-op logger used when the real log file cannot be created."""

    path = ""

    def write(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass

    def warn(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass

    def section(self, *a, **k):
        pass

    def close(self):
        pass


def _report_file_path(plan_settings, plan_name, sn):
    ensure_dirs()
    base = plan_settings.get("report_dir") or REPORTS_DIR
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        syslog.exception("报告目录不可用，回退默认目录：{}".format(base))
        base = REPORTS_DIR
    sn_part = (sn or "NOSN").replace("/", "_").replace("\\", "_")
    return os.path.join(base, "{}_{}_{}.html".format(plan_name or "plan", sn_part, _stamp()))


def _fmt_expected(exp):
    """把 expected 转成可读字符串：字典转 'key=value, key=value'，其余原样。"""
    if isinstance(exp, dict):
        return ", ".join("{}={}".format(k, v) for k, v in exp.items())
    return str(exp)


def _fmt_calib_value(value):
    """把结构化结果里的任意值转成报告可读字符串（dict/list 转 compact JSON）。"""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        try:
            import json
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            return str(value)
    return str(value)


def _session_expected(s):
    """报告显示用：优先取页面/YAML 配置的 expected；没有时回退函数自描述的 expected。"""
    exp = s.get("expected") or {}
    if not exp:
        md = s.get("measured")
        if isinstance(md, dict) and md.get("expected") is not None:
            return _fmt_expected(md["expected"])
        return ""
    return _fmt_expected(exp)


def _session_measured(s):
    """报告显示用：自描述结果只展示 value/单位/区间/打印信息，避免整字典过乱。"""
    md = s.get("measured")
    if not isinstance(md, dict):
        return md
    if "value" in md and ("expected" in md or "message" in md or "upper" in md or "lower" in md):
        text = "value={}".format(md.get("value"))
        if md.get("unit"):
            text += " {}".format(md["unit"])
        if md.get("upper") is not None or md.get("lower") is not None:
            text += " 区间[{}, {}]".format(md.get("lower"), md.get("upper"))
        if md.get("message"):
            text += " {}".format(md["message"])
        return text
    return md


class ReportBuilder:
    def __init__(self, plan, context, results, sn=None, speed=1.0):
        self.plan = plan
        self.context = context
        self.results = results  # list of dict
        self.sn = sn
        self.speed = speed

    def build_html(self):
        plan = self.plan
        title = "测试报告 - {}".format(html.escape(plan.name or "未命名计划"))
        total = len(self.results)
        passed = sum(1 for r in self.results if r.get("passed") is True)
        skipped = sum(1 for r in self.results if r.get("skipped"))
        failed = total - passed - skipped

        rows = []
        for r in self.results:
            status = r.get("state", "")
            color = {"PASS": "#27ae60", "FAIL": "#e74c3c", "跳过": "#95a5a6",
                     "超时": "#f39c12", "运行中": "#3498db", "待执行": "#95a5a6"}.get(status, "#333")
            details = (r.get("detail") or "").replace("\n", "<br/>")
            sub = ""
            if r.get("payload"):
                payload_sub = "".join(
                    "<tr><td>{}</td><td>{}</td></tr>".format(
                        html.escape(str(k)),
                        html.escape(_fmt_calib_value(v)))
                    for k, v in r["payload"].items()
                )
                sub += ("<tr><td colspan='6'><table border='1' cellpadding='4' "
                        "style='border-collapse:collapse;width:100%'><tr bgcolor='#eaf3fb'>"
                        "<th>参数</th><th>值</th></tr>{}</table></td></tr>").format(payload_sub)
            if r.get("sessions"):
                sub_rows = "".join(
                    "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>"
                    "<font color='{}'><b>{}</b></font></td></tr>".format(
                        "{:02d}".format(i + 1),
                        html.escape(str(s.get("name", ""))),
                        html.escape(str(s.get("input", ""))),
                        html.escape(str(_session_expected(s))),
                        html.escape(str(_session_measured(s))),
                        html.escape(str(s.get("detail", ""))),
                        "#27ae60" if s.get("passed") else "#e74c3c",
                        "PASS" if s.get("passed") else "FAIL"
                    )
                    for i, s in enumerate(r["sessions"])
                )
                sub = ("<tr><td colspan='6'><table border='1' cellpadding='4' "
                       "style='border-collapse:collapse;width:100%'><tr bgcolor='#f8f9fa'>"
                       "<th>#</th><th>名称</th><th>输入</th><th>期望值</th><th>实际值</th>"
                       "<th>判定</th><th>结果</th></tr>{}</table></td></tr>").format(sub_rows)
            rows.append(
                "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td style='color:{}'><b>{}</b></td>"
                "<td>{:.3f}s</td></tr>{}".format(
                    r.get("index", ""), html.escape(r.get("name", "")), html.escape(r.get("type", "")),
                    details, color, html.escape(status), r.get("duration", 0.0), sub
                )
            )
        rows_html = "\n".join(rows)

        overall = self.context.get_overall()
        overall_text = "PASS" if overall else "FAIL"
        overall_color = "#27ae60" if overall else "#e74c3c"

        html_doc = """<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8">
<title>{title}</title>
<style>
 body {{ font-family: "Microsoft YaHei", sans-serif; margin: 24px; color: #333; }}
 h1 {{ color: #2c5aa0; }}
 table {{ border-collapse: collapse; width: 100%; margin-top: 12px; }}
 th, td {{ border: 1px solid #e1e4e8; padding: 8px; text-align: left; font-size: 13px; }}
 th {{ background: #f8f9fa; }}
 .overall {{ font-size: 22px; font-weight: bold; }}
 .sn {{ font-size: 18px; color: #2c5aa0; font-weight: bold; }}
 .stat {{ margin-top: 8px; font-size: 14px; }}
</style></head>
<body>
<h1>{title}</h1>
<p>生成时间：{time}</p>
<p>计划名称：{plan_name}</p>
<p class="sn">SN：{sn}</p>
<p class="overall" style="color:{overall_color}">总体结果：{overall_text}</p>
<p class="stat">总用例：{total} ｜ 通过：{passed} ｜ 失败：{failed} ｜ 跳过：{skipped}</p>
<table>
<tr><th>序号</th><th>名称</th><th>类型</th><th>详情</th><th>状态</th><th>耗时</th></tr>
{rows}
</table>
</body></html>""".format(
            title=title, time=_now_str(), plan_name=html.escape(plan.name or ""),
            sn=html.escape(str(self.sn or "")), overall_color=overall_color,
            overall_text=overall_text, total=total, passed=passed, failed=failed,
            skipped=skipped, rows=rows_html
        )
        return html_doc


def _fallback_report(plan, results, sn, reason):
    """HTML 报告生成异常时输出的最小降级报告，保证有内容可看且不中断流程。"""
    items = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            html.escape(str(r.get("index", ""))),
            html.escape(str(r.get("name", ""))),
            html.escape(str(r.get("type", ""))),
            html.escape(str(r.get("state", ""))))
        for r in results)
    return ("<!DOCTYPE html><html lang='zh'><head><meta charset='UTF-8'>"
            "<title>测试报告（降级）</title></head><body>"
            "<h1>测试报告（降级）</h1><p>SN：{}</p><p>生成失败原因：{}</p>"
            "<table border='1' cellpadding='4' style='border-collapse:collapse'>"
            "<tr><th>序号</th><th>名称</th><th>类型</th><th>状态</th></tr>"
            "{}</table></body></html>").format(
        html.escape(str(sn or "")), html.escape(str(reason)), items)


def save_local_report(plan, context, results, sn=None, speed=1.0, log_path=None):
    ensure_dirs()
    builder = ReportBuilder(plan, context, results, sn=sn, speed=speed)
    try:
        html_doc = builder.build_html()
    except Exception as e:
        syslog.exception("生成 HTML 报告失败")
        html_doc = _fallback_report(plan, results, sn, str(e))
    report_path = _report_file_path(plan.settings, plan.name, sn)
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(html_doc)
    except Exception:
        report_path = None

    # 详情日志已在运行期间由 RunLogger 实时写入；
    # 此处仅把结果汇总追加到该日志文件末尾（若未提供则单独生成汇总日志）
    if log_path:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("\n[{}] [INFO] ========== 结果汇总 ==========\n".format(_now_str()))
                for r in results:
                    f.write("[{}] {} | {} | {} | {:.3f}s | {}\n".format(
                        r.get("index", ""), r.get("name", ""), r.get("type", ""),
                        r.get("state", ""), r.get("duration", 0.0), r.get("detail", "")))
        except Exception:
            pass
        return report_path, log_path

    log_path = _log_file_path(plan.settings, plan.name)
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            for r in results:
                f.write("[{index}] {name} | {type} | {state} | {duration:.3f}s | {detail}\n".format(**r))
    except Exception:
        log_path = None
    return report_path, log_path


def _sn_record(test_time, batch, sn, case_name, value, upper, lower, expected=None,
               type_="", status=""):
    return {
        "test_time": test_time,
        "batch": batch,
        "sn": sn or "",
        "case_name": case_name,
        "type": type_,
        "status": status,
        "value": value,
        "expected": expected,
        "threshold_upper": upper,
        "threshold_lower": lower,
    }


def _fmt_expected_payload(expected):
    """上报时把 expected 转成可读字符串：字典转 'k=v, k=v'，标量/范围原样。"""
    if isinstance(expected, dict):
        return ", ".join("{}={}".format(k, v) for k, v in expected.items())
    return expected


def basic_auth(settings):
    """从计划设置读取服务器用户名/密码，构造 requests 的 Basic Auth。

    用户名密码都为空时返回 None（不做认证），服务器不校验时也兼容。
    """
    user = (settings.get("server_username") or "").strip()
    psw = settings.get("server_password") or ""
    if not user and not psw:
        return None
    return (user, psw)


def send_json_cases(plan, context, results, sn=None):
    """逐用例 JSON 数据上报.

    一轮测试对应一个 SN/一个密钥，POST 一次，body 为：
        {
          "key": "<配置的密钥>",
          "sn": "...",
          "batch": "...",
          "test_time": "YYYY-MM-DD HH:MM:SS.mmm",
          "records": [
            {test_time, batch, sn, case_name, type, status,
             value, expected, threshold_upper, threshold_lower,
             ...},                         # 普通用例/测量项一条一条
            {..., "type": "loop",
             "list": [ {name, status, value, expected,
                        threshold_upper, threshold_lower, message}, ... ]}  # Loop: 父节点+子项列表
          ]
        }
    返回 (ok, message)。
    """
    settings = plan.settings
    url = settings.get("json_upload_url", "")
    # 密钥来源：脚本通过 test_api.set_upload_key(...) 写入的密钥优先；
    # 未设置时回退到计划设置里旧版"上报密钥"（兼容旧计划文件）。
    # 密钥可为空：服务器认证走计划设置的"服务器用户名/密码"（HTTP 基本认证）。
    key = (context.get_upload_key() if context is not None else "") or settings.get("json_upload_key", "")
    if not url:
        return False, "未配置 JSON 上报接口地址（计划设置→逐用例JSON上报）"
    auth = basic_auth(settings)

    batch = settings.get("batch", "") or ""
    test_time = _now_ms_str()
    records = []
    for r in results:
        if r.get("skipped"):
            continue
        name = r.get("name", "")
        case_type = r.get("type", "")
        state = r.get("state", "")
        base = {
            "test_time": test_time,
            "batch": batch,
            "sn": sn or "",
            "case_name": name,
            "type": case_type,
            "status": state,
        }
        # Loop：父节点一条记录，子项放在 list 中
        sessions = r.get("sessions")
        if case_type == "loop" and sessions:
            items = []
            for s in sessions:
                measured = s.get("measured")
                if isinstance(measured, dict):
                    mval = measured.get("value")
                    mmsg = measured.get("message") or s.get("detail", "")
                else:
                    mval = measured
                    mmsg = s.get("detail", "")
                items.append({
                    "name": s.get("name", ""),
                    "status": "PASS" if s.get("passed") else "FAIL",
                    "value": mval,
                    "expected": _fmt_expected_payload(s.get("expected") or {}),
                    "threshold_upper": measured.get("upper") if isinstance(measured, dict) else None,
                    "threshold_lower": measured.get("lower") if isinstance(measured, dict) else None,
                    "message": mmsg,
                })
            rec = dict(base)
            rec["value"] = None
            rec["expected"] = None
            rec["threshold_upper"] = None
            rec["threshold_lower"] = None
            rec["list"] = items
            records.append(rec)
            continue
        # Measurement：每个返回项一条记录（含实际值 + 阈值上下限）
        measurements = r.get("measurements")
        if measurements:
            for m in measurements:
                records.append(_sn_record(test_time, batch, sn, name,
                                          m.get("value"),
                                          m.get("upper"), m.get("lower"),
                                          m.get("expected"), case_type, state))
            continue
        # 其它类型：一条记录，实际值记结果状态；若该用例带有结构化结果（如标定参数），
        # 一并附到记录里并展开到顶层，便于后端直接入库标定数据。
        rec = _sn_record(test_time, batch, sn, name, state, None, None,
                         None, case_type, state)
        payload = r.get("payload")
        if isinstance(payload, dict):
            flat = {k: payload[k] for k in payload if k in (
                "sn", "passed", "rmse", "threshold", "note") and payload[k] is not None}
            if flat:
                rec.update(flat)
            if payload.get("extrinsic") is not None:
                rec["extrinsic"] = payload["extrinsic"]
        records.append(rec)
    if not records:
        return False, "没有可上报的用例数据"

    payload = {
        "key": key,
        "sn": sn or "",
        "batch": batch,
        "station_id": context.get_station_id() if context is not None else "",
        "test_time": test_time,
        "records": records,
    }
    try:
        import requests
        resp = requests.post(url, json=payload, timeout=15, auth=auth)
        return resp.ok, "HTTP {}".format(resp.status_code)
    except Exception as e:
        return False, str(e)


def send_remote_report(plan, context, results, sn=None):
    """Best-effort remote reporting via the configured MES interface."""
    settings = plan.settings
    server = settings.get("mes_server", "")
    interface = settings.get("mes_interface", "")
    if not server:
        return False, "未配置 MES 服务器地址"
    auth = basic_auth(settings)
    url = interface if interface.startswith("http") else (server.rstrip("/") + "/" + interface.lstrip("/"))
    payload = {
        "plan": plan.name,
        "sn": sn,
        "station_id": context.get_station_id() if context is not None else "",
        "overall": bool(context.get_overall()),
        "total": len(results),
        "passed": sum(1 for r in results if r.get("passed") is True),
        "failed": sum(1 for r in results if r.get("passed") is not True and not r.get("skipped")),
        "skipped": sum(1 for r in results if r.get("skipped")),
        "time": _now_str(),
        "results": results,
    }
    try:
        import requests
        resp = requests.post(url, json=payload, timeout=10, auth=auth)
        return resp.ok, "HTTP {}".format(resp.status_code)
    except Exception as e:
        return False, str(e)


def upload_reports(report_path, log_path, plan_name, sn, url, auth=None):
    """把本地 HTML 报告与日志文件上传到远程服务器（multipart/form-data）。

    返回 (ok, message)。字段：report、log，另附 plan / sn。
    """
    if not url:
        return False, "未配置远程存储服务器地址"
    try:
        import requests
        with open(report_path, "rb") if report_path and os.path.exists(report_path) else None as fr, \
             open(log_path, "rb") if log_path and os.path.exists(log_path) else None as fl:
            files = {}
            if fr is not None:
                files["report"] = ("report.html", fr, "text/html")
            if fl is not None:
                files["log"] = (os.path.basename(log_path), fl, "text/plain")
            if not files:
                return False, "本地报告/日志文件不存在"
            data = {"plan": plan_name, "sn": sn or ""}
            resp = requests.post(url, data=data, files=files, timeout=15, auth=auth)
        return resp.ok, "HTTP {}".format(resp.status_code)
    except Exception as e:
        return False, "上传异常：{}".format(e)
