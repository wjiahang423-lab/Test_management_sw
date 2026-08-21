"""User account & permission management.

One-key permission toggle (一键分权): when `require_login` is enabled the app
shows the login page on next startup; when disabled it goes straight to the
management page with a default admin session.
"""
import json
import os
import threading

from . import syslog
from .paths import USERS_FILE

ROLE_ADMIN = "管理员"
ROLE_OPERATOR = "操作员"


class UserManager:
    def __init__(self, file_path=None):
        self._file = file_path or USERS_FILE
        self._lock = threading.RLock()
        self._users = {}
        self.require_login = False
        self.load()

    def load(self):
        with self._lock:
            self._users = {}
            self.require_login = False
            if not os.path.exists(self._file):
                return
            try:
                with open(self._file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.require_login = bool(data.get("require_login", False))
                for name, item in (data.get("users", {}) or {}).items():
                    self._users[str(name)] = {
                        "name": str(name),
                        "password": item.get("password", ""),
                        "role": item.get("role", ROLE_OPERATOR),
                        "description": item.get("description", ""),
                    }
            except Exception:
                syslog.exception("读取用户数据失败：{}".format(self._file))

    def save(self):
        with self._lock:
            data = {
                "require_login": self.require_login,
                "users": self._users,
            }
            try:
                os.makedirs(os.path.dirname(self._file), exist_ok=True)
                with open(self._file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception:
                syslog.exception("保存用户数据失败：{}".format(self._file))

    def verify(self, name, password, role=None):
        with self._lock:
            item = self._users.get(str(name))
            if not item:
                return False, "用户不存在"
            if item["password"] != str(password):
                return False, "密码错误"
            if role is not None and item["role"] != role:
                return False, "所选角色与账号角色不匹配"
            return True, item["role"]

    def list_users(self):
        with self._lock:
            return [dict(u) for u in self._users.values()]

    def get(self, name):
        with self._lock:
            item = self._users.get(str(name))
            return dict(item) if item else None

    def add(self, name, password, role, description=""):
        with self._lock:
            if not name or str(name) in self._users:
                return False, "用户名无效或已存在"
            if role not in (ROLE_ADMIN, ROLE_OPERATOR):
                return False, "无效角色"
            self._users[str(name)] = {
                "name": str(name),
                "password": str(password),
                "role": role,
                "description": description,
            }
            self.save()
            return True, ""

    def update(self, name, password=None, role=None, description=None):
        with self._lock:
            if str(name) not in self._users:
                return False, "用户不存在"
            item = self._users[str(name)]
            if password is not None:
                item["password"] = str(password)
            if role is not None:
                if role not in (ROLE_ADMIN, ROLE_OPERATOR):
                    return False, "无效角色"
                item["role"] = role
            if description is not None:
                item["description"] = description
            self.save()
            return True, ""

    def change_password(self, name, old_password, new_password):
        with self._lock:
            if str(name) not in self._users:
                return False, "用户不存在"
            if self._users[str(name)]["password"] != str(old_password):
                return False, "原密码错误"
            if not new_password:
                return False, "新密码不能为空"
            self._users[str(name)]["password"] = str(new_password)
            self.save()
            return True, ""

    def remove(self, name):
        with self._lock:
            if str(name) not in self._users:
                return False, "用户不存在"
            del self._users[str(name)]
            self.save()
            return True, ""

    def set_require_login(self, flag):
        with self._lock:
            self.require_login = bool(flag)
            self.save()

    def get_require_login(self):
        with self._lock:
            return self.require_login
