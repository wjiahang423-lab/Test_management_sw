from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                             QLabel, QLineEdit, QMessageBox, QPushButton,
                             QScrollArea, QTextEdit, QVBoxLayout, QWidget)

from app.core import syslog


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

        grp_storage = QGroupBox("日志与报告存储方式")
        f1 = QFormLayout(grp_storage)
        self.combo_mode = QComboBox()
        self.combo_mode.addItem("本地存储", "local")
        self.combo_mode.addItem("远程存储", "remote")
        self.combo_mode.currentIndexChanged.connect(self._toggle)
        f1.addRow("存储模式：", self.combo_mode)

        self.edit_batch = QLineEdit()
        self.edit_batch.setPlaceholderText("当前生产批次，如 A-20260814")
        f1.addRow("当前批次：", self.edit_batch)

        row1 = QHBoxLayout()
        self.edit_log_dir = QLineEdit()
        row1.addWidget(self.edit_log_dir)
        btn1 = QPushButton("浏览")
        btn1.clicked.connect(lambda: self._browse(self.edit_log_dir))
        row1.addWidget(btn1)
        f1.addRow("本地日志目录：", row1)

        row2 = QHBoxLayout()
        self.edit_report_dir = QLineEdit()
        row2.addWidget(self.edit_report_dir)
        btn2 = QPushButton("浏览")
        btn2.clicked.connect(lambda: self._browse(self.edit_report_dir))
        row2.addWidget(btn2)
        f1.addRow("HTML报告目录：", row2)
        add(grp_storage)

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

        grp_file = QGroupBox("远程文件存储（报告/日志上传）")
        f3 = QFormLayout(grp_file)
        self.chk_remote_file = QCheckBox("启用远程存储报告和日志")
        f3.addRow("功能开关：", self.chk_remote_file)
        self.edit_remote_storage_url = QLineEdit()
        self.edit_remote_storage_url.setPlaceholderText("如 http://192.168.1.100:8000/upload")
        f3.addRow("上传服务器地址：", self.edit_remote_storage_url)
        self.combo_upload_content = QComboBox()
        self.combo_upload_content.addItem("报告+日志都上传", "both")
        self.combo_upload_content.addItem("仅上传报告", "report_only")
        self.combo_upload_content.addItem("仅上传日志", "log_only")
        f3.addRow("上传内容：", self.combo_upload_content)
        self.combo_upload_strategy = QComboBox()
        self.combo_upload_strategy.addItem("每次测试完成都上传", "always")
        self.combo_upload_strategy.addItem("仅失败时上传", "on_failure")
        f3.addRow("上传策略：", self.combo_upload_strategy)
        f3.addRow("", QLabel("开启后按策略将 HTML 报告与/或运行日志上传到该接口\n"
                             "（multipart/form-data：report、log 字段，另附 plan、sn）。\n"
                             "本地仍会保留报告与日志，过期文件自动清理。"))
        add(grp_file)

        grp_json = QGroupBox("测试结果上报（JSON）")
        f4 = QFormLayout(grp_json)
        self.chk_json = QCheckBox("启用")
        self.chk_json.setToolTip("勾选后每轮测试结束，将所有测试用例的结果打包以 JSON 上报到服务器")
        f4.addRow("功能开关：", self.chk_json)
        self.edit_json_url = QLineEdit()
        self.edit_json_url.setPlaceholderText("如 http://192.168.1.100:8000/api/test_data")
        f4.addRow("上报接口地址：", self.edit_json_url)
        f4.addRow("", QLabel("开启后每轮测试结束，将本轮所有用例结果打包 POST 一次到该接口。\n"
                             "报文结构：{key, sn, batch, test_time, records[]}，\n"
                             "records 包含所有用例的结果（每个用例一条记录）。\n"
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
        f5.addRow("", QLabel("用于逐用例 JSON 上报、远程文件上传等 HTTP 请求的基本认证\n"
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
        f6.addRow("", QLabel("启用后，测试完成后先请求登录接口获取Token，\n"
                             "再带着Token发送数据到后端服务器。\n"
                             "如果后端有问题，将暂存数据，最多重试3次。"))
        add(grp_token)
        self._content_layout.addStretch(1)

        self.combo_mode.setCurrentIndex(1 if self.settings.get("storage_mode") == "remote" else 0)
        self.edit_batch.setText(self.settings.get("batch", ""))
        self.edit_log_dir.setText(self.settings.get("log_dir", ""))
        self.edit_report_dir.setText(self.settings.get("report_dir", ""))
        self.edit_mes_server.setText(self.settings.get("mes_server", ""))
        self.edit_mes_interface.setText(self.settings.get("mes_interface", ""))
        self.edit_remote_file_server.setText(self.settings.get("remote_file_server", ""))
        self.edit_remote_db_ip.setText(self.settings.get("remote_db_ip", ""))
        self.edit_remote_db_table.setText(self.settings.get("remote_db_table", ""))
        self.edit_template.setPlainText(self.settings.get("mes_template", ""))
        self.chk_remote_file.setChecked(bool(self.settings.get("remote_storage_enabled", False)))
        self.edit_remote_storage_url.setText(self.settings.get("remote_storage_url", ""))
        # 上传内容：both/report_only/log_only
        upload_content = self.settings.get("remote_storage_content", "both")
        idx = self.combo_upload_content.findData(upload_content)
        if idx >= 0:
            self.combo_upload_content.setCurrentIndex(idx)
        # 上传策略：always/on_failure
        upload_strategy = self.settings.get("remote_storage_strategy", "always")
        idx = self.combo_upload_strategy.findData(upload_strategy)
        if idx >= 0:
            self.combo_upload_strategy.setCurrentIndex(idx)
        self.chk_json.setChecked(bool(self.settings.get("json_upload_enabled", False)))
        self.edit_json_url.setText(self.settings.get("json_upload_url", ""))
        self.edit_server_user.setText(self.settings.get("server_username", "root"))
        self.edit_server_pwd.setText(self.settings.get("server_password", "root"))
        self.chk_use_token.setChecked(bool(self.settings.get("use_token_auth", False)))
        self.edit_login_url.setText(self.settings.get("login_url", ""))
        self.edit_login_user.setText(self.settings.get("login_username", ""))
        self.edit_login_pwd.setText(self.settings.get("login_password", ""))
        self.edit_token_header.setText(self.settings.get("token_header", "Authorization"))
        self.edit_token_prefix.setText(self.settings.get("token_prefix", "Bearer "))

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.chk_remote_file.toggled.connect(self._toggle_file)
        self.chk_json.toggled.connect(self._toggle_json)
        self.chk_use_token.toggled.connect(self._toggle_json)
        self._toggle()
        self._toggle_file()
        self._toggle_json()

    def _toggle(self):
        remote = self.combo_mode.currentData() == "remote"
        for w in (self.edit_mes_server, self.edit_mes_interface, self.edit_remote_file_server,
                  self.edit_remote_db_ip, self.edit_remote_db_table, self.edit_template):
            w.setEnabled(remote)

    def _toggle_file(self):
        enabled = self.chk_remote_file.isChecked()
        self.edit_remote_storage_url.setEnabled(enabled)
        self.combo_upload_content.setEnabled(enabled)
        self.combo_upload_strategy.setEnabled(enabled)

    def _toggle_json(self):
        enabled = self.chk_json.isChecked()
        self.edit_json_url.setEnabled(enabled)
        # Token认证相关控件
        token_enabled = self.chk_use_token.isChecked()
        self.edit_login_url.setEnabled(token_enabled)
        self.edit_login_user.setEnabled(token_enabled)
        self.edit_login_pwd.setEnabled(token_enabled)
        self.edit_token_header.setEnabled(token_enabled)
        self.edit_token_prefix.setEnabled(token_enabled)

    def _browse(self, edit):
        path = QFileDialog.getExistingDirectory(self, "选择目录")
        if path:
            edit.setText(path)

    def _on_save(self):
        try:
            self.settings["storage_mode"] = self.combo_mode.currentData()
            self.settings["batch"] = self.edit_batch.text().strip()
            self.settings["log_dir"] = self.edit_log_dir.text().strip()
            self.settings["report_dir"] = self.edit_report_dir.text().strip()
            self.settings["remote_storage_enabled"] = self.chk_remote_file.isChecked()
            self.settings["remote_storage_url"] = self.edit_remote_storage_url.text().strip()
            self.settings["remote_storage_content"] = self.combo_upload_content.currentData()
            self.settings["remote_storage_strategy"] = self.combo_upload_strategy.currentData()
            self.settings["json_upload_enabled"] = self.chk_json.isChecked()
            self.settings["json_upload_url"] = self.edit_json_url.text().strip()
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
