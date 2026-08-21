from PyQt5.QtWidgets import (QCheckBox, QDoubleSpinBox, QFormLayout, QGroupBox,
                             QHBoxLayout, QLineEdit, QMessageBox, QPushButton,
                             QSpinBox, QVBoxLayout, QWidget)

from app.core import syslog


class SettingsPage(QWidget):
    def __init__(self, settings, apply_callback, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.apply_callback = apply_callback

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        grp = QGroupBox("全局基础设置")
        form = QFormLayout(grp)

        self.spin_font = QSpinBox()
        self.spin_font.setRange(9, 32)
        form.addRow("UI字体大小：", self.spin_font)

        row_size = QHBoxLayout()
        self.spin_width = QSpinBox()
        self.spin_width.setRange(800, 4096)
        self.spin_height = QSpinBox()
        self.spin_height.setRange(600, 4096)
        row_size.addWidget(self.spin_width)
        row_size.addWidget(QPushButton("x"))
        row_size.addWidget(self.spin_height)
        form.addRow("主窗口尺寸(宽x高)：", row_size)

        self.spin_speed = QDoubleSpinBox()
        self.spin_speed.setRange(0.01, 10.0)
        self.spin_speed.setSingleStep(0.1)
        self.spin_speed.setDecimals(2)
        form.addRow("执行速度系数(延时缩放)：", self.spin_speed)

        self.edit_password = QLineEdit()
        self.edit_password.setEchoMode(QLineEdit.Password)
        form.addRow("清零统计密码：", self.edit_password)

        self.edit_station_id = QLineEdit()
        form.addRow("工位ID：", self.edit_station_id)

        self.edit_station_name = QLineEdit()
        form.addRow("工位名称：", self.edit_station_name)

        layout.addWidget(grp)

        btn_row = QHBoxLayout()
        self.btn_save = QPushButton("保存设置")
        self.btn_apply = QPushButton("应用")
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_apply)
        btn_row.addWidget(self.btn_save)
        layout.addLayout(btn_row)
        layout.addStretch(1)

        self.spin_font.setValue(self.settings.font_size)
        self.spin_width.setValue(self.settings.window_width)
        self.spin_height.setValue(self.settings.window_height)
        self.spin_speed.setValue(self.settings.speed_factor)
        self.edit_password.setText(self.settings.clear_password)
        self.edit_station_id.setText(self.settings.station_id)
        self.edit_station_name.setText(self.settings.station_name)

        self.btn_apply.clicked.connect(self.apply_now)
        self.btn_save.clicked.connect(self.save_now)

    def apply_now(self):
        try:
            self.settings.update(
                font_size=self.spin_font.value(),
                window_width=self.spin_width.value(),
                window_height=self.spin_height.value(),
                speed_factor=self.spin_speed.value(),
                clear_password=self.edit_password.text() or "0000",
                station_id=self.edit_station_id.text().strip() or "01",
                station_name=self.edit_station_name.text().strip() or "机器人测试01工位",
            )
            if self.apply_callback:
                self.apply_callback()
            QMessageBox.information(self, "提示", "设置已保存并应用")
        except Exception as e:
            syslog.exception("保存全局设置失败")
            QMessageBox.critical(self, "错误", "保存设置失败：{}".format(e))

    def save_now(self):
        self.apply_now()
