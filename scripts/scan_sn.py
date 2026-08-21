"""
SN扫描脚本 - 前置步骤

用于在每次测试开始前扫描SN码。
扫描到SN码后返回，继续执行后续测试步骤。
包含PyQt小页面等待用户输入SN码。
功能：原生窗口图标设置为白底蓝字EOL（代码生成，无需图片）
"""

import sys
from PyQt5.QtCore import Qt, pyqtSignal, pyqtSlot, QObject, QMetaObject, QThread
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QFont, QColor
from PyQt5.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QSpacerItem, QSizePolicy,
    QMenuBar, QMenu, QAction          # QAction 在此处导入
)


class _SNRunner(QObject):
    """在 GUI 线程创建并运行 SN 输入对话框（跨线程安全桥接）。"""

    def __init__(self, prompt):
        super().__init__()
        self.prompt = prompt
        self.sn = ""
        self.accepted = False

    @pyqtSlot()
    def run(self):
        dlg = SNInputDialog(prompt=self.prompt)
        self.accepted = False
        if dlg.exec_() == QDialog.Accepted:
            self.sn = dlg.get_sn()
            self.accepted = True


def create_eol_icon() -> QIcon:
    """生成白底蓝字 EOL 图标，用于窗口标题栏图标"""
    size = 64
    pix = QPixmap(size, size)
    pix.fill(QColor("white"))

    painter = QPainter(pix)
    font = QFont()
    font.setPointSize(26)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor("#0055cc"))
    painter.drawText(pix.rect(), Qt.AlignCenter, "EOL")
    painter.end()
    return QIcon(pix)


class SNInputDialog(QDialog):
    """SN码输入对话框"""

    sn_received = pyqtSignal(str)
    abort_requested = pyqtSignal()

    def __init__(self, parent=None, prompt="请扫描或输入SN码:"):
        super().__init__(parent)
        self.setWindowTitle("SN码扫描")
        self.setFixedSize(520, 380)          # 高度略增以容纳菜单栏
        self.setWindowIcon(create_eol_icon())

        self._sn = ""
        self._aborted = False
        self._build_ui(prompt)

    def _build_ui(self, prompt: str) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(25, 10, 25, 20)
        main_layout.setSpacing(12)

        # ----- 菜单栏（新增） -----
        menu_bar = QMenuBar(self)
        menu_bar.setStyleSheet("QMenuBar { background-color: transparent; }")
        menu = menu_bar.addMenu("操作")
        abort_action = QAction("中止测试", self)
        abort_action.triggered.connect(self._on_abort)
        menu.addAction(abort_action)
        main_layout.addWidget(menu_bar)

        # 上方弹性占位
        main_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))

        prompt_label = QLabel(prompt)
        prompt_label.setStyleSheet("font-size: 15px; font-weight: bold;")
        main_layout.addWidget(prompt_label)

        self._sn_input = QLineEdit()
        self._sn_input.setPlaceholderText("请扫描SN码或手动输入后按回车...")
        self._sn_input.setFixedHeight(50)
        self._sn_input.setStyleSheet("font-size: 14px; padding: 6px;")
        main_layout.addWidget(self._sn_input)

        # 中间弹性间距
        main_layout.addSpacerItem(QSpacerItem(20, 30, QSizePolicy.Minimum, QSizePolicy.Expanding))

        # 按钮布局：仅确认按钮居中
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        self._btn_submit = QPushButton("确认")
        self._btn_submit.setFixedSize(130, 48)
        self._btn_submit.setStyleSheet("font-size:14px;")
        self._btn_submit.clicked.connect(self._on_submit)
        self._btn_submit.setDefault(True)          # 设为默认按钮，回车触发
        btn_row.addWidget(self._btn_submit)

        btn_row.addStretch(1)
        main_layout.addLayout(btn_row)

        # 下方少量占位
        main_layout.addSpacerItem(QSpacerItem(20, 10, QSizePolicy.Minimum, QSizePolicy.Minimum))

        # 初始焦点置于输入框，方便直接扫描
        self._sn_input.setFocus()

    def _on_submit(self) -> None:
        """提交SN码"""
        sn = self._sn_input.text().strip()
        if sn:
            self._sn = sn
            self.sn_received.emit(sn)
            self.accept()

    def _on_abort(self) -> None:
        """中止测试"""
        self._aborted = True
        self.abort_requested.emit()
        self.reject()

    def was_aborted(self) -> bool:
        return self._aborted

    def get_sn(self) -> str:
        return self._sn

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self._sn_input.clear()
        else:
            super().keyPressEvent(event)


class SNScanner:
    """SN扫描器，等待用户输入SN码"""

    def __init__(self):
        self._dialog = None
        self._app = None

    def get_sn_sync(self, prompt="请扫描或输入SN码:") -> str:
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
            self._app = app

        main_thread = app.thread()
        if QThread.currentThread() is main_thread:
            # 已在 GUI 线程：直接显示对话框
            dialog = SNInputDialog(prompt=prompt)
            self._dialog = dialog
            if dialog.exec_() == QDialog.Accepted:
                return dialog.get_sn()
            return ""

        # 引擎工作线程：把对话框委托到 GUI 线程执行（避免跨线程创建 Qt 窗口导致崩溃）
        runner = _SNRunner(prompt)
        runner.moveToThread(main_thread)
        QMetaObject.invokeMethod(runner, "run", Qt.BlockingQueuedConnection)
        self._dialog = runner
        return runner.sn if runner.accepted else ""


_scanner = SNScanner()


def scan_sn(params: dict = None) -> dict:
    if params is None:
        params = {}

    prompt = params.get("prompt", "请扫描或输入SN码:")

    print(f"\n{'='*50}")
    print(f"[SN扫描] {prompt}")
    print(f"{'='*50}")
   

    show_dialog = params.get("_show_sn_dialog")
    if show_dialog is not None:
        try:
            sn = show_dialog(prompt)
        except Exception as e:
            return {"sn": "", "success": False, "message": f"SN扫描出错: {str(e)}"}
    else:
        try:
            sn = _scanner.get_sn_sync(prompt)
             # 设置sn到面板
            set_sn_to_Panel(sn)
        except Exception as e:
            return {"sn": "", "success": False, "message": f"SN扫描出错: {str(e)}"}

    if not sn:
        return {"sn": "", "success": False, "message": "未输入SN码"}

    print(f"[SN扫描] 扫描成功: {sn}")
    return {"sn": sn, "success": True, "message": f"SN码: {sn}"}


def scan_sn_for_loop(params: dict = None) -> dict:
    return scan_sn(params)

# 状态栏显示的内容
def report_robot_status(temperature=25.5, current=1.2, voltage=12.3, battery=87):
    set_robot_temperature(temperature)
    set_robot_current(current)
    set_robot_voltage(voltage)
    set_robot_battery(battery)
    return {"ok": True}

scan = scan_sn
scan_loop = scan_sn_for_loop


if __name__ == "__main__":
    app = QApplication(sys.argv)
    result = scan_sn()
    print(f"结果: {result}")