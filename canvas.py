"""
canvas.py — 画布(QGraphicsView + QGraphicsScene)
支持元件拖拽, 连线, 删除, 选中, 属性编辑联动
"""
from __future__ import annotations
import logging
import uuid
from typing import Dict, Optional, List

from PyQt5.QtCore import Qt, QPointF, QRectF, QLineF, pyqtSignal
from PyQt5.QtGui import (
    QBrush, QPen, QColor, QPainter, QPainterPath, QFont, QPolygonF, QPixmap
)
from PyQt5.QtWidgets import (
    QGraphicsScene, QGraphicsView, QGraphicsItem,
    QGraphicsEllipseItem, QGraphicsPathItem,
    QGraphicsTextItem, QMenu, QInputDialog
)

from solver import (
    Network, BusNode, GenUnit, LoadUnit,
    LineBranch, TrafoBranch, ImpedanceBranch, ShuntUnit
)
import defaults as D


# ============================================================
# 颜色与样式
# ============================================================

COLOR_BUS = QColor(60, 120, 200)
COLOR_BUS_FILL = QColor(220, 235, 250)
COLOR_GEN = QColor(220, 100, 30)
COLOR_GEN_FILL = QColor(255, 230, 210)
COLOR_LOAD = QColor(80, 160, 80)
COLOR_LOAD_FILL = QColor(225, 245, 225)
COLOR_LINE = QColor(80, 80, 80)
COLOR_TRAFO = QColor(150, 60, 150)
COLOR_IMP = QColor(120, 120, 60)

# 结果可视化阈值: 统一由 defaults 提供, 避免画布/属性面板/结果表各写一套
V_NORMAL = D.V_NORMAL_PU
V_WARN_LOW = D.V_WARN_LOW_PU
V_WARN_HIGH = D.V_WARN_HIGH_PU
V_HIGH = D.V_HIGH_PU

# 负载率阈值 (%)
LOADING_WARN = D.LOADING_WARN_PERCENT
LOADING_CRIT = D.LOADING_CRIT_PERCENT

COLOR_LOADING_OK = QColor(80, 150, 80)       # < 80%: 绿
COLOR_LOADING_WARN = QColor(220, 170, 40)    # 80-100%: 黄
COLOR_LOADING_CRIT = QColor(210, 60, 60)     # >= 100%: 红

COLOR_BUS_ISOLATED = QColor(205, 205, 205)   # 孤立母线(NaN 电压): 灰
COLOR_BUS_DC = QColor(215, 220, 235)         # DC 模式下电压幅值无意义: 中性蓝灰
COLOR_V_OVER_LIMIT = QColor(255, 150, 150)   # 严重越限
COLOR_V_OVER = QColor(255, 230, 150)         # 越限
COLOR_V_NORMAL = QColor(200, 240, 200)       # 正常

# ---- 复用的绘图对象 ----
# paint() 以前每帧都 new 一批 QPen/QBrush/QFont, 118 母线规模下是高频
# 分配 + GC 压力。这里全提成常量(除 QFont 见 _font 缓存)。
_PEN_BUS = QPen(COLOR_BUS, 2)
_PEN_GEN = QPen(COLOR_GEN, 2)
_PEN_LOAD = QPen(COLOR_LOAD, 2)
_PEN_SHUNT = QPen(QColor(32, 128, 128), 2)
_PEN_BLACK_2 = QPen(Qt.black, 2)
_PEN_BLACK_1 = QPen(Qt.black, 1)
_PEN_DARKGRAY = QPen(Qt.darkGray, 1.5)
_PEN_GRID = QPen(QColor(225, 225, 225), 1)
_PEN_LINECOMP = QPen(COLOR_LINE, 3)
_PEN_TRAFO = QPen(COLOR_TRAFO, 3)
_PEN_IMP = QPen(COLOR_IMP, 3)
_PEN_TRAFO_MID = QPen(COLOR_TRAFO, 2)
_PEN_IMP_MID = QPen(COLOR_IMP, 2)
_PEN_LINECOMP_MID = QPen(COLOR_LINE, 2)
_BRUSH_GEN_FILL = QBrush(COLOR_GEN_FILL)
_BRUSH_LOAD_FILL = QBrush(COLOR_LOAD_FILL)
_BRUSH_WHITE = QBrush(Qt.white)

_FONT_CACHE: Dict[tuple, QFont] = {}


def _font(size: int, bold: bool = False) -> QFont:
    """按 (字号, 加粗) 缓存 QFont。

    QFont 依赖字体数据库, 因此不在导入期构造, 首次绘图时再建并复用。
    """
    key = (size, bold)
    f = _FONT_CACHE.get(key)
    if f is None:
        f = QFont("Arial", size, QFont.Bold if bold else QFont.Normal)
        _FONT_CACHE[key] = f
    return f



def loading_color(loading_percent: Optional[float]) -> Optional[QColor]:
    if loading_percent is None or loading_percent != loading_percent:
        return None
    if loading_percent >= LOADING_CRIT:
        return COLOR_LOADING_CRIT
    if loading_percent >= LOADING_WARN:
        return COLOR_LOADING_WARN
    return COLOR_LOADING_OK


COLOR_BUS_ISOLATED = QColor(205, 205, 205)   # 孤立母线(NaN 电压): 灰


def voltage_color(v_pu: Optional[float], dc_mode: bool = False) -> QColor:
    """按电压标幺给母线着色。

    dc_mode=True 时电压幅值恒为 1.0(pandapower 直流潮流不求解幅值),
    此时按"正常/越限"着色会给用户错误暗示, 统一给中性色。
    """
    if dc_mode:
        return COLOR_BUS_DC
    if v_pu is None:
        return COLOR_BUS_FILL
    if v_pu != v_pu:                     # NaN: 未连入电网
        return COLOR_BUS_ISOLATED
    if v_pu < V_WARN_LOW or v_pu > V_HIGH:
        return COLOR_V_OVER_LIMIT        # 严重越限: 红
    if v_pu < V_NORMAL or v_pu > V_WARN_HIGH:
        return COLOR_V_OVER              # 越限: 黄
    return COLOR_V_NORMAL                # 正常: 绿



# ============================================================
# 元件基类
# ============================================================

class PortItem(QGraphicsEllipseItem):
    """A connection port on a component (small circle). 8x8.

    端口自身的鼠标事件**不在这里处理**: CircuitView.mousePressEvent 命中
    端口后会直接接管并 return, 事件的传送链根本走不到这里。以前这里留了
    一个调用 scene.views()[0] 的处理函数 —— 既是死代码, 又埋了一个雷:
    views()[0] 依赖创建顺序(小地图也共享同一个 scene), 谁先建谁就被当成
    "主视图", 顺序一变连线就派发到错误视图; 而且哪天有人补上 super()
    还会双击发(两条橡皮线)。统一由视图层处理, 这里只描述外观。
    """

    def __init__(self, parent_item: "BaseComponent", port_id: str):
        super().__init__(-4, -4, 8, 8, parent_item)
        self.port_id = port_id
        self.setBrush(QBrush(QColor(40, 40, 40)))
        self.setPen(_PEN_BLACK_1)
        self.setFlag(QGraphicsItem.ItemIsSelectable, False)
        self.setCursor(Qt.CrossCursor)
        # Ports must receive mouse events even when the view is in
        # RubberBandDrag mode. RubberBandDrag intercepts mousePress at
        # the QGraphicsView level; without these flags a click on the
        # port would either start a rubber-band or be silently consumed
        # by the parent item — neither of which lets us start drawing
        # a connection line.
        self.setFlag(QGraphicsItem.ItemHasNoContents, False)
        self.setAcceptedMouseButtons(Qt.LeftButton)


class BaseComponent(QGraphicsItem):
    """所有元件的基类"""
    KIND = "Base"
    W = 80
    H = 50
    HAS_PORTS = True

    def __init__(self, model):
        super().__init__()
        self.model = model
        self.setToolTip(model.name)   # hover 提示, 运行后由 refresh_results 充实
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsScenePositionChanges, True)
        self.setAcceptHoverEvents(True)
        self._hovered = False
        # ItemSendsGeometryChanges 才能让 itemChange 收到 ItemPositionHasChanged
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self._ports: Dict[str, PortItem] = {}
        self._connections: List["ConnectionItem"] = []   # 与本元件相连的所有连线
        if self.HAS_PORTS:
            self._init_ports()
        self._label = QGraphicsTextItem(self.model.name, self)
        self._label.setFont(_font(9, bold=True))
        self._label.setDefaultTextColor(Qt.black)
        # 文字位置: 元件下方
        br = self._label.boundingRect()
        self._label.setPos(self.W / 2 - br.width() / 2, self.H + 2)

    def _init_ports(self):
        # 默认: 左右两个端口 (子类可重写)
        self.add_port("p1", self.W, self.H / 2)
        self.add_port("p2", 0, self.H / 2)

    def add_port(self, port_id: str, x: float, y: float):
        p = PortItem(self, port_id)
        p.setPos(x, y)
        self._ports[port_id] = p

    def port_item(self, port_id: str) -> Optional[PortItem]:
        return self._ports.get(port_id)

    def ports(self) -> List[PortItem]:
        return list(self._ports.values())

    def register_connection(self, conn: "ConnectionItem"):
        self._connections.append(conn)

    def unregister_connection(self, conn: "ConnectionItem"):
        if conn in self._connections:
            self._connections.remove(conn)

    def itemChange(self, change, value):
        # 移动元件时, 同步所有连接线, 并把位置回写 model
        # (不回写的话保存/载入会丢掉用户摆好的布局)
        # 注意: 变压器/阻抗的 model 没有 x/y 字段, 回写前先探测
        if change == QGraphicsItem.ItemPositionHasChanged:
            if not self._writeback_suspended():
                if hasattr(self.model, "x"):
                    self.model.x = float(self.pos().x())
                if hasattr(self.model, "y"):
                    self.model.y = float(self.pos().y())
            # 只需刷新一次: 顶层图元移动时 ItemPositionHasChanged 与
            # ItemScenePositionHasChanged 都会到, 以前两个分支各刷一遍,
            # 每条连线被重算两次。
            for c in self._connections:
                c.refresh()
        return super().itemChange(change, value)

    def _writeback_suspended(self) -> bool:
        """程序化重建(载入/撤销恢复)期间禁止坐标回写。

        重建时对"坐标为 0,0"的老元件会做兜底摆放(如 bus.x+20),
        若此时回写 model, 兜底坐标就被当成用户布局存盘 —— 元件位置
        会随着每次载入/撤销无意识漂移。"""
        sc = self.scene()
        return bool(sc is not None and getattr(sc, "_suspend_writeback", False))


    def hoverEnterEvent(self, event):
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    def selection_pen(self, base_pen):
        """选中红色加粗; 悬停加粗一档 (子类绘制时统一走这里)"""
        if self.isSelected():
            return QPen(Qt.red, base_pen.width() + 1)
        if self._hovered:
            pen = QPen(base_pen)
            pen.setWidthF(base_pen.widthF() + 1)
            pen.setStyle(Qt.DashLine)
            return pen
        return base_pen

    def boundingRect(self):
        return QRectF(0, 0, self.W, self.H + 22)

    def shape(self):
        path = QPainterPath()
        path.addRect(QRectF(0, 0, self.W, self.H))
        return path

    def paint(self, painter, option, widget):
        # 子类重写
        pass

    def update_label(self, name: str):
        self.model.name = name
        self._label.setPlainText(name)
        br = self._label.boundingRect()
        self._label.setPos(self.W / 2 - br.width() / 2, self.H + 2)


# ============================================================
# 具体元件类
# ============================================================

class BusItem(BaseComponent):
    KIND = "Bus"
    HAS_PORTS = True

    def __init__(self, model: BusNode):
        super().__init__(model)
        self._v_label = QGraphicsTextItem("", self)
        self._v_label.setDefaultTextColor(QColor(0, 80, 0))
        self._v_label.setFont(_font(8))

    def _init_ports(self):
        # 母线: 4 个端口 (左/右/上/下)
        self.add_port("left", 0, self.H / 2)
        self.add_port("right", self.W, self.H / 2)
        self.add_port("top", self.W / 2, 0)
        self.add_port("bottom", self.W / 2, self.H)

    def paint(self, painter, option, widget):
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0, 0, self.W, self.H)
        # 计算填色(基于电压)
        sc = self.scene()
        net = sc.network if sc is not None else None
        v_pu = net.bus_voltage_pu.get(self.model.uid) if net is not None else None
        dc_mode = bool(net is not None and net.result_kind == "dc")
        painter.setBrush(voltage_color(v_pu, dc_mode=dc_mode))
        painter.setPen(self.selection_pen(_PEN_BUS))
        painter.drawRoundedRect(rect, 6, 6)
        # 画 "≡" 母线符号 — use QLineF so float coords work
        painter.setPen(_PEN_BLACK_2)
        for i in range(3):
            y = self.H * 0.3 + i * (self.H * 0.2)
            painter.drawLine(QLineF(self.W * 0.15, y, self.W * 0.85, y))
        # 电压数值 (标签模式可在视图菜单切换 pu / kV)
        if dc_mode:
            txt = "DC"               # 直流潮流下电压幅值无物理意义
        elif v_pu is None:
            txt = f"{self.model.vn_kv:.0f} kV"
        elif v_pu != v_pu:
            txt = "未连通"          # NaN: 孤立母线
        elif getattr(sc, "v_label_mode", "pu") == "kv":
            txt = f"{v_pu * self.model.vn_kv:.1f} kV"
        else:
            txt = f"{v_pu:.3f} pu"
        self._v_label.setPlainText(txt)
        br = self._v_label.boundingRect()
        self._v_label.setPos(self.W / 2 - br.width() / 2, -16)

    def update_results(self):
        self.update()


class GenItem(BaseComponent):
    KIND = "Gen"
    HAS_PORTS = True

    def _init_ports(self):
        self.add_port("out", self.W / 2, self.H)

    def paint(self, painter, option, widget):
        painter.setRenderHint(QPainter.Antialiasing)
        # 发电机: 圆圈 + 内部 "G"
        r = min(self.W, self.H) / 2 - 4
        cx, cy = self.W / 2, self.H / 2
        painter.setBrush(_BRUSH_GEN_FILL)
        painter.setPen(self.selection_pen(_PEN_GEN))
        painter.drawEllipse(QPointF(cx, cy), r, r)
        painter.setPen(_PEN_BLACK_2)
        painter.setFont(_font(12, bold=True))
        painter.drawText(QRectF(0, 0, self.W, self.H), Qt.AlignCenter, "G")


class LoadItem(BaseComponent):
    KIND = "Load"
    HAS_PORTS = True

    def _init_ports(self):
        self.add_port("in", self.W / 2, 0)

    def paint(self, painter, option, widget):
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(_BRUSH_LOAD_FILL)
        painter.setPen(self.selection_pen(_PEN_LOAD))
        # 负荷: 三角形 (▽)
        poly = QPolygonF([
            QPointF(self.W / 2, self.H - 4),
            QPointF(4, 4),
            QPointF(self.W - 4, 4),
        ])
        painter.drawPolygon(poly)
        painter.setPen(_PEN_BLACK_2)
        painter.setFont(_font(10, bold=True))
        painter.drawText(QRectF(0, 0, self.W, self.H), Qt.AlignCenter, "L")


class LineCompItem(BaseComponent):
    """中间连线型元件(变压器 / 阻抗), 画为水平连线 + 中间方块"""
    KIND = "Base"
    HAS_PORTS = True
    LINE_COLOR = COLOR_LINE
    SYMBOL = "?"
    _base_pen = _PEN_LINECOMP
    _mid_pen = _PEN_LINECOMP_MID

    def _init_ports(self):
        self.add_port("p1", 0, self.H / 2)
        self.add_port("p2", self.W, self.H / 2)

    def paint(self, painter, option, widget):
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(self.selection_pen(self._base_pen))
        # Use QLineF so the (H/2) float coordinate works under PyQt5
        # (the (int, int, int, int) overload rejects float in PyQt5).
        painter.drawLine(QLineF(0, self.H / 2, self.W, self.H / 2))
        # 中间标识
        rect = QRectF(self.W * 0.35, 4, self.W * 0.30, self.H - 8)
        painter.setBrush(_BRUSH_WHITE)
        painter.setPen(self._mid_pen)
        painter.drawRect(rect)
        painter.setPen(_PEN_BLACK_1)
        painter.setFont(_font(9, bold=True))
        painter.drawText(rect, Qt.AlignCenter, self.SYMBOL)


class TrafoItem(LineCompItem):
    KIND = "Trafo"
    LINE_COLOR = COLOR_TRAFO
    SYMBOL = "T"
    _base_pen = _PEN_TRAFO
    _mid_pen = _PEN_TRAFO_MID


class ImpedanceItem(LineCompItem):
    KIND = "Impedance"
    LINE_COLOR = COLOR_IMP
    SYMBOL = "Z"
    _base_pen = _PEN_IMP
    _mid_pen = _PEN_IMP_MID


class ShuntItem(BaseComponent):
    """并联电容/电抗器: 两条水平极板 + 引线"""
    KIND = "Shunt"
    HAS_PORTS = True
    COLOR = QColor(32, 128, 128)
    FILL = QColor(216, 240, 240)

    def _init_ports(self):
        self.add_port("in", self.W / 2, 0)   # 上端接母线

    def paint(self, painter, option, widget):
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(self.selection_pen(_PEN_SHUNT))
        # 引线: 顶部中点 → 极板区
        plate_top = self.H * 0.45
        painter.drawLine(QLineF(self.W / 2, 0, self.W / 2, plate_top - 7))
        painter.drawLine(QLineF(self.W / 2, self.H, self.W / 2, plate_top + 7))
        # 两块极板 (电容器符号)
        w2 = self.W * 0.5
        painter.drawLine(QLineF(self.W / 2 - w2 / 2, plate_top - 7,
                                self.W / 2 + w2 / 2, plate_top - 7))
        painter.drawLine(QLineF(self.W / 2 - w2 / 2, plate_top + 7,
                                self.W / 2 + w2 / 2, plate_top + 7))


def kind_of(item) -> str:
    """元件种类名 — 统一 isinstance 判断链的单一来源"""
    if isinstance(item, BusItem):
        return "Bus"
    if isinstance(item, GenItem):
        return "Gen"
    if isinstance(item, LoadItem):
        return "Load"
    if isinstance(item, TrafoItem):
        return "Trafo"
    if isinstance(item, ImpedanceItem):
        return "Impedance"
    if isinstance(item, ShuntItem):
        return "Shunt"
    return "Base"


# ============================================================
# 连接线
# ============================================================

class ConnectionItem(QGraphicsPathItem):
    """两个端口之间的一条连线"""
    def __init__(self, a_comp: BaseComponent, a_port: PortItem,
                 b_comp: BaseComponent, b_port: PortItem):
        super().__init__()
        self.a_comp = a_comp
        self.a_port = a_port
        self.b_comp = b_comp
        self.b_port = b_port
        # 判断是哪类连接: 母线-母线 是线路; 母线-母线 通过变压器/阻抗也是对应分支
        self.kind = "Line"   # 默认; 由 canvas 在创建后根据两端元件修正
        self.uid: Optional[str] = None   # 创建后由 canvas 写入 net.lines / trafos / imp 的 uid
        self.rebind_prev_bus: Optional[str] = None   # Visual 连线: 建连前的挂接母线
        self.loading_color: Optional[QColor] = None   # 运行潮流后按负载率着色
        self._label = QGraphicsTextItem("", self)
        self._label.setDefaultTextColor(Qt.darkMagenta)
        self._label.setFont(_font(7, bold=True))
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setZValue(-1)   # 置于元件之下
        self.refresh()

    def refresh(self):
        # 取两端端口的绝对坐标
        if not self.a_port.scene() or not self.b_port.scene():
            return
        p1 = self.a_port.scenePos()
        p2 = self.b_port.scenePos()
        path = QPainterPath(p1)
        path.lineTo(p2)
        self.setPath(path)
        # 更新标签位置: 线段中点偏上
        mid = QPointF((p1.x() + p2.x()) / 2, (p1.y() + p2.y()) / 2 - 8)
        self._label.setPos(mid)

    def paint(self, painter, option, widget):
        pen_color = COLOR_LINE
        if self.kind == "Trafo":
            pen_color = COLOR_TRAFO
        elif self.kind == "Impedance":
            pen_color = COLOR_IMP
        if self.loading_color is not None:
            pen_color = self.loading_color
        pen = QPen(pen_color, 2.5)
        if self.isSelected():
            pen = QPen(Qt.red, 3.5)
        painter.setPen(pen)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.drawPath(self.path())


# ============================================================
# 画布视图
# ============================================================

class CircuitView(QGraphicsView):
    """QGraphicsView subclass: handles mouse interaction AND drops from
    the left-side component palette."""
    element_selected = pyqtSignal(object)   # selected component/connection model, or None

    def __init__(self, scene: QGraphicsScene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.Antialiasing)
        # IMPORTANT: do NOT use RubberBandDrag. With RubberBandDrag active,
        # the QGraphicsView intercepts mousePress at the view level before
        # our overrides can see it, and clicks on PortItem either start a
        # rubber-band selection or get silently consumed. We do selection
        # manually by clicking components instead. ScrollDrag is the right
        # mode here: middle-button drag pans, no automatic selection box.
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setMouseTracking(True)
        self._pending_port: Optional[PortItem] = None
        self._rubber_line: Optional[QGraphicsPathItem] = None
        # Accept drops from the component palette.
        self.setAcceptDrops(True)
        self._drag_kind: Optional[str] = None  # kind while a drag is over us
        self._press_positions: Optional[Dict[str, tuple]] = None  # 按下时各元件坐标

    # ---- Drag and drop ----
    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-powerflow-component"):
            kind = bytes(event.mimeData().data("application/x-powerflow-component")).decode("utf-8")
            self._drag_kind = kind
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat("application/x-powerflow-component"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._drag_kind = None
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        # Prefer the cached kind from dragEnter, but fall back to reading
        # the MIME payload directly — that way, if any drag event was lost
        # (focus change, modifier toggle), we still recover the kind.
        kind = self._drag_kind
        if not kind and event.mimeData().hasFormat("application/x-powerflow-component"):
            kind = bytes(event.mimeData().data("application/x-powerflow-component")).decode("utf-8")
        self._drag_kind = None
        if not kind:
            event.ignore()
            return
        try:
            scene_pos = self.mapToScene(event.pos().toPoint() if hasattr(event.pos(), "toPoint") else event.pos())
            x, y = float(scene_pos.x()), float(scene_pos.y())
            if not (x == x and y == y):  # NaN guard
                event.ignore()
                return
            comp = self.scene().add_component(kind, x, y)
        except Exception:
            logging.getLogger("powerflow.crash").exception("dropEvent exception")
            event.ignore()
            return
        self.scene().clearSelection()
        if comp is not None:
            comp.setSelected(True)
            event.acceptProposedAction()
            if hasattr(self.scene(), "_on_drop"):
                self.scene()._on_drop(comp, kind)
        else:
            event.ignore()

    # ---- 缩放 ----
    ZOOM_MIN = 0.3
    ZOOM_MAX = 4.0
    ZOOM_STEP = 1.15

    def wheelEvent(self, event):
        """滚轮以光标为锚缩放画布(平移仍用中键拖拽)"""
        angle = event.angleDelta().y()
        if angle == 0:
            super().wheelEvent(event)
            return
        factor = self.ZOOM_STEP if angle > 0 else 1.0 / self.ZOOM_STEP
        cur = self.transform().m11()
        if not (self.ZOOM_MIN <= cur * factor <= self.ZOOM_MAX):
            return
        anchor = self.transformationAnchor()
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.scale(factor, factor)
        self.setTransformationAnchor(anchor)

    def fit_view(self) -> None:
        """缩放到正好看到全部元件"""
        if self.scene() is None or not self.scene().items():
            self.resetTransform()
            return
        rect = self.scene().itemsBoundingRect().adjusted(-60, -60, 60, 60)
        self.fitInView(rect, Qt.KeepAspectRatio)

    def zoom_in(self):
        self._zoom_step(self.ZOOM_STEP)

    def zoom_out(self):
        self._zoom_step(1.0 / self.ZOOM_STEP)

    def _zoom_step(self, factor: float):
        cur = self.transform().m11()
        if not (self.ZOOM_MIN <= cur * factor <= self.ZOOM_MAX):
            return
        anchor = self.transformationAnchor()
        self.setTransformationAnchor(QGraphicsView.AnchorViewCenter)
        self.scale(factor, factor)
        self.setTransformationAnchor(anchor)

    # ---- Mouse interaction ----
    def mousePressEvent(self, event):
        # Left-click on a port -> start drawing a connection
        if event.button() == Qt.LeftButton:
            # QGraphicsView.itemAt() takes QPoint (int). event.pos()
            # returns QPointF in PyQt5; convert if needed.
            pt = event.pos()
            item = self.itemAt(pt.toPoint() if hasattr(pt, "toPoint") else pt)
            if isinstance(item, PortItem):
                self._start_connection_from_port(item, event)
                event.accept()
                return
            if item is None:
                # 点空白处: 取消选中 (否则选中状态没有取消途径)
                self.scene().clearSelection()
            # 按下时不再全量序列化整张网络 —— 点空白、点选也要付这个代价
            # 太亏(118 母线下每点一次都是一次深拷贝+JSON)。这里只记下
            # 可能被拖动的那几个元件的坐标, 释放时若真的动了, 再用这份
            # 坐标把快照里的位置改回去, 序列化最多发生一次。
            self._press_positions = self._record_move_positions(item)
        super().mousePressEvent(event)

    def _record_move_positions(self, item) -> Dict[str, tuple]:
        """记下本次按下可能被拖动的元件的当前 (x, y)"""
        comps = [it for it in self.scene().selectedItems()
                 if isinstance(it, BaseComponent)]
        if item is not None and not isinstance(item, BaseComponent):
            parent = item.parentItem()      # 点在名称标签/装饰子项上
            if isinstance(parent, BaseComponent):
                item = parent
        if isinstance(item, BaseComponent) and item not in comps:
            comps.append(item)
        out: Dict[str, tuple] = {}
        for it in comps:
            m = it.model
            if hasattr(m, "x") and hasattr(m, "y"):
                out[m.uid] = (float(m.x), float(m.y))
        return out

    def _has_moved(self, positions: Dict[str, tuple]) -> bool:
        net = self.scene().network
        for key in ("buses", "gens", "loads", "trafos", "impedances", "shunts"):
            for m in getattr(net, key, {}).values():
                rec = positions.get(getattr(m, "uid", None))
                if rec is not None and hasattr(m, "x"):
                    if (float(m.x), float(m.y)) != rec:
                        return True
        return False

    @staticmethod
    def _patch_snapshot(snapshot: dict, positions: Dict[str, tuple]) -> dict:
        """把快照里若干元件的位置改回按下前的坐标(浅拷贝, 不污染原快照)"""
        out = {}
        for key, val in snapshot.items():
            out[key] = [dict(e) for e in val] if isinstance(val, list) else val
        for entries in out.values():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                pos = positions.get(entry.get("uid"))
                if pos is not None:
                    entry["x"], entry["y"] = pos
        return out

    def _start_connection_from_port(self, port, event):
        """Initialise rubber-band state for drawing a connection from `port`."""
        self._cleanup_pending_connection()   # 防上次残留的中间态
        self._pending_port = port
        path = QPainterPath(port.scenePos())
        # QGraphicsView.mapToScene accepts QPoint (int). event.pos() can be
        # QPoint or QPointF depending on PyQt5 version, so normalise.
        path.lineTo(self.mapToScene(event.pos().toPoint() if hasattr(event.pos(), "toPoint") else event.pos()))
        self._rubber_line = QGraphicsPathItem(path)
        self._rubber_line.setPen(_PEN_DARKGRAY)
        self._rubber_line.setZValue(-2)
        self.scene().addItem(self._rubber_line)

    def _cleanup_pending_connection(self) -> None:
        """原子地清理拖线中间态(橡皮线 + 起始端口)。

        以前只在 "_pending_port 与 _rubber_line 同时非空" 时才清理,
        异常路径下 _pending_port 会残留, 下一次在端口上按下就多出
        一条泄漏的橡皮线。释放 / ESC / 重新开始 三处共用这一个出口。
        """
        line = self._rubber_line
        self._rubber_line = None
        self._pending_port = None
        if line is not None and line.scene() is not None:
            self.scene().removeItem(line)


    def mouseMoveEvent(self, event):
        if self._pending_port and self._rubber_line:
            path = QPainterPath(self._pending_port.scenePos())
            path.lineTo(self.mapToScene(event.pos().toPoint() if hasattr(event.pos(), "toPoint") else event.pos()))
            self._rubber_line.setPath(path)
            event.accept()
            return
        # 状态栏光标画布坐标
        win = self.window()
        if hasattr(win, "_update_coords"):
            sp = self.mapToScene(event.pos().toPoint()
                                 if hasattr(event.pos(), "toPoint") else event.pos())
            win._update_coords(sp.x(), sp.y())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._pending_port and self._rubber_line:
            # Find the item under the cursor. Ports are tiny (8x8),
            # so the user often releases on the component body instead
            # of the port dot itself. If we hit a BaseComponent, walk
            # to its nearest port to the cursor; only ignore if we hit
            # something unrelated (e.g. another rubber-line, empty area).
            target = self._find_release_target(QPointF(event.pos()))
            if isinstance(target, PortItem) and target is not self._pending_port:
                # Find the two components the ports belong to
                a_item = self._pending_port.parentItem()
                b_item = target.parentItem()
                if a_item is not b_item:
                    self.scene().create_connection(a_item, self._pending_port, b_item, target)
            self._cleanup_pending_connection()
            event.accept()
            return
        # 普通点击/拖动结束: 只有真的发生位移才把移动作为一次撤销入栈
        if self._press_positions:
            win = self.window()
            if hasattr(win, "snapshot_network") and hasattr(win, "push_move_undo") \
                    and self._has_moved(self._press_positions):
                after = win.snapshot_network()
                before = self._patch_snapshot(after, self._press_positions)
                win.push_move_undo(before, after)
            self._press_positions = None
        super().mouseReleaseEvent(event)

    def _find_release_target(self, view_pos):
        """Resolve the item under the cursor at the moment of release.
        If the user released on the component body (a BaseComponent) rather
        than on a port dot, return the nearest PortItem on that component.
        If they released on a label or some other child, walk up to the
        owning component first.
        """
        scene_pt = self.mapToScene(view_pos.toPoint() if hasattr(view_pos, "toPoint") else view_pos)
        # QGraphicsView.itemAt takes QPoint (int) — round QPointF if needed.
        vp = view_pos.toPoint() if hasattr(view_pos, "toPoint") else view_pos
        item = self.itemAt(vp)
        # Common case: user released on the port itself.
        if isinstance(item, PortItem):
            return item
        # Walk up to the owning BaseComponent for non-port hits (label
        # text, decoration rectangles, etc. all live as children).
        comp = item
        if comp is not None and not isinstance(comp, BaseComponent):
            comp = comp.parentItem()
        # Snap to the nearest port on that component.
        if isinstance(comp, BaseComponent):
            best_port = None
            best_dist = None
            for port_id, port in comp._ports.items():
                port_scene = port.scenePos()
                dx = port_scene.x() - scene_pt.x()
                dy = port_scene.y() - scene_pt.y()
                d2 = dx * dx + dy * dy
                if best_dist is None or d2 < best_dist:
                    best_dist = d2
                    best_port = port
            # Accept ports within the snap radius of the cursor (covers
            # port dots and the immediate area around them).
            if best_port is not None and best_dist is not None \
                    and best_dist <= D.PORT_SNAP_DIST ** 2:
                return best_port
        return item

    def contextMenuEvent(self, event):
        """右键菜单: 元件=重命名/删除, 连线=删除, 空白=运行潮流/适配视图"""
        menu = QMenu(self)
        item = self.itemAt(event.pos())
        # 右键落在端口小圆点上时 itemAt 返回 PortItem —— 它既不是元件也不是
        # 连线, 以前会掉进"空白"分支弹出与上下文无关的菜单。上溯到元件。
        if isinstance(item, PortItem):
            parent = item.parentItem()
            if isinstance(parent, BaseComponent):
                item = parent
        win = self.window()
        # 多选时的对齐/分布
        sel_comps = [it for it in self.scene().selectedItems()
                     if isinstance(it, BaseComponent)]
        if len(sel_comps) >= 2:
            align_menu = menu.addMenu("对齐/分布")
            before_snap = (win.snapshot_network()
                           if hasattr(win, "snapshot_network") else None)

            def _do_align(mode, _before=before_snap):
                n = apply_alignment(self.scene(), mode)
                if _before is not None and hasattr(win, "push_move_undo"):
                    win.push_move_undo(_before, win.snapshot_network())
                if hasattr(win, "status"):
                    win.status.showMessage(f"已对齐 {n} 个元件", 3000)

            align_menu.addAction("水平中线", lambda: _do_align("hcenter"))
            align_menu.addAction("垂直中线", lambda: _do_align("vcenter"))
            align_menu.addAction("左对齐", lambda: _do_align("left"))
            align_menu.addAction("右对齐", lambda: _do_align("right"))
            align_menu.addAction("顶对齐", lambda: _do_align("top"))
            align_menu.addAction("底对齐", lambda: _do_align("bottom"))
            if len(sel_comps) >= 3:
                align_menu.addAction("横向等间距", lambda: _do_align("dist_h"))
                align_menu.addAction("纵向等间距", lambda: _do_align("dist_v"))
        if isinstance(item, BaseComponent):
            def _rename():
                before = (win.snapshot_network()
                          if hasattr(win, "snapshot_network") else None)
                text, ok = QInputDialog.getText(
                    self, "重命名", "新名称:", text=item.model.name)
                if ok and text.strip():
                    item.update_label(text.strip())
                    if before is not None and hasattr(win, "push_move_undo"):
                        win.push_move_undo(before, win.snapshot_network(),
                                           label="重命名")
            act_rename = menu.addAction(f"重命名 {item.model.name}")
            act_rename.triggered.connect(_rename)
            act_del = menu.addAction("删除")
            act_del.triggered.connect(lambda: self.scene().delete_item(item))
        elif isinstance(item, ConnectionItem):
            act_del = menu.addAction("删除连线")
            act_del.triggered.connect(lambda: self.scene().delete_item(item))
        else:
            if hasattr(win, "_run_power_flow"):
                act_run = menu.addAction("▶ 运行潮流")
                act_run.triggered.connect(win._run_power_flow)
            act_fit = menu.addAction("⤢ 适配视图")
            act_fit.triggered.connect(self.fit_view)
        if menu.actions():
            menu.exec_(event.globalPos())
        super().contextMenuEvent(event)

    def keyPressEvent(self, event):
        # ESC: 正在拖连线则取消拖拽, 否则取消选中
        if event.key() == Qt.Key_Escape:
            if self._pending_port or self._rubber_line:
                self._cleanup_pending_connection()
                event.accept()
                return
            if self.scene().selectedItems():
                self.scene().clearSelection()
                event.accept()
                return
        # Delete / Backspace deletes the selection
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            for it in list(self.scene().selectedItems()):
                self.scene().delete_item(it)
            event.accept()
            return
        super().keyPressEvent(event)


# ============================================================
# 画布场景
# ============================================================

class CircuitScene(QGraphicsScene):
    """维护 Network + 所有 QGraphicsItem"""
    component_class_map = {
        "Bus": BusItem,
        "Gen": GenItem,
        "Load": LoadItem,
        "Trafo": TrafoItem,
        "Impedance": ImpedanceItem,
        "Line": None,   # 线路不是元件, 由两母线连线生成
    }

    def __init__(self, network: Network, parent=None):
        super().__init__(parent)
        self.network = network
        self.setSceneRect(0, 0, D.CANVAS_WIDTH, D.CANVAS_HEIGHT)
        self._comp_by_uid: Dict[str, BaseComponent] = {}
        self._connections: List[ConnectionItem] = []
        self._name_seq: Dict[str, int] = {}   # 显示名计数器, 按 prefix 分池
        self._clipboard = None                # copy_selection 的内容
        self.snap_enabled = False             # 网格对齐开关(影响新放置的元件)
        self.v_label_mode = "pu"              # 母线电压标签: "pu" 或 "kv"
        self.snap_grid = 10.0
        self.selection_changed_handler = None
        self._view = None  # set by set_view() after construction
        # 程序化重建(载入/撤销恢复)期间挂起坐标回写, 防止兜底坐标被写进
        # model 并在下次保存时落盘(元件位置无意识漂移)
        self._suspend_writeback = False
        self._grid_cache = None
        self._grid_cache_key = None
        # 表驱动: kind -> (model 工厂, model 存入的 network 字典, Item 类)
        # 加新元件类型只需: solver 加 dataclass + 这里加一行 + properties 加表单
        self._builders = {
            "Bus": (self._make_bus_model, self.network.buses, BusItem, "B"),
            "Gen": (self._make_gen_model, self.network.gens, GenItem, "G"),
            "Load": (self._make_load_model, self.network.loads, LoadItem, "L"),
            "Trafo": (self._make_trafo_model, self.network.trafos, TrafoItem, "T"),
            "Impedance": (self._make_impedance_model, self.network.impedances, ImpedanceItem, "Z"),
            "Shunt": (self._make_shunt_model, self.network.shunts, ShuntItem, "C"),
        }

    # ---- 各元件的 model 工厂(自动绑定母线) ----
    def _make_bus_model(self, uid: str, name: str, x: float, y: float) -> BusNode:
        return BusNode(uid=uid, name=name, x=x, y=y, vn_kv=D.DEFAULT_VN_KV)

    def _make_gen_model(self, uid: str, name: str, x: float, y: float) -> GenUnit:
        bus_uid = self._nearest_bus(x, y)
        if bus_uid is None:
            bus_uid = self._ensure_first_bus(x, y)
        return GenUnit(uid=uid, name=name, bus_uid=bus_uid)

    def _make_load_model(self, uid: str, name: str, x: float, y: float) -> LoadUnit:
        bus_uid = self._nearest_bus(x, y)
        if bus_uid is None:
            bus_uid = self._ensure_first_bus(x, y)
        return LoadUnit(uid=uid, name=name, bus_uid=bus_uid)

    def _make_shunt_model(self, uid: str, name: str, x: float, y: float) -> ShuntUnit:
        bus_uid = self._nearest_bus(x, y)
        if bus_uid is None:
            bus_uid = self._ensure_first_bus(x, y)
        return ShuntUnit(uid=uid, name=name, bus_uid=bus_uid)

    def _make_trafo_model(self, uid: str, name: str, x: float, y: float) -> TrafoBranch:
        uid_a = self._nearest_bus(x, y - D.AUTO_BUS_OFFSET)
        uid_b = self._nearest_bus(x, y + D.AUTO_BUS_OFFSET)
        if uid_a is None or uid_b is None or uid_a == uid_b:
            uid_a, uid_b = self._ensure_two_buses(x, y)
        return TrafoBranch(uid=uid, name=name, hv_bus=uid_a, lv_bus=uid_b)

    def _make_impedance_model(self, uid: str, name: str, x: float, y: float) -> ImpedanceBranch:
        uid_a = self._nearest_bus(x, y - D.AUTO_BUS_OFFSET)
        uid_b = self._nearest_bus(x, y + D.AUTO_BUS_OFFSET)
        if uid_a is None or uid_b is None or uid_a == uid_b:
            uid_a, uid_b = self._ensure_two_buses(x, y)
        return ImpedanceBranch(uid=uid, name=name, from_bus=uid_a, to_bus=uid_b)

    # ------- 元件创建 / 删除 -------
    def _next_name(self, prefix: str, container: dict, seq_key: Optional[str] = None) -> str:
        """生成不与 container 内现有显示名冲突的 prefix+N。

        不能用 len(container)+1: 删除元件后再建会与现存元件重名,
        例如建 L1、L2, 删 L1, 再建一条会再次叫 L2。
        """
        key = seq_key or prefix
        used = {m.name for m in container.values()}
        n = self._name_seq.get(key, 0)
        while True:
            n += 1
            candidate = f"{prefix}{n}"
            if candidate not in used:
                self._name_seq[key] = n
                return candidate

    def drawBackground(self, painter, rect):
        """网格对齐开启时绘制背景参考线。

        网格以前是每帧现算现画: 2000×1400 / 50px ≈ 1120 条 QLineF,
        平移或缩放的每一帧都来一遍。现在预渲染到一张 QPixmap,
        只有 sceneRect / 网格粒度变化时才重建。
        """
        super().drawBackground(painter, rect)
        if not self.snap_enabled:
            return
        painter.drawPixmap(self.sceneRect().topLeft(), self._grid_pixmap())

    def _grid_pixmap(self):
        r = self.sceneRect()
        key = (r.width(), r.height(), self.snap_grid)
        if self._grid_cache is not None and self._grid_cache_key == key:
            return self._grid_cache
        step = self.snap_grid * 5   # 50px 一格, 10px 吸附粒度太密不适合画线
        w, h = max(1, int(r.width())), max(1, int(r.height()))
        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setPen(_PEN_GRID)
        x = 0.0
        while x <= w:
            p.drawLine(QLineF(x, 0, x, h))
            x += step
        y = 0.0
        while y <= h:
            p.drawLine(QLineF(0, y, w, y))
            y += step
        p.end()
        self._grid_cache = pm
        self._grid_cache_key = key
        return pm

    def snap_point(self, x: float, y: float) -> tuple:
        """网格对齐开启时把坐标吸附到网格; 关闭时原样返回"""
        if not self.snap_enabled:
            return x, y
        g = self.snap_grid
        return round(x / g) * g, round(y / g) * g

    def add_component(self, kind: str, x: float, y: float,
                      name: Optional[str] = None) -> BaseComponent:
        if kind not in self._builders:
            raise ValueError(f"未知元件种类: {kind}")
        make_model, container, item_cls, prefix = self._builders[kind]
        uid = uuid.uuid4().hex[:8]
        name = name or self._next_name(prefix, container)
        # 界外坐标收回画布内(留出元件自身的宽高), 再做网格对齐
        x = min(max(x, 0.0), max(0.0, D.CANVAS_WIDTH - item_cls.W))
        y = min(max(y, 0.0), max(0.0, D.CANVAS_HEIGHT - item_cls.H))
        x, y = self.snap_point(x, y)
        model = make_model(uid, name, x, y)
        container[uid] = model
        item = item_cls(model)
        item.setPos(x, y)
        self.addItem(item)
        self._comp_by_uid[uid] = item

        return item

    def set_view(self, view) -> None:
        """Optional back-reference so drop / scene events can reach the view
        (and through it, the status bar)."""
        self._view = view

    def _on_drop(self, comp, kind: str) -> None:
        """Hook called by CircuitView.dropEvent after a successful drop."""
        name = getattr(comp.model, "name", "?")
        if self._view is not None and hasattr(self._view, "window"):
            win = self._view.window()
            if hasattr(win, "status"):
                win.status.showMessage(f"已创建 {kind}  {name}", 3000)

    def _ensure_first_bus(self, x, y):
        # 没有任何母线时自动建一个
        return self.add_component("Bus", x, y).model.uid

    def _ensure_two_buses(self, x, y):
        a = self.add_component("Bus", x - D.AUTO_BUS_OFFSET, y - D.AUTO_BUS_OFFSET).model.uid
        b = self.add_component("Bus", x + D.AUTO_BUS_OFFSET, y + D.AUTO_BUS_OFFSET).model.uid
        if a == b:   # 两次都吸附到了同一条母线: 强制新建一条
            b = self.add_component("Bus", x + 2 * D.AUTO_BUS_OFFSET, y + D.AUTO_BUS_OFFSET).model.uid
        return a, b

    def _nearest_bus(self, x, y, max_dist=D.NEAREST_BUS_DIST):
        best = None
        best_d = max_dist ** 2
        for uid, b in self.network.buses.items():
            d = (b.x - x) ** 2 + (b.y - y) ** 2
            if d < best_d:
                best_d = d
                best = uid
        return best

    def _register_internal_link(self, comp_item: BaseComponent, bus_uid: str):
        """[兼容保留] Gen/Load 与母线的挂接关系现在由 network 实时派生,
        无需登记; 保留此方法只为不破坏既有调用方。"""
        return

    @property
    def _internal_links(self) -> Dict[str, List[str]]:
        """挂在每条母线上的 Gen/Load —— 由 network 实时派生的视图。

        旧实现是一份手工维护的缓存: 删元件、改挂接母线时必须记得同步,
        否则留下失效 uid (删母线时会做一次无害却无意义的 _remove_component)。
        改成派生视图后永远与 network 一致, 不存在"忘了同步"这类 bug。
        """
        links: Dict[str, List[str]] = {}
        for uid, g in self.network.gens.items():
            links.setdefault(g.bus_uid, []).append(uid)
        for uid, l in self.network.loads.items():
            links.setdefault(l.bus_uid, []).append(uid)
        return links

    def create_connection(self, a_item: BaseComponent, a_port: PortItem,
                          b_item: BaseComponent, b_port: PortItem) -> Optional[ConnectionItem]:
        # Reject self-loops immediately
        if a_item is b_item:
            return
        # 同一对端口上重复拉线: 以前每次都会新建一条 Line 并写进
        # network.lines, 手一抖就是一条"看不见的并联线路"。这里挡掉,
        # 想要真正并联的两回线请用母线上不同的端口连。
        for c in self._connections:
            if {id(c.a_port), id(c.b_port)} == {id(a_port), id(b_port)}:
                if self._view is not None:
                    win = self._view.window()
                    if hasattr(win, "status"):
                        win.status.showMessage("这两个端口之间已经有连线了", 3000)
                return None
        # Gen / Load are bound to a bus at add-time (bus_uid on the model).
        # User-drawn lines to / from them are visual hints — the topology
        # mutation below only runs for real Bus <-> Bus, Bus <-> LineComp
        # and LineComp <-> LineComp pairs. Everything else still gets a
        # visible connection line for feedback, but the user's bus_uid
        # binding stays as-is.
        # 母线对母线 → 线路
        if isinstance(a_item, BusItem) and isinstance(b_item, BusItem):
            uid = uuid.uuid4().hex[:8]
            model = LineBranch(
                uid=uid, name=self._next_name("L", self.network.lines, seq_key="Line"),
                from_bus=a_item.model.uid, to_bus=b_item.model.uid
            )
            self.network.lines[uid] = model
            conn = ConnectionItem(a_item, a_port, b_item, b_port)
            conn.kind = "Line"
            conn.uid = uid
            # 更新 model 中的 from/to 顺序: from 端口在左/上 视为 from
            from_pos = a_port.scenePos()
            to_pos = b_port.scenePos()
            if from_pos.x() > to_pos.x() or (from_pos.x() == to_pos.x() and from_pos.y() > to_pos.y()):
                model.from_bus, model.to_bus = model.to_bus, model.from_bus
                conn.a_port, conn.b_port = conn.b_port, conn.a_port
                conn.a_comp, conn.b_comp = conn.b_comp, conn.a_comp
        # 母线 ↔ LineComp(变压器/阻抗): 把母线 uid 固化到 LineComp 的对应字段
        elif isinstance(a_item, BusItem) and isinstance(b_item, LineCompItem):
            comp = b_item
            # 按母线相对 LineComp 的位置决定接哪一端: 左半边接 hv/from, 右半边接 lv/to
            bus_on_left = a_port.scenePos().x() < comp.scenePos().x() + comp.W / 2
            if isinstance(comp, TrafoItem):
                if bus_on_left:
                    comp.model.hv_bus = a_item.model.uid
                else:
                    comp.model.lv_bus = a_item.model.uid
            else:
                if bus_on_left:
                    comp.model.from_bus = a_item.model.uid
                else:
                    comp.model.to_bus = a_item.model.uid
            conn = ConnectionItem(a_item, a_port, b_item, b_port)
            conn.kind = "Trafo" if isinstance(b_item, TrafoItem) else "Impedance"
            conn.uid = b_item.model.uid
            conn.refresh()
        elif isinstance(a_item, LineCompItem) and isinstance(b_item, BusItem):
            # 反过来, 递归
            self.create_connection(b_item, b_port, a_item, a_port)
            return
        elif isinstance(a_item, LineCompItem) and isinstance(b_item, LineCompItem):
            # Trafo <-> Trafo, Trafo <-> Impedance, Impedance <-> Impedance
            # are not standard power-system topologies. Draw the line so
            # the user gets visual feedback, but skip the topology mutation.
            conn = ConnectionItem(a_item, a_port, b_item, b_port)
            conn.kind = "LineComp-Link"
            conn.uid = ""
        else:
            # Gen/Load/Shunt 拖线连到母线: 把挂接关系转正 (bus_uid 改写)。
            # 以前这种线只是视觉装饰, 用户把 Gen 拖线连到 B3, 实际拓扑
            # 还挂在 B1 —— 语义陷阱; 而且从 Shunt 拖线压根不在匹配范围,
            # 视觉在 B2、拓扑还在 B1。现在统一真正改挂接母线, 并把改前的
            # 母线记在连线上, 删除该连线时对称地退回去。
            mover = None
            bus = None
            for x_item, y_item in ((a_item, b_item), (b_item, a_item)):
                if isinstance(x_item, (GenItem, LoadItem, ShuntItem)) \
                        and isinstance(y_item, BusItem):
                    mover, bus = x_item, y_item
                    break
            prev_bus = None
            if mover is not None:
                prev_bus = mover.model.bus_uid
                self._rebind_genload_to_bus(mover, bus)
            conn = ConnectionItem(a_item, a_port, b_item, b_port)
            conn.kind = "Visual"
            conn.uid = ""
            conn.rebind_prev_bus = prev_bus

        self.addItem(conn)
        a_item.register_connection(conn)
        b_item.register_connection(conn)
        self._connections.append(conn)

    def _rebind_genload_to_bus(self, comp_item, bus_item) -> None:
        """把 Gen/Load/Shunt 的挂接母线改到 bus_item (拖线换母线的拓扑转正)

        `_internal_links` 由 network 派生, 改完 model 即自动一致, 无需同步。
        """
        old_uid = comp_item.model.bus_uid
        new_uid = bus_item.model.uid
        if old_uid == new_uid:
            return
        comp_item.model.bus_uid = new_uid
        if self._view is not None:
            win = self._view.window()
            if hasattr(win, "status"):
                win.status.showMessage(
                    f"{comp_item.model.name} 已改挂到 {bus_item.model.name}", 3000)

    # ------- 复制 / 粘贴 -------
    def copy_selection(self) -> int:
        """把选中元件(不含连线)放入内部剪贴板, 返回复制的元件数"""
        import dataclasses
        sel = [it for it in self.selectedItems() if isinstance(it, BaseComponent)]
        if not sel:
            return 0
        self._clipboard = {
            "components": [(kind_of(it), dataclasses.asdict(it.model))
                           for it in sel]
        }
        return len(sel)

    def paste_clipboard(self, offset: tuple = (40, 40)) -> int:
        """粘贴剪贴板元件: 新 uid / 顺延名字 / 位置偏移。

        两端都在复制集内的线路会一并复制并重连; 挂接母线若也被复制
        则自动改挂到副本母线, 否则仍挂原母线。
        """
        if not getattr(self, "_clipboard", None):
            return 0
        model_cls = {"Bus": BusNode, "Gen": GenUnit, "Load": LoadUnit,
                     "Trafo": TrafoBranch, "Impedance": ImpedanceBranch,
                     "Shunt": ShuntUnit}
        containers = {"Bus": self.network.buses, "Gen": self.network.gens,
                      "Load": self.network.loads,
                      "Trafo": self.network.trafos,
                      "Impedance": self.network.impedances,
                      "Shunt": self.network.shunts}
        uid_map: Dict[str, str] = {}
        new_items: List[BaseComponent] = []
        for kind, raw in self._clipboard["components"]:
            mdict = dict(raw)   # 不改剪贴板原件, 否则二次粘贴坐标/名字会被污染
            container = containers[kind]
            item_cls = self._builders[kind][2]
            prefix = self._builders[kind][3]
            old_uid = mdict.get("uid")
            new_uid = uuid.uuid4().hex[:8]
            uid_map[old_uid] = new_uid
            mdict["uid"] = new_uid
            mdict["name"] = self._next_name(prefix, container)
            mdict["x"] = mdict.get("x", 0.0) + offset[0]
            mdict["y"] = mdict.get("y", 0.0) + offset[1]
            model = model_cls[kind](**mdict)
            if kind in ("Gen", "Load", "Shunt"):
                model.bus_uid = uid_map.get(model.bus_uid, model.bus_uid)
            elif kind == "Trafo":
                model.hv_bus = uid_map.get(model.hv_bus, model.hv_bus)
                model.lv_bus = uid_map.get(model.lv_bus, model.lv_bus)
            elif kind == "Impedance":
                model.from_bus = uid_map.get(model.from_bus, model.from_bus)
                model.to_bus = uid_map.get(model.to_bus, model.to_bus)
            container[new_uid] = model
            item = item_cls(model)
            item.setPos(model.x, model.y)
            self.addItem(item)
            self._comp_by_uid[new_uid] = item
            new_items.append(item)
        # 两端都在复制集内的线路: 复制模型并重建连线
        for ln in list(self.network.lines.values()):
            if ln.from_bus in uid_map and ln.to_bus in uid_map:
                new_line = LineBranch(
                    uid=uuid.uuid4().hex[:8],
                    name=self._next_name("L", self.network.lines, seq_key="Line"),
                    from_bus=uid_map[ln.from_bus],
                    to_bus=uid_map[ln.to_bus])
                self.network.lines[new_line.uid] = new_line
                a = self._comp_by_uid[new_line.from_bus]
                b = self._comp_by_uid[new_line.to_bus]
                conn = ConnectionItem(a, a.port_item("right"),
                                      b, b.port_item("left"))
                conn.kind = "Line"
                conn.uid = new_line.uid
                a.register_connection(conn)
                b.register_connection(conn)
                self.addItem(conn)
                self._connections.append(conn)
        self.clearSelection()
        for it in new_items:
            it.setSelected(True)
        return len(new_items)

    def delete_item(self, item) -> None:
        if isinstance(item, BaseComponent):
            uid = item.model.uid
            kind = kind_of(item)
            # 删所有相关连接
            for c in list(self._connections):
                if c.a_comp is item or c.b_comp is item:
                    c.a_comp.unregister_connection(c)
                    c.b_comp.unregister_connection(c)
                    self.removeItem(c)
                    if c in self._connections:
                        self._connections.remove(c)
            # 从 network 删
            if kind == "Bus":
                self.network.buses.pop(uid, None)
                # 挂接在这个母线上的 gen/load: model 和图形项一起删
                # (_internal_links 由 network 派生, 此时 gens/loads 仍指向
                #  该母线 uid, 因此派生视图依然能列出它们)
                for child_uid in list(self._internal_links.get(uid, [])):
                    self._remove_component(child_uid)
                # 以该母线为端点的线路/变压器/阻抗: model 和图形项一起删
                self._purge_branches_on_bus(uid)
                # 挂在该母线上的电容/电抗同样级联删除(model+图形项), 防孤儿引用
                for sh_uid in [u for u, sh in self.network.shunts.items()
                               if sh.bus_uid == uid]:
                    self._remove_component(sh_uid)
            elif kind == "Gen":
                self.network.gens.pop(uid, None)
            elif kind == "Load":
                self.network.loads.pop(uid, None)
            elif kind == "Trafo":
                self.network.trafos.pop(uid, None)
            elif kind == "Impedance":
                self.network.impedances.pop(uid, None)
            elif kind == "Shunt":
                self.network.shunts.pop(uid, None)
            self._comp_by_uid.pop(uid, None)
            self.removeItem(item)
        elif isinstance(item, ConnectionItem):
            item.a_comp.unregister_connection(item)
            item.b_comp.unregister_connection(item)
            if item.uid and item.kind == "Line":
                self.network.lines.pop(item.uid, None)
            # 母线↔变压器/阻抗: 少了一侧母线, 整个支路模型不再完整,
            # 一起删掉 (留空引用会导致 存档载入被拒/撤销快照失效/求解报错)
            if item.kind in ("Trafo", "Impedance"):
                comp = item.a_comp
                if not isinstance(comp, (TrafoItem, ImpedanceItem)):
                    comp = item.b_comp
                self.removeItem(item)
                if item in self._connections:
                    self._connections.remove(item)
                self._remove_component(comp.model.uid)
                return
            # 视觉连线(Gen/Load/Shunt ↔ 母线): 建连时把挂接母线改到了另一端,
            # 断连必须对称地退回去 —— 否则界面显示"已断开", 元件却仍挂在
            # 目标母线上参与计算, 用户完全看不出来。
            if item.kind == "Visual" and getattr(item, "rebind_prev_bus", None):
                mover = next((c for c in (item.a_comp, item.b_comp)
                              if isinstance(c, (GenItem, LoadItem, ShuntItem))),
                             None)
                if mover is not None:
                    mover.model.bus_uid = item.rebind_prev_bus
                    item.rebind_prev_bus = None
            self.removeItem(item)
            if item in self._connections:
                self._connections.remove(item)

    def _remove_component(self, comp_uid: str) -> None:
        """删掉一个挂接元件(gen/load/trafo/imp): model 与图形项一起清理。

        只删 model 不删图形项会留下"幽灵元件"——还能选中/编辑,
        却不参与计算, 保存后凭空消失。
        """
        for d in (self.network.gens, self.network.loads,
                  self.network.trafos, self.network.impedances,
                  self.network.shunts):
            if comp_uid in d:
                d.pop(comp_uid)
                break
        item = self._comp_by_uid.pop(comp_uid, None)
        if item is not None:
            self._detach_item_connections(item)
            self.removeItem(item)

    def _detach_item_connections(self, item) -> None:
        """把挂在 item 上的所有连线从场景、对端元件、_connections 里摘除"""
        for c in list(item._connections):
            other = c.b_comp if c.a_comp is item else c.a_comp
            other.unregister_connection(c)
            if c in self._connections:
                self._connections.remove(c)
            if c.scene() is not None:
                self.removeItem(c)
        item._connections.clear()

    def _purge_branches_on_bus(self, bus_uid: str) -> None:
        for d in (self.network.lines, self.network.trafos, self.network.impedances):
            to_del = [uid for uid, br in d.items()
                      if (getattr(br, "from_bus", "") == bus_uid or
                          getattr(br, "to_bus", "") == bus_uid or
                          getattr(br, "hv_bus", "") == bus_uid or
                          getattr(br, "lv_bus", "") == bus_uid)]
            for uid in to_del:
                d.pop(uid, None)
                # 变压器/阻抗有图形项, 一并清掉; 线路没有独立图形项
                if uid in self._comp_by_uid:
                    self._remove_component(uid)

    # ------- 结果可视化刷新 -------
    def refresh_results(self) -> None:
        """按最新结果重绘现有图元。

        注意这里**不重算连线几何**: 结果只改连线标签文字与颜色, 端点位置
        没变, 旧版每条连线都调一次 refresh() 纯属白算(N-1 循环里会被反复
        触发)。几何变化由 itemChange 负责。
        """
        # 重画所有 BusItem (更新电压色), 并更新 hover 提示
        for uid, item in self._comp_by_uid.items():
            if isinstance(item, BusItem):
                item.update()
            elif isinstance(item, (GenItem, LoadItem)):
                m = item.model
                if isinstance(item, GenItem):
                    p = self.network.gen_p_mw.get(uid)
                    q = self.network.gen_q_mvar.get(uid)
                    tip = (f"{m.name}\n设定 P={m.p_mw:.1f} MW, V={m.vm_pu:.3f} pu"
                           + (f"\n实际 P={p:+.2f} MW, Q={q:+.2f} Mvar"
                              if p is not None else ""))
                else:
                    v = self.network.bus_voltage_pu.get(m.bus_uid)
                    tip = (f"{m.name}\nP={m.p_mw:.1f} MW, Q={m.q_mvar:.1f} Mvar"
                           + (f"\n母线电压 {v:.4f} pu" if v is not None else ""))
                item.setToolTip(tip)
        # 更新所有连线标签(显示 P/Q / loading) + 按负载率着色
        for c in self._connections:
            if c.kind == "Line" and c.uid and c.uid in self.network.lines:
                ln = self.network.lines[c.uid]
                loading = self.network.line_loading_percent.get(c.uid)
                p_from = self.network.line_p_from_mw.get(c.uid)
                q_from = self.network.line_q_from_mvar.get(c.uid)
                txt = (f"{p_from:.1f}MW\n{loading:.0f}%"
                       if loading is not None and p_from is not None else ln.name)
                c._label.setPlainText(txt)
                c.setToolTip(f"线路 {ln.name}"
                             + (f"\nP={p_from:.2f} MW, Q={q_from:+.2f} Mvar"
                                f"\n负载率 {loading:.1f}%"
                                if loading is not None and p_from is not None else ""))
                c.loading_color = loading_color(loading)
                c.update()
            elif c.kind == "Trafo" and c.uid and c.uid in self.network.trafos:
                loading = self.network.trafo_loading_percent.get(c.uid)
                p_hv = self.network.trafo_p_hv_mw.get(c.uid)
                txt = (f"{p_hv:.1f}MW\n{loading:.0f}%"
                       if loading is not None and p_hv is not None else "")
                if txt:
                    c._label.setPlainText(txt)
                c.setToolTip(f"变压器 {self.network.trafos[c.uid].name}"
                             + (f"\n高压侧 P={p_hv:+.2f} MW\n负载率 {loading:.1f}%"
                                if loading is not None and p_hv is not None else ""))
                c.loading_color = loading_color(loading)
                c.update()


def apply_alignment(scene, mode: str) -> int:
    """对齐/分布选中的元件 (右键菜单调用)。mode:
    left/right/top/bottom/hcenter/vcenter/dist_h/dist_v
    返回参与数量, 不足时不动作。
    """
    items = [it for it in scene.selectedItems() if isinstance(it, BaseComponent)]
    n = len(items)
    if n < 2:
        return n
    if mode == "left":
        x = min(it.x() for it in items)
        for it in items:
            it.setPos(x, it.y())
    elif mode == "right":
        x = max(it.x() + it.W for it in items)
        for it in items:
            it.setPos(x - it.W, it.y())
    elif mode == "top":
        y = min(it.y() for it in items)
        for it in items:
            it.setPos(it.x(), y)
    elif mode == "bottom":
        y = max(it.y() + it.H for it in items)
        for it in items:
            it.setPos(it.x(), y - it.H)
    elif mode == "hcenter":
        cy = sum(it.y() + it.H / 2 for it in items) / n
        for it in items:
            it.setPos(it.x(), cy - it.H / 2)
    elif mode == "vcenter":
        cx = sum(it.x() + it.W / 2 for it in items) / n
        for it in items:
            it.setPos(cx - it.W / 2, it.y())
    elif mode in ("dist_h", "dist_v"):
        if n < 3:
            return n
        key = (lambda it: it.x()) if mode == "dist_h" else (lambda it: it.y())
        ordered = sorted(items, key=key)
        lo, hi = key(ordered[0]), key(ordered[-1])
        step = (hi - lo) / (n - 1)
        for i, it in enumerate(ordered):
            if mode == "dist_h":
                it.setPos(lo + i * step, it.y())
            else:
                it.setPos(it.x(), lo + i * step)
    else:
        raise ValueError(f"未知对齐模式: {mode}")
    return n
