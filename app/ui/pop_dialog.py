from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton,
                             QVBoxLayout)

try:
    from app.core.settings_manager import settings_manager
except Exception:
    settings_manager = None

POP_DEFAULT_WIDTH = 840
POP_DEFAULT_HEIGHT = 440
POP_DEFAULT_FONT = 30
POP_DEFAULT_BG = "#b8c0c4"


def _pop_setting(name, default):
    value = getattr(settings_manager, name, default) if settings_manager is not None else default
    return value if value not in (None, "") else default


class PopDialog(QDialog):
    """Runtime human-interaction dialog with two buttons returning True/False."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle(str(config.get("title", "提示")))
        self.setModal(True)

        width = int(_pop_setting("pop_width", POP_DEFAULT_WIDTH))
        height = int(_pop_setting("pop_height", POP_DEFAULT_HEIGHT))
        font_px = int(_pop_setting("pop_font_size", POP_DEFAULT_FONT))
        bg_color = str(_pop_setting("pop_bg_color", POP_DEFAULT_BG))
        btn_font_px = max(12, int(round(font_px * 28 / 30.0)))

        self.setMinimumSize(width, height)
        self.resize(width, height)
        self.setStyleSheet(
            "QDialog {{ background-color: {0}; }}".format(bg_color))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(max(12, font_px // 2))

        title_text = str(config.get("title", "")).strip()
        if title_text:
            title = QLabel(title_text)
            title.setAlignment(Qt.AlignCenter)
            title.setStyleSheet(
                "font-size: {}px; font-weight: bold; color: #000000;"
                "background: transparent;".format(font_px + 4))
            layout.addWidget(title)

        content = QLabel(str(config.get("content", "")))
        content.setWordWrap(True)
        content.setAlignment(Qt.AlignCenter)
        content.setTextInteractionFlags(Qt.TextSelectableByMouse)
        content.setStyleSheet(
            "font-size: {}px; font-weight: bold; color: #000000;"
            "padding: 12px; background: transparent;".format(font_px))
        layout.addWidget(content, 1)

        btn_true_text = str(config.get("btn_true", "确认"))
        btn_false_text = str(config.get("btn_false", "取消"))

        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)
        self.btn_true = QPushButton(btn_true_text)
        self.btn_true.setMinimumHeight(max(44, int(round(btn_font_px * 2.2))))
        self.btn_true.setStyleSheet(
            "background:#1e8449;color:white;font-weight:bold;"
            "padding:12px 44px;border:none;border-radius:8px;"
            "font-size:{}px;".format(btn_font_px))
        self.btn_false = QPushButton(btn_false_text)
        self.btn_false.setMinimumHeight(max(44, int(round(btn_font_px * 2.2))))
        self.btn_false.setStyleSheet(
            "background:#5d6d7e;color:white;font-weight:bold;"
            "padding:12px 44px;border:none;border-radius:8px;"
            "font-size:{}px;".format(btn_font_px))
        btn_layout.addWidget(self.btn_true)
        btn_layout.addSpacing(max(16, font_px))
        btn_layout.addWidget(self.btn_false)
        btn_layout.addStretch(1)
        layout.addLayout(btn_layout)

        self.btn_true.clicked.connect(self._on_true)
        self.btn_false.clicked.connect(self._on_false)

    def _on_true(self):
        self.done(QDialog.Accepted)

    def _on_false(self):
        self.done(QDialog.Rejected)
