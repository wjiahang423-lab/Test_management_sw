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
        self.font_size = 14
        self.window_width = 1280
        self.window_height = 800
        self.speed_factor = 1.0
        self.clear_password = DEFAULT_CLEAR_PASSWORD
        self.station_id = "01"
        self.station_name = "机器人测试01工位"
        self.load()

    def load(self):
        with self._lock:
            if not os.path.exists(self._file):
                return
            try:
                with open(self._file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.font_size = int(data.get("font_size", self.font_size))
                self.window_width = int(data.get("window_width", self.window_width))
                self.window_height = int(data.get("window_height", self.window_height))
                self.speed_factor = float(data.get("speed_factor", self.speed_factor))
                self.clear_password = str(data.get("clear_password", self.clear_password))
                self.station_id = str(data.get("station_id", self.station_id))
                self.station_name = str(data.get("station_name", self.station_name))
            except Exception:
                syslog.exception("读取全局设置失败：{}".format(self._file))

    def save(self):
        with self._lock:
            data = {
                "font_size": self.font_size,
                "window_width": self.window_width,
                "window_height": self.window_height,
                "speed_factor": self.speed_factor,
                "clear_password": self.clear_password,
                "station_id": self.station_id,
                "station_name": self.station_name,
            }
            try:
                os.makedirs(os.path.dirname(self._file), exist_ok=True)
                with open(self._file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception:
                syslog.exception("保存全局设置失败：{}".format(self._file))

    def update(self, **kwargs):
        with self._lock:
            if "font_size" in kwargs:
                self.font_size = int(kwargs["font_size"])
            if "window_width" in kwargs:
                self.window_width = int(kwargs["window_width"])
            if "window_height" in kwargs:
                self.window_height = int(kwargs["window_height"])
            if "speed_factor" in kwargs:
                self.speed_factor = float(kwargs["speed_factor"])
            if "clear_password" in kwargs:
                self.clear_password = str(kwargs["clear_password"])
            if "station_id" in kwargs:
                self.station_id = str(kwargs["station_id"])
            if "station_name" in kwargs:
                self.station_name = str(kwargs["station_name"])
            self.save()
