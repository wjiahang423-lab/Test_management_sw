"""Application bootstrap & login flow."""
import os

from PyQt5.QtWidgets import QApplication

from app.core import report as report_mod
from app.core.api import register_api
from app.core.context import RuntimeContext
from app.core.settings_manager import SettingsManager
from app.core.user_manager import ROLE_ADMIN, UserManager
from app.core.variable_manager import VariableManager
from app.ui.login_dialog import LoginDialog
from app.ui.main_window import MainWindow

_shared = {}


def _seed_defaults(users):
    if not users.get("admin"):
        users.add("admin", "admin", ROLE_ADMIN, "系统默认管理员")
    if not users.get("operator"):
        users.add("operator", "operator", "操作员", "系统默认操作员")


def bootstrap(app=None):
    """Create shared managers & runtime context. Returns MainWindow or None."""
    global _shared
    report_mod.ensure_dirs()
    # 本地只保留当天报告与日志，启动时清理过期文件
    report_mod.cleanup_old_reports()

    settings = SettingsManager()
    variables = VariableManager()
    users = UserManager()
    context = RuntimeContext()
    context.set_variables(variables)

    # 清理过期的待上报数据（按设置页"待上报数据保留天数"）
    try:
        report_mod.cleanup_old_pending(settings.pending_retention_days)
    except Exception:
        pass

    _seed_defaults(users)
    register_api(context.build_api())

    _shared.update({
        "settings": settings,
        "variables": variables,
        "users": users,
        "context": context,
    })

    if users.get_require_login():
        dlg = LoginDialog(users)
        if dlg.exec_() != dlg.Accepted:
            return None
        user = dlg.logged_user
    else:
        # 一键分权关闭：直接进入管理页面，按管理员身份
        user = {"name": "admin", "role": ROLE_ADMIN}

    win = MainWindow(context, variables, settings, users, user)
    return win


def show_login_and_run(users, settings):
    """Show login again (used after logout)."""
    app = QApplication.instance()
    dlg = LoginDialog(users)
    if dlg.exec_() != dlg.Accepted:
        if app:
            app.quit()
        return
    context = _shared["context"]
    variables = _shared["variables"]
    win = MainWindow(context, variables, settings, users, dlg.logged_user)
    win.show()
