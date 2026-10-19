import os
import traceback

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QFont, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)


class ImageTextWindow(QDialog):
    """显示图片和说明，并等待操作员选择 Yes 或 No。"""

    def __init__(self, window_width, window_height, font_size,
                 image_width, image_height, text_content, image_path):
        super().__init__()
        self.window_width = window_width
        self.window_height = window_height
        self.font_size = font_size
        self.image_width = image_width
        self.image_height = image_height
        self.text_content = text_content
        self.image_path = image_path
        self.result = "Failed"
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("请参考图片与文字")
        self.setFixedSize(self.window_width, self.window_height)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setFixedSize(self.image_width, self.image_height)
        self.image_label.setStyleSheet(
            "border: 1px solid #cccccc; background-color: #f5f5f5;"
        )
        self.load_image()

        text_label = QLabel(self.text_content)
        text_label.setAlignment(Qt.AlignCenter)
        text_label.setWordWrap(True)
        font = QFont()
        font.setPointSize(self.font_size)
        text_label.setFont(font)

        yes_button = QPushButton("Yes")
        no_button = QPushButton("No")
        yes_button.clicked.connect(lambda: self.finish("Pass"))
        no_button.clicked.connect(lambda: self.finish("Failed"))
        yes_button.setMinimumHeight(40)
        no_button.setMinimumHeight(40)
        yes_button.setVisible(False)
        no_button.setVisible(False)
        self.yes_button = yes_button
        self.no_button = no_button

        self.button_timer = QTimer(self)
        self.button_timer.setSingleShot(True)
        self.button_timer.timeout.connect(self.show_result_buttons)
        self.button_timer.start(10000)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(yes_button)
        button_layout.addWidget(no_button)
        button_layout.addStretch()

        main_layout.addStretch()
        main_layout.addWidget(self.image_label, alignment=Qt.AlignCenter)
        main_layout.addWidget(text_label)
        main_layout.addStretch()
        main_layout.addLayout(button_layout)

    def load_image(self):
        """加载图片，图片不存在或无法加载时显示占位图。"""
        if not os.path.exists(self.image_path):
            self.set_placeholder("图片未找到\n(占位图)")
            return

        pixmap = QPixmap(self.image_path)
        if pixmap.isNull():
            self.set_placeholder("图片加载失败")
            return

        self.image_label.setPixmap(pixmap.scaled(
            self.image_width,
            self.image_height,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        ))

    def set_placeholder(self, text):
        pixmap = QPixmap(self.image_width, self.image_height)
        pixmap.fill(Qt.lightGray)
        self.image_label.setPixmap(pixmap)
        self.image_label.setText(text)

    def finish(self, result):
        self.button_timer.stop()
        self.result = result
        self.accept()

    def show_result_buttons(self):
        self.yes_button.setVisible(True)
        self.no_button.setVisible(True)

def _resolve_image_path(image_name):
    """Resolve an image name against the fixed project data/images directory."""
    image_name = os.path.basename(str(image_name).replace("\\", "/").strip())
    try:
        from app.core.paths import DATA_DIR
        image_dir = os.path.join(DATA_DIR, "images")
    except Exception:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        image_dir = os.path.join(project_root, "data", "images")
    return os.path.join(image_dir, image_name)

def test_set_picture_show(window_width: int = 950,
                          window_height: int = 900,
                          font_size: int = 22,
                          image_width: int = 900,
                          image_height: int = 750,
                          text_content: str = "请参考图片内容，来调整电机！",
                          image_name: str = "showPicture.jpg") -> dict:
    """显示 data/images 下的图片；调用时只需传图片文件名。"""
    image_path = _resolve_image_path(image_name)
    config = {
        "dialog_class": ImageTextWindow,
        "window_width": window_width,
        "window_height": window_height,
        "font_size": font_size,
        "image_width": image_width,
        "image_height": image_height,
        "text_content": text_content,
        "image_path": image_path,
    }
    try:
        # 软件执行测试函数在线程池中运行，Qt 窗口必须交给 GUI 主线程创建。
        from test_api import request_picture
        return request_picture(config)
    except RuntimeError:
        # 脚本独立运行时没有软件 API，使用本地 Qt 事件循环。
        pass
    except Exception as exc:
        traceback.print_exc()
        return {"result": "Failed", "message": str(exc)}

    try:
        app = QApplication.instance()
        if app is None:
            app = QApplication([])

        window = ImageTextWindow(
            window_width,
            window_height,
            font_size,
            image_width,
            image_height,
            text_content,
            image_path,
        )
        window.exec_()
        return {"result": window.result}
    except Exception as exc:
        traceback.print_exc()
        return {"result": "Failed", "message": str(exc)}


if __name__ == "__main__":
    print(test_set_picture_show())
