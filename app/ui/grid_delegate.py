"""A QStyledItemDelegate that paints grid separators (row/column lines)
for QTreeWidget tables."""
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPen
from PyQt5.QtWidgets import QStyledItemDelegate


class GridLineDelegate(QStyledItemDelegate):
    def __init__(self, color, parent=None):
        super().__init__(parent)
        self.line_color = QColor(color)

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        try:
            painter.save()
            pen = QPen(self.line_color, 1)
            pen.setStyle(Qt.SolidLine)
            painter.setPen(pen)
            rect = option.rect

            tree = self.parent()
            header = tree.header() if tree is not None else None

            # vertical separator at each column boundary
            if header is not None:
                for col in range(header.count() - 1):
                    x = header.sectionViewportPosition(col) + header.sectionSize(col)
                    painter.drawLine(x, rect.top(), x, rect.bottom() + 1)

            # horizontal separator under each row
            painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())

            painter.restore()
        except Exception:
            try:
                painter.restore()
            except Exception:
                pass
