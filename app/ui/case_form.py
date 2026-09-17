"""Test case editing dialog with 5 type-specific dynamic forms.

Each type form loads its own .ui file (case_action / case_delay / case_pop /
case_measurement / case_loop) via QtUiLoader. The dialog adds the common
configuration section (name, type, timeout, retry, fail policy).
"""
import json
import os

from PyQt5 import uic
from PyQt5.QtCore import QEvent, QObject, Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QAbstractScrollArea, QCheckBox, QComboBox, QDialog,
                             QDialogButtonBox, QFileDialog, QFormLayout,
                             QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QSizePolicy, QSpinBox,
                             QTableWidget, QTableWidgetItem, QVBoxLayout)

from app.core.paths import CASE_TYPE_NAMES, UI_FILES
from app.core import syslog
from app.core.script_loader import get_function_info, list_functions

JUDGE_OPTIONS = ["", "等于", "不等于", "大于", "小于", "范围内", "包含", "长度"]
OVERRIDE_TYPES = ["str", "int", "float", "list", "dict"]


class _WheelGuardMixin:
    """Guard against accidental value changes while scrolling the form.

    Qt changes the value of a combo/spin box under the mouse cursor on every
    wheel notch even without keyboard focus, so scrolling the editor dialog can
    silently modify function names and other dropdown/setpoint fields.  Wheel
    events on an unfocused combo/spin are forwarded to the nearest scrollable
    ancestor, which scrolls by the same amount Qt normally would.
    """

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            _forward_wheel(self, event)


class GuardedComboBox(_WheelGuardMixin, QComboBox):
    pass


class GuardedSpinBox(_WheelGuardMixin, QSpinBox):
    pass


def _forward_wheel(widget, event):
    """Scroll the nearest scrollable ancestor by the wheel delta.

    Qt scrolls a scroll area by ``delta / 120 * singleStep`` per wheel notch;
    replicate that so a wheel over an unfocused combo/spin scrolls the form
    instead of mutating the field.
    """
    event.accept()
    delta = event.angleDelta().y()
    if delta == 0:
        return
    p = widget.parentWidget()
    while p is not None:
        if isinstance(p, QAbstractScrollArea):
            bar = p.verticalScrollBar()
            if bar.maximum() > bar.minimum():
                bar.setValue(bar.value() - delta * bar.singleStep() // 120)
                return
        p = p.parentWidget()


class _WheelGuardFilter(QObject):
    """Event filter guarding combos/spins loaded from .ui files (unfocusable
    subclasses cannot be installed after the fact)."""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Wheel and not obj.hasFocus():
            _forward_wheel(obj, event)
            return True
        return super().eventFilter(obj, event)


def _guard_widgets(widget):
    for w in widget.findChildren((QComboBox, QSpinBox)):
        if isinstance(w, _WheelGuardMixin):
            continue
        w.installEventFilter(_WheelGuardFilter(w))


def _make_combo(items, editable=True, default=""):
    cb = GuardedComboBox()
    cb.setEditable(editable)
    for it in items:
        cb.addItem(it)
    if default and default not in items:
        cb.addItem(default)
    if default:
        cb.setCurrentText(str(default))
    return cb


def _make_type_combo(default="str"):
    cb = GuardedComboBox()
    cb.addItems(OVERRIDE_TYPES)
    cb.setCurrentText(default if default in OVERRIDE_TYPES else "str")
    return cb


def _cast_str(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False) if value is not None else ""


def _align_cell_widgets(table):
    """Fix Qt5 setCellWidget layout bug.

    Cell widgets added before the table is laid out can keep a stale geometry
    (e.g. the first row's QComboBox stuck at (0,0), visually covering the row
    above).  updateGeometries() recomputes every cell widget's position.
    """
    try:
        table.updateGeometries()
    except Exception:
        pass


class BaseCaseForm:
    form_file = None

    def __init__(self):
        try:
            self.widget = uic.loadUi(self.form_file)
        except Exception as e:
            syslog.exception("加载用例表单失败：{}".format(self.form_file))
            raise RuntimeError("加载用例表单失败：{}".format(e))
        _guard_widgets(self.widget)
        self._after_load()
        self.refresh([])

    def _after_load(self):
        pass

    def refresh(self, variables):
        """Called with the current global variable names to refresh combos."""
        pass

    def get_config(self):
        return {}

    def set_config(self, cfg):
        pass


class ActionForm(BaseCaseForm):
    form_file = UI_FILES["action"]

    def _after_load(self):
        self.widget.btn_browse.clicked.connect(self._browse)
        self.widget.lineEdit_script.editingFinished.connect(self._load_functions)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self.widget, "选择脚本", "", "Python 脚本 (*.py)")
        if path:
            self.widget.lineEdit_script.setText(path)
            self._load_functions()

    def _load_functions(self):
        path = self.widget.lineEdit_script.text().strip()
        self.widget.comboBox_func.clear()
        if not path or not os.path.exists(path):
            return
        for fn in list_functions(path):
            self.widget.comboBox_func.addItem(fn["name"])

    def get_config(self):
        return {
            "script": self.widget.lineEdit_script.text().strip(),
            "function": self.widget.comboBox_func.currentText().strip(),
            "description": self.widget.lineEdit_desc.text().strip(),
        }

    def set_config(self, cfg):
        self.widget.lineEdit_script.setText(cfg.get("script", ""))
        self.widget.comboBox_func.clear()
        if cfg.get("script"):
            for fn in list_functions(cfg["script"]):
                self.widget.comboBox_func.addItem(fn["name"])
        self.widget.comboBox_func.setCurrentText(cfg.get("function", ""))
        self.widget.lineEdit_desc.setText(cfg.get("description", ""))


class DelayForm(BaseCaseForm):
    form_file = UI_FILES["delay"]

    def _after_load(self):
        self.widget.spinBox_delay.setSuffix("")

    def get_config(self):
        value = self.widget.spinBox_delay.value()
        unit = self.widget.comboBox_unit.currentIndex()
        if unit == 1:
            value = value * 1000
        elif unit == 2:
            value = value * 60000
        return {
            "delay_ms": int(value),
            "description": self.widget.lineEdit_desc.text().strip(),
        }

    def set_config(self, cfg):
        ms = int(cfg.get("delay_ms", 1000))
        unit_idx = 0
        if ms % 60000 == 0:
            unit_idx = 2
            ms = ms // 60000
        elif ms % 1000 == 0:
            unit_idx = 1
            ms = ms // 1000
        self.widget.comboBox_unit.setCurrentIndex(unit_idx)
        self.widget.spinBox_delay.setValue(ms)
        self.widget.lineEdit_desc.setText(cfg.get("description", ""))


class PopForm(BaseCaseForm):
    form_file = UI_FILES["pop"]

    def refresh(self, variables):
        cb = self.widget.comboBox_resultVar
        current = cb.currentText()
        cb.clear()
        for v in variables:
            cb.addItem(v)
        if current:
            cb.setCurrentText(current)

    def get_config(self):
        return {
            "title": self.widget.lineEdit_title.text().strip() or "提示",
            "content": self.widget.textEdit_content.toPlainText(),
            "btn_true": self.widget.lineEdit_btnTrue.text().strip() or "确认",
            "btn_false": self.widget.lineEdit_btnFalse.text().strip() or "取消",
            "result_var": self.widget.comboBox_resultVar.currentText().strip(),
            "modal": self.widget.comboBox_modal.currentIndex() == 0,
        }

    def set_config(self, cfg):
        self.widget.lineEdit_title.setText(cfg.get("title", "提示"))
        self.widget.textEdit_content.setPlainText(cfg.get("content", ""))
        self.widget.lineEdit_btnTrue.setText(cfg.get("btn_true", "确认"))
        self.widget.lineEdit_btnFalse.setText(cfg.get("btn_false", "取消"))
        self.widget.comboBox_resultVar.setCurrentText(cfg.get("result_var", ""))
        self.widget.comboBox_modal.setCurrentIndex(0 if cfg.get("modal", True) else 1)


class MeasurementForm(BaseCaseForm):
    form_file = UI_FILES["measurement"]

    def _after_load(self):
        self.widget.btn_browse.clicked.connect(self._browse)
        self.widget.btn_loadFunc.clicked.connect(self._load_functions)
        self.widget.btn_addParam.clicked.connect(self._add_param)
        self.widget.btn_delParam.clicked.connect(self._del_param)
        self.widget.btn_addReturn.clicked.connect(self._add_return)
        self.widget.btn_delReturn.clicked.connect(self._del_return)
        self.widget.comboBox_func.currentTextChanged.connect(self._load_signature)
        self.widget.tableWidget_params.horizontalHeader().setStretchLastSection(True)
        self.widget.tableWidget_returns.horizontalHeader().setStretchLastSection(True)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self.widget, "选择脚本", "", "Python 脚本 (*.py)")
        if path:
            self.widget.lineEdit_script.setText(path)
            self._load_functions()

    def _load_functions(self):
        path = self.widget.lineEdit_script.text().strip()
        self.widget.comboBox_func.clear()
        if not path or not os.path.exists(path):
            return
        for fn in list_functions(path):
            self.widget.comboBox_func.addItem(fn["name"])

    def _load_signature(self, func_name):
        path = self.widget.lineEdit_script.text().strip()
        func_name = func_name.strip()
        if not path or not func_name:
            return
        functions = list_functions(path)
        info = None
        for fn in functions:
            if fn["name"] == func_name:
                info = fn.get("params") or {}
                break
        if info is None:
            return
        params = info.get("params", [])
        table = self.widget.tableWidget_params
        table.setRowCount(len(params))
        for row, p in enumerate(params):
            table.setItem(row, 0, QTableWidgetItem(p["name"]))
            table.setCellWidget(row, 1, _make_type_combo(p.get("type", "str")))
            table.setItem(row, 2, QTableWidgetItem(_cast_str(p.get("default", "")) if p.get("default") is not None else ""))
            combo = GuardedComboBox()
            combo.addItems(["值", "变量"])
            combo.setCurrentIndex(0)
            table.setCellWidget(row, 3, combo)
        returns = info.get("returns", [])
        rtable = self.widget.tableWidget_returns
        rtable.setRowCount(len(returns))
        for row, r in enumerate(returns):
            rtable.setItem(row, 0, QTableWidgetItem(r.get("item", "return")))
            rtable.setCellWidget(row, 1, _make_type_combo(r.get("type", "str")))
            rtable.setItem(row, 2, QTableWidgetItem(""))
            judge = GuardedComboBox()
            judge.addItems(JUDGE_OPTIONS)
            rtable.setCellWidget(row, 3, judge)
            rtable.setItem(row, 4, QTableWidgetItem(""))
        _align_cell_widgets(table)
        _align_cell_widgets(rtable)

    def _add_param(self):
        table = self.widget.tableWidget_params
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem(""))
        table.setCellWidget(row, 1, _make_type_combo("str"))
        table.setItem(row, 2, QTableWidgetItem(""))
        combo = GuardedComboBox()
        combo.addItems(["值", "变量"])
        table.setCellWidget(row, 3, combo)
        _align_cell_widgets(table)
        table.scrollToBottom()

    def _del_param(self):
        table = self.widget.tableWidget_params
        rows = sorted({i.row() for i in table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        for r in rows:
            table.removeRow(r)

    def _add_return(self):
        table = self.widget.tableWidget_returns
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem("return"))
        table.setCellWidget(row, 1, _make_type_combo("str"))
        bind = _make_combo([], editable=True)
        table.setCellWidget(row, 2, bind)
        judge = GuardedComboBox()
        judge.addItems(JUDGE_OPTIONS)
        table.setCellWidget(row, 3, judge)
        table.setItem(row, 4, QTableWidgetItem(""))
        _align_cell_widgets(table)
        table.scrollToBottom()

    def _del_return(self):
        table = self.widget.tableWidget_returns
        rows = sorted({i.row() for i in table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        for r in rows:
            table.removeRow(r)

    def refresh(self, variables):
        table = self.widget.tableWidget_returns
        for row in range(table.rowCount()):
            item = table.item(row, 2)
            if item is not None:
                current = item.text()
                combo = _make_combo(variables, editable=True, default=current)
                table.setCellWidget(row, 2, combo)
        _align_cell_widgets(table)

    def get_config(self):
        params = []
        table = self.widget.tableWidget_params
        for row in range(table.rowCount()):
            name_item = table.item(row, 0)
            type_w = table.cellWidget(row, 1)
            val_item = table.item(row, 2)
            source_w = table.cellWidget(row, 3)
            if name_item is None:
                continue
            params.append({
                "name": name_item.text(),
                "type": type_w.currentText() if type_w else "str",
                "value": val_item.text() if val_item else "",
                "source": "variable" if source_w and source_w.currentIndex() == 1 else "value",
            })
        returns = []
        rtable = self.widget.tableWidget_returns
        for row in range(rtable.rowCount()):
            item0 = rtable.item(row, 0)
            type_w = rtable.cellWidget(row, 1)
            bind_w = rtable.cellWidget(row, 2)
            judge_w = rtable.cellWidget(row, 3)
            thr_item = rtable.item(row, 4)
            if item0 is None:
                continue
            returns.append({
                "item": item0.text(),
                "type": type_w.currentText() if type_w else "str",
                "bind_var": bind_w.currentText() if bind_w else "",
                "judge": judge_w.currentText() if judge_w else "",
                "threshold": thr_item.text() if thr_item else "",
            })
        return {
            "display": self.widget.lineEdit_display.text().strip(),
            "script": self.widget.lineEdit_script.text().strip(),
            "function": self.widget.comboBox_func.currentText().strip(),
            "params": params,
            "returns": returns,
        }

    def set_config(self, cfg):
        self.widget.lineEdit_display.setText(cfg.get("display", ""))
        self.widget.lineEdit_script.setText(cfg.get("script", ""))
        self.widget.comboBox_func.clear()
        if cfg.get("script"):
            for fn in list_functions(cfg["script"]):
                self.widget.comboBox_func.addItem(fn["name"])
        self.widget.comboBox_func.setCurrentText(cfg.get("function", ""))
        params = cfg.get("params", [])
        table = self.widget.tableWidget_params
        table.setRowCount(len(params))
        for row, p in enumerate(params):
            table.setItem(row, 0, QTableWidgetItem(p.get("name", "")))
            table.setCellWidget(row, 1, _make_type_combo(p.get("type", "str")))
            table.setItem(row, 2, QTableWidgetItem(_cast_str(p.get("value", ""))))
            combo = GuardedComboBox()
            combo.addItems(["值", "变量"])
            combo.setCurrentIndex(1 if p.get("source") == "variable" else 0)
            table.setCellWidget(row, 3, combo)
        returns = cfg.get("returns", [])
        rtable = self.widget.tableWidget_returns
        rtable.setRowCount(len(returns))
        for row, r in enumerate(returns):
            rtable.setItem(row, 0, QTableWidgetItem(r.get("item", "return")))
            rtable.setCellWidget(row, 1, _make_type_combo(r.get("type", "str")))
            bind = _make_combo([], editable=True, default=r.get("bind_var", ""))
            rtable.setCellWidget(row, 2, bind)
            judge = GuardedComboBox()
            judge.addItems(JUDGE_OPTIONS)
            if r.get("judge"):
                judge.setCurrentText(r.get("judge", ""))
            rtable.setCellWidget(row, 3, judge)
            rtable.setItem(row, 4, QTableWidgetItem(_cast_str(r.get("threshold", ""))))
        _align_cell_widgets(table)
        _align_cell_widgets(rtable)


class LoopForm(BaseCaseForm):
    form_file = UI_FILES["loop"]

    def _after_load(self):
        self.widget.btn_browseYaml.clicked.connect(self._browse_yaml)
        self.widget.btn_browseScript.clicked.connect(self._browse_script)
        self.widget.btn_loadFunc.clicked.connect(self._load_functions)
        self.widget.btn_add.clicked.connect(self._add_override)
        self.widget.btn_del.clicked.connect(self._del_override)
        self.widget.tableWidget_override.horizontalHeader().setStretchLastSection(True)
        self._sync_parser()

    def _browse_yaml(self):
        path, _ = QFileDialog.getOpenFileName(self.widget, "选择 YAML 数据", "", "YAML 文件 (*.yaml *.yml);;所有文件 (*)")
        if path:
            self.widget.lineEdit_yaml.setText(path)

    def _browse_script(self):
        path, _ = QFileDialog.getOpenFileName(self.widget, "选择脚本", "", "Python 脚本 (*.py)")
        if path:
            self.widget.lineEdit_script.setText(path)
            self._load_functions()

    def _load_functions(self):
        path = self.widget.lineEdit_script.text().strip()
        self.widget.comboBox_func.clear()
        self.widget.comboBox_parser.clear()
        if not path or not os.path.exists(path):
            return
        for fn in list_functions(path):
            self.widget.comboBox_func.addItem(fn["name"])
            self.widget.comboBox_parser.addItem(fn["name"])

    def _sync_parser(self):
        self.widget.comboBox_parser.setCurrentText(self.widget.comboBox_func.currentText())

    def _add_override(self):
        table = self.widget.tableWidget_override
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem(""))
        combo = GuardedComboBox()
        combo.addItems(OVERRIDE_TYPES)
        table.setCellWidget(row, 1, combo)
        table.setItem(row, 2, QTableWidgetItem(""))

    def _del_override(self):
        table = self.widget.tableWidget_override
        rows = set(i.row() for i in table.selectedIndexes())
        for r in sorted(rows, reverse=True):
            table.removeRow(r)

    def get_config(self):
        overrides = []
        table = self.widget.tableWidget_override
        for row in range(table.rowCount()):
            item0 = table.item(row, 0)
            type_w = table.cellWidget(row, 1)
            item2 = table.item(row, 2)
            if item0 is None:
                continue
            overrides.append({
                "item": item0.text(),
                "type": type_w.currentText() if type_w else "str",
                "value": item2.text() if item2 else "",
            })
        return {
            "yaml_path": self.widget.lineEdit_yaml.text().strip(),
            "script": self.widget.lineEdit_script.text().strip(),
            "function": self.widget.comboBox_func.currentText().strip(),
            "parser": self.widget.comboBox_parser.currentText().strip(),
            "session_key": self.widget.lineEdit_sessionKey.text().strip() or "sessions",
            "overrides": overrides,
        }

    def set_config(self, cfg):
        self.widget.lineEdit_yaml.setText(cfg.get("yaml_path", ""))
        self.widget.lineEdit_script.setText(cfg.get("script", ""))
        self.widget.comboBox_func.clear()
        self.widget.comboBox_parser.clear()
        if cfg.get("script"):
            for fn in list_functions(cfg["script"]):
                self.widget.comboBox_func.addItem(fn["name"])
                self.widget.comboBox_parser.addItem(fn["name"])
        self.widget.comboBox_func.setCurrentText(cfg.get("function", ""))
        self.widget.comboBox_parser.setCurrentText(cfg.get("parser", ""))
        self.widget.lineEdit_sessionKey.setText(cfg.get("session_key", "sessions"))
        table = self.widget.tableWidget_override
        table.setRowCount(0)
        for ov in cfg.get("overrides", []):
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(ov.get("item", "")))
            combo = GuardedComboBox()
            combo.addItems(OVERRIDE_TYPES)
            combo.setCurrentText(ov.get("type", "str"))
            table.setCellWidget(row, 1, combo)
            table.setItem(row, 2, QTableWidgetItem(_cast_str(ov.get("value", ""))))


FORM_CLASSES = {
    "action": ActionForm,
    "delay": DelayForm,
    "pop": PopForm,
    "measurement": MeasurementForm,
    "loop": LoopForm,
}


class CaseEditorDialog(QDialog):
    def __init__(self, variables, case=None, parent=None):
        super().__init__(parent)
        self.variables = variables
        self.setWindowTitle("编辑测试用例")
        self.setMinimumSize(860, 660)
        self.resize(980, 760)
        self._form = None
        self._build()

        if case is not None:
            self._load_case(case)

    def _build(self):
        layout = QVBoxLayout(self)

        common = QGroupBox("通用配置")
        form = QFormLayout(common)
        self.lineEdit_case_no = QLineEdit()
        self.lineEdit_case_no.setPlaceholderText("可选，如 TC001")
        form.addRow("测试编号：", self.lineEdit_case_no)
        self.lineEdit_name = QLineEdit()
        form.addRow("用例名称：", self.lineEdit_name)
        self.combo_type = GuardedComboBox()
        for key, name in CASE_TYPE_NAMES.items():
            self.combo_type.addItem(name, key)
        self.combo_type.currentIndexChanged.connect(self._switch_type)
        form.addRow("执行类型：", self.combo_type)
        self.spin_timeout = GuardedSpinBox()
        self.spin_timeout.setMinimum(-1)
        self.spin_timeout.setMaximum(99999999)
        self.spin_timeout.setValue(5000)
        self.spin_timeout.setSpecialValueText("-1 (无限等待)")
        self.spin_timeout.setSuffix(" ms")
        form.addRow("超时时间：", self.spin_timeout)
        self.spin_retry = GuardedSpinBox()
        self.spin_retry.setRange(0, 10)
        form.addRow("失败重试次数：", self.spin_retry)
        self.combo_fail = GuardedComboBox()
        self.combo_fail.addItem("失败后停止执行", "pause")
        self.combo_fail.addItem("失败后继续下一条", "continue")
        form.addRow("失败策略：", self.combo_fail)
        self.chk_skip = QCheckBox("跳过此用例（执行时不执行，报告中标记为跳过）")
        form.addRow("", self.chk_skip)
        self.lineEdit_desc = QLineEdit()
        form.addRow("用例描述：", self.lineEdit_desc)
        layout.addWidget(common)

        self.stack = None
        layout.addWidget(self._build_stack(), 1)
        _guard_widgets(self)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._buttons = buttons

    def _build_stack(self):
        from PyQt5.QtWidgets import QScrollArea, QStackedWidget
        self.stack = QStackedWidget()
        self.forms = {}
        for key, cls in FORM_CLASSES.items():
            form = cls()
            self.forms[key] = form
            form.widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            self.stack.addWidget(form.widget)
        # 放在滚动区域中：内容过宽/过高时可通过滑条查看隐藏部分
        self._stack_scroll = QScrollArea()
        self._stack_scroll.setWidgetResizable(True)
        self._stack_scroll.setFrameShape(0)
        self._stack_scroll.setWidget(self.stack)
        return self._stack_scroll

    def _switch_type(self, index):
        if self.stack is None:
            return
        self.stack.setCurrentIndex(index)

    def current_case_type(self):
        return self.combo_type.currentData()

    def _load_case(self, case):
        self.lineEdit_case_no.setText(getattr(case, "case_no", ""))
        self.lineEdit_name.setText(case.name)
        self.combo_type.setCurrentIndex(list(CASE_TYPE_NAMES.keys()).index(case.type) if case.type in CASE_TYPE_NAMES else 0)
        self.spin_timeout.setValue(int(case.timeout_ms))
        self.spin_retry.setValue(int(case.retry))
        self.combo_fail.setCurrentIndex(0 if case.fail_policy == "pause" else 1)
        self.chk_skip.setChecked(bool(getattr(case, "skip", False)))
        self.lineEdit_desc.setText(case.description)
        form = self.forms.get(case.type)
        if form:
            try:
                form.set_config(case.config or {})
            except Exception:
                syslog.exception("应用用例配置失败：{} - {}".format(case.type, case.name))

    def _on_save(self):
        name = self.lineEdit_name.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请输入用例名称")
            return
        self.accept()

    def get_result(self):
        case_type = self.current_case_type()
        form = self.forms.get(case_type)
        config = form.get_config() if form else {}
        return {
            "case_no": self.lineEdit_case_no.text().strip(),
            "name": self.lineEdit_name.text().strip(),
            "type": case_type,
            "timeout_ms": int(self.spin_timeout.value()),
            "retry": int(self.spin_retry.value()),
            "fail_policy": self.combo_fail.currentData(),
            "skip": self.chk_skip.isChecked(),
            "description": self.lineEdit_desc.text().strip(),
            "config": config,
        }
