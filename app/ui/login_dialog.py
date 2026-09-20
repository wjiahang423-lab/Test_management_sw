import os

from PyQt5 import uic
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QLinearGradient, QPainter, QPixmap
from PyQt5.QtWidgets import QApplication, QDialog

from app.core import syslog
from app.core.paths import LOGIN_BG_FILE, UI_FILES
from app.core.user_manager import UserManager
from app.ui.brand import make_logo_label

# 登录卡片固定尺寸（与原登录对话框内容尺寸一致，输入框保持原大小，不随屏幕放大）
LOGIN_CARD_SIZE = (480, 482)


class LoginDialog(QDialog):
    """登录对话框。

    与执行页面/管理页面一致：show 时始终全屏显示背景；
    中间登录卡片固定尺寸并居中，用户名/密码等输入框保持原来的大小。
    """

    def __init__(self, user_manager, parent=None):
        super().__init__(parent)
        self.users = user_manager
        self.logged_user = None
        self._bg_pixmap = QPixmap()
        if os.path.exists(LOGIN_BG_FILE):
            pm = QPixmap(LOGIN_BG_FILE)
            if not pm.isNull():
                self._bg_pixmap = pm

        try:
            uic.loadUi(UI_FILES["login"], self)
        except Exception as e:
            syslog.exception("加载登录界面失败：{}".format(UI_FILES["login"]))
            raise RuntimeError("加载登录界面失败（UI/login.ui 缺失或损坏）：{}".format(e))
        self.setWindowTitle("测试用例管理系统 - 登录")
        self._form = self.formContainer
        # self.logo_label = make_logo_label(72)
        # self.verticalLayout_2.insertWidget(0, self.logo_label)
        self.loginBtn.clicked.connect(self._do_login)
        self.cancelBtn.clicked.connect(self.reject)
        self.lineEdit_password.returnPressed.connect(self._do_login)
        self.lineEdit_username.setText("")
        self.lineEdit_username.setFocus()
        self.label_error.setText("")
        self._apply_theme()
        self._fix_card_size()

    def _fix_card_size(self):
        """固定登录卡片尺寸，输入框等控件保持原来的大小，不随屏幕放大。"""
        w, h = LOGIN_CARD_SIZE
        self.loginCard.setMinimumSize(w, h)
        self.loginCard.setMaximumSize(w, h)

    def _apply_theme(self):
        """主题化：整窗铺背景，登录卡片白底圆角，内容区半透明展示背景。"""
        self.setStyleSheet("""
            QDialog { background: transparent; }
            QFrame#loginCard {
                background-color: rgba(255,255,255,242);
                border: 1px solid rgba(255,255,255,150);
                border-radius: 8px;
            }
            QFrame#titleFrame {
                background-color: rgba(44, 90, 160, 255);
                border-top-left-radius: 7px;
                border-top-right-radius: 7px;
            }
            QLabel#titleLabel { color: white; font-size: 18px; font-weight: bold; }
            QLabel#subtitleLabel { color: #d0ddf0; font-size: 12px; }
            QLabel { font-size: 13px; }
            QLabel#label_error { color: #ff6b6b; font-size: 12px; }
            QLineEdit {
                padding: 8px 12px;
                border: 1px solid rgba(255,255,255,200);
                border-radius: 4px;
                background-color: rgba(255,255,255,255);
                font-size: 13px;
            }
            QLineEdit:focus { border: 1px solid #2c5aa0; }
            QPushButton#loginBtn {
                background-color: #2c5aa0; color: white; border: none;
                border-radius: 4px; padding: 10px; font-size: 14px; font-weight: bold;
            }
            QPushButton#loginBtn:hover { background-color: #3a6bb8; }
            QPushButton#cancelBtn {
                background-color: rgba(255,255,255,255); color: #333;
                border: 1px solid rgba(209,215,220,255); border-radius: 4px;
                padding: 10px; font-size: 14px;
            }
            QPushButton#cancelBtn:hover { background-color: rgba(230,234,240,255); }
            QComboBox { background-color: rgba(255,255,255,255); }
        """)
        if self._form is not None:
            self._form.setStyleSheet("QWidget#formContainer { background-color: transparent; }")

    def showEvent(self, event):
        super().showEvent(event)
        self.showFullScreen()
        self._force_full_geometry()

    def _force_full_geometry(self):
        """部分 WM 下 showFullScreen 不会自动铺满，显式对齐当前屏幕（同主窗口）。"""
        try:
            screen = self.screen() or QApplication.primaryScreen()
            if screen is not None:
                self.setGeometry(screen.geometry())
        except Exception:
            syslog.exception("设置登录页全屏几何失败")

    def paintEvent(self, event):
        """.ui 不会绘制窗口背景，这里铺满整窗背景并叠加轻量遮罩。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self._bg_pixmap.isNull():
            scaled = self._bg_pixmap.scaled(
                self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 40))
        else:
            # 门户风格的深蓝渐变，与网页登录页观感一致
            gradient = QLinearGradient(0, 0, self.width(), self.height())
            gradient.setColorAt(0.0, QColor(12, 40, 104))
            gradient.setColorAt(0.5, QColor(30, 72, 168))
            gradient.setColorAt(1.0, QColor(12, 46, 120))
            painter.fillRect(self.rect(), gradient)
        painter.end()

    def _do_login(self):
        try:
            name = self.lineEdit_username.text().strip()
            password = self.lineEdit_password.text()
            role = self.comboBox_role.currentText()
            ok, role_actual = self.users.verify(name, password, role)
            if not ok:
                self.label_error.setText(role_actual)
                return
            self.logged_user = {"name": name, "role": role_actual}
            self.accept()
        except Exception:
            syslog.exception("登录校验异常")
            self.label_error.setText("登录校验出错，详情见系统日志")
