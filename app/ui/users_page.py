from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QFormLayout, QHBoxLayout, QHeaderView, QInputDialog,
                             QLabel, QLineEdit, QMessageBox, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from app.core.user_manager import ROLE_ADMIN, ROLE_OPERATOR
from app.core import syslog


def _guarded(fn):
    """Wrap a UI callback so an unexpected exception is logged, not crash.

    Qt signals may pass extra arguments (e.g. clicked(bool)); we drop any
    positional args the target function doesn't declare."""
    from functools import wraps
    import inspect

    try:
        sig = inspect.signature(fn)
        max_pos = sum(1 for p in sig.parameters.values()
                      if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD))
        has_var = any(p.kind == p.VAR_POSITIONAL for p in sig.parameters.values())
    except (TypeError, ValueError):
        sig = None
        max_pos = 0
        has_var = True

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            if sig is not None and not has_var and len(args) > max_pos:
                args = args[:max_pos]
            return fn(*args, **kwargs)
        except Exception:
            syslog.exception("用户管理操作异常：{}".format(getattr(fn, "__name__", str(fn))))
            QMessageBox.critical(args[0] if args else None, "错误", "操作执行失败，详情已写入系统日志（data/logs/）")
            return None
    return wrapper


class UsersPage(QWidget):
    def __init__(self, user_manager, parent=None):
        super().__init__(parent)
        self.users = user_manager
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.chk_login = QCheckBox("开启一键分权（启用后下次启动软件显示登录页面；不开启直接进入管理页面）")
        self.chk_login.setChecked(self.users.get_require_login())
        self.chk_login.toggled.connect(lambda v: self._guarded(self._toggle_login)(v))
        layout.addWidget(self.chk_login)

        btn_layout = QHBoxLayout()
        btn_add = QPushButton("+ 新增账号")
        btn_edit = QPushButton("修改账号")
        btn_pwd = QPushButton("修改密码")
        btn_del = QPushButton("删除账号")
        for b in (btn_add, btn_edit, btn_pwd, btn_del):
            btn_layout.addWidget(b)
        btn_layout.addStretch(1)
        layout.addLayout(btn_layout)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["用户名", "角色", "描述"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table, 1)

        btn_add.clicked.connect(self._guarded(self.add_user))
        btn_edit.clicked.connect(self._guarded(self.edit_user))
        btn_pwd.clicked.connect(self._guarded(self.change_password))
        btn_del.clicked.connect(self._guarded(self.delete_user))
        self.reload()

    def _guarded(self, fn):
        return _guarded(fn)

    def _toggle_login(self, v):
        self.users.set_require_login(v)

    def reload(self):
        users = self.users.list_users()
        self.table.setRowCount(len(users))
        for row, u in enumerate(users):
            self.table.setItem(row, 0, QTableWidgetItem(u["name"]))
            self.table.setItem(row, 1, QTableWidgetItem(u["role"]))
            self.table.setItem(row, 2, QTableWidgetItem(u.get("description", "")))

    def _selected(self):
        rows = set(i.row() for i in self.table.selectedIndexes())
        if not rows:
            return None
        row = min(rows)
        return self.table.item(row, 0).text()

    def add_user(self):
        dlg = _UserEditDialog(self.users, None, self)
        if dlg.exec_() == QDialog.Accepted:
            name, password, role, desc = dlg.result()
            ok, msg = self.users.add(name, password, role, desc)
            if not ok:
                QMessageBox.warning(self, "提示", msg)
            self.reload()

    def edit_user(self):
        name = self._selected()
        if not name:
            QMessageBox.information(self, "提示", "请先选择账号")
            return
        item = self.users.get(name)
        dlg = _UserEditDialog(self.users, item, self)
        if dlg.exec_() == QDialog.Accepted:
            new_name, password, role, desc = dlg.result()
            if new_name != name and not self.users.get(new_name):
                self.users.add(new_name, password, role, desc)
                self.users.remove(name)
            else:
                self.users.update(name, password, role, desc)
            self.reload()

    def change_password(self):
        name = self._selected()
        if not name:
            QMessageBox.information(self, "提示", "请先选择账号")
            return
        text, ok = QInputDialog.getText(self, "修改密码", "请输入新密码（管理员直接修改）：", echo=QLineEdit.Password)
        if not ok:
            return
        if not text:
            QMessageBox.warning(self, "提示", "密码不能为空")
            return
        self.users.update(name, password=text)
        QMessageBox.information(self, "提示", "密码已修改")

    def delete_user(self):
        name = self._selected()
        if not name:
            QMessageBox.information(self, "提示", "请先选择账号")
            return
        if QMessageBox.question(self, "确认", "删除账号 {}？".format(name)) == QMessageBox.Yes:
            ok, msg = self.users.remove(name)
            if not ok:
                QMessageBox.warning(self, "提示", msg)
            self.reload()


class _UserEditDialog(QDialog):
    def __init__(self, users, item, parent=None):
        super().__init__(parent)
        self.setWindowTitle("账号编辑")
        form = QFormLayout(self)
        self.edit_name = QLineEdit()
        form.addRow("用户名：", self.edit_name)
        self.edit_pwd = QLineEdit()
        self.edit_pwd.setEchoMode(QLineEdit.Password)
        form.addRow("密码：", self.edit_pwd)
        self.combo_role = QComboBox()
        self.combo_role.addItems([ROLE_ADMIN, ROLE_OPERATOR])
        form.addRow("角色：", self.combo_role)
        self.edit_desc = QLineEdit()
        form.addRow("描述：", self.edit_desc)
        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Save).setText("保存")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self._ok)
        btns.rejected.connect(self.reject)
        form.addRow(btns)
        if item:
            self.edit_name.setText(item["name"])
            self.edit_pwd.setText(item["password"])
            self.combo_role.setCurrentText(item["role"])
            self.edit_desc.setText(item.get("description", ""))

    def _ok(self):
        if not self.edit_name.text().strip() or not self.edit_pwd.text():
            QMessageBox.warning(self, "提示", "用户名和密码不能为空")
            return
        self.accept()

    def result(self):
        return (self.edit_name.text().strip(), self.edit_pwd.text(),
                self.combo_role.currentText(), self.edit_desc.text().strip())
