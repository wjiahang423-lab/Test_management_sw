"""Test plan data model: PLAN -> sequences -> test cases.

Each plan is persisted as an independent JSON/plan file.
"""
import json
import os
import time
import uuid

from . import syslog
from .paths import PLANS_DIR, relpath_from_root


def new_id():
    return uuid.uuid4().hex[:12]


class TestCase:
    def __init__(self):
        self.id = new_id()
        self.case_no = ""  # 测试编号（可选）
        self.name = ""
        self.type = "action"
        self.timeout_ms = 5000
        self.retry = 0
        self.fail_policy = "continue"  # 'pause' | 'continue'
        self.skip = False  # True: 跳过此用例，执行时不执行
        self.config = {}
        self.description = ""

    def to_dict(self):
        config = dict(self.config or {})
        for key in ("script", "yaml_path"):
            if key in config and isinstance(config[key], str):
                config[key] = relpath_from_root(config[key])
        return {
            "id": self.id,
            "case_no": self.case_no,
            "name": self.name,
            "type": self.type,
            "timeout_ms": self.timeout_ms,
            "retry": self.retry,
            "fail_policy": self.fail_policy,
            "skip": self.skip,
            "description": self.description,
            "config": config,
        }

    @classmethod
    def from_dict(cls, d):
        try:
            case = cls()
            case.id = d.get("id") or new_id()
            case.case_no = d.get("case_no", "")
            case.name = d.get("name", "")
            case.type = d.get("type", "action")
            case.timeout_ms = int(d.get("timeout_ms", 5000))
            case.retry = int(d.get("retry", 0))
            case.fail_policy = d.get("fail_policy", "continue")
            case.skip = bool(d.get("skip", False))
            case.description = d.get("description", "")
            case.config = d.get("config", {}) or {}
            return case
        except Exception:
            syslog.exception("解析测试用例数据失败")
            raise


class TestSequence:
    def __init__(self):
        self.id = new_id()
        self.name = ""
        self.cases = []

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "cases": [c.to_dict() for c in self.cases],
        }

    @classmethod
    def from_dict(cls, d):
        seq = cls()
        try:
            seq.id = d.get("id") or new_id()
            seq.name = d.get("name", "")
            seq.cases = [TestCase.from_dict(c) for c in d.get("cases", [])]
        except Exception:
            syslog.exception("解析测试序列数据失败")
            raise
        return seq


class TestPlan:
    def __init__(self):
        self.name = ""
        self.description = ""
        self.sequences = []
        self.variables = {}  # 全局变量随 PLAN 保存：{name: {type, value, description}}
        self.settings = {
            "storage_mode": "local",  # 'local' | 'remote'
            "batch": "",  # 当前批次
            "log_dir": "",
            "report_dir": "",
            "continuous": False,  # 连续循环：清空完成后自动从头再执行，直到强制停止/退出
            "remote_storage_enabled": False,  # 启用远程文件存储（报告/日志上传）
            "remote_storage_url": "",         # 远程文件上传接口地址
            "remote_storage_content": "both", # 上传内容：both/report_only/log_only
            "remote_storage_strategy": "always",  # 上传策略：always/on_failure
            "json_upload_enabled": False,     # 启用测试结果上报（JSON）
            "json_upload_url": "",            # 测试结果上报接口地址
            "json_upload_key": "",            # 上报密钥（可为空，服务器认证见下）
            "series_id": "",                  # 产品系列ID（接口文档 seriesId）
            "is_final": False,                # 是否总装线（接口文档 isFinal）
            "server_username": "root",        # 服务器登录用户名（HTTP 基本认证）
            "server_password": "root",        # 服务器登录密码
            "mes_server": "",
            "mes_interface": "",
            "mes_template": "",
            "remote_file_server": "",
            "remote_db_ip": "",
            "remote_db_table": "",
            # Token认证相关配置
            "login_url": "",                  # 登录接口地址
            "login_username": "",             # 登录用户名
            "login_password": "",             # 登录密码
            "token_header": "Authorization",  # Token在请求头中的字段名
            "token_prefix": "Bearer ",        # Token前缀
        }
        self.file_path = None

    def to_dict(self):
        return {
            "name": self.name,
            "description": self.description,
            "variables": self.variables,
            "settings": self.settings,
            "sequences": [s.to_dict() for s in self.sequences],
        }

    @classmethod
    def from_dict(cls, d):
        plan = cls()
        try:
            plan.name = d.get("name", "")
            plan.description = d.get("description", "")
            plan.variables = dict(d.get("variables", {}) or {})
            settings = d.get("settings", {}) or {}
            for key in plan.settings:
                if key in settings:
                    plan.settings[key] = settings[key]
            plan.sequences = [TestSequence.from_dict(s) for s in d.get("sequences", [])]
        except Exception:
            syslog.exception("解析测试计划数据失败")
            raise
        return plan

    @property
    def total_cases(self):
        return sum(len(s.cases) for s in self.sequences)

    def save(self, file_path=None):
        try:
            target = file_path or self.file_path
            if not target:
                target = os.path.join(PLANS_DIR, (self.name or "未命名计划") + ".plan")
            if not target.endswith(".plan") and not target.endswith(".json"):
                target += ".plan"
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
            self.file_path = target
            return target
        except Exception:
            syslog.exception("保存测试计划失败：{}".format(file_path or self.file_path))
            raise

    @classmethod
    def load(cls, file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            plan = cls.from_dict(data)
            plan.file_path = file_path
            return plan
        except Exception:
            syslog.exception("加载测试计划失败：{}".format(file_path))
            raise

    def flatten_cases(self):
        """Yield (sequence_index, case_index, sequence, case) in order."""
        for si, seq in enumerate(self.sequences):
            for ci, case in enumerate(seq.cases):
                yield si, ci, seq, case
