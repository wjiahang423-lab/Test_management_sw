import os

from PyQt5 import uic
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QFileDialog, QHeaderView, QInputDialog, QMenu,
                             QMessageBox, QPushButton, QStackedWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

from app.core.paths import CASE_TYPE_NAMES, PLAN_FILE_FILTER, PLANS_DIR, UI_FILES
from app.core.plan_model import TestPlan
from app.core import syslog
from app.ui.case_form import CaseEditorDialog
from app.ui.brand import make_logo_label
from app.ui.debug_runner import DebugRunner
from app.ui.grid_delegate import GridLineDelegate
from app.ui.help_dialog import HelpDialog
from app.ui.plan_settings import PlanSettingsDialog
from app.ui.settings_page import SettingsPage
from app.ui.users_page import UsersPage
from app.ui.variables_page import VariablesPage

NAV_PLAN = 0
NAV_VARIABLES = 1
NAV_SETTINGS = 2
NAV_USERS = 3


class ManagePage:
    def __init__(self, context, variables, settings, user_manager, settings_apply_cb=None, window=None):
        self.ctx = context
        self.variables = variables
        self.settings = settings
        self.user_manager = user_manager
        self.settings_apply_cb = settings_apply_cb
        self._window = window
        self.ui = None
        self.plan = None
        self.stack = None
        self.on_switch_execute = None
        self._clipboard_cases = []

    # ---------------- lifecycle ----------------
    def setup(self, host):
        uic.loadUi(UI_FILES["manage"], host)
        self.ui = host
        
        self._plan_delegate = GridLineDelegate("#b3d4fc", self.ui.treeWidget_plan)
        self.ui.treeWidget_plan.setItemDelegate(self._plan_delegate)
        self.ui.treeWidget_plan.setContextMenuPolicy(Qt.CustomContextMenu)
        # 列宽（.ui 中的 width 不生效，必须用代码设置）：0名称拉伸，其余固定
        tree = self.ui.treeWidget_plan
        header = tree.header()
        header.setStretchLastSection(False)
        for col, width in ((1, 380), (2, 320), (3, 120)):
            tree.setColumnWidth(col, width)
            header.setSectionResizeMode(col, QHeaderView.Fixed)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        self._add_sidebar_logo()
        self._restructure()
        self._add_toolbar_buttons()
        self._wire()
        self.ui.navList.currentRowChanged.connect(self._guarded(self._on_nav))
        self.ui.treeWidget_plan.customContextMenuRequested.connect(
            self._guarded(self._on_tree_context_menu))
        self.ui.navList.setCurrentRow(0)
        self.set_current_user("管理员", "管理员")
        self._on_nav(0)
        return host

    def _add_sidebar_logo(self):
        self.ui.logoFrame.setMinimumHeight(92)
        self.ui.logoFrame.setStyleSheet("background-color: #1a252f;")
        logo = make_logo_label(48, self.ui.logoFrame)
        self.ui.logoLayout.insertWidget(0, logo)

    def _restructure(self):
        """Move the plan-management content into a QStackedWidget and add sub-pages."""
        content_layout = self.ui.contentLayout
        content_layout.takeAt(0)  # topBar
        content_layout.takeAt(0)  # bodyLayout

        self.stack = QStackedWidget(self.ui)
        content_layout.addWidget(self.stack)

        # Page 0: plan management (topBar + tree)
        plan_page = QWidget()
        pv = QVBoxLayout(plan_page)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.addWidget(self.ui.topBar)
        plan_body = QWidget()
        pb = QVBoxLayout(plan_body)
        pb.setContentsMargins(0, 0, 0, 0)
        pb.addWidget(self.ui.groupBox_tree, 1)
        pv.addWidget(plan_body, 1)
        self.stack.addWidget(plan_page)

        self.ui.groupBox_editor.hide()

        # Page 1: variables
        self.variables_page = VariablesPage(self.variables)
        self.stack.addWidget(self.variables_page)

        # Page 2: settings
        self.settings_page = SettingsPage(self.settings, self.settings_apply_cb)
        self.stack.addWidget(self.settings_page)

        # Page 3: users
        self.users_page = UsersPage(self.user_manager)
        self.stack.addWidget(self.users_page)

    def _add_toolbar_buttons(self):
        layout = self.ui.topBarLayout
        spacer = layout.itemAt(9)
        self.btn_debug = _make_btn("▶ 执行", "#8e44ad")
        self.btn_switch_execute = _make_btn("切换执行页面", "#2c5aa0")
        self.btn_help = _make_btn("帮助", "#16a085")
        layout.insertWidget(9, self.btn_debug)
        layout.insertWidget(10, self.btn_switch_execute)
        layout.insertWidget(11, self.btn_help)

        # 窗口控制按钮：最小化 / 最大化还原 / 退出（无边框窗口自绘）
        layout.addStretch(1)
        for text, tip, slot in (("－", "最小化", self._minimize),
                                ("□", "最大化/还原", self._toggle_maximize),
                                ("✕", "退出", self._close_window)):
            b = QPushButton(text)
            b.setFixedSize(32, 32)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton { background: rgba(255,255,255,220); color: #333;"
                " border: none; border-radius: 6px; font-size: 16px;"
                " font-weight: bold; }"
                "QPushButton:hover { background: rgba(44,90,160,220); color: white; }")
            b.clicked.connect(slot)
            layout.addWidget(b)
            if text == "✕":
                b.setStyleSheet(
                    "QPushButton { background: rgba(231,76,60,230); color: white;"
                    " border: none; border-radius: 6px; font-size: 16px;"
                    " font-weight: bold; }"
                    "QPushButton:hover { background: rgba(200,40,30,255); color: white; }")
        self._win = self._window or self.ui.window()
        self._win_max_btn = layout.itemAt(layout.count() - 2).widget()
        self._sync_max_icon()

    # ---------------- window controls ----------------
    def _minimize(self):
        self._win.showMinimized()

    def _toggle_maximize(self):
        if self._win.isMaximized():
            self._win.showNormal()
        else:
            self._win.showMaximized()
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(120, self._sync_max_icon)

    def _close_window(self):
        from PyQt5.QtWidgets import QApplication
        self._win.close()
        QApplication.instance().quit()

    def _sync_max_icon(self):
        if hasattr(self, "_win_max_btn"):
            self._win_max_btn.setText("❐" if self._win.isMaximized() else "□")

    def _wire(self):
        self.ui.btn_newPlan.clicked.connect(self._guarded(self.new_plan))
        self.ui.btn_openPlan.clicked.connect(self._guarded(self.open_plan))
        self.ui.btn_savePlan.clicked.connect(self._guarded(self.save_plan))
        self.ui.btn_saveAs.clicked.connect(self._guarded(self.save_plan_as))
        self.ui.btn_deletePlan.clicked.connect(self._guarded(self.delete_plan))
        self.ui.btn_planSettings.clicked.connect(self._guarded(self.edit_plan_settings))
        self.ui.btn_addSeq.clicked.connect(self._guarded(self.add_sequence))
        self.ui.btn_addCase.clicked.connect(self._guarded(self.add_case))
        self.ui.btn_editItem.clicked.connect(self._guarded(self.edit_item))
        self.ui.btn_deleteItem.clicked.connect(self._guarded(self.delete_item))
        self.btn_debug.clicked.connect(self._guarded(self.debug_run))
        self.btn_switch_execute.clicked.connect(self._guarded(self._switch_execute))
        self.btn_help.clicked.connect(lambda: self._guarded(lambda: HelpDialog(self.ui).exec_())())

    def _guarded(self, fn):
        """Wrap a UI callback so an unexpected exception never crashes the app:
        the error is written to the system log and shown to the user.

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
                syslog.exception("管理页面操作异常：{}".format(getattr(fn, "__name__", str(fn))))
                QMessageBox.critical(self.ui, "错误", "操作执行失败，详情已写入系统日志（data/logs/）")
                return None
        return wrapper

    def _switch_execute(self):
        if self.on_switch_execute:
            self.on_switch_execute()

    def _on_nav(self, index):
        if self.stack is None:
            return
        self.stack.setCurrentIndex(index)
        if index == NAV_VARIABLES:
            self.variables_page.reload()
        elif index == NAV_USERS:
            self.users_page.reload()

    def set_current_user(self, name, role):
        # 当前用户统一显示在菜单栏右侧（main_window），此处保留接口不显示
        pass

    # ---------------- plan CRUD ----------------
    def new_plan(self):
        name, ok = QInputDialog.getText(self.ui, "新建计划", "计划名称：")
        if not ok or not name.strip():
            return
        self.plan = TestPlan()
        self.plan.name = name.strip()
        self.plan.file_path = None
        self.variables.load_dict({})
        self.variables_page.reload()
        self._refresh_tree()
        self._update_plan_label()
        self.ui.lineEdit_currentPlan.setPlaceholderText(name.strip())

    def open_plan(self, path=None):
        try:
            if not path:
                path, _ = QFileDialog.getOpenFileName(self.ui, "打开测试计划", PLANS_DIR, PLAN_FILE_FILTER)
                if not path:
                    return
            self.plan = TestPlan.load(path)
            # 全局变量随 PLAN 加载
            self.variables.load_dict(self.plan.variables)
            self.variables_page.reload()
            self._refresh_tree()
            self._update_plan_label()
            QMessageBox.information(self.ui, "提示", "已打开计划：{}".format(self.plan.name))
        except Exception as e:
            syslog.exception("打开测试计划失败：{}".format(path))
            QMessageBox.critical(self.ui, "错误", "打开计划失败：{}".format(e))
            self.plan = None
            self._refresh_tree()
            self._update_plan_label()

    def save_plan(self):
        try:
            if not self.plan:
                QMessageBox.information(self.ui, "提示", "当前没有测试计划")
                return
            self._capture_variables()
            if self.plan.file_path:
                self.plan.save(self.plan.file_path)
                QMessageBox.information(self.ui, "提示", "已保存：{}".format(self.plan.file_path))
            else:
                self.save_plan_as()
        except Exception as e:
            syslog.exception("保存测试计划失败")
            QMessageBox.critical(self.ui, "错误", "保存失败：{}".format(e))

    def _capture_variables(self):
        if self.plan is not None:
            self.plan.variables = self.variables.to_dict()

    def save_plan_as(self):
        try:
            if not self.plan:
                QMessageBox.information(self.ui, "提示", "当前没有测试计划")
                return
            self._capture_variables()
            default = self.plan.file_path or os.path.join(PLANS_DIR, (self.plan.name or "未命名") + ".plan")
            path, _ = QFileDialog.getSaveFileName(self.ui, "另存为", default, PLAN_FILE_FILTER)
            if not path:
                return
            if not path.endswith(".plan"):
                path += ".plan"
            self.plan.save(path)
            self._update_plan_label()
            QMessageBox.information(self.ui, "提示", "已另存为：{}".format(path))
        except Exception as e:
            syslog.exception("另存测试计划失败")
            QMessageBox.critical(self.ui, "错误", "另存失败：{}".format(e))

    def delete_plan(self):
        if not self.plan or not self.plan.file_path:
            QMessageBox.information(self.ui, "提示", "当前计划未关联文件")
            return
        if QMessageBox.question(self.ui, "确认", "删除计划文件：{}？".format(self.plan.file_path)) == QMessageBox.Yes:
            try:
                os.remove(self.plan.file_path)
            except Exception as e:
                syslog.exception("删除计划文件失败：{}".format(self.plan.file_path))
                QMessageBox.warning(self.ui, "提示", "删除文件失败：{}".format(e))
            self.plan = None
            self._refresh_tree()
            self._update_plan_label()

    def edit_plan_settings(self):
        if not self.plan:
            QMessageBox.information(self.ui, "提示", "请先新建或打开测试计划")
            return
        try:
            dlg = PlanSettingsDialog(self.plan, self.ui)
            if dlg.exec_() == dlg.Accepted:
                self.plan.settings = dlg.get_settings()
        except Exception:
            syslog.exception("编辑计划设置失败")
            QMessageBox.critical(self.ui, "错误", "编辑计划设置失败，详情见系统日志")

    # ---------------- sequence / case editing ----------------
    def _selected_item(self):
        items = self.ui.treeWidget_plan.selectedItems()
        return items[0] if items else None

    def add_sequence(self):
        if not self.plan:
            QMessageBox.information(self.ui, "提示", "请先新建或打开测试计划")
            return
        name, ok = QInputDialog.getText(self.ui, "新增序列", "序列名称：")
        if not ok or not name.strip():
            return
        from app.core.plan_model import TestSequence
        seq = TestSequence()
        seq.name = name.strip()
        self.plan.sequences.append(seq)
        self._refresh_tree()

    def add_case(self):
        if not self.plan:
            QMessageBox.information(self.ui, "提示", "请先新建或打开测试计划")
            return
        seq_index = self._selected_seq_index()
        if seq_index is None:
            QMessageBox.information(self.ui, "提示", "请先在左侧选择要添加用例的序列")
            return
        dlg = CaseEditorDialog(self.variables, parent=self.ui)
        if dlg.exec_() == dlg.Accepted:
            data = dlg.get_result()
            from app.core.plan_model import TestCase
            case = TestCase()
            case.name = data["name"]
            case.type = data["type"]
            case.timeout_ms = data["timeout_ms"]
            case.retry = data["retry"]
            case.fail_policy = data["fail_policy"]
            case.skip = data.get("skip", False)
            case.description = data["description"]
            case.config = data["config"]
            self.plan.sequences[seq_index].cases.append(case)
            self._refresh_tree()

    def _selected_seq_index(self):
        item = self._selected_item()
        if not item:
            return None
        kind = item.data(0, Qt.UserRole)
        if not kind:
            return None
        return kind.get("seq")

    def edit_item(self):
        item = self._selected_item()
        if not item:
            QMessageBox.information(self.ui, "提示", "请选择要编辑的序列或用例")
            return
        kind = item.data(0, Qt.UserRole)
        if not kind:
            return
        if kind["kind"] == "sequence":
            name, ok = QInputDialog.getText(self.ui, "修改序列名称", "序列名称：", text=item.text(0))
            if ok and name.strip():
                seq = self.plan.sequences[kind["seq"]]
                seq.name = name.strip()
                self._refresh_tree()
        else:
            seq = self.plan.sequences[kind["seq"]]
            case = seq.cases[kind["case"]]
            dlg = CaseEditorDialog(self.variables, case=case, parent=self.ui)
            if dlg.exec_() == dlg.Accepted:
                data = dlg.get_result()
                case.name = data["name"]
                case.type = data["type"]
                case.timeout_ms = data["timeout_ms"]
                case.retry = data["retry"]
                case.fail_policy = data["fail_policy"]
                case.skip = data.get("skip", False)
                case.description = data["description"]
                case.config = data["config"]
                self._refresh_tree()

    def delete_item(self):
        item = self._selected_item()
        if not item:
            QMessageBox.information(self.ui, "提示", "请选择要删除的序列或用例")
            return
        kind = item.data(0, Qt.UserRole)
        if not kind:
            return
        if kind["kind"] == "sequence":
            if QMessageBox.question(self.ui, "确认", "删除该序列及其所有用例？") == QMessageBox.Yes:
                del self.plan.sequences[kind["seq"]]
        else:
            seq = self.plan.sequences[kind["seq"]]
            if QMessageBox.question(self.ui, "确认", "删除该用例？") == QMessageBox.Yes:
                del seq.cases[kind["case"]]
        self._refresh_tree()

    # ---------------- copy / paste cases ----------------
    def _on_tree_context_menu(self, pos):
        tree = self.ui.treeWidget_plan
        item = tree.itemAt(pos)
        kind = item.data(0, Qt.UserRole) if item else None

        menu = QMenu(self.ui)
        act_copy = menu.addAction("复制用例")
        act_paste = menu.addAction("粘贴用例")

        has_plan = self.plan is not None
        can_copy = bool(has_plan and kind and kind["kind"] == "case")
        can_paste = bool(has_plan and self._clipboard_cases
                         and self._resolve_paste_seq(item) is not None)
        act_copy.setEnabled(can_copy)
        act_paste.setEnabled(can_paste)

        if item is not None:
            tree.setCurrentItem(item)

        chosen = menu.exec_(tree.viewport().mapToGlobal(pos))
        if chosen == act_copy:
            self._copy_cases()
        elif chosen == act_paste:
            self._paste_cases(item)

    def _resolve_paste_seq(self, item):
        """Determine the target sequence index for pasting."""
        if item is not None:
            kind = item.data(0, Qt.UserRole)
            if kind and kind["kind"] == "case":
                return kind["seq"]
            if kind and kind["kind"] == "sequence":
                return kind["seq"]
        return self._selected_seq_index()

    def _copy_cases(self):
        tree = self.ui.treeWidget_plan
        cases = []
        for item in tree.selectedItems():
            kind = item.data(0, Qt.UserRole)
            if kind and kind["kind"] == "case":
                seq = self.plan.sequences[kind["seq"]]
                cases.append(seq.cases[kind["case"]].to_dict())
        if not cases:
            QMessageBox.information(self.ui, "提示", "请先选择要复制的用例")
            return
        self._clipboard_cases = cases
        QMessageBox.information(self.ui, "提示", "已复制 {} 个用例".format(len(cases)))

    def _paste_cases(self, item):
        seq_index = self._resolve_paste_seq(item)
        if seq_index is None:
            QMessageBox.information(self.ui, "提示", "请先选择要粘贴的目标序列")
            return
        from app.core.plan_model import TestCase, new_id
        seq = self.plan.sequences[seq_index]
        for d in self._clipboard_cases:
            case = TestCase.from_dict(d)
            case.id = new_id()
            seq.cases.append(case)
        self._refresh_tree()

    # ---------------- tree / debug ----------------
    def _refresh_tree(self):
        tree = self.ui.treeWidget_plan
        tree.clear()
        if not self.plan:
            return
        for si, seq in enumerate(self.plan.sequences):
            seq_item = QTreeWidgetItem([seq.name, "序列", "", ""])
            seq_item.setData(0, Qt.UserRole, {"kind": "sequence", "seq": si})
            for ci, case in enumerate(seq.cases):
                name_text = case.name + ("  [跳过]" if getattr(case, "skip", False) else "")
                case_item = QTreeWidgetItem([
                    name_text,
                    CASE_TYPE_NAMES.get(case.type, case.type),
                    str(case.timeout_ms),
                    str(case.retry),
                ])
                if getattr(case, "skip", False):
                    case_item.setForeground(0, Qt.gray)
                case_item.setData(0, Qt.UserRole, {"kind": "case", "seq": si, "case": ci})
                seq_item.addChild(case_item)
            tree.addTopLevelItem(seq_item)
        tree.expandAll()

    def _update_plan_label(self):
        if not self.plan:
            self.ui.lineEdit_currentPlan.setText("")
            self.ui.lineEdit_currentPlan.setPlaceholderText("未加载测试计划")
        else:
            path = self.plan.file_path or "（未保存）"
            self.ui.lineEdit_currentPlan.setText("{} - {}".format(self.plan.name, path))

    def debug_run(self):
        if not self.plan:
            QMessageBox.information(self.ui, "提示", "请先新建或打开测试计划")
            return
        if not self.plan.sequences:
            QMessageBox.information(self.ui, "提示", "当前计划没有序列")
            return
        dlg = DebugRunner(self.plan, self.ctx, self.variables, self.settings, self.ui)
        dlg.exec_()


def _make_btn(text, color):
    from PyQt5.QtWidgets import QPushButton
    b = QPushButton(text)
    b.setStyleSheet(
        "QPushButton {{ background-color: {0}; color: white; }}"
        "QPushButton:hover {{ background-color: {1}; }}".format(color, color + "cc"))
    return b
