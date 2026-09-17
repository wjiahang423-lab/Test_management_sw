from PyQt5.QtWidgets import (QDoubleSpinBox, QFormLayout, QGroupBox,
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

        self.spin_manage_font = QSpinBox()
        self.spin_manage_font.setRange(9, 32)
        form.addRow("管理页面字体大小：", self.spin_manage_font)

        self.spin_exec_font = QSpinBox()
        self.spin_exec_font.setRange(9, 32)
        form.addRow("执行页面字体大小：", self.spin_exec_font)

        self.spin_small_font = QSpinBox()
        self.spin_small_font.setRange(8, 40)
        form.addRow("执行页小字体号(px)：", self.spin_small_font)

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

        self.edit_line_id = QLineEdit()
        self.edit_line_id.setPlaceholderText("产线ID，如 101")
        form.addRow("产线ID（lineId）：", self.edit_line_id)

        self.spin_retention_days = QSpinBox()
        self.spin_retention_days.setRange(1, 365)
        self.spin_retention_days.setSuffix(" 天")
        form.addRow("报告保留天数：", self.spin_retention_days)

        self.spin_pending_days = QSpinBox()
        self.spin_pending_days.setRange(1, 365)
        self.spin_pending_days.setSuffix(" 天")
        self.spin_pending_days.setToolTip("待上报数据保留天数，超过后自动删除")
        form.addRow("待上报数据保留天数：", self.spin_pending_days)

        layout.addWidget(grp)

        btn_row = QHBoxLayout()
        self.btn_save = QPushButton("保存设置")
        self.btn_apply = QPushButton("应用")
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_apply)
        btn_row.addWidget(self.btn_save)
        layout.addLayout(btn_row)
        layout.addStretch(1)

        self.spin_manage_font.setValue(self.settings.manage_font_size)
        self.spin_exec_font.setValue(self.settings.font_size)
        self.spin_small_font.setValue(self.settings.exec_small_font)
        self.spin_speed.setValue(self.settings.speed_factor)
        self.edit_password.setText(self.settings.clear_password)
        self.edit_station_id.setText(self.settings.station_id)
        self.edit_station_name.setText(self.settings.station_name)
        self.edit_line_id.setText(self.settings.line_id)
        self.spin_retention_days.setValue(self.settings.report_retention_days)
        self.spin_pending_days.setValue(self.settings.pending_retention_days)

        self.btn_apply.clicked.connect(self.apply_now)
        self.btn_save.clicked.connect(self.save_now)

    def apply_now(self):
        try:
            self.settings.update(
                font_size=self.spin_exec_font.value(),
                manage_font_size=self.spin_manage_font.value(),
                exec_small_font=self.spin_small_font.value(),
                speed_factor=self.spin_speed.value(),
                clear_password=self.edit_password.text() or "0000",
                station_id=self.edit_station_id.text().strip() or "01",
                station_name=self.edit_station_name.text().strip() or "机器人测试01工位",
                line_id=self.edit_line_id.text().strip(),
                report_retention_days=self.spin_retention_days.value(),
                pending_retention_days=self.spin_pending_days.value(),
            )
            if self.apply_callback:
                self.apply_callback()
            QMessageBox.information(self, "提示", "设置已保存并应用")
        except Exception as e:
            syslog.exception("保存全局设置失败")
            QMessageBox.critical(self, "错误", "保存设置失败：{}".format(e))

    def save_now(self):
        self.apply_now()
