import time

from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (QAction, QApplication,
                             QLabel, QMainWindow, QMessageBox,
                             QStackedWidget, QWidget)
from app.core import report as report_mod
from app.core import syslog
from app.core import system_status
from app.core.api import register_api
from app.core.context import RuntimeContext
from app.ui.brand import load_icon
from app.ui.execute_page import ExecutePage
from app.ui.manage_page import ManagePage

ROLE_ADMIN = "管理员"
ROLE_OPERATOR = "操作员"

# 品牌菜单名：如需修改，改这里即可
BRAND_MENU_NAME = ".."
# 品牌菜单名：如需修改，改这里即可
BRAND_MENU_NAME02 = "_"

class LogBridge(QObject):
    """Thread-safe bridge routing worker-thread log output to the GUI thread."""
    sig_log = pyqtSignal(str)


class MainWindow(QMainWindow):
    def __init__(self, context, variables, settings, user_manager, user):
        super().__init__()
        self.ctx = context
        self.variables = variables
        self.settings = settings
        self.users = user_manager
        self.user = user
        self.role = user.get("role", ROLE_ADMIN)

        report_mod.ensure_dirs()
        self.setWindowTitle("测试用例管理与执行系统 - {}".format(self.user.get("name", "")))
        # 窗口全屏（产线触屏 Kiosk 模式：系统顶栏与左侧坞自动隐藏）
        # 始终以当前屏幕全分辨率全屏显示，不再使用可配置窗口尺寸
        self.setWindowState(Qt.WindowFullScreen)
        # 全屏守护：防止触摸屏从顶部下滑/拖拽把全屏窗口还原或最小化
        self._windowed_until = 0.0
        self._kiosk_timer = QTimer(self)
        self._kiosk_timer.timeout.connect(self._kiosk_guard)
        self._kiosk_timer.start(800)
        icon = load_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)
        # 隐藏系统标题栏（"测试用例管理与执行系统 - 执行页面"），产线触屏无边框全屏
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)

        self.stack = QStackedWidget(self)
        self.setCentralWidget(self.stack)

        # Execute page host
        self.execute_host = QWidget()
        self.execute_page = ExecutePage(context, variables, settings)
        try:
            self.execute_page.setup(self.execute_host)
        except Exception as e:
            syslog.exception("执行页面初始化失败")
            QMessageBox.critical(self, "错误", "执行页面初始化失败：{}\n详情见系统日志".format(e))
        self.execute_page.set_current_user(user.get("name", ""), self.role)
        self.execute_page.on_switch_manage = self.switch_to_manage
        self.execute_page.set_manage_button_visible(self.role == ROLE_ADMIN)
        self.stack.addWidget(self.execute_host)

        # Manage page host (admin only)
        self.manage_host = QWidget()
        self.manage_page = ManagePage(context, variables, settings, user_manager,
                                      settings_apply_cb=self.apply_settings, window=self)
        try:
            self.manage_page.setup(self.manage_host)
        except Exception as e:
            syslog.exception("管理页面初始化失败")
            QMessageBox.critical(self, "错误", "管理页面初始化失败：{}\n详情见系统日志".format(e))
        self.manage_page.set_current_user(user.get("name", ""), self.role)
        self.manage_page.on_switch_execute = self.switch_to_execute
        self.stack.addWidget(self.manage_host)

        self._build_menu()
        self._setup_status_bar()
        self.apply_settings(init=True)

        if self.role == ROLE_ADMIN:
            self.switch_to_manage()
        else:
            self.switch_to_execute()

        # route script API log output to execute page log (thread-safe)
        self.log_bridge = LogBridge(self)
        self.log_bridge.sig_log.connect(self.execute_page.append_log)
        self.ctx.log_callback = self.log_bridge.sig_log.emit
        register_api(self.ctx.build_api())

    def _setup_status_bar(self):
        """底部状态栏：显示设备电量与 WiFi 信号强度（Kiosk 模式替代系统顶栏/坞）。"""
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        sb.setStyleSheet(
            "QStatusBar { background-color: #1F5AA8; color: white; font-size: 13px; }"
            "QStatusBar QLabel { color: white; padding: 0 14px; }")
        self.status_wifi = QLabel("WiFi: --")
        self.status_battery = QLabel("电池: --")
        sb.addPermanentWidget(self.status_wifi)
        sb.addPermanentWidget(self.status_battery)
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_system_status)
        self._status_timer.start(5000)
        self._refresh_system_status()

    def _refresh_system_status(self):
        try:
            wifi = system_status.wifi_status()
            if wifi:
                pct = min(100, int(wifi["quality"] / 70.0 * 100)) if wifi.get("quality") else 0
                self.status_wifi.setText("WiFi: {}%（{} dBm）".format(pct, int(round(wifi["level"]))))
            elif system_status.wired_connected():
                self.status_wifi.setText("网络: 有线")
            else:
                self.status_wifi.setText("WiFi: 无网络")
        except Exception:
            self.status_wifi.setText("WiFi: --")
        try:
            bat = system_status.battery_status()
            if bat is None:
                self.status_battery.setText("电源: 无电池")
            else:
                label = {"Charging": "充电中", "Discharging": "放电中",
                         "Full": "已充满", "Not charging": "未充电"}.get(bat["status"], bat["status"] or "在线")
                self.status_battery.setText("电池: {}% {}".format(bat["percent"], label))
        except Exception:
            self.status_battery.setText("电池: --")

    def _build_menu(self):
        """产线触屏模式：顶部菜单栏隐藏（去除退出/登录等按钮），改用快捷键。"""
        mbar = self.menuBar()
        mbar.setVisible(False)

        # 隐藏快捷键：管理员 Ctrl+M 管理页；Ctrl+Shift+L 重新登录；Ctrl+Q 退出
        if self.role == ROLE_ADMIN:
            act_manage = QAction("管理页面", self)
            act_manage.setShortcut("Ctrl+M")
            act_manage.triggered.connect(self.switch_to_manage)
            self.addAction(act_manage)
        act_logout = QAction("重新登录", self)
        act_logout.setShortcut("Ctrl+Shift+L")
        act_logout.triggered.connect(self._logout)
        self.addAction(act_logout)
        act_quit = QAction("退出", self)
        act_quit.setShortcut("Ctrl+Q")
        act_quit.triggered.connect(self.close)
        self.addAction(act_quit)
        act_fs = QAction("切换全屏", self)
        act_fs.setShortcut("F11")
        act_fs.triggered.connect(self._toggle_fullscreen)
        self.addAction(act_fs)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self._windowed_until = time.time() + 60
            self.showMaximized()
        else:
            self._windowed_until = 0
            self.showFullScreen()
            self._force_full_geometry()

    def _force_full_geometry(self):
        """Frameless 窗口在部分 WM 下 showFullScreen 不会自动铺满，
        这里显式把窗口几何对齐当前屏幕，避免只占部分区域。"""
        try:
            screen = self.screen() or QApplication.primaryScreen()
            if screen is not None:
                geo = screen.geometry()
                self.setGeometry(geo)
        except Exception:
            syslog.exception("设置全屏几何失败")

    def _kiosk_guard(self):
        """Kiosk 全屏守护：窗口若被系统手势还原/最小化，自动恢复全屏。
        F11 手动退出后 60 秒内不自动回全屏，便于维护。"""
        try:
            if not self.isFullScreen() and time.time() > self._windowed_until:
                self.showFullScreen()
                self._force_full_geometry()
        except Exception:
            syslog.exception("全屏守护异常")

    def switch_to_execute(self):
        self.stack.setCurrentWidget(self.execute_host)
        self.setWindowTitle("测试用例管理与执行系统 - 执行页面（{}）".format(self.user.get("name", "")))

    def switch_to_manage(self):
        self.stack.setCurrentWidget(self.manage_host)
        self.setWindowTitle("测试用例管理与执行系统 - 管理页面（{}）".format(self.user.get("name", "")))

    def apply_settings(self, init=False):
        try:
            self.execute_page.apply_font_scale()
            self.execute_page.apply_small_font()
            self.execute_page.refresh_station_title()
            # 应用管理页面字体缩放
            self.manage_page.apply_font_scale()
        except Exception:
            syslog.exception("应用全局设置失败")

    def _logout(self):
        from PyQt5.QtWidgets import QMessageBox
        try:
            self.close()
            from app.main_flow import show_login_and_run
            show_login_and_run(self.users, self.settings)
        except Exception:
            syslog.exception("退出重新登录失败")
            QMessageBox.critical(self, "错误", "退出登录失败，详情见系统日志")
