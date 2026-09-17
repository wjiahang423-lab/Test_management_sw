"""HTML report generation and result persistence (local or remote)."""
import datetime
import html
import json
import os
import threading
import time

from . import syslog
from .paths import LOGS_DIR, REPORTS_DIR

# 待上报数据缓存目录
PENDING_UPLOAD_DIR = os.path.join(os.path.dirname(REPORTS_DIR), "pending_uploads")


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


def _cutoff_ts(retention_days=1):
    """计算截止时间戳：retention_days 天前的凌晨0点。"""
    now = datetime.datetime.now()
    cutoff = now - datetime.timedelta(days=retention_days)
    return datetime.datetime(cutoff.year, cutoff.month, cutoff.day).timestamp()


def _cleanup_dir(directory, retention_days=1):
    """删除目录中修改时间早于截止时间的文件。"""
    if not directory or not os.path.isdir(directory):
        return
    cutoff = _cutoff_ts(retention_days)
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


def cleanup_old_reports(dirs=None, retention_days=1):
    """清理旧报告与日志，保留指定天数。"""
    targets = list(dirs or [])
    targets.extend([LOGS_DIR, REPORTS_DIR])
    seen = set()
    for d in targets:
        key = os.path.abspath(d)
        if key in seen:
            continue
        seen.add(key)
        _cleanup_dir(d, retention_days)


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
               type_="", status="", case_no=""):
    return {
        "test_time": test_time,
        "batch": batch,
        "sn": sn or "",
        "case_no": case_no,
        "case_name": case_name,
        "type": type_,
        "status": status,
        "value": value,
        "expected": expected,
        "threshold_upper": upper,
        "threshold_lower": lower,
    }


def _build_record(r, det_time=""):
    """把单条测试结果转换为 V1.4 4.2 records 元素。

    standard/actualInput/curveData 均为真实 JSON 对象/数组，不序列化为字符串。
    det_time 为顶层检测时间，作为 records 元素 testTime 的回退默认值。
    """
    case_type = r.get("type", "")
    passed = r.get("passed")
    case_result = 0 if passed is True else 1
    duration_ms = int(r.get("duration", 0) * 1000) if r.get("duration") else int(r.get("duration_ms", 0))

    rec = {
        "caseId": r.get("id", "") or r.get("case_no", ""),
        "caseName": r.get("name", ""),
        "category": case_type,
        "standard": _build_standard(r),
        "actualInput": _build_actual_input(r),
        "result": case_result,
        "durationMs": duration_ms,
        "testTime": r.get("test_time") or det_time,
        "deviceId": 0,
    }

    # Loop：展开子项到 curveData（真实对象，points 为数组）
    sessions = r.get("sessions")
    if case_type == "loop" and sessions:
        points = []
        for s in sessions:
            measured = s.get("measured")
            if isinstance(measured, dict):
                pts = measured.get("points", [])
                if isinstance(pts, list):
                    points.extend(pts)
        if points:
            rec["curveData"] = {
                "xAxis": "index",
                "yAxis": "value",
                "points": points,
            }

    # Measurement / Loop 子项：记录实际测量值以数组补充 actualInput
    measurements = r.get("measurements")
    if measurements:
        vals = []
        for m in measurements:
            v = m.get("value")
            if v is not None:
                vals.append(v)
        if vals:
            rec["actualInput"] = {"values": vals}

    # payload 扩展
    payload = r.get("payload")
    if isinstance(payload, dict):
        for k in ("extrinsic", "intrinsic", "parameters"):
            if k in payload and payload[k] is not None:
                rec[k] = payload[k]

    return rec


def _build_standard(r):
    """从结果中的 expected/阈值构造 standard 对象（真实 JSON 对象）。"""
    expected = r.get("expected")
    if isinstance(expected, dict):
        return expected
    rec = {}
    low = r.get("threshold_lower")
    up = r.get("threshold_upper")
    # 从 measurements 行的阈值兜底
    if low is None or up is None:
        ms = r.get("measurements")
        if ms:
            lows = [m.get("lower") for m in ms if m.get("lower") is not None]
            ups = [m.get("upper") for m in ms if m.get("upper") is not None]
            if lows and (low is None or low == 0):
                low = min(lows) if len(lows) > 1 else lows[0]
            if ups and (up is None or up == 0):
                up = max(ups) if len(ups) > 1 else ups[0]
    if low is not None or up is not None:
        rec["lower"] = low
        rec["upper"] = up
    if expected is not None and expected != "":
        rec["expected"] = expected
    elif r.get("detail"):
        rec["standard"] = r["detail"]
    return rec if rec else {}


def _build_final_bom(settings, is_final):
    """构造 finalBom 数组（4.4 结构：{subSn, subLineId}）。

    分装线(isFinal=false)可返回空数组；总装线从计划设置 bom_list 读取。
    """
    if not is_final:
        return []
    bom = settings.get("bom_list") or []
    if isinstance(bom, list):
        return [{"subSn": str(b.get("subSn", "")), "subLineId": int(b.get("subLineId", 0))} for b in bom]
    return []


def _fmt_expected_payload(expected):
    """上报时把 expected 转成可读字符串：字典转 'k=v, k=v'，标量/范围原样。"""
    if isinstance(expected, dict):
        return ", ".join("{}={}".format(k, v) for k, v in expected.items())
    return expected


def _build_actual_input(result_entry):
    """从结果条目构建 actualInput 字段（真实 JSON 对象，不序列化为字符串）。"""
    measurements = result_entry.get("measurements")
    if measurements:
        vals = {}
        for m in measurements:
            name = m.get("name", "")
            v = m.get("value")
            if v is not None:
                vals[name or "value"] = v
        return vals if vals else {}
    payload = result_entry.get("payload")
    if isinstance(payload, dict):
        return payload
    value = result_entry.get("value")
    if value is not None:
        return {"value": value}
    return {}


def basic_auth(settings):
    """从计划设置读取服务器用户名/密码，构造 requests 的 Basic Auth。

    用户名密码都为空时返回 None（不做认证），服务器不校验时也兼容。
    """
    user = (settings.get("server_username") or "").strip()
    psw = settings.get("server_password") or ""
    if not user and not psw:
        return None
    return (user, psw)


def login_token(settings):
    """登录服务器获取Token。

    返回 (token, message)。token为None表示登录失败。
    """
    login_url = settings.get("login_url", "")
    username = settings.get("login_username", "")
    password = settings.get("login_password", "")

    if not login_url:
        return None, "未配置登录接口地址"

    try:
        import requests
        payload = {"username": username, "password": password}
        resp = requests.post(login_url, json=payload, timeout=10)

        if not resp.ok:
            return None, "登录失败: HTTP {}".format(resp.status_code)

        data = resp.json()
        # 尝试从常见的响应结构中提取token
        token = data.get("token") or data.get("access_token") or data.get("data", {}).get("token")
        if not token:
            return None, "登录响应中未找到token字段"

        syslog.info("登录成功，已获取Token")
        return token, "登录成功"
    except requests.exceptions.Timeout:
        return None, "登录请求超时"
    except requests.exceptions.ConnectionError as e:
        return None, "登录连接失败: {}".format(str(e))
    except Exception as e:
        return None, "登录异常: {}".format(str(e))


def token_auth(settings):
    """获取Token认证的请求头。

    返回 headers dict，如果未启用Token认证则返回None。
    """
    if not settings.get("use_token_auth"):
        return None

    token, msg = login_token(settings)
    if not token:
        syslog.warn("Token认证失败: {}".format(msg))
        return None

    header_name = settings.get("token_header", "Authorization")
    prefix = settings.get("token_prefix", "Bearer ")
    return {header_name: "{}{}".format(prefix, token)}


def ensure_pending_dir():
    """确保待上报数据缓存目录存在。"""
    try:
        os.makedirs(PENDING_UPLOAD_DIR, exist_ok=True)
    except Exception:
        syslog.exception("创建待上报数据目录失败")


def save_pending_data(data, plan_name, sn, key=None, log_path=None):
    """暂存待上报数据到本地文件。

    文件名格式: {plan_name}_{sn}_{timestamp}.json
    log_path: 本次测试的日志文件路径，续传时随 det_data 一起上传。
    """
    ensure_pending_dir()
    timestamp = int(time.time() * 1000)
    filename = "{}_{}_{}.json".format(
        plan_name.replace("/", "_").replace("\\", "_"),
        (sn or "NOSN").replace("/", "_").replace("\\", "_"),
        timestamp
    )
    filepath = os.path.join(PENDING_UPLOAD_DIR, filename)

    try:
        pending_info = {
            "plan_name": plan_name,
            "sn": sn,
            "create_time": _now_str(),
            "retry_count": 0,
            "data": data,
        }
        if key:
            pending_info["key"] = key
        if log_path:
            pending_info["log_path"] = log_path
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(pending_info, f, ensure_ascii=False, indent=2)
        syslog.info("数据已暂存: {}".format(filepath))
        return filepath
    except Exception as e:
        syslog.exception("暂存数据失败")
        return None


def load_pending_data():
    """加载所有待上报的数据。

    返回 [(filepath, pending_info), ...] 列表。
    """
    ensure_pending_dir()
    result = []
    try:
        for filename in os.listdir(PENDING_UPLOAD_DIR):
            if not filename.endswith(".json"):
                continue
            filepath = os.path.join(PENDING_UPLOAD_DIR, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    pending_info = json.load(f)
                result.append((filepath, pending_info))
            except Exception:
                syslog.warn("读取待上报文件失败: {}".format(filepath))
    except Exception:
        syslog.exception("扫描待上报数据目录失败")
    return result


def delete_pending_data(filepath):
    """删除已上报成功的待上报数据文件。"""
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            syslog.info("已删除待上报文件: {}".format(filepath))
    except Exception as e:
        syslog.warn("删除待上报文件失败: {}".format(str(e)))


def update_pending_retry(filepath, pending_info):
    """更新待上报数据的重试次数。"""
    try:
        pending_info["retry_count"] = pending_info.get("retry_count", 0) + 1
        pending_info["last_retry_time"] = _now_str()
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(pending_info, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def send_json_cases(plan, context, results, sn=None, log_path=None, run_log=None):
    """逐用例 JSON 数据上报，符合产线检测云平台接口文档 V1.4。

    采用 multipart/form-data 格式，表单字段：
      - det_data: 检测数据（真实 JSON 对象值）
      - log_file: 日志文件（仅 detResult=1 时上传）

    det_data 结构（字段类型见 V1.4 4.1 表）：
      {
        "lineId": long,           # 产线ID（系统设置）
        "stationId": long,        # 工位ID
        "productSn": string,      # 产品SN
        "batchId": long,          # 批次编号（来自plan.settings.batch）
        "detTime": string,        # 检测时间 yyyy-MM-dd HH:mm:ss
        "deviceId": long,         # 检测终端ID（预留，暂为空）
        "seriesId": long,         # 产品系列ID（计划设置）
        "detResult": int,         # 0=正常，1=异常
        "dtcCode": string,        # 故障码（异常时填写）
        "caseNum": int,           # 用例数量
        "passNum": int,           # 合格数量
        "failNum": int,           # 失败数量
        "records": array,         # 用例结果 JSON 数组（4.2 结构）
        "isFinal": boolean,       # 是否总装线
        "finalBom": array         # 总装线BOM（isFinal=true时上传，4.4 结构）
      }
    records 元素（4.2 结构）由 _build_record 生成，standard/actualInput/curveData
    均为真实 JSON 对象/数组，不序列化为字符串。
    返回 (ok, message)。
    """
    settings = plan.settings
    url = settings.get("json_upload_url", "")
    if not url:
        return False, "未配置 JSON 上报接口地址（计划设置→逐用例JSON上报）"

    # 密钥来源
    key = (context.get_upload_key() if context is not None else "") or settings.get("json_upload_key", "")

    # 从系统设置获取 lineId（产线ID）
    from .settings_manager import settings_manager
    line_id_str = getattr(settings_manager, 'line_id', '')
    try:
        line_id = int(line_id_str) if line_id_str else 0
    except (ValueError, TypeError):
        line_id = 0

    # 从计划设置获取 seriesId 和 isFinal
    series_id_str = settings.get("series_id", "")
    try:
        series_id = int(series_id_str) if series_id_str else 0
    except (ValueError, TypeError):
        series_id = 0
    is_final = bool(settings.get("is_final", False))

    # 时间
    det_time = _now_str()  # 格式: yyyy-MM-dd HH:mm:ss

    # 计算统计信息
    executed = [r for r in results if not r.get("skipped")]
    case_num = len(executed)
    pass_num = sum(1 for r in executed if r.get("passed") is True)
    fail_num = sum(1 for r in executed if r.get("passed") is False)
    det_result = 0 if (case_num == 0 or fail_num == 0) else 1
    # 更精确的 detResult: 所有执行用例都通过则为0，否则为1
    det_result = 0 if (executed and all(r.get("passed") is True for r in executed)) else 1

    # dtcCode: 失败时填写（简化实现，实际可由脚本提供）
    dtc_code = settings.get("dtc_code", "") if det_result == 1 else ""

    # 构建 records 数组（真实 JSON 对象，standard/actualInput/curveData 不转字符串）
    records = []
    for r in results:
        if r.get("skipped"):
            continue
        rec = _build_record(r, det_time)
        if rec:
            records.append(rec)

    # 构建 det_data
    det_data = {
        "lineId": line_id,
        "stationId": int(context.get_station_id()) if context is not None and context.get_station_id() else 0,
        "productSn": sn or "",
        "batchId": _parse_batch_id(settings.get("batch", "")),
        "detTime": det_time,
        "deviceId": 0,
        "seriesId": series_id,
        "detResult": det_result,
        "dtcCode": dtc_code,
        "caseNum": case_num,
        "passNum": pass_num,
        "failNum": fail_num,
        "records": records,
        "isFinal": is_final,
        "finalBom": _build_final_bom(settings, is_final),
    }

    # 日志文件处理：仅测试失败(detResult=1)时才与日志一起上传；
    # log_path 为本次测试的日志文件（由引擎传入），找不到时回退目录最新日志
    if det_result == 1:
        log_path = log_path or _get_last_log_path(plan, sn)

    return _send_json(url, det_data, log_path, settings, plan.name, sn, key,
                      token=(context.get_auth_token() if context is not None else ""),
                      run_log=run_log)


def _parse_batch_id(batch_str):
    """尝试将 batch 字符串解析为 long，失败则返回 0。"""
    if not batch_str:
        return 0
    try:
        return int(batch_str)
    except (ValueError, TypeError):
        return 0


def _get_last_log_path(plan, sn):
    """获取最近一次测试的日志文件路径。"""
    import os
    log_dir = plan.settings.get("log_dir", "") or os.path.join(os.path.dirname(__file__), "..", "..", "data", "logs")
    try:
        files = [f for f in os.listdir(log_dir) if f.endswith(".log")]
        if files:
            files.sort(reverse=True)
            return os.path.join(log_dir, files[0])
    except Exception:
        pass
    return None


def _send_json(url, det_data, log_path, settings, plan_name, sn, key=None, token=None, run_log=None):
    """发送检测数据，统一 multipart/form-data 格式（V1.4 4.1）。

    表单字段：
      - det_data: (None, JSON文本)，真实 JSON 对象值
      - log_file: (日志文件名, 文件, 'text/plain')，仅测试失败(detResult=1)时上传
    正常（全部通过）时仅发送 det_data。
    key 鉴权密钥放入 det_data 的 key 字段（服务端按 JSON 对象解析）。
    token: 启动时登录获取的 Token，启用 use_token_auth 时作为请求头附带。
    run_log: 可选的运行日志写入回调（如 logger.info），用于把本次上报的 det_data 写入测试日志。
    """
    import requests as _requests
    try:
        auth = basic_auth(settings)
        headers = {}
        if settings.get("use_token_auth") and token:
            header_name = settings.get("token_header", "Authorization")
            prefix = settings.get("token_prefix", "Bearer ")
            headers[header_name] = "{}{}".format(prefix, token)
        det_data_with_key = dict(det_data)
        if key:
            det_data_with_key["key"] = key
        payload_str = json.dumps(det_data_with_key, ensure_ascii=False)
        if run_log:
            try:
                run_log("上传接口：{}".format(url))
                run_log("上传的检测数据（det_data）：{}".format(payload_str))
                run_log("上传日志文件：{}".format(log_path if log_path and os.path.isfile(log_path) else "无"))
            except Exception:
                pass
        files = {"det_data": (None, payload_str)}
        if log_path and os.path.isfile(log_path):
            with open(log_path, "rb") as f:
                files["log_file"] = (os.path.basename(log_path), f, "text/plain")
                resp = _requests.post(url, files=files, headers=headers, auth=auth, timeout=30)
        else:
            resp = _requests.post(url, files=files, headers=headers, auth=auth, timeout=30)

        if resp.ok:
            syslog.info("检测数据上报成功: HTTP {}".format(resp.status_code))
            if run_log:
                try:
                    run_log("上传结果：成功（HTTP {}）".format(resp.status_code))
                except Exception:
                    pass
            return True, "HTTP {}".format(resp.status_code)
        else:
            syslog.warn("检测数据上报失败(HTTP {}): {}".format(resp.status_code, resp.text[:200]))
            if run_log:
                try:
                    run_log("上传结果：失败（HTTP {}，响应：{}）".format(resp.status_code, resp.text[:200]))
                except Exception:
                    pass
            save_pending_data(det_data, plan_name, sn, key, log_path)
            return False, "HTTP {}".format(resp.status_code)
    except Exception as e:
        syslog.warn("检测数据上报异常: {}".format(str(e)))
        if run_log:
            try:
                run_log("上传结果：异常（{}）".format(e))
            except Exception:
                pass
        save_pending_data(det_data, plan_name, sn, key, log_path)
        return False, str(e)


def _send_with_retry(url, payload, settings, plan_name, sn, key=None, log_path=None, run_log=None):
    """发送数据到服务器，统一 multipart/form-data 格式（V1.4 4.1），
    支持Token/基本认证、重试和数据暂存。

    - det_data: 真实 JSON 对象值（表单字段值为 JSON 文本，服务端按 JSON 对象解析）
    - key: 可选密钥字段
    - log_path: 待上报数据对应的日志文件（续传时随 det_data 一起上传）
    - run_log: 可选的运行日志写入回调（如 logger.info），把本次上报内容写入测试日志
    返回 (ok, message)。
    """
    import requests

    # 确定认证方式：Token认证优先
    use_token = settings.get("use_token_auth", False)
    auth = None
    headers = {}

    if use_token:
        # Token认证：先登录获取token
        token, msg = login_token(settings)
        if token:
            header_name = settings.get("token_header", "Authorization")
            prefix = settings.get("token_prefix", "Bearer ")
            headers[header_name] = "{}{}".format(prefix, token)
            syslog.info("使用Token认证进行数据上报")
        else:
            syslog.warn("Token获取失败({})，尝试使用基本认证".format(msg))
            auth = basic_auth(settings)
    else:
        # 使用基本认证
        auth = basic_auth(settings)

    payload_with_key = dict(payload)
    if key:
        payload_with_key["key"] = key
    payload_str = json.dumps(payload_with_key, ensure_ascii=False)
    if run_log:
        try:
            run_log("【续传】上传接口：{}".format(url))
            run_log("【续传】上传的检测数据（det_data）：{}".format(payload_str))
            run_log("【续传】上传日志文件：{}".format(log_path if log_path and os.path.isfile(log_path) else "无"))
        except Exception:
            pass

    files = {"det_data": (None, payload_str)}
    log_fh = None
    if log_path and os.path.isfile(log_path):
        log_fh = open(log_path, "rb")
        files["log_file"] = (os.path.basename(log_path), log_fh, "text/plain")

    max_retries = 3
    last_error = ""
    result = None
    try:
        for attempt in range(max_retries):
            try:
                resp = requests.post(url, files=files, timeout=15, auth=auth, headers=headers)
                if resp.ok:
                    syslog.info("数据上报成功: HTTP {}".format(resp.status_code))
                    if run_log:
                        try:
                            run_log("【续传】上传结果：成功（HTTP {}）".format(resp.status_code))
                        except Exception:
                            pass
                    return True, "HTTP {}".format(resp.status_code)
                else:
                    last_error = "HTTP {}".format(resp.status_code)
                    syslog.warn("数据上报失败(第{}次): {}".format(attempt + 1, last_error))
            except requests.exceptions.Timeout:
                last_error = "请求超时"
                syslog.warn("数据上报超时(第{}次)".format(attempt + 1))
            except requests.exceptions.ConnectionError as e:
                last_error = "连接失败: {}".format(str(e)[:100])
                syslog.warn("数据上报连接失败(第{}次): {}".format(attempt + 1, last_error))
            except Exception as e:
                last_error = str(e)
                syslog.warn("数据上报异常(第{}次): {}".format(attempt + 1, last_error))

            # 如果不是最后一次尝试，等待1秒后重试
            if attempt < max_retries - 1:
                time.sleep(1)

        # 3次重试都失败，暂存数据
        syslog.error("数据上报{}次均失败，暂存数据".format(max_retries))
        if run_log:
            try:
                run_log("【续传】上传结果：失败（{}）".format(last_error))
            except Exception:
                pass
        save_pending_data(payload, plan_name, sn, key, log_path)

        return False, "上报失败({})，数据已暂存，将在下次测试时重试".format(last_error)
    finally:
        if log_fh is not None:
            try:
                log_fh.close()
            except Exception:
                pass


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


def cleanup_old_pending(retention_days=2):
    """清理过期的待上报数据文件（按设置页"待上报数据保留天数"）。

    - 优先以文件中 create_time 字段为准（即数据实际创建时间）
    - 无 create_time 或解析失败时回退为文件修改时间（mtime）
    - 删除保留天数前创建/落盘的待上报 JSON 文件
    """
    ensure_pending_dir()
    try:
        cutoff = _cutoff_ts(retention_days)
        for filename in os.listdir(PENDING_UPLOAD_DIR):
            if not filename.endswith(".json"):
                continue
            filepath = os.path.join(PENDING_UPLOAD_DIR, filename)
            try:
                if not os.path.isfile(filepath):
                    continue
                ts = os.path.getmtime(filepath)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        info = json.load(f)
                    ct = info.get("create_time", "")
                    if ct:
                        import datetime as _dt
                        parsed = _dt.datetime.strptime(ct, "%Y-%m-%d %H:%M:%S")
                        ts = parsed.timestamp()
                except Exception:
                    pass
                if ts < cutoff:
                    os.remove(filepath)
                    syslog.info("已删除过期待上报文件: {}".format(filepath))
            except Exception:
                pass
    except Exception:
        pass
