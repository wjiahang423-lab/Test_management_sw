import configparser
import json
import os
import time

from PyQt5 import uic
from PyQt5.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QHeaderView,
                             QInputDialog, QLabel, QLineEdit, QMenu,
                             QMessageBox, QPushButton, QSizePolicy, QSpacerItem,
                             QTreeWidgetItem)

from app.core import syslog
from app.core.context import RuntimeContext
from app.core.engine import EngineWorker
from app.core.paths import (CASE_STATE_FAIL, CASE_STATE_PASS, CASE_STATE_SKIP,
                            CASE_STATE_TIMEOUT, CASE_TYPE_NAMES, PLANS_DIR,
                            PLAN_FILE_FILTER, STATS_FILE, STATS_INI_FILE,
                            UI_FILES)
from app.core.plan_model import TestPlan
from app.ui.brand import make_logo_label
from app.ui.grid_delegate import GridLineDelegate
from app.ui.pop_dialog import PopDialog

# 默认工位设置（可在"系统设置→全局基础设置"中修改工位ID/工位名称）
STATION_TITLE = "机器人测试01工位"
STATION_ID = "01"

STATE_COLORS = {
    "待执行": "#95a5a6",
    "运行中": "#3498db",
    "PASS": "#27ae60",
    "FAIL": "#e74c3c",
    "跳过": "#95a5a6",
    "超时": "#f39c12",
}


def _fmt_robot(value):
    if value is None:
        return "--"
    return "{}".format(value)


class _PlanPathClickFilter(QObject):
    """点击测试计划输入框时发出信号，用于弹出可选计划菜单。"""
    sig_clicked = pyqtSignal()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self.sig_clicked.emit()
            return True
        return super().eventFilter(obj, event)


class ExecutePage:
    """Loads execute_page.ui and wires all business logic.

    Intended to be loaded into a host QWidget via setup(host)."""

    def __init__(self, context, variables, settings):
        self.ctx = context
        self.variables = variables
        self.settings = settings
        self.ui = None
        self.engine = None
        self.plan = None
        self._case_items = {}
        self._seq_items = {}
        self._waiting_sn = False
        self._run_cycle = 0
        self._stats = self._load_stats()
        self.log_callback = None
        self.on_switch_manage = None

    # ---------------- lifecycle ----------------
    def setup(self, host):
        uic.loadUi(UI_FILES["execute"], host)
        self.ui = host
        self._current_user = "管理员"
        self._current_role = "管理员"
        self._plan_click = _PlanPathClickFilter(host)

        # ---------- 顶部标题栏：logo | 工位标题 | 时钟 ----------
        top = self.ui.topBarLayout
        top.insertWidget(0, make_logo_label(34))
        for w in (self.ui.label_planPath, self.ui.lineEdit_planPath, self.ui.openPlanBtn,
                  self.ui.separator1, self.ui.pauseBtn, self.ui.stopBtn):
            top.removeWidget(w)
            w.hide()
        for i in range(top.count() - 1, -1, -1):
            if isinstance(top.itemAt(i), QSpacerItem):
                top.removeItem(top.itemAt(i))
        # 工位标题（.ui 已预置；旧 .ui 缺失时兜底动态创建）
        if not hasattr(self.ui, "label_title") or self.ui.label_title is None:
            self.ui.label_title = QLabel(host)
        title = self.ui.label_title
        top.removeWidget(title)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            "font-size: 28px; font-weight: bold; color: #1F5AA8; letter-spacing: 2px;")
        self.refresh_station_title()
        # 返回管理页面按钮（.ui 已预置；仅管理员显示，默认隐藏）
        if not hasattr(self.ui, "btn_manage") or self.ui.btn_manage is None:
            self.ui.btn_manage = QPushButton("管理页面")
        self.btn_manage = self.ui.btn_manage
        top.removeWidget(self.btn_manage)
        self.btn_manage.setToolTip("返回管理页面（仅管理员）")
        self.btn_manage.setStyleSheet(
            "QPushButton { background-color: #2c5aa0; color: white; font-weight: bold;"
            " border: none; border-radius: 6px; padding: 10px 20px; min-height: 40px;"
            " font-size: 15px; }"
            "QPushButton:pressed { background-color: #3a6bb8; }")
        self.btn_manage.clicked.connect(self._switch_manage)
        self.btn_manage.hide()
        # 时钟（.ui 已预置；旧 .ui 缺失时兜底动态创建）
        if not hasattr(self.ui, "label_time") or self.ui.label_time is None:
            self.ui.label_time = QLabel("")
        tlabel = self.ui.label_time
        top.removeWidget(tlabel)
        tlabel.setStyleSheet(
            "font-size: 16px; font-weight: bold; color: #1F5AA8; padding: 0 10px;")
        # 重新按顺序摆放：两个弹性spacer包裹标题
        top.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum))
        top.addWidget(title, 1)
        top.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum))
        top.addWidget(self.btn_manage)
        top.addWidget(tlabel)

        # ---------- 左侧：测试步骤(上，约80%) + 执行日志(下，约20%，默认隐藏) ----------
        self.ui.stepsLayout.addWidget(self.ui.groupBox_log)
        self.ui.stepsLayout.setStretch(0, 4)
        self.ui.stepsLayout.setStretch(1, 1)
        self.ui.groupBox_log.setVisible(True)
        self.ui.logFrame.setVisible(False)
        self.ui.logToggleBtn.setText("▼ 显示日志")

        # ---------- 右侧：产品信息重排 + 开始按钮放底部 ----------
        # SN：保留标签，去掉输入框，改为显示当前SN（脚本 set_sn_to_Panel 输入）
        sn_row = self.ui.snRow1
        sn_row.removeWidget(self.ui.lineEdit_sn)
        sn_row.removeWidget(self.ui.btn_startBig)
        self.ui.lineEdit_sn.hide()
        # 当前SN显示（.ui 已预置；旧 .ui 缺失时兜底动态创建）
        if not hasattr(self.ui, "label_sn_value") or self.ui.label_sn_value is None:
            self.ui.label_sn_value = QLabel("--")
        sn_value = self.ui.label_sn_value
        sn_row.removeWidget(sn_value)
        sn_value.setStyleSheet(
            "font-size: 22px; font-weight: bold; color: #1F5AA8;")
        sn_value.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        sn_row.insertWidget(1, sn_value, 1)
        # 当前批次 / 测试节拍改为上下排列（原为同一行），减少空间占用
        self.ui.snLayout.removeItem(self.ui.snRow2)
        for label in (self.ui.label_batch, self.ui.label_takt):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(label)
            self.ui.snLayout.addLayout(row)
        # 测试计划：点击输入框弹出可选项（去掉"打开计划"按钮）
        self.ui.lineEdit_planPath.setReadOnly(True)
        self.ui.lineEdit_planPath.setPlaceholderText("点击选择测试计划")
        self.ui.lineEdit_planPath.installEventFilter(self._plan_click)
        self._plan_click.sig_clicked.connect(self._show_plan_picker)
        row_plan = QHBoxLayout()
        row_plan.addWidget(self.ui.label_planPath)
        row_plan.addWidget(self.ui.lineEdit_planPath, 1)
        self.ui.label_planPath.show()
        self.ui.lineEdit_planPath.show()
        self.ui.snLayout.addLayout(row_plan)
        # 开始按钮移到右侧底部（原日志区域），靠最右
        self.ui.rightLayout.addWidget(self.ui.btn_startBig, 0, Qt.AlignRight)
        # 布局：左步骤区约 48%（再缩小20%），右操作区约 52%
        self.ui.bodyLayout.setStretch(0, 12)
        self.ui.bodyLayout.setStretch(1, 13)

        # ---------- 控件连接 ----------
        self.ui.btn_startBig.clicked.connect(self._guarded(self.start_run))
        self.ui.resetBtn.clicked.connect(self._guarded(self.reset_stats))
        self.ui.logToggleBtn.clicked.connect(self._guarded(self.toggle_log))
        self.ui.logClearBtn.clicked.connect(lambda: self._guarded(lambda: self.ui.logText.clear())())
        self.btn_manage.clicked.connect(self._guarded(self._switch_manage))
        self._plan_click.sig_clicked.connect(self._guarded(self._show_plan_picker))

        # ---------- 树控件 ----------
        self.ui.treeWidget_steps.setColumnWidth(0, 70)
        self.ui.treeWidget_steps.setColumnWidth(1, 300)
        header = self.ui.treeWidget_steps.header()
        header.setStretchLastSection(False)
        for col, width in ((0, 70), (2, 120), (3, 100), (4, 100)):
            self.ui.treeWidget_steps.setColumnWidth(col, width)
            header.setSectionResizeMode(col, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)  # 测试项名称列自适应拉伸
        self._steps_delegate = GridLineDelegate("#d5d8dc", self.ui.treeWidget_steps)
        self.ui.treeWidget_steps.setItemDelegate(self._steps_delegate)
        self.ui.logText.setPlainText("")
        self._update_stats_display()
        self.set_overall("待机中", "#95a5a6")

        # 定时器：机器人状态 + SN 显示刷新 + 跨天检测 + 时钟
        self._robot_timer = QTimer(host)
        self._robot_timer.timeout.connect(self._refresh_robot_status)
        self._robot_timer.timeout.connect(self._update_sn_display)
        self._robot_timer.start(500)
        self._clock_timer = QTimer(host)
        self._clock_timer.timeout.connect(self._check_date_rollover)
        self._clock_timer.timeout.connect(self._update_time_label)
        self._clock_timer.start(1000)
        self._update_time_label()
        self._update_sn_display()
        return host

    def refresh_station_title(self):
        """根据全局设置刷新顶部工位标题，并同步工位ID到运行时上下文（供脚本/上报使用）。"""
        name = (self.settings.station_name if self.settings else "") or STATION_TITLE
        station_id = (self.settings.station_id if self.settings else "") or STATION_ID
        if getattr(self, "ui", None) is not None and hasattr(self.ui, "label_title"):
            self.ui.label_title.setText(name)
        if self.ctx is not None:
            self.ctx.set_station_info(station_id, name)

    def _update_time_label(self):
        if self.ui and hasattr(self.ui, "label_time"):
            self.ui.label_time.setText(time.strftime("%Y-%m-%d %H:%M:%S"))

    def _guarded(self, fn):
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
                syslog.exception("执行页面操作异常：{}".format(getattr(fn, "__name__", str(fn))))
                QMessageBox.critical(self.ui, "错误", "操作执行失败，详情已写入系统日志（data/logs/）")
                return None
        return wrapper

    def _update_sn_display(self):
        if self.ui and hasattr(self.ui, "label_sn_value"):
            sn = (self.ctx.get_sn() or "").strip()
            self.ui.label_sn_value.setText(sn if sn else "--")

    def set_manage_button_visible(self, visible):
        """显示/隐藏"管理页面"按钮（仅管理员可见）。"""
        if hasattr(self, "btn_manage"):
            self.btn_manage.setVisible(visible)

    def _switch_manage(self):
        if self.on_switch_manage:
            self.on_switch_manage()

    def set_current_user(self, user_name, role):
        self._current_user = user_name
        self._current_role = role

    # ---------------- robot status / clock ----------------
    def _refresh_robot_status(self):
        if not self.ui:
            return
        status = self.ctx.get_robot_status()
        self.ui.label_robotTemp.setText("温度: {}".format(_fmt_robot(status.get("temperature"))))
        self.ui.label_robotCurrent.setText("电流: {}".format(_fmt_robot(status.get("current"))))
        self.ui.label_robotVoltage.setText("电压: {}".format(_fmt_robot(status.get("voltage"))))
        self.ui.label_robotBattery.setText("电池: {}".format(_fmt_robot(status.get("battery"))))

    # ---------------- plan ----------------
    def open_plan(self, path=None):
        try:
            if not path:
                start_dir = os.path.dirname(self.plan.file_path) if self.plan and self.plan.file_path else ""
                path, _ = QFileDialog.getOpenFileName(self.ui, "打开测试计划", start_dir, PLAN_FILE_FILTER)
                if not path:
                    return
            plan = TestPlan.load(path)
            self.plan = plan
            # 全局变量随 PLAN 加载
            self.variables.load_dict(plan.variables)
            self.ui.lineEdit_planPath.setText(path)
            self.ctx.set_sn("")
            self._update_sn_display()
            self._update_batch()
            self._load_tree()
            self.append_log("已加载测试计划：{}（{} 条用例）".format(plan.name, plan.total_cases))
            self.set_overall("待机中", "#95a5a6")
            self.ui.btn_startBig.setEnabled(True)
        except Exception as e:
            syslog.exception("加载测试计划失败：{}".format(path))
            QMessageBox.critical(self.ui, "错误", "加载计划失败：{}".format(e))

    def _plan_search_dirs(self):
        dirs = [PLANS_DIR]
        if self.plan and self.plan.file_path:
            d = os.path.dirname(self.plan.file_path)
            if os.path.normpath(d) != os.path.normpath(PLANS_DIR):
                dirs.append(d)
        return dirs

    def _show_plan_picker(self):
        menu = QMenu(self.ui)
        found = []
        for d in self._plan_search_dirs():
            if not os.path.isdir(d):
                continue
            for name in sorted(os.listdir(d)):
                if name.lower().endswith((".plan", ".json")):
                    found.append(os.path.join(d, name))
        if not found:
            act = menu.addAction("未找到测试计划文件（data/plans/）")
            act.setEnabled(False)
        else:
            current = self.plan.file_path if self.plan and self.plan.file_path else None
            for path in found:
                act = menu.addAction(os.path.basename(path))
                act.setData(path)
                if current and os.path.normpath(path) == os.path.normpath(current):
                    act.setCheckable(True)
                    act.setChecked(True)
            menu.addSeparator()
            browse = menu.addAction("浏览其他位置...")
            browse.setData("__browse__")
        chosen = menu.exec_(self.ui.lineEdit_planPath.mapToGlobal(
            self.ui.lineEdit_planPath.rect().bottomLeft()))
        if not chosen or chosen.data() is None:
            return
        if chosen.data() == "__browse__":
            self.open_plan()
        else:
            self.open_plan(chosen.data())

    def _update_batch(self):
        if self.ui:
            batch = (self.plan.settings.get("batch", "") if self.plan else "") or "--"
            self.ui.label_batch.setText("当前批次：{}".format(batch))

    def _load_tree(self):
        tree = self.ui.treeWidget_steps
        tree.clear()
        self._case_items = {}
        self._seq_items = {}
        if not self.plan:
            return
        seq_index = 1
        for si, seq in enumerate(self.plan.sequences, 1):
            seq_item = QTreeWidgetItem(["序列 {}".format(si), seq.name, "序列", "待执行", "0.0s"])
            seq_item.setExpanded(True)
            for ci, case in enumerate(seq.cases, 1):
                case_item = QTreeWidgetItem([
                    "{}.{}".format(si, ci),
                    case.name,
                    CASE_TYPE_NAMES.get(case.type, case.type),
                    "待执行",
                    "0.0s",
                ])
                seq_item.addChild(case_item)
                self._case_items[case.id] = case_item
                self._seq_items[case.id] = seq_item
            tree.addTopLevelItem(seq_item)
        tree.expandAll()

    # ---------------- run control ----------------
    def start_run(self):
        if not self.plan:
            QMessageBox.information(self.ui, "提示", "请先打开测试计划")
            return
        if self.engine and self.engine.isRunning():
            QMessageBox.information(self.ui, "提示", "测试正在执行中")
            return
        self._waiting_sn = False
        self._run_cycle = 0
        self.ctx.set_sn("")
        self._update_sn_display()
        self._run_start_time = time.time()
        try:
            self._build_engine()
            self.engine.wait_sn = False  # SN 由脚本通过 set_sn_to_Panel 输入
            self.ui.btn_startBig.setEnabled(False)
            self.ui.btn_startBig.setText("▶ 执行中...")
            self.engine.start()
        except Exception:
            syslog.exception("启动测试执行失败")
            self.ui.btn_startBig.setEnabled(True)
            self.ui.btn_startBig.setText("▶ 开始")
            QMessageBox.critical(self.ui, "错误", "启动执行失败，详情见系统日志")

    def _build_engine(self):
        self.engine = EngineWorker(self.plan, self.ctx, self.variables, self.settings)
        self.engine.wait_sn = False
        self.engine.sig_log.connect(self.append_log)
        self.engine.sig_case_state.connect(self._on_case_state)
        self.engine.sig_case_sessions.connect(self._on_case_sessions)
        self.engine.sig_overall.connect(self._on_overall)
        self.engine.sig_progress.connect(self._on_progress)
        self.engine.sig_run_finished.connect(self._on_run_finished)
        self.engine.sig_run_started.connect(self._on_run_started)
        self.engine.sig_request_pop.connect(self._on_pop_requested)
        self.engine.sig_phase.connect(self._on_phase)

    def _on_run_started(self):
        self.append_log("测试开始执行")

    def _on_pop_requested(self, config):
        dlg = PopDialog(config, self.ui)
        result = dlg.exec_() == QDialog.Accepted
        self.engine.pop_result = result
        self.engine.pop_requested.set()

    def _on_case_state(self, case_id, state, detail, elapsed):
        item = self._case_items.get(case_id)
        if not item:
            return
        color = STATE_COLORS.get(state, "#333")
        item.setText(3, state)
        item.setForeground(3, QColor(color))
        item.setText(4, "{:.2f}s".format(elapsed))
        seq_item = self._seq_items.get(case_id)
        if seq_item:
            states = [self._case_items[c].text(3) for c in self._case_items if self._seq_items.get(c) is seq_item]
            if any(s == CASE_STATE_FAIL or s == CASE_STATE_TIMEOUT for s in states):
                seq_item.setText(3, "FAIL")
                seq_item.setForeground(3, QColor("#e74c3c"))
            elif states and all(s == CASE_STATE_PASS for s in states):
                seq_item.setText(3, "PASS")
                seq_item.setForeground(3, QColor("#27ae60"))
            else:
                seq_item.setText(3, "执行中")
                seq_item.setForeground(3, QColor("#3498db"))

    def _on_case_sessions(self, case_id, sessions):
        item = self._case_items.get(case_id)
        if not item:
            return
        for child in list(item.takeChildren()):
            pass
        item.setText(2, "Loop")
        for i, s in enumerate(sessions, 1):
            state = "PASS" if s["passed"] else "FAIL"
            sub = QTreeWidgetItem(["", "  #{} {}".format(i, s.get("name", "")), "session", state, ""])
            sub.setText(3, state)
            sub.setForeground(3, Qt.green if s["passed"] else Qt.red)
            sub.setToolTip(3, str(s.get("detail", "")))
            item.addChild(sub)
        item.setExpanded(True)

    def _on_overall(self, text):
        color = "#27ae60" if text == "PASS" else "#e74c3c" if text == "FAIL" else "#3498db" if text == "执行中" else "#95a5a6"
        self.set_overall(text, color)

    def _on_progress(self, done, total):
        self.ui.label_progress.setText("进度：{} / {}".format(done, total))
        if total:
            self.ui.progressBar.setValue(int(done * 100.0 / total))

    def _on_phase(self, phase):
        if phase == "finished":
            self.ui.btn_startBig.setEnabled(True)
            self.ui.btn_startBig.setText("▶ 开始")

    def _on_run_finished(self, ok, message):
        self.append_log(message)
        if self.engine.stop_requested:
            self.append_log("本次运行已停止，未计入生产统计")
            self.set_overall("已停止", "#e74c3c")
            self.ui.btn_startBig.setEnabled(True)
            self.ui.btn_startBig.setText("▶ 开始")
            return
        overall = self.ctx.get_overall()
        self.record_result(overall is True)
        self.set_overall("PASS" if overall else "FAIL")
        if hasattr(self, "_run_start_time"):
            takt = time.time() - self._run_start_time
            self.ui.label_takt.setText("测试节拍：{:.1f}s".format(takt))
        self.ui.btn_startBig.setEnabled(True)
        self.ui.btn_startBig.setText("▶ 开始")
        self._update_sn_display()

    def set_overall(self, text, color=None):
        if not self.ui:
            return
        self.ui.label_overallStatus.setText("● {}".format(text))
        if color:
            self.ui.label_overallStatus.setStyleSheet(
                "font-size: 66px; font-weight: bold; color: {}".format(color))

    # ---------------- log ----------------
    def toggle_log(self):
        vis = self.ui.logFrame.isVisible()
        self.ui.logFrame.setVisible(not vis)
        self.ui.logToggleBtn.setText("▲ 隐藏日志" if not vis else "▼ 显示日志")

    def append_log(self, msg):
        if self.log_callback:
            self.log_callback(msg)
        if not self.ui:
            return
        ts = time.strftime("%H:%M:%S")
        self.ui.logText.append("[{}] {}".format(ts, msg))
        sb = self.ui.logText.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ---------------- stats (INI, per-date) ----------------
    @staticmethod
    def _today():
        return time.strftime("%Y-%m-%d")

    def _load_stats(self):
        """读取当天统计数据（data/stats.ini 按日期分节累加）。"""
        self._stats_date = self._today()
        stats = {"total": 0, "pass": 0, "fail": 0}
        if os.path.exists(STATS_INI_FILE):
            try:
                cp = configparser.ConfigParser()
                cp.read(STATS_INI_FILE, encoding="utf-8")
                if cp.has_section(self._stats_date):
                    sec = cp[self._stats_date]
                    stats["total"] = sec.getint("total", 0)
                    stats["pass"] = sec.getint("pass", 0)
                    stats["fail"] = sec.getint("fail", 0)
                return stats
            except Exception:
                pass
        elif os.path.exists(STATS_FILE):
            # 迁移旧版 stats.json（仅一次）
            try:
                with open(STATS_FILE, "r", encoding="utf-8") as f:
                    old = json.load(f)
                stats = {
                    "total": old.get("total", 0),
                    "pass": old.get("pass", 0),
                    "fail": old.get("fail", 0),
                }
                self._save_stats_to_ini(stats)
                try:
                    os.remove(STATS_FILE)
                except Exception:
                    pass
                return stats
            except Exception:
                pass
        return stats

    def _save_stats_to_ini(self, stats):
        try:
            cp = configparser.ConfigParser()
            if os.path.exists(STATS_INI_FILE):
                cp.read(STATS_INI_FILE, encoding="utf-8")
            if not cp.has_section(self._stats_date):
                cp.add_section(self._stats_date)
            cp.set(self._stats_date, "total", str(stats.get("total", 0)))
            cp.set(self._stats_date, "pass", str(stats.get("pass", 0)))
            cp.set(self._stats_date, "fail", str(stats.get("fail", 0)))
            os.makedirs(os.path.dirname(STATS_INI_FILE), exist_ok=True)
            with open(STATS_INI_FILE, "w", encoding="utf-8") as f:
                cp.write(f)
        except Exception:
            pass

    def _save_stats(self):
        self._save_stats_to_ini(self._stats)

    def _check_date_rollover(self):
        today = self._today()
        if today != self._stats_date:
            self._stats = self._load_stats()
            self._update_stats_display()

    def _update_stats_display(self):
        if not self.ui:
            return
        total = self._stats.get("total", 0)
        p = self._stats.get("pass", 0)
        f = self._stats.get("fail", 0)
        self.ui.statValue_total.setText(str(total))
        self.ui.statValue_pass.setText(str(p))
        self.ui.statValue_fail.setText(str(f))
        yield_rate = (p / total * 100.0) if total else 0.0
        defect_rate = (f / total * 100.0) if total else 0.0
        self.ui.statValue_yield.setText("{:.2f}%".format(yield_rate))
        self.ui.statValue_defectRate.setText("{:.2f}%".format(defect_rate))

    def record_result(self, passed):
        if passed:
            self._stats["pass"] = self._stats.get("pass", 0) + 1
        else:
            self._stats["fail"] = self._stats.get("fail", 0) + 1
        self._stats["total"] = self._stats.get("total", 0) + 1
        self._save_stats()
        self._update_stats_display()

    def reset_stats(self):
        text, ok = QInputDialog.getText(self.ui, "清零统计", "请输入密码：", echo=QLineEdit.Password)
        if not ok:
            return
        if text != self.settings.clear_password:
            QMessageBox.warning(self.ui, "提示", "密码错误，无法清零")
            return
        try:
            cp = configparser.ConfigParser()
            if os.path.exists(STATS_INI_FILE):
                cp.read(STATS_INI_FILE, encoding="utf-8")
            if cp.has_section(self._stats_date):
                cp.remove_section(self._stats_date)
                os.makedirs(os.path.dirname(STATS_INI_FILE), exist_ok=True)
                with open(STATS_INI_FILE, "w", encoding="utf-8") as f:
                    cp.write(f)
        except Exception:
            pass
        self._stats = {"total": 0, "pass": 0, "fail": 0}
        self._update_stats_display()
        self.append_log("统计数据已清零（仅当天：{}）".format(self._stats_date))
