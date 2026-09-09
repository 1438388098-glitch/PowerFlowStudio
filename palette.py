"""
palette.py — Left-side component palette
Five custom buttons that start a real Qt drag-and-drop when pressed.
The canvas (CircuitView) accepts drops; this widget does not need to
coordinate any pending-state with MainWindow.
"""
from PyQt5.QtCore import Qt, QMimeData, QPoint, QByteArray, QDataStream, QIODevice
from PyQt5.QtGui import QDrag, QPixmap, QPainter, QColor, QBrush, QPen, QPolygonF, QFont
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLabel, QSizePolicy


# Custom MIME type — kept short and globally unique for this app.
COMP_MIME = "application/x-powerflow-component"


class ComponentButton(QPushButton):
    """A push-button that starts a QDrag on mouse press."""

    KIND = ""
    LABEL = ""
    COLOR = "#888"

    def __init__(self, parent=None):
        super().__init__(self.LABEL, parent)
        self.setMinimumHeight(46)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.OpenHandCursor)
        # Visual hint: small colored dot to the left of the label.
        self.setStyleSheet(
            f"QPushButton {{ text-align:left; padding-left:14px; font-weight:bold;"
            f" border:1px solid #888; border-radius:4px; background:{self.COLOR}22; }}"
            f"QPushButton:hover {{ background:{self.COLOR}55; }}"
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            try:
                drag = QDrag(self)
                mime = QMimeData()
                mime.setData(COMP_MIME, QByteArray(self.KIND.encode("utf-8")))
                drag.setMimeData(mime)

                # Make the drag pixmap show the button itself, with a small
                # colored square (so users see what's being dragged).
                pm = QPixmap(self.size())
                self.render(pm)
                drag.setPixmap(pm)
                drag.setHotSpot(QPoint(20, 20))

                # Use exec_() so we know whether the drag completed; we
                # don't actually need the return value, but calling exec_
                # rather than exec_ would block — Qt's exec_ returns
                # when the drop is finished or cancelled.
                drag.exec_(Qt.CopyAction)
                event.accept()
                return
            except Exception:
                import traceback
                with open("crash.log", "a", encoding="utf-8") as f:
                    f.write("\n=== palette mousePressEvent exception ===\n")
                    traceback.print_exc(file=f)
                # Don't propagate — fall through to default handling.
        super().mousePressEvent(event)


class BusButton(ComponentButton):
    KIND = "Bus"; LABEL = "母线  Bus"; COLOR = "#3c78c8"

class GenButton(ComponentButton):
    KIND = "Gen"; LABEL = "电源  Gen"; COLOR = "#dc6420"

class LoadButton(ComponentButton):
    KIND = "Load"; LABEL = "负荷  Load"; COLOR = "#50a050"

class TrafoButton(ComponentButton):
    KIND = "Trafo"; LABEL = "变压器  Trafo"; COLOR = "#963c96"

class ImpedanceButton(ComponentButton):
    KIND = "Impedance"; LABEL = "阻抗  Impedance"; COLOR = "#78783c"


class ComponentPalette(QWidget):
    """Container panel holding the five component buttons."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(150)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel("元件库")
        title.setStyleSheet("font-weight:bold; font-size:12pt;")
        layout.addWidget(title)

        hint = QLabel("按住拖到画布")
        hint.setStyleSheet("color:#666; font-size:9pt;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        for cls in (BusButton, GenButton, LoadButton, TrafoButton, ImpedanceButton):
            layout.addWidget(cls())

        layout.addStretch(1)
