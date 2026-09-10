"""
palette.py — Left-side component palette

Six custom buttons that start a real Qt drag-and-drop when pressed.
The canvas (CircuitView) accepts drops; this widget does not need to
coordinate any pending-state with MainWindow.

外观与尺寸走 theme.py: 按钮高度/内边距随界面倍数缩放, 并带一个与画布
元件同色的圆点图标, 让用户一眼把"元件库按钮"和"画布上的图元"对应起来。
"""
import logging

from PyQt5.QtCore import Qt, QMimeData, QPoint, QByteArray, QSize
from PyQt5.QtGui import QColor, QDrag, QIcon, QPainter, QPixmap
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLabel, QSizePolicy

import theme

# Custom MIME type — kept short and globally unique for this app.
COMP_MIME = "application/x-powerflow-component"


def tint(color_hex: str, amount: float, base_hex: str = theme.BG) -> str:
    """把元件主色按 ``amount`` 比例混进底色, 返回**实色** #RRGGBB。

    不能用 ``f"{color}1a"`` 这类 8 位写法: Qt 的样式表颜色解析器把
    ``#RRGGBBAA`` 当成 ``#AARRGGBB``, 于是 ``#dc64201a`` 被读成
    "86% 不透明的深褐色" —— 按钮会变成脏兮兮的深色块, 而不是预期的
    浅色底。这里在 Python 侧算好混合色, 输出 6 位实色, 彻底避开歧义,
    也不必依赖 rgba() 的写法差异。
    """
    a = float(max(0.0, min(1.0, amount)))
    c = QColor(color_hex)
    b = QColor(base_hex)
    r = round(c.red() * a + b.red() * (1 - a))
    g = round(c.green() * a + b.green() * (1 - a))
    bl = round(c.blue() * a + b.blue() * (1 - a))
    return QColor(r, g, bl).name()


def _dot_icon(color_hex: str, size: int = 14) -> QIcon:
    """生成一个实心圆点图标(与画布元件配色一致, 免去外部图片资源)"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color_hex))
    p.setPen(QColor(color_hex).darker(130))
    p.drawEllipse(1, 1, size - 2, size - 2)
    p.end()
    return QIcon(pm)


class ComponentButton(QPushButton):
    """A push-button that starts a QDrag on mouse press."""

    KIND = ""
    LABEL = ""
    COLOR = "#888"

    def __init__(self, parent=None):
        super().__init__(self.LABEL, parent)
        s = theme.ui_scale()
        self.setMinimumHeight(int(46 * s))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.OpenHandCursor)
        self.setIcon(_dot_icon(self.COLOR))
        self.setIconSize(QSize(int(14 * s), int(14 * s)))
        self.setToolTip(f"按住拖到画布放置「{self.LABEL}」"
                        f"{self._tip_hint()}")
        # 视觉提示: 左侧色条 + 悬停高亮(颜色取自元件主色)。
        # 底色用实色混合算出来, 不用 8 位十六进制(见 tint() 的说明)。
        bg = tint(self.COLOR, 0.12)
        bg_hover = tint(self.COLOR, 0.22)
        bg_press = tint(self.COLOR, 0.32)
        self.setStyleSheet(
            f"QPushButton {{"
            f" text-align:left; padding:{theme.px(6)} {theme.px(12)};"
            f" font-weight:bold;"
            f" border:1px solid {theme.BORDER}; border-left:{theme.px(4)} solid"
            f" {self.COLOR}; border-radius:{theme.px(5)};"
            f" background:{bg}; }}"
            f"QPushButton:hover {{ background:{bg_hover};"
            f" border-color:{self.COLOR}; }}"
            f"QPushButton:pressed {{ background:{bg_press}; }}"
        )

    def _tip_hint(self) -> str:
        return ""

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            try:
                drag = QDrag(self)
                mime = QMimeData()
                mime.setData(COMP_MIME, QByteArray(self.KIND.encode("utf-8")))
                drag.setMimeData(mime)

                # Make the drag pixmap show the button itself, so users see
                # what is being dragged.
                pm = QPixmap(self.size())
                pm.fill(Qt.transparent)
                self.render(pm)
                drag.setPixmap(pm)
                s = theme.ui_scale()
                drag.setHotSpot(QPoint(int(20 * s), int(20 * s)))

                # exec_() returns when the drop is finished or cancelled;
                # we don't need the return value but must not fall through
                # to the default handler (that would double-handle the press).
                drag.exec_(Qt.CopyAction)
                event.accept()
                return
            except Exception:
                logging.getLogger("powerflow.crash").exception(
                    "palette mousePressEvent exception")
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

class ShuntButton(ComponentButton):
    KIND = "Shunt"; LABEL = "电容/电抗  Shunt"; COLOR = "#208080"


class ComponentPalette(QWidget):
    """Container panel holding the six component buttons."""
    def __init__(self, parent=None):
        super().__init__(parent)
        s = theme.ui_scale()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(int(10 * s), int(10 * s),
                                  int(10 * s), int(10 * s))
        layout.setSpacing(int(6 * s))

        title = QLabel("元件库")
        title.setStyleSheet(f"font-weight:bold;"
                            f" font-size:{theme.base_font_point_size() + 2:.1f}pt;"
                            f" color:{theme.TEXT};")
        layout.addWidget(title)

        hint = QLabel("按住拖到画布")
        hint.setStyleSheet(f"color:{theme.MUTED};"
                           f" font-size:{theme.base_font_point_size() - 1:.1f}pt;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = [cls() for cls in (BusButton, GenButton, LoadButton,
                                     TrafoButton, ImpedanceButton, ShuntButton)]
        for b in buttons:
            layout.addWidget(b)
        layout.addStretch(1)

        # 宽度按"最长按钮文字实际需要的像素"来定, 而不是写死 px —— 字号
        # 随界面倍数变化(4K 上会放大), 写死宽度会把"阻抗 Impedance"截断。
        need = max(b.sizeHint().width() for b in buttons)
        margins = layout.contentsMargins().left() + layout.contentsMargins().right()
        self.setMinimumWidth(need + margins + int(4 * s))
        self.setMaximumWidth(max(int(280 * s), need + margins + int(60 * s)))
