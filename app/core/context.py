import threading


class RuntimeContext:
    """Thread-safe runtime context shared between the GUI thread and engine worker.

    Also provides the built-in API functions available to user test scripts.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self.current_sn = ""
        self.current_result = None
        self.current_detail = ""
        self.last_result = None
        self.last_detail = ""
        self.overall_result = None
        self.overall_detail = ""
        self.display_info = {}
        self.robot_status = {"temperature": None, "current": None, "voltage": None, "battery": None}
        self.log_callback = None
        self._variables = None
        self.measure_result = None  # loop 会话脚本写入的自描述测量结果
        self.upload_key = ""  # 逐用例 JSON 上报密钥（脚本通过 set_upload_key 写入）
        self.auth_token = ""  # 登录接口获取的 Token（启动时请求登录后写入，用于上报鉴权）
        self.station_id = ""
        self.station_name = ""

    def set_variables(self, variables):
        self._variables = variables

    def get_variables(self):
        return self._variables

    # ---------- SN ----------
    def set_sn(self, sn):
        with self._lock:
            self.current_sn = str(sn or "")

    def get_sn(self):
        with self._lock:
            return self.current_sn

    # ---------- results ----------
    def set_current_result(self, result, detail=""):
        with self._lock:
            self.current_result = result
            self.current_detail = detail

    def get_current_result(self):
        with self._lock:
            return self.current_result

    def get_current_detail(self):
        with self._lock:
            return self.current_detail

    def finish_case(self, result, detail=""):
        with self._lock:
            self.current_result = result
            self.current_detail = detail
            self.last_result = result
            self.last_detail = detail

    def get_last_result(self):
        with self._lock:
            return self.last_result

    def get_last_detail(self):
        with self._lock:
            return self.last_detail

    def set_overall(self, result, detail=""):
        with self._lock:
            self.overall_result = result
            self.overall_detail = detail

    def get_overall(self):
        with self._lock:
            return self.overall_result

    # ---------- display info ----------
    def set_display(self, key, value):
        with self._lock:
            self.display_info[str(key)] = value

    def get_display_info(self):
        with self._lock:
            return dict(self.display_info)

    def reset_display(self):
        with self._lock:
            self.display_info.clear()

    # ---------- robot status ----------
    def set_robot_status(self, **kwargs):
        """更新机器人状态字段（temperature/current/voltage/battery）。"""
        with self._lock:
            for key, value in kwargs.items():
                if key in self.robot_status:
                    self.robot_status[key] = value

    def get_robot_status(self, key=None):
        with self._lock:
            if key is None:
                return dict(self.robot_status)
            return self.robot_status.get(str(key))

    # ---------- measure result（Loop 自描述测量） ----------
    def set_measure_result(self, value=None, expected=None, unit=None, upper=None,
                           lower=None, message=None, passed=None):
        """写入一个 Loop 会话的自描述测量结果字段。

        这些字段会被合并进该 session 的测量结果，显示在报告中并逐条上报后端：
        value / expected / upper / lower / unit / message / pass / ok。
        优先级：页面覆盖配置的 expected 判定（逐键）依然优先；
        未配置时本字段作为实际值、阈值上下限、期望值、打印信息使用。
        """
        with self._lock:
            if self.measure_result is None:
                self.measure_result = {}
            if value is not None:
                self.measure_result["value"] = value
            if expected is not None:
                self.measure_result["expected"] = expected
            if unit is not None:
                self.measure_result["unit"] = str(unit)
            if upper is not None:
                self.measure_result["upper"] = upper
            if lower is not None:
                self.measure_result["lower"] = lower
            if message is not None:
                self.measure_result["message"] = str(message)
            if passed is not None:
                self.measure_result["pass"] = bool(passed)
                self.measure_result["ok"] = bool(passed)
        return True

    def set_measure_field(self, key, value):
        """写入任意单个测量结果字段（如 code/msg 等自定义键）。"""
        with self._lock:
            if self.measure_result is None:
                self.measure_result = {}
            self.measure_result[str(key)] = value
        return True

    def get_measure_result(self):
        with self._lock:
            return dict(self.measure_result) if self.measure_result else {}

    def get_measure_value(self):
        return self.get_measure_result().get("value")

    def get_measure_expected(self):
        return self.get_measure_result().get("expected")

    def get_measure_range(self):
        r = self.get_measure_result()
        return r.get("lower"), r.get("upper")

    def get_measure_message(self):
        return self.get_measure_result().get("message")

    def clear_measure_result(self):
        with self._lock:
            self.measure_result = None

    # ---------- JSON 上报密钥 ----------
    def set_upload_key(self, key):
        """设置逐用例 JSON 上报密钥（脚本从产品/服务器获取后回填，与上报报文 key 字段关联）。"""
        with self._lock:
            self.upload_key = str(key or "")
        return True

    def get_upload_key(self):
        with self._lock:
            return self.upload_key

    # ---------- 登录 Token（启动时请求登录接口获取） ----------
    def set_auth_token(self, token):
        with self._lock:
            self.auth_token = str(token or "")
        return True

    def get_auth_token(self):
        with self._lock:
            return self.auth_token

    # ---------- 工位信息（ID 上报后端，名称显示在页面） ----------
    def set_station_info(self, station_id=None, station_name=None):
        with self._lock:
            if station_id is not None:
                self.station_id = str(station_id)
            if station_name is not None:
                self.station_name = str(station_name)

    def get_station_id(self):
        with self._lock:
            return self.station_id

    def get_station_name(self):
        with self._lock:
            return self.station_name

    # ---------- logging ----------
    def emit_log(self, msg):
        cb = self.log_callback
        if cb is not None:
            try:
                cb(msg)
            except Exception:
                pass

    # ---------- API ----------
    def build_api(self):
        """Return dict of built-in API functions available to test scripts."""
        ctx = self

        def set_sn_to_Panel(sn):
            ctx.set_sn(sn)
            return True

        def get_sn():
            return ctx.get_sn()

        def get_test_result():
            return ctx.get_current_result()

        def get_last_test_result():
            return ctx.get_last_result()

        def get_last_test_detail():
            return ctx.get_last_detail()

        def get_display_info(key=None):
            info = ctx.get_display_info()
            if key is None:
                return info
            return info.get(str(key))

        def set_display_info(key, value):
            ctx.set_display(key, value)
            return True

        def log(msg):
            ctx.emit_log("[脚本] {}".format(msg))
            return True

        def get_variable(name):
            variables = ctx.get_variables()
            if variables is not None:
                return variables.get_value(name)
            return None

        def set_variable(name, value):
            variables = ctx.get_variables()
            if variables is not None:
                variables.set_value(name, value)
                return True
            return False

        # ---- 机器人状态 ----
        def set_robot_status(**kwargs):
            ctx.set_robot_status(**kwargs)
            ctx.emit_log("[硬件] 机器人状态更新：{}".format(kwargs))
            return True

        def set_robot_temperature(value):
            ctx.set_robot_status(temperature=value)
            ctx.emit_log("[硬件] 机器人温度：{}".format(value))
            return True

        def set_robot_current(value):
            ctx.set_robot_status(current=value)
            ctx.emit_log("[硬件] 机器人电流：{}".format(value))
            return True

        def set_robot_voltage(value):
            ctx.set_robot_status(voltage=value)
            ctx.emit_log("[硬件] 机器人电压：{}".format(value))
            return True

        def set_robot_battery(value):
            ctx.set_robot_status(battery=value)
            ctx.emit_log("[硬件] 机器人电池：{}".format(value))
            return True

        def get_robot_status(key=None):
            return ctx.get_robot_status(key)

        # ---- 自描述测量结果（Loop 会话上报/展示） ----
        def set_measure_result(value=None, expected=None, unit=None, upper=None,
                               lower=None, message=None, passed=None):
            ctx.emit_log("[测量] set_measure_result value={} expected={} lower={} upper={} passed={}".format(
                value, expected, lower, upper, passed))
            return ctx.set_measure_result(value, expected, unit, upper, lower, message, passed)

        def set_measure_value(value, unit=None):
            return ctx.set_measure_result(value=value, unit=unit)

        def set_measure_expected(expected):
            return ctx.set_measure_result(expected=expected)

        def set_measure_range(lower, upper):
            return ctx.set_measure_result(lower=lower, upper=upper)

        def set_measure_message(message):
            return ctx.set_measure_result(message=message)

        def set_measure_field(key, value):
            return ctx.set_measure_field(key, value)

        def get_measure_result():
            return ctx.get_measure_result()

        def get_measure_value():
            return ctx.get_measure_value()

        def get_measure_expected():
            return ctx.get_measure_expected()

        def get_measure_range():
            return ctx.get_measure_range()

        def get_measure_message():
            return ctx.get_measure_message()

        def reset_measure_result():
            ctx.clear_measure_result()
            return True

        # ---- JSON 上报密钥 ----
        def set_upload_key(key):
            ctx.emit_log("[上报] 已设置上报密钥：{}".format("****" if key else "(空)"))
            return ctx.set_upload_key(key)

        def get_upload_key():
            return ctx.get_upload_key()

        def get_station_id():
            return ctx.get_station_id()

        def get_station_name():
            return ctx.get_station_name()

        return {
            "set_sn_to_Panel": set_sn_to_Panel,
            "get_sn": get_sn,
            "get_test_result": get_test_result,
            "get_last_test_result": get_last_test_result,
            "get_last_test_detail": get_last_test_detail,
            "get_display_info": get_display_info,
            "set_display_info": set_display_info,
            "log": log,
            "get_variable": get_variable,
            "set_variable": set_variable,
            "set_robot_status": set_robot_status,
            "set_robot_temperature": set_robot_temperature,
            "set_robot_current": set_robot_current,
            "set_robot_voltage": set_robot_voltage,
            "set_robot_battery": set_robot_battery,
            "get_robot_status": get_robot_status,
            "set_measure_result": set_measure_result,
            "set_measure_value": set_measure_value,
            "set_measure_expected": set_measure_expected,
            "set_measure_range": set_measure_range,
            "set_measure_message": set_measure_message,
            "set_measure_field": set_measure_field,
            "get_measure_result": get_measure_result,
            "get_measure_value": get_measure_value,
            "get_measure_expected": get_measure_expected,
            "get_measure_range": get_measure_range,
            "get_measure_message": get_measure_message,
            "reset_measure_result": reset_measure_result,
            "set_upload_key": set_upload_key,
            "get_upload_key": get_upload_key,
            "get_station_id": get_station_id,
            "get_station_name": get_station_name,
        }
