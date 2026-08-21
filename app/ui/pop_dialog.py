from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                             QPushButton, QVBoxLayout, QWidget)


class PopDialog(QDialog):
    """Runtime human-interaction dialog with two buttons returning True/False."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle(str(config.get("title", "提示")))
        self.setModal(True)
        self.setMinimumSize(420, 220)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        content = QLabel(str(config.get("content", "")))
        content.setWordWrap(True)
        content.setTextInteractionFlags(Qt.TextSelectableByMouse)
        content.setStyleSheet("font-size: 15px; padding: 8px;")
        layout.addWidget(content)

        btn_true_text = str(config.get("btn_true", "确认"))
        btn_false_text = str(config.get("btn_false", "取消"))

        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)
        self.btn_true = QPushButton(btn_true_text)
        self.btn_true.setStyleSheet(
            "background:#27ae60;color:white;font-weight:bold;padding:10px 32px;border:none;border-radius:4px;font-size:14px;")
        self.btn_false = QPushButton(btn_false_text)
        self.btn_false.setStyleSheet(
            "background:#95a5a6;color:white;padding:10px 32px;border:none;border-radius:4px;font-size:14px;")
        btn_layout.addWidget(self.btn_true)
        btn_layout.addSpacing(16)
        btn_layout.addWidget(self.btn_false)
        btn_layout.addStretch(1)
        layout.addLayout(btn_layout)

        self.btn_true.clicked.connect(self._on_true)
        self.btn_false.clicked.connect(self._on_false)

    def _on_true(self):
        self.done(QDialog.Accepted)

    def _on_false(self):
        self.done(QDialog.Rejected)
