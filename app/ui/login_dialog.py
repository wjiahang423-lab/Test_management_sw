import os

from PyQt5 import uic
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPainter, QPixmap
from PyQt5.QtWidgets import QDialog

from app.core import syslog
from app.core.paths import LOGIN_BG_FILE, UI_FILES
from app.core.user_manager import UserManager
from app.ui.brand import make_logo_label


class LoginDialog(QDialog):
    """登录对话框。

    背景与 eol_tester_gui-main -V1.3 的登录页一致：使用机器人(zioneer)图案，
    图片铺满并叠加半透明遮罩以增强可读性。
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

        uic.loadUi(UI_FILES["login"], self)
        self.setWindowTitle("测试用例管理系统 - 登录")
        self._form = self.formContainer
        self.logo_label = make_logo_label(72)
        self.verticalLayout_2.insertWidget(0, self.logo_label)
        self.loginBtn.clicked.connect(self._do_login)
        self.cancelBtn.clicked.connect(self.reject)
        self.lineEdit_password.returnPressed.connect(self._do_login)
        self.lineEdit_username.setText("")
        self.lineEdit_username.setFocus()
        self.label_error.setText("")
        self._apply_theme()

    def _apply_theme(self):
        """主题化：标题栏与表单容器半透明，露出机器人背景。"""
        self.setStyleSheet("""
            QDialog { background: transparent; }
            QFrame#titleFrame {
                background-color: rgba(44, 90, 160, 200);
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
            }
            QLabel#titleLabel { color: white; font-size: 18px; font-weight: bold; }
            QLabel#subtitleLabel { color: #d0ddf0; font-size: 12px; }
            QLabel { font-size: 13px; }
            QLabel#label_error { color: #ff6b6b; font-size: 12px; }
            QLineEdit {
                padding: 8px 12px;
                border: 1px solid rgba(255,255,255,200);
                border-radius: 4px;
                background-color: rgba(255,255,255,230);
                font-size: 13px;
            }
            QLineEdit:focus { border: 1px solid #2c5aa0; }
            QPushButton#loginBtn {
                background-color: #2c5aa0; color: white; border: none;
                border-radius: 4px; padding: 10px; font-size: 14px; font-weight: bold;
            }
            QPushButton#loginBtn:hover { background-color: #3a6bb8; }
            QPushButton#cancelBtn {
                background-color: rgba(255,255,255,220); color: #333;
                border: none; border-radius: 4px; padding: 10px; font-size: 14px;
            }
            QPushButton#cancelBtn:hover { background-color: rgba(230,234,240,230); }
            QComboBox { background-color: rgba(255,255,255,230); }
        """)
        if self._form is not None:
            self._form.setStyleSheet("QWidget#formContainer { background-color: rgba(255,255,255,235); }")

    def paintEvent(self, event):
        """.ui 不会绘制窗口背景，这里用机器人背景图铺满并叠加遮罩。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self._bg_pixmap.isNull():
            scaled = self._bg_pixmap.scaled(
                self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 60))
        else:
            from PyQt5.QtGui import QLinearGradient
            gradient = QLinearGradient(0, 0, self.width(), self.height())
            gradient.setColorAt(0.0, QColor(10, 30, 80))
            gradient.setColorAt(0.5, QColor(20, 60, 140))
            gradient.setColorAt(1.0, QColor(10, 40, 100))
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
