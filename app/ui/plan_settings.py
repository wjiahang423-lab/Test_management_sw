from PyQt5.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox,
                             QFormLayout, QGroupBox,
                             QInputDialog, QLabel, QLineEdit, QMessageBox,
                             QScrollArea, QTextEdit, QVBoxLayout, QWidget)

from app.core import syslog

# 循环测试模式启用密码：不对外公开，仅内部/授权维护人员使用
CONTINUOUS_ENABLE_PASSWORD = "0000"


class PlanSettingsDialog(QDialog):
    """Per-PLAN private configuration: storage mode, MES, remote DB, etc."""

    def __init__(self, plan, parent=None):
        super().__init__(parent)
        self.setWindowTitle("计划设置 - {}".format(plan.name or "未命名"))
        self.setMinimumWidth(560)
        self.settings = dict(plan.settings)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)

        # 内容放入滚动区域，窗口过小时可下拉查看隐藏部分
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(0)
        container = QWidget()
        self._content_layout = QVBoxLayout(container)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll.setWidget(container)
        layout.addWidget(self.scroll, 1)

        def add(w):
            self._content_layout.addWidget(w)

        grp_continuous = QGroupBox("循环测试模式（耐久测试）")
        f_cont = QFormLayout(grp_continuous)
        self.chk_continuous = QCheckBox("启动循环测试模式")
        self.chk_continuous.setToolTip("勾选后，一轮测试结束自动从头重新执行，直到手动停止或退出。\n"
                                       "仅限特殊测试需求（如耐久测试）使用，请谨慎启用！\n"
                                       "启用需输入授权密码。")
        f_cont.addRow("功能开关：", self.chk_continuous)
        f_cont.addRow("", QLabel("开启后循环执行整个测试计划，直到手动点击【停止】或退出程序。\n"
                                 "该功能不能随意启用，仅在特殊测试需求（如耐久测试）下使用。"))
        add(grp_continuous)

        grp_remote = QGroupBox("远程接口配置")
        f2 = QFormLayout(grp_remote)
        self.edit_mes_server = QLineEdit()
        f2.addRow("MES服务器地址：", self.edit_mes_server)
        self.edit_mes_interface = QLineEdit()
        f2.addRow("上报交互接口：", self.edit_mes_interface)
        self.edit_remote_file_server = QLineEdit()
        f2.addRow("远程文件服务器地址：", self.edit_remote_file_server)
        self.edit_remote_db_ip = QLineEdit()
        f2.addRow("远程数据库IP：", self.edit_remote_db_ip)
        self.edit_remote_db_table = QLineEdit()
        f2.addRow("目标数据表/表单名称：", self.edit_remote_db_table)
        self.edit_template = QTextEdit()
        self.edit_template.setMaximumHeight(120)
        self.edit_template.setPlaceholderText("上报报文内容模板（支持变量占位，如 $sn$ $result$）")
        f2.addRow("上报报文模板：", self.edit_template)
        add(grp_remote)

        grp_json = QGroupBox("测试结果上报（JSON）")
        f4 = QFormLayout(grp_json)
        self.chk_json = QCheckBox("启用")
        self.chk_json.setToolTip("勾选后每轮测试结束，将所有测试用例的结果打包以 JSON 上报到服务器")
        f4.addRow("功能开关：", self.chk_json)
        self.edit_json_url = QLineEdit()
        self.edit_json_url.setPlaceholderText("如 http://192.168.1.100:8000/eol_api/det/upload")
        f4.addRow("上报接口地址：", self.edit_json_url)
        self.edit_batch = QLineEdit()
        self.edit_batch.setPlaceholderText("当前生产批次，如 A-20260814")
        f4.addRow("当前批次：", self.edit_batch)
        self.edit_series_id = QLineEdit()
        self.edit_series_id.setPlaceholderText("产品系列ID，如 1001")
        f4.addRow("产品系列ID（seriesId）：", self.edit_series_id)
        self.chk_is_final = QCheckBox("是否总装线（isFinal）")
        self.chk_is_final.setToolTip("勾选表示当前为总装线，未勾选为分装线。"
                                     "总装线需上传 finalBom，分装线可不传。")
        f4.addRow("总装线标识（isFinal）：", self.chk_is_final)
        f4.addRow("", QLabel("开启后每轮测试结束，将本轮所有用例结果打包 POST 一次到该接口。\n"
                             "格式：multipart/form-data，det_data 为检测数据 JSON 对象（真实JSON），\n"
                             "log_file 为日志文件（仅检测失败时上传）。\n"
                             "det_data 内含 records[]（4.2 结构）与 lineId/stationId/productSn 等字段。\n"
                             "如需认证请在下方配置 Token认证 或 服务器用户名/密码。"))
        add(grp_json)

        grp_auth = QGroupBox("服务器连接认证")
        f5 = QFormLayout(grp_auth)
        self.edit_server_user = QLineEdit()
        self.edit_server_user.setPlaceholderText("如 root")
        f5.addRow("服务器用户名：", self.edit_server_user)
        self.edit_server_pwd = QLineEdit()
        self.edit_server_pwd.setPlaceholderText("如 root")
        self.edit_server_pwd.setEchoMode(QLineEdit.Password)
        f5.addRow("服务器密码：", self.edit_server_pwd)
        f5.addRow("", QLabel("用于逐用例 JSON 上报 HTTP 请求的基本认证\n"
                             "（requests 的 auth 参数）。留空则不发送认证头。"))
        add(grp_auth)

        grp_token = QGroupBox("Token认证配置（可选）")
        f6 = QFormLayout(grp_token)
        self.chk_use_token = QCheckBox("启用Token认证（优先于基本认证）")
        self.chk_use_token.setToolTip("勾选后先登录获取Token，再使用Token进行数据上报")
        f6.addRow("功能开关：", self.chk_use_token)
        self.edit_login_url = QLineEdit()
        self.edit_login_url.setPlaceholderText("如 http://192.168.1.100:8000/api/login")
        f6.addRow("登录接口地址：", self.edit_login_url)
        self.edit_login_user = QLineEdit()
        self.edit_login_user.setPlaceholderText("登录用户名")
        f6.addRow("登录用户名：", self.edit_login_user)
        self.edit_login_pwd = QLineEdit()
        self.edit_login_pwd.setPlaceholderText("登录密码")
        self.edit_login_pwd.setEchoMode(QLineEdit.Password)
        f6.addRow("登录密码：", self.edit_login_pwd)
        self.edit_token_header = QLineEdit("Authorization")
        f6.addRow("Token字段名：", self.edit_token_header)
        self.edit_token_prefix = QLineEdit("Bearer ")
        f6.addRow("Token前缀：", self.edit_token_prefix)
        f6.addRow("", QLabel("启用后，点\"启动\"时先请求登录接口获取Token，\n"
                             "再带着Token发送数据到后端服务器。\n"
                             "如果后端有问题，将暂存数据，最多重试3次。"))
        add(grp_token)
        self._content_layout.addStretch(1)

        self.edit_batch.setText(self.settings.get("batch", ""))
        self.edit_mes_server.setText(self.settings.get("mes_server", ""))
        self.edit_mes_interface.setText(self.settings.get("mes_interface", ""))
        self.edit_remote_file_server.setText(self.settings.get("remote_file_server", ""))
        self.edit_remote_db_ip.setText(self.settings.get("remote_db_ip", ""))
        self.edit_remote_db_table.setText(self.settings.get("remote_db_table", ""))
        self.edit_template.setPlainText(self.settings.get("mes_template", ""))
        self.chk_json.setChecked(bool(self.settings.get("json_upload_enabled", False)))
        self.edit_json_url.setText(self.settings.get("json_upload_url", ""))
        self.edit_series_id.setText(self.settings.get("series_id", ""))
        self.chk_is_final.setChecked(bool(self.settings.get("is_final", False)))
        self.edit_server_user.setText(self.settings.get("server_username", "root"))
        self.edit_server_pwd.setText(self.settings.get("server_password", "root"))
        self.chk_use_token.setChecked(bool(self.settings.get("use_token_auth", False)))
        self.edit_login_url.setText(self.settings.get("login_url", ""))
        self.edit_login_user.setText(self.settings.get("login_username", ""))
        self.edit_login_pwd.setText(self.settings.get("login_password", ""))
        self.edit_token_header.setText(self.settings.get("token_header", "Authorization"))
        self.edit_token_prefix.setText(self.settings.get("token_prefix", "Bearer "))
        self.chk_continuous.setChecked(bool(self.settings.get("continuous", False)))

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.chk_json.toggled.connect(self._toggle_json)
        self.chk_use_token.toggled.connect(self._toggle_json)
        self.chk_continuous.toggled.connect(self._on_continuous_toggled)
        self._toggle_json()

    def _on_continuous_toggled(self, checked):
        """启用循环测试模式需输入授权密码（密码不对外公开）。"""
        if not checked:
            return
        pwd, ok = QInputDialog.getText(self, "启用循环测试模式",
                                       "请输入授权密码：", echo=QLineEdit.Password)
        if not ok:
            pwd = ""
        if pwd != CONTINUOUS_ENABLE_PASSWORD:
            QMessageBox.warning(self, "验证失败",
                                "授权密码错误，无法启用循环测试模式。\n"
                                "该功能仅限特殊测试需求（如耐久测试）使用，请勿随意启用。")
            self.chk_continuous.blockSignals(True)
            self.chk_continuous.setChecked(False)
            self.chk_continuous.blockSignals(False)

    def _toggle_json(self):
        enabled = self.chk_json.isChecked()
        self.edit_batch.setEnabled(enabled)
        self.edit_json_url.setEnabled(enabled)
        self.edit_series_id.setEnabled(enabled)
        self.chk_is_final.setEnabled(enabled)
        # Token认证相关控件
        token_enabled = self.chk_use_token.isChecked()
        self.edit_login_url.setEnabled(token_enabled)
        self.edit_login_user.setEnabled(token_enabled)
        self.edit_login_pwd.setEnabled(token_enabled)
        self.edit_token_header.setEnabled(token_enabled)
        self.edit_token_prefix.setEnabled(token_enabled)

    def _on_save(self):
        try:
            self.settings["batch"] = self.edit_batch.text().strip()
            self.settings["continuous"] = self.chk_continuous.isChecked()
            self.settings["json_upload_enabled"] = self.chk_json.isChecked()
            self.settings["json_upload_url"] = self.edit_json_url.text().strip()
            self.settings["series_id"] = self.edit_series_id.text().strip()
            self.settings["is_final"] = self.chk_is_final.isChecked()
            self.settings["server_username"] = self.edit_server_user.text().strip()
            self.settings["server_password"] = self.edit_server_pwd.text()
            self.settings["use_token_auth"] = self.chk_use_token.isChecked()
            self.settings["login_url"] = self.edit_login_url.text().strip()
            self.settings["login_username"] = self.edit_login_user.text().strip()
            self.settings["login_password"] = self.edit_login_pwd.text()
            self.settings["token_header"] = self.edit_token_header.text().strip() or "Authorization"
            self.settings["token_prefix"] = self.edit_token_prefix.text()
            self.settings["mes_server"] = self.edit_mes_server.text().strip()
            self.settings["mes_interface"] = self.edit_mes_interface.text().strip()
            self.settings["remote_file_server"] = self.edit_remote_file_server.text().strip()
            self.settings["remote_db_ip"] = self.edit_remote_db_ip.text().strip()
            self.settings["remote_db_table"] = self.edit_remote_db_table.text().strip()
            self.settings["mes_template"] = self.edit_template.toPlainText()
            self.accept()
        except Exception:
            syslog.exception("保存计划设置失败")
            QMessageBox.critical(self, "错误", "保存计划设置失败，详情见系统日志（data/logs/）")

    def get_settings(self):
        return self.settings
