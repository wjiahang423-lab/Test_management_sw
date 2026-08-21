"""Global variable management with type-aware storage."""
import json
import os
import threading

from . import syslog
from .paths import VARIABLES_FILE

TYPE_NAMES = ("int", "str", "float", "list", "dict")

SUPPORTED_TYPES = {
    "int": int,
    "str": str,
    "float": float,
    "list": list,
    "dict": dict,
}


class VariableManager:
    def __init__(self, file_path=None):
        self._file = file_path or VARIABLES_FILE
        self._lock = threading.RLock()
        self._variables = {}
        # 兼容旧版本：启动时读取一次旧 variables.json 作为初始种子，
        # 之后变量随 PLAN 保存（打开计划时 load_dict 会整体覆盖）。
        self.load()

    def load(self):
        with self._lock:
            self._variables = {}
            if not os.path.exists(self._file):
                return
            try:
                with open(self._file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for name, item in (data.get("variables", {}) or {}).items():
                    self._variables[str(name)] = {
                        "name": str(name),
                        "type": item.get("type", "str"),
                        "value": item.get("value"),
                        "description": item.get("description", ""),
                    }
            except Exception:
                syslog.exception("读取变量数据失败：{}".format(self._file))

    def save(self):
        with self._lock:
            data = {"variables": self._variables}
            try:
                os.makedirs(os.path.dirname(self._file), exist_ok=True)
                with open(self._file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception:
                syslog.exception("保存变量数据失败：{}".format(self._file))

    def to_dict(self):
        """导出当前变量为 PLAN 内存储格式：{name: {type, value, description}}。"""
        with self._lock:
            return {name: {
                "type": v["type"],
                "value": v["value"],
                "description": v.get("description", ""),
            } for name, v in self._variables.items()}

    def load_dict(self, variables):
        """用 PLAN 中保存的变量整体替换当前变量集（不写文件）。"""
        with self._lock:
            self._variables = {}
            for name, item in (variables or {}).items():
                self._variables[str(name)] = {
                    "name": str(name),
                    "type": item.get("type", "str"),
                    "value": item.get("value"),
                    "description": item.get("description", ""),
                }

    def list_variables(self):
        with self._lock:
            return [dict(v) for v in self._variables.values()]

    def get(self, name):
        with self._lock:
            item = self._variables.get(str(name))
            return dict(item) if item else None

    def exists(self, name):
        with self._lock:
            return str(name) in self._variables

    def add(self, name, vtype="str", value=None, description=""):
        with self._lock:
            if not name or str(name) in self._variables:
                return False, "变量名无效或已存在"
            if vtype not in SUPPORTED_TYPES:
                return False, "不支持的变量类型"
            self._variables[str(name)] = {
                "name": str(name),
                "type": vtype,
                "value": value,
                "description": description,
            }
            return True, ""

    def update(self, name, vtype=None, value=None, description=None):
        with self._lock:
            if str(name) not in self._variables:
                return False, "变量不存在"
            item = self._variables[str(name)]
            if vtype is not None:
                if vtype not in SUPPORTED_TYPES:
                    return False, "不支持的变量类型"
                item["type"] = vtype
            if value is not None:
                item["value"] = value
            if description is not None:
                item["description"] = description
            return True, ""

    def remove(self, name):
        with self._lock:
            if str(name) in self._variables:
                del self._variables[str(name)]
                return True, ""
            return False, "变量不存在"

    def get_value(self, name):
        with self._lock:
            item = self._variables.get(str(name))
            return item["value"] if item else None

    def set_value(self, name, value):
        with self._lock:
            item = self._variables.get(str(name))
            if not item:
                self._variables[str(name)] = {
                    "name": str(name),
                    "type": self._infer(value),
                    "value": value,
                    "description": "",
                }
                return True
            item["value"] = value
            return True

    @staticmethod
    def _infer(value):
        if isinstance(value, int) and not isinstance(value, bool):
            return "int"
        if isinstance(value, float):
            return "float"
        if isinstance(value, dict):
            return "dict"
        if isinstance(value, (list, tuple)):
            return "list"
        return "str"

    @staticmethod
    def cast(vtype, raw):
        """Convert raw string/number to the target python type."""
        try:
            if vtype == "int":
                return int(float(str(raw).strip()))
            if vtype == "float":
                return float(str(raw).strip())
            if vtype == "str":
                return str(raw)
            if vtype == "list":
                if isinstance(raw, str):
                    import json as _json
                    return _json.loads(raw)
                return list(raw)
            if vtype == "dict":
                if isinstance(raw, str):
                    import json as _json
                    return _json.loads(raw)
                return dict(raw)
        except Exception:
            pass
        return raw
