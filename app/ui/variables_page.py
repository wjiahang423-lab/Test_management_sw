from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                             QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                             QLineEdit, QMessageBox, QPushButton, QTableWidget,
                             QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget)

from app.core.variable_manager import TYPE_NAMES
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
            syslog.exception("变量管理操作异常：{}".format(getattr(fn, "__name__", str(fn))))
            QMessageBox.critical(args[0] if args else None, "错误", "操作执行失败，详情已写入系统日志（data/logs/）")
            return None
    return wrapper


class VariablesPage(QWidget):
    def __init__(self, variables, parent=None):
        super().__init__(parent)
        self.variables = variables
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        btn_layout = QHBoxLayout()
        btn_add = QPushButton("+ 新增变量")
        btn_edit = QPushButton("编辑")
        btn_del = QPushButton("删除")
        btn_refresh = QPushButton("刷新")
        for b in (btn_add, btn_edit, btn_del, btn_refresh):
            btn_layout.addWidget(b)
        btn_layout.addStretch(1)
        layout.addLayout(btn_layout)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["变量名", "类型", "值", "描述"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table, 1)

        hint = QLabel("说明：全局变量随测试计划（PLAN）保存——打开计划时自动加载，保存计划时写入计划文件。"
                      "脚本中可用 get_variable(name) / set_variable(name, value) 读写。")
        hint.setStyleSheet("color: #7f8c8d; font-size: 12px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        btn_add.clicked.connect(self._guarded(self.add_variable))
        btn_edit.clicked.connect(self._guarded(self.edit_variable))
        btn_del.clicked.connect(self._guarded(self.delete_variable))
        btn_refresh.clicked.connect(self._guarded(self.reload))

    def _guarded(self, fn):
        return _guarded(fn)

    def reload(self):
        items = self.variables.list_variables()
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            self.table.setItem(row, 0, QTableWidgetItem(item["name"]))
            self.table.setItem(row, 1, QTableWidgetItem(item["type"]))
            self.table.setItem(row, 2, QTableWidgetItem(str(item["value"]) if item["value"] is not None else ""))
            self.table.setItem(row, 3, QTableWidgetItem(item.get("description", "")))

    def _selected_row(self):
        rows = set(i.row() for i in self.table.selectedIndexes())
        return min(rows) if rows else -1

    def add_variable(self):
        dlg = _VarEditDialog(None, self.variables, self)
        if dlg.exec_() == QDialog.Accepted:
            name, vtype, value, desc = dlg.result()
            ok, msg = self.variables.add(name, vtype, value, desc)
            if not ok:
                QMessageBox.warning(self, "提示", msg)
            self.reload()

    def edit_variable(self):
        row = self._selected_row()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择要编辑的变量")
            return
        name = self.table.item(row, 0).text()
        item = self.variables.get(name)
        dlg = _VarEditDialog(item, self.variables, self)
        if dlg.exec_() == QDialog.Accepted:
            new_name, vtype, value, desc = dlg.result()
            if new_name != name and not self.variables.exists(new_name):
                self.variables.add(new_name, vtype, value, desc)
                self.variables.remove(name)
            else:
                self.variables.update(name, vtype, value, desc)
            self.reload()

    def delete_variable(self):
        row = self._selected_row()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择要删除的变量")
            return
        name = self.table.item(row, 0).text()
        if QMessageBox.question(self, "确认", "删除变量 {}？".format(name)) == QMessageBox.Yes:
            self.variables.remove(name)
            self.reload()


class _VarEditDialog(QDialog):
    def __init__(self, item, variables, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑变量")
        self._item = item
        form = QFormLayout(self)
        self.edit_name = QLineEdit()
        form.addRow("变量名：", self.edit_name)
        self.combo_type = QComboBox()
        self.combo_type.addItems(TYPE_NAMES)
        form.addRow("类型：", self.combo_type)
        self.edit_value = QLineEdit()
        form.addRow("值：", self.edit_value)
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
            self.combo_type.setCurrentText(item["type"])
            value = item.get("value")
            self.edit_value.setText(str(value) if value is not None else "")
            self.edit_desc.setText(item.get("description", ""))

    def _ok(self):
        if not self.edit_name.text().strip():
            QMessageBox.warning(self, "提示", "变量名不能为空")
            return
        self.accept()

    def result(self):
        import json as _json
        vtype = self.combo_type.currentText()
        raw = self.edit_value.text()
        value = raw
        try:
            if vtype == "int":
                value = int(float(raw.strip())) if raw.strip() else 0
            elif vtype == "float":
                value = float(raw.strip()) if raw.strip() else 0.0
            elif vtype == "list":
                value = _json.loads(raw) if raw.strip() else []
            elif vtype == "dict":
                value = _json.loads(raw) if raw.strip() else {}
        except Exception:
            pass
        return self.edit_name.text().strip(), vtype, value, self.edit_desc.text().strip()
