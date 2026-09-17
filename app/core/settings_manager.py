"""Global software settings."""
import json
import os
import threading
from . import syslog
from .paths import SETTINGS_FILE, DEFAULT_CLEAR_PASSWORD


class SettingsManager:
    def __init__(self, file_path=None):
        self._file = file_path or SETTINGS_FILE
        self._lock = threading.RLock()
        self.font_size = 20           # 执行页面字体大小
        self.manage_font_size = 18    # 管理页面字体大小
        self.exec_small_font = 20
        self.speed_factor = 1.0
        self.clear_password = DEFAULT_CLEAR_PASSWORD
        self.station_id = "01"
        self.station_name = "机器人测试01工位"
        self.report_retention_days = 1  # HTML报告保留天数，默认1天（只保留当天）
        self.pending_retention_days = 2  # 待上报数据保留天数，默认2天
        self.line_id = ""  # 产线ID（接口文档 lineId，long类型）
        self.load()

    def load(self):
        with self._lock:
            if not os.path.exists(self._file):
                return
            try:
                with open(self._file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.font_size = int(data.get("font_size", self.font_size))
                self.manage_font_size = int(data.get("manage_font_size", self.manage_font_size))
                self.exec_small_font = int(data.get("exec_small_font", self.exec_small_font))
                self.speed_factor = float(data.get("speed_factor", self.speed_factor))
                self.clear_password = str(data.get("clear_password", self.clear_password))
                self.station_id = str(data.get("station_id", self.station_id))
                self.station_name = str(data.get("station_name", self.station_name))
                self.report_retention_days = max(1, int(data.get("report_retention_days", self.report_retention_days)))
                self.pending_retention_days = max(1, int(data.get("pending_retention_days", self.pending_retention_days)))
                self.line_id = str(data.get("line_id", self.line_id))
            except Exception:
                syslog.exception("读取全局设置失败：{}".format(self._file))

    def save(self):
        with self._lock:
            data = {
                "font_size": self.font_size,
                "manage_font_size": self.manage_font_size,
                "exec_small_font": self.exec_small_font,
                "speed_factor": self.speed_factor,
                "clear_password": self.clear_password,
                "station_id": self.station_id,
                "station_name": self.station_name,
                "report_retention_days": self.report_retention_days,
                "pending_retention_days": self.pending_retention_days,
                "line_id": self.line_id,
            }
            try:
                os.makedirs(os.path.dirname(self._file), exist_ok=True)
                with open(self._file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception:
                syslog.exception("保存全局设置失败：{}".format(self._file))

    def update(self, **kwargs):
        with self._lock:
            try:
                if "font_size" in kwargs:
                    self.font_size = max(9, min(32, int(kwargs["font_size"])))
                if "manage_font_size" in kwargs:
                    self.manage_font_size = max(9, min(32, int(kwargs["manage_font_size"])))
                if "exec_small_font" in kwargs:
                    self.exec_small_font = max(8, min(40, int(kwargs["exec_small_font"])))
                if "speed_factor" in kwargs:
                    self.speed_factor = max(0.01, min(10.0, float(kwargs["speed_factor"])))
                if "clear_password" in kwargs:
                    self.clear_password = str(kwargs["clear_password"])
                if "station_id" in kwargs:
                    self.station_id = str(kwargs["station_id"])
                if "station_name" in kwargs:
                    self.station_name = str(kwargs["station_name"])
                if "report_retention_days" in kwargs:
                    self.report_retention_days = max(1, int(kwargs["report_retention_days"]))
                if "pending_retention_days" in kwargs:
                    self.pending_retention_days = max(1, int(kwargs["pending_retention_days"]))
                if "line_id" in kwargs:
                    self.line_id = str(kwargs["line_id"])
                self.save()
            except Exception:
                syslog.exception("更新全局设置失败")


# 全局设置管理器实例（供其他模块直接访问）
try:
    settings_manager = SettingsManager()
except Exception:
    settings_manager = None
