"""
palette.py — 左侧元件库面板
双击或在画布上拖放即可创建元件
"""
from PyQt5.QtCore import Qt, pyqtSignal, QPointF
from PyQt5.QtGui import QPainter, QColor, QBrush, QPen, QPolygonF, QFont, QIcon, QPixmap
from PyQt5.QtWidgets import (
    QListWidget, QListWidgetItem, QWidget, QVBoxLayout, QLabel, QAbstractItemView
)


class ComponentPalette(QWidget):
    """左侧元件库: 一列按钮 / 一列可拖动列表"""
    component_chosen = pyqtSignal(str)   # 发出元件种类名: Bus/Gen/Load/Trafo/Impedance

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(150)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        title = QLabel("元件库")
        title.setStyleSheet("font-weight:bold; font-size:12pt;")
        layout.addWidget(title)

        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QPixmap(48, 48).size())
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setSpacing(6)
        self.list.setUniformItemSizes(True)
        layout.addWidget(self.list, 1)

        for kind, label, color in [
            ("Bus",       "母线",     "#3c78c8"),
            ("Gen",       "电源",     "#dc6420"),
            ("Load",      "负荷",     "#50a050"),
            ("Trafo",     "变压器",   "#963c96"),
            ("Impedance", "阻抗",     "#78783c"),
        ]:
            pix = QPixmap(48, 48)
            pix.fill(Qt.transparent)
            p = QPainter(pix)
            p.setRenderHint(QPainter.Antialiasing)
            p.setBrush(QBrush(QColor(color)))
            p.setPen(QPen(Qt.black, 1.5))
            if kind == "Bus":
                p.drawRoundedRect(4, 16, 40, 16, 4, 4)
                p.drawLine(10, 22, 38, 22)
                p.drawLine(10, 26, 38, 26)
                p.drawLine(10, 30, 38, 30)
            elif kind == "Gen":
                p.drawEllipse(8, 8, 32, 32)
                p.setPen(Qt.black)
                f = QFont("Arial", 14, QFont.Bold)
                p.setFont(f)
                p.drawText(pix.rect(), Qt.AlignCenter, "G")
            elif kind == "Load":
                poly = QPolygonF([QPointF(24, 38), QPointF(8, 10), QPointF(40, 10)])
                p.drawPolygon(poly)
                p.setPen(Qt.black)
                f = QFont("Arial", 11, QFont.Bold)
                p.setFont(f)
                p.drawText(pix.rect(), Qt.AlignCenter, "L")
            elif kind == "Trafo":
                p.drawLine(4, 24, 44, 24)
                p.setBrush(QBrush(Qt.white))
                p.drawRect(14, 14, 20, 20)
                p.setPen(Qt.black)
                f = QFont("Arial", 10, QFont.Bold)
                p.setFont(f)
                p.drawText(pix.rect(), Qt.AlignCenter, "T")
            elif kind == "Impedance":
                p.drawLine(4, 24, 44, 24)
                p.setBrush(QBrush(Qt.white))
                p.drawRect(14, 14, 20, 20)
                p.setPen(Qt.black)
                f = QFont("Arial", 10, QFont.Bold)
                p.setFont(f)
                p.drawText(pix.rect(), Qt.AlignCenter, "Z")
            p.end()
            item = QListWidgetItem(QIcon(pix), label)
            item.setData(Qt.UserRole, kind)
            item.setTextAlignment(Qt.AlignHCenter)
            self.list.addItem(item)

        self.list.itemClicked.connect(self._on_clicked)
        self.list.itemDoubleClicked.connect(self._on_clicked)

    def _on_clicked(self, item):
        kind = item.data(Qt.UserRole)
        self.component_chosen.emit(kind)
