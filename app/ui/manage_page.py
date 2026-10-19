import os

from PyQt5 import uic
from PyQt5.QtCore import QEvent, QObject, Qt
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (QAbstractItemView, QFileDialog, QHeaderView,
                             QInputDialog, QMenu, QMessageBox, QPushButton,
                             QStackedWidget, QTreeWidgetItem, QVBoxLayout,
                             QWidget)

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

# 用例类型列的文字颜色（作为第二视觉维度，同类型成组）
TYPE_COLORS = {
    "action": "#2c6fb8",
    "measurement": "#1e8449",
    "loop": "#b9770e",
    "pop": "#7d3c98",
    "delay": "#707070",
}

FAIL_POLICY_NAMES = {
    "continue": "继续",
    "pause": "暂停",
}

# ==========================================================================
# 测试计划树 显示配置（自行调整这里即可，无需改其它代码）
# ==========================================================================
# 列顺序
PLAN_TREE_COLUMNS = ["测试编号", "用例名称", "描述", "类型", "失败策略", "用例跳过", "重试", "超时(ms)"]
# 各列宽度（px）——「描述」列宽度在这里调
PLAN_TREE_WIDTHS = {
    "测试编号": 115,
    "用例名称": 460,
    "描述": 440,
    "类型": 250,
    "失败策略": 100,
    "用例跳过": 100,
    "重试": 100,
    "超时(ms)": 100,
}
# 字体（px）
PLAN_TREE_BASE_FONT_PX = 13        # 树基准字号（未被 QSS 指定像素时兜底）
PLAN_TREE_CONTENT_FONT_PX = 12      # 内容列（描述/类型/失败策略/用例跳过）字号
PLAN_TREE_DELAY_FONT_PX = 11       # 延时用例行字号（整行）
PLAN_TREE_INDENT_PX = 16          # 子级缩进（px），太小会遮住折叠箭头
# 背景色
PLAN_TREE_PLAN_BG = "#1f4e79"      # PLAN 节点背景色（深海军蓝）
PLAN_TREE_SEQ_BG = "#2c5aa0"       # 序列节点背景色
PLAN_TREE_DELAY_BG = "#e0e0e0"     # 延时用例行背景色（Windows 灰色框）
# ==========================================================================

NAV_PLAN = 0
NAV_VARIABLES = 1
NAV_SETTINGS = 2
NAV_USERS = 3

class _PlanTreeDropFilter(QObject):
    """拦截计划树上的放置事件，把用例行拖到其它序列（模型层面移动）。"""

    def __init__(self, handler, tree):
        super().__init__(tree)
        self._handler = handler

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Drop:
            return self._handler(obj, event)
        return super().eventFilter(obj, event)


# 管理页面字体基准值（与 .ui 文件中的原始值一致）
FONT_BASE = 14  # 基准 font_size


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
        # 列宽（.ui 中的 width 不生效，必须用代码设置）：0编号，1名称拉伸，其余固定
        tree = self.ui.treeWidget_plan
        tree.setColumnCount(len(PLAN_TREE_COLUMNS))
        tree.setHeaderLabels(list(PLAN_TREE_COLUMNS))
        header = tree.header()
        header.setStretchLastSection(False)
        # 宽度由顶部 PLAN_TREE_WIDTHS 配置统一定义，全部固定
        for col, name in enumerate(PLAN_TREE_COLUMNS):
            tree.setColumnWidth(col, PLAN_TREE_WIDTHS.get(name, 100))
            header.setSectionResizeMode(col, QHeaderView.Fixed)
        tree.setIndentation(PLAN_TREE_INDENT_PX)  # 保留缩进让折叠箭头可见，编号列加宽以防截断
        self._enable_case_drag_drop(tree)
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

    def apply_font_scale(self):
        """按 manage_font_size 缩放管理页面的字体和控件大小。"""
        if not self.settings or not self.ui:
            return

        font_size = self.settings.manage_font_size if self.settings else FONT_BASE
        scale = font_size / float(FONT_BASE)

        def px(base):
            return int(round(base * scale))

        # 导航列表字体和间距
        nav_list = self.ui.navList
        if nav_list:
            nav_list.setStyleSheet(
                "QListWidget#navList {{ background-color: #2c3e50; border: none; color: #ecf0f1; "
                "font-size: {}px; outline: none; }}"
                "QListWidget#navList::item {{ padding: {}px {}px; border-left: 3px solid transparent; }}"
                "QListWidget#navList::item:hover {{ background-color: #34495e; }}"
                "QListWidget#navList::item:selected {{ background-color: #34495e; "
                "border-left: 3px solid #3498db; color: white; }}".format(
                    px(14), px(12), px(16)))

        # 工具栏按钮字体和大小
        btn_names = [
            "btn_newPlan", "btn_openPlan", "btn_savePlan", "btn_saveAs",
            "btn_deletePlan", "btn_planSettings", "btn_addSeq", "btn_addCase",
            "btn_editItem", "btn_deleteItem"
        ]
        btn_colors = {
            "btn_newPlan": "#27ae60", "btn_openPlan": "#2c5aa0",
            "btn_savePlan": "#f39c12", "btn_saveAs": "#16a085",
            "btn_deletePlan": "#e74c3c", "btn_planSettings": "#8e44ad",
            "btn_addSeq": "#3498db", "btn_addCase": "#3498db",
            "btn_editItem": "#95a5a6", "btn_deleteItem": "#e74c3c",
        }
        for name in btn_names:
            btn = getattr(self.ui, name, None)
            if btn:
                color = btn_colors.get(name, "#3498db")
                btn.setFixedSize(px(100), px(32))
                btn.setStyleSheet(
                    "QPushButton {{ background-color: {0}; color: white; border: none; "
                    "border-radius: {1}px; padding: {2}px {3}px; font-size: {4}px; }}"
                    "QPushButton:hover {{ background-color: {0}cc; }}".format(
                        color, px(4), px(7), px(14), px(13)))

        # 窗口控制按钮字体和大小
        if hasattr(self, "_win_max_btn") and self._win_max_btn:
            layout = self.ui.topBarLayout
            btn_size = px(32)
            for i in range(layout.count()):
                item = layout.itemAt(i)
                if item and item.widget():
                    w = item.widget()
                    if isinstance(w, QPushButton) and w.text() in ("－", "□", "❐", "✕"):
                        w.setFixedSize(btn_size, btn_size)
                        text = w.text()
                        if text == "✕":
                            w.setStyleSheet(
                                "QPushButton {{ background: rgba(231,76,60,230); color: white;"
                                " border: none; border-radius: {0}px; font-size: {1}px;"
                                " font-weight: bold; }}"
                                "QPushButton:hover {{ background: rgba(200,40,30,255); color: white; }}".format(
                                    px(6), px(16)))
                        else:
                            w.setStyleSheet(
                                "QPushButton {{ background: rgba(255,255,255,220); color: #333;"
                                " border: none; border-radius: {0}px; font-size: {1}px;"
                                " font-weight: bold; }}"
                                "QPushButton:hover {{ background: rgba(44,90,160,220); color: white; }}".format(
                                    px(6), px(16)))

        # 切换/帮助按钮字体和大小
        for btn in [self.btn_debug, self.btn_switch_execute, self.btn_help]:
            if btn:
                btn.setFixedSize(px(110), px(32))
                old_style = btn.styleSheet() or ""
                import re
                if "font-size:" in old_style:
                    new_style = re.sub(r"font-size:\s*\d+px", "font-size: {}px".format(px(13)), old_style)
                else:
                    new_style = old_style + " font-size: {}px;".format(px(13))
                btn.setStyleSheet(new_style)

        # 输入框和表格字体
        line_edit = getattr(self.ui, "lineEdit_currentPlan", None)
        if line_edit:
            line_edit.setStyleSheet("font-size: {}px; padding: {}px;".format(px(13), px(6)))

        tree = self.ui.treeWidget_plan
        if tree:
            tree.setStyleSheet(
                "QTreeWidget {{ background-color: white; border: 1px solid #e1e4e8; "
                "border-radius: 14px; font-size: {}px; }}"
                "QTreeWidget::item {{ padding: {}px; }}".format(px(13), px(4)))

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
            case.case_no = data.get("case_no", "")
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
        if kind["kind"] == "plan":
            return
        if kind["kind"] == "sequence":
            name, ok = QInputDialog.getText(self.ui, "修改序列名称", "序列名称：", text=item.text(1))
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
                case.case_no = data.get("case_no", "")
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
        if kind["kind"] == "plan":
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
        act_up = menu.addAction("上移")
        act_down = menu.addAction("下移")
        menu.addSeparator()
        act_skip_seq = menu.addAction("跳过该序列")
        act_skip_seq.setCheckable(True)
        menu.addSeparator()
        act_copy = menu.addAction("复制用例")
        act_paste = menu.addAction("粘贴用例")

        has_plan = self.plan is not None
        has_skip = bool(has_plan and kind and kind["kind"] == "sequence")
        act_skip_seq.setEnabled(has_skip)
        if has_skip:
            act_skip_seq.setChecked(bool(self.plan.sequences[kind["seq"]].skip))
        can_copy = bool(has_plan and kind and kind["kind"] == "case")
        can_paste = bool(has_plan and self._clipboard_cases
                         and self._resolve_paste_seq(item) is not None)
        act_copy.setEnabled(can_copy)
        act_paste.setEnabled(can_paste)
        if has_plan and kind:
            if kind["kind"] == "case":
                cases = self.plan.sequences[kind["seq"]].cases
                act_up.setEnabled(kind["case"] > 0)
                act_down.setEnabled(kind["case"] < len(cases) - 1)
            elif kind["kind"] == "sequence":
                act_up.setEnabled(kind["seq"] > 0)
                act_down.setEnabled(kind["seq"] < len(self.plan.sequences) - 1)
            else:
                act_up.setEnabled(False)
                act_down.setEnabled(False)
        else:
            act_up.setEnabled(False)
            act_down.setEnabled(False)

        if item is not None:
            tree.setCurrentItem(item)

        chosen = menu.exec_(tree.viewport().mapToGlobal(pos))
        if chosen == act_up:
            self._move_item(item, -1)
        elif chosen == act_down:
            self._move_item(item, 1)
        elif chosen == act_skip_seq:
            if has_skip:
                seq = self.plan.sequences[kind["seq"]]
                seq.skip = not seq.skip
                self._refresh_tree()
        elif chosen == act_copy:
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

    # ---------------- move cases / sequences ----------------
    def _enable_case_drag_drop(self, tree):
        """开启拖拽：用例可直接拖到其它序列（代替复制粘贴）。"""
        tree.setDragDropMode(QAbstractItemView.DragDrop)
        tree.setDefaultDropAction(Qt.MoveAction)
        self._drop_filter = _PlanTreeDropFilter(self._on_plan_tree_drop, tree)
        tree.installEventFilter(self._drop_filter)

    def _on_plan_tree_drop(self, tree, event):
        """放置事件回调：只处理来自本树的用例拖拽，其余交给默认逻辑。"""
        if not self.plan:
            return False
        if event.source() is not tree:
            return False
        try:
            self._apply_case_drag(tree, event.pos())
        except Exception:
            syslog.exception("拖拽用例失败")
        # 内部拖拽一律消费掉，避免 QTreeWidget 默认重排导致树与模型不一致
        return True

    def _apply_case_drag(self, tree, pos):
        """把拖拽的用例（可多个）移动到目标序列/目标用例前后。"""
        # 1) 收集被拖拽的用例 (seq, case)
        dragged, seen = [], set()
        for item in tree.selectedItems():
            kind = item.data(0, Qt.UserRole)
            if kind and kind.get("kind") == "case":
                key = (kind["seq"], kind["case"])
                if key not in seen:
                    seen.add(key)
                    dragged.append(key)
        if not dragged:
            return
        # 2) 目标位置
        target_item = tree.itemAt(pos)
        if target_item is None:
            return
        tkind = target_item.data(0, Qt.UserRole)
        if not tkind or tkind.get("kind") not in ("sequence", "case"):
            return  # 拖到 PLAN 根或空白处：忽略
        target_seq = tkind["seq"]
        below = False
        if tkind["kind"] == "case":
            if (target_seq, tkind["case"]) in seen:
                return  # 拖到自己：忽略
            below = pos.y() > tree.visualItemRect(target_item).center().y()
        # 3) 从源序列弹出（同一序列内按索引倒序弹出以保持原始顺序）
        by_seq = {}
        for si, ci in dragged:
            by_seq.setdefault(si, []).append(ci)
        popped = []
        for si in sorted(by_seq):
            seq = self.plan.sequences[si]
            for ci in sorted(by_seq[si], reverse=True):
                popped.append(seq.cases.pop(ci))
        popped.reverse()
        # 4) 计算目标插入位置并插入
        target_seq_obj = self.plan.sequences[target_seq]
        if tkind["kind"] == "case":
            shifted = sum(1 for si, cis in by_seq.items()
                          for ci in cis if si == target_seq and ci < tkind["case"])
            at = max(0, tkind["case"] - shifted) + (1 if below else 0)
        else:
            at = len(target_seq_obj.cases)  # 拖到序列行：追加到序列末尾
        for case in popped:
            target_seq_obj.cases.insert(at, case)
            at += 1
        self._refresh_tree()

    def _move_item(self, item, delta):
        """按方向移动用例（序列内）或序列（计划内），-1 上移 / +1 下移。"""
        if not self.plan or item is None:
            return
        kind = item.data(0, Qt.UserRole)
        if not kind:
            return
        if kind["kind"] == "sequence":
            seqs = self.plan.sequences
            idx = kind["seq"]
            new = idx + delta
            if 0 <= new < len(seqs):
                seqs[idx], seqs[new] = seqs[new], seqs[idx]
                self._refresh_tree()
                self._select_tree_item(new)
        elif kind["kind"] == "case":
            seq = self.plan.sequences[kind["seq"]]
            idx = kind["case"]
            new = idx + delta
            if 0 <= new < len(seq.cases):
                seq.cases[idx], seq.cases[new] = seq.cases[new], seq.cases[idx]
                self._refresh_tree()
                self._select_tree_item(kind["seq"], new)

    def _select_tree_item(self, seq_index, case_index=None):
        """刷新后重新选中并滚动到指定用例/序列。"""
        tree = self.ui.treeWidget_plan
        plan_item = tree.topLevelItem(0)
        top = plan_item.child(seq_index) if (plan_item is not None and seq_index is not None) else None
        if top is None:
            return
        item = top.child(case_index) if case_index is not None else top
        if item is None:
            return
        tree.setCurrentItem(item)
        tree.setFocus()
        try:
            tree.scrollToItem(item)
        except Exception:
            pass

    # ---------------- tree / debug ----------------
    def _refresh_tree(self):
        tree = self.ui.treeWidget_plan
        tree.clear()
        if not self.plan:
            return
        total_case = sum(len(s.cases) for s in self.plan.sequences)
        # 字体由顶部 PLAN_TREE_*_FONT_PX 配置统一定义
        content_font = QFont(tree.font())
        content_font.setPixelSize(PLAN_TREE_CONTENT_FONT_PX)
        delay_font = QFont(tree.font())
        delay_font.setPixelSize(PLAN_TREE_DELAY_FONT_PX)

        # ① PLAN 根节点：深色标题行
        plan_item = QTreeWidgetItem(
            ["", "{}　（共 {} 序列 / {} 用例）".format(self.plan.name or "未命名计划",
                                                 len(self.plan.sequences), total_case)] + [""] * 6)
        font = plan_item.font(0)
        font.setBold(True)
        for c in range(8):
            plan_item.setFont(c, font)
            plan_item.setBackground(c, QColor(PLAN_TREE_PLAN_BG))
            plan_item.setForeground(c, Qt.white)
        plan_item.setData(0, Qt.UserRole, {"kind": "plan"})

        for si, seq in enumerate(self.plan.sequences):
            # ② 序列节点：蓝色题头行
            seq_title = "序列{}　{}（{} 用例）".format(si + 1, seq.name, len(seq.cases))
            if getattr(seq, "skip", False):
                seq_title += "　[跳过]"
            seq_item = QTreeWidgetItem(["", seq_title] + [""] * 6)
            seq_item.setData(0, Qt.UserRole, {"kind": "sequence", "seq": si})
            font = seq_item.font(0)
            font.setBold(True)
            for c in range(8):
                seq_item.setFont(c, font)
                seq_item.setBackground(c, QColor(PLAN_TREE_SEQ_BG))
                flag = Qt.gray if getattr(seq, "skip", False) else Qt.white
                seq_item.setForeground(c, flag)
            for ci, case in enumerate(seq.cases):
                # ③ 用例行
                case_no = getattr(case, "case_no", "")
                if case.type != "delay":
                    case_no = case_no or "{}.{}".format(si + 1, ci + 1)
                name_text = case.name + ("  [跳过]" if getattr(case, "skip", False) else "")
                case_item = QTreeWidgetItem([
                    case_no,
                    name_text,
                    getattr(case, "description", "") or "",
                    CASE_TYPE_NAMES.get(case.type, case.type),
                    FAIL_POLICY_NAMES.get(getattr(case, "fail_policy", "continue"), "继续"),
                    "是" if getattr(case, "skip", False) else "否",
                    str(case.retry),
                    str(case.timeout_ms),
                ])
                # 内容列（描述/类型/失败策略/用例跳过）字体缩小 50%
                for c in (2, 3, 4, 5):
                    case_item.setFont(c, content_font)
                if getattr(case, "skip", False):
                    case_item.setForeground(1, Qt.gray)
                    case_item.setForeground(5, QColor("#9e9e9e"))
                if case.type == "delay":
                    # 延时用例：整行小字号 + 灰色背景块（Windows 灰色框样式）
                    for c in range(8):
                        case_item.setFont(c, delay_font)
                        case_item.setBackground(c, QColor(PLAN_TREE_DELAY_BG))
                    for c in (2, 3, 4, 5):
                        case_item.setFont(c, content_font)
                else:
                    case_item.setForeground(3, QColor(TYPE_COLORS.get(case.type, "#333333")))
                    bg = QColor("#ffffff") if ci % 2 == 0 else QColor("#eef3f9")
                    for c in range(8):
                        case_item.setBackground(c, bg)
                case_item.setData(0, Qt.UserRole, {"kind": "case", "seq": si, "case": ci})
                seq_item.addChild(case_item)
            plan_item.addChild(seq_item)
        tree.addTopLevelItem(plan_item)
        # 默认展开 PLAN 根与第一个序列，其余折叠，突出「PLAN→序列→用例」三层
        plan_item.setExpanded(True)
        if plan_item.childCount():
            plan_item.child(0).setExpanded(True)

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
