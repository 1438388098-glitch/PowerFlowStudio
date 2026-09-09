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
    QBrush, QPen, QColor, QPainter, QPainterPath, QFont, QPolygonF
)
from PyQt5.QtWidgets import (
    QGraphicsScene, QGraphicsView, QGraphicsItem,
    QGraphicsEllipseItem, QGraphicsPathItem,
    QGraphicsTextItem, QMenu, QInputDialog
)

from solver import (
    Network, BusNode, GenUnit, LoadUnit,
    LineBranch, TrafoBranch, ImpedanceBranch
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

# 结果可视化: 电压标幺阈值
V_NORMAL = 0.95
V_WARN_LOW = 0.90
V_WARN_HIGH = 1.05
V_HIGH = 1.10

# 负载率阈值 (%)
LOADING_WARN = 80.0
LOADING_CRIT = 100.0

COLOR_LOADING_OK = QColor(80, 150, 80)       # < 80%: 绿
COLOR_LOADING_WARN = QColor(220, 170, 40)    # 80-100%: 黄
COLOR_LOADING_CRIT = QColor(210, 60, 60)     # >= 100%: 红


def loading_color(loading_percent: Optional[float]) -> Optional[QColor]:
    if loading_percent is None or loading_percent != loading_percent:
        return None
    if loading_percent >= LOADING_CRIT:
        return COLOR_LOADING_CRIT
    if loading_percent >= LOADING_WARN:
        return COLOR_LOADING_WARN
    return COLOR_LOADING_OK


COLOR_BUS_ISOLATED = QColor(205, 205, 205)   # 孤立母线(NaN 电压): 灰


def voltage_color(v_pu: Optional[float]) -> QColor:
    if v_pu is None:
        return COLOR_BUS_FILL
    if v_pu != v_pu:                     # NaN: 未连入电网
        return COLOR_BUS_ISOLATED
    if v_pu < V_WARN_LOW or v_pu > V_HIGH:
        return QColor(255, 150, 150)   # 严重越限: 红
    if v_pu < V_NORMAL or v_pu > V_WARN_HIGH:
        return QColor(255, 230, 150)   # 越限: 黄
    return QColor(200, 240, 200)       # 正常: 绿


# ============================================================
# 元件基类
# ============================================================

class PortItem(QGraphicsEllipseItem):
    """A connection port on a component (small circle). 8x8."""
    def __init__(self, parent_item: "BaseComponent", port_id: str):
        super().__init__(-4, -4, 8, 8, parent_item)
        self.port_id = port_id
        self.setBrush(QBrush(QColor(40, 40, 40)))
        self.setPen(QPen(Qt.black, 1))
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

    def mousePressEvent(self, event):
        # Hand control back to the view so it can begin drawing a
        # connection. The view looks up its own _pending_port from
        # scene().itemAt() (or directly from this item via the event
        # position), but we set a flag on the scene so the view can
        # find us without ambiguity.
        if event.button() == Qt.LeftButton:
            scene = self.scene()
            view = scene.views()[0] if scene.views() else None
            if view is not None and hasattr(view, "_start_connection_from_port"):
                view._start_connection_from_port(self, event)
                event.accept()
                return
        super().mousePressEvent(event)


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
        f = QFont()
        f.setPointSize(9)
        f.setBold(True)
        self._label.setFont(f)
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
            if hasattr(self.model, "x"):
                self.model.x = float(self.pos().x())
            if hasattr(self.model, "y"):
                self.model.y = float(self.pos().y())
            for c in self._connections:
                c.refresh()
        elif change == QGraphicsItem.ItemScenePositionHasChanged:
            for c in self._connections:
                c.refresh()
        return super().itemChange(change, value)

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
        f = QFont(); f.setPointSize(8)
        self._v_label.setFont(f)

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
        v_pu = self.scene().network.bus_voltage_pu.get(self.model.uid) if self.scene() else None
        fill = voltage_color(v_pu)
        painter.setBrush(fill)
        painter.setPen(self.selection_pen(QPen(COLOR_BUS, 2)))
        painter.drawRoundedRect(rect, 6, 6)
        # 画 "≡" 母线符号 — use QLineF so float coords work
        painter.setPen(QPen(Qt.black, 2))
        for i in range(3):
            y = self.H * 0.3 + i * (self.H * 0.2)
            painter.drawLine(QLineF(self.W * 0.15, y, self.W * 0.85, y))
        # 电压数值 (标签模式可在视图菜单切换 pu / kV)
        if v_pu is None:
            txt = f"{self.model.vn_kv:.0f} kV"
        elif v_pu != v_pu:
            txt = "未连通"          # NaN: 孤立母线
        elif getattr(self.scene(), "v_label_mode", "pu") == "kv":
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
        painter.setBrush(QBrush(COLOR_GEN_FILL))
        painter.setPen(self.selection_pen(QPen(COLOR_GEN, 2)))
        painter.drawEllipse(QPointF(cx, cy), r, r)
        painter.setPen(QPen(Qt.black, 2))
        painter.setFont(QFont("Arial", 12, QFont.Bold))
        painter.drawText(QRectF(0, 0, self.W, self.H), Qt.AlignCenter, "G")


class LoadItem(BaseComponent):
    KIND = "Load"
    HAS_PORTS = True

    def _init_ports(self):
        self.add_port("in", self.W / 2, 0)

    def paint(self, painter, option, widget):
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QBrush(COLOR_LOAD_FILL))
        painter.setPen(self.selection_pen(QPen(COLOR_LOAD, 2)))
        # 负荷: 三角形 (▽)
        poly = QPolygonF([
            QPointF(self.W / 2, self.H - 4),
            QPointF(4, 4),
            QPointF(self.W - 4, 4),
        ])
        painter.drawPolygon(poly)
        painter.setPen(QPen(Qt.black, 2))
        painter.setFont(QFont("Arial", 10, QFont.Bold))
        painter.drawText(QRectF(0, 0, self.W, self.H), Qt.AlignCenter, "L")


class LineCompItem(BaseComponent):
    """中间连线型元件(变压器 / 阻抗), 画为水平连线 + 中间方块"""
    KIND = "Base"
    HAS_PORTS = True

    def _init_ports(self):
        self.add_port("p1", 0, self.H / 2)
        self.add_port("p2", self.W, self.H / 2)

    def paint(self, painter, option, widget):
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(self.LINE_COLOR, 3))
        if self.isSelected():
            painter.setPen(QPen(Qt.red, 4))
        # Use QLineF so the (H/2) float coordinate works under PyQt5
        # (the (int, int, int, int) overload rejects float in PyQt5).
        painter.drawLine(QLineF(0, self.H / 2, self.W, self.H / 2))
        # 中间标识
        rect = QRectF(self.W * 0.35, 4, self.W * 0.30, self.H - 8)
        painter.setBrush(QBrush(Qt.white))
        painter.setPen(QPen(self.LINE_COLOR, 2))
        painter.drawRect(rect)
        painter.setPen(QPen(Qt.black, 1))
        painter.setFont(QFont("Arial", 9, QFont.Bold))
        painter.drawText(rect, Qt.AlignCenter, self.SYMBOL)


class TrafoItem(LineCompItem):
    KIND = "Trafo"
    LINE_COLOR = COLOR_TRAFO
    SYMBOL = "T"


class ImpedanceItem(LineCompItem):
    KIND = "Impedance"
    LINE_COLOR = COLOR_IMP
    SYMBOL = "Z"


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
        self.loading_color: Optional[QColor] = None   # 运行潮流后按负载率着色
        self._label = QGraphicsTextItem("", self)
        self._label.setDefaultTextColor(Qt.darkMagenta)
        f = QFont(); f.setPointSize(7); f.setBold(True)
        self._label.setFont(f)
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
        self._move_before = None   # 拖动前的网络快照, 用于移动撤销

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
            # 记录拖动前快照(移动撤销用); 快照函数由 MainWindow 提供
            self._move_before = None
            win = self.window()
            if hasattr(win, "snapshot_network"):
                self._move_before = win.snapshot_network()
        super().mousePressEvent(event)

    def _start_connection_from_port(self, port, event):
        """Initialise rubber-band state for drawing a connection from `port`."""
        self._pending_port = port
        path = QPainterPath(port.scenePos())
        # QGraphicsView.mapToScene accepts QPoint (int). event.pos() can be
        # QPoint or QPointF depending on PyQt5 version, so normalise.
        path.lineTo(self.mapToScene(event.pos().toPoint() if hasattr(event.pos(), "toPoint") else event.pos()))
        self._rubber_line = QGraphicsPathItem(path)
        self._rubber_line.setPen(QPen(Qt.darkGray, 1.5))
        self._rubber_line.setZValue(-2)
        self.scene().addItem(self._rubber_line)

    def mouseMoveEvent(self, event):
        if self._pending_port and self._rubber_line:
            path = QPainterPath(self._pending_port.scenePos())
            path.lineTo(self.mapToScene(event.pos().toPoint() if hasattr(event.pos(), "toPoint") else event.pos()))
            self._rubber_line.setPath(path)
            event.accept()
            return
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
            self.scene().removeItem(self._rubber_line)
            self._rubber_line = None
            self._pending_port = None
            event.accept()
            return
        # 普通点击/拖动结束: 若元件被移动, 把移动作为一次撤销入栈
        if self._move_before is not None:
            win = self.window()
            if hasattr(win, "push_move_undo"):
                after = win.snapshot_network()
                if after != self._move_before:
                    win.push_move_undo(self._move_before, after)
            self._move_before = None
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
        win = self.window()
        if isinstance(item, BaseComponent):
            def _rename():
                text, ok = QInputDialog.getText(
                    self, "重命名", "新名称:", text=item.model.name)
                if ok and text.strip():
                    item.update_label(text.strip())
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
            if self._pending_port and self._rubber_line:
                self.scene().removeItem(self._rubber_line)
                self._rubber_line = None
                self._pending_port = None
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
        # 表驱动: kind -> (model 工厂, model 存入的 network 字典, Item 类)
        # 加新元件类型只需: solver 加 dataclass + 这里加一行 + properties 加表单
        self._builders = {
            "Bus": (self._make_bus_model, self.network.buses, BusItem, "B"),
            "Gen": (self._make_gen_model, self.network.gens, GenItem, "G"),
            "Load": (self._make_load_model, self.network.loads, LoadItem, "L"),
            "Trafo": (self._make_trafo_model, self.network.trafos, TrafoItem, "T"),
            "Impedance": (self._make_impedance_model, self.network.impedances, ImpedanceItem, "Z"),
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
        """网格对齐开启时绘制背景参考线"""
        super().drawBackground(painter, rect)
        if not self.snap_enabled:
            return
        step = self.snap_grid * 5   # 50px 一格, 10px 吸附粒度太密不适合画线
        painter.setPen(QPen(QColor(225, 225, 225), 1))
        x = int(rect.left() // step) * step
        while x < rect.right():
            painter.drawLine(QLineF(x, rect.top(), x, rect.bottom()))
            x += step
        y = int(rect.top() // step) * step
        while y < rect.bottom():
            painter.drawLine(QLineF(rect.left(), y, rect.right(), y))
            y += step

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

        # gen / load 自动创建到母线的"内部连接"记录(只是拓扑关系, 不画线)
        if kind in ("Gen", "Load"):
            self._register_internal_link(item, model.bus_uid)

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
        """记录 gen/load 与母线的逻辑连接, 用于删除元件时联动清理"""
        if not hasattr(self, "_internal_links"):
            self._internal_links: Dict[str, List[str]] = {}
        self._internal_links.setdefault(bus_uid, []).append(comp_item.model.uid)

    def create_connection(self, a_item: BaseComponent, a_port: PortItem,
                          b_item: BaseComponent, b_port: PortItem) -> Optional[ConnectionItem]:
        # Reject self-loops immediately
        if a_item is b_item:
            return
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
            # Gen/Load 拖线连到母线: 把挂接关系转正 (bus_uid 改写)。
            # 以前这种线只是视觉装饰, 用户把 Gen 拖线连到 B3, 实际拓扑
            # 还挂在 B1 —— 语义陷阱。现在真正改挂接母线。
            genload = None
            bus = None
            for x_item, y_item in ((a_item, b_item), (b_item, a_item)):
                if isinstance(x_item, (GenItem, LoadItem)) and isinstance(y_item, BusItem):
                    genload, bus = x_item, y_item
                    break
            if genload is not None:
                self._rebind_genload_to_bus(genload, bus)
            # Gen / Load: just draw a visual line. Topology is bound
            # by the bus_uid recorded when the component was added.
            conn = ConnectionItem(a_item, a_port, b_item, b_port)
            conn.kind = "Visual"
            conn.uid = ""

        self.addItem(conn)
        a_item.register_connection(conn)
        b_item.register_connection(conn)
        self._connections.append(conn)

    def _rebind_genload_to_bus(self, comp_item, bus_item) -> None:
        """把 Gen/Load 的挂接母线改到 bus_item (拖线换母线的拓扑转正)"""
        old_uid = comp_item.model.bus_uid
        new_uid = bus_item.model.uid
        if old_uid == new_uid:
            return
        comp_item.model.bus_uid = new_uid
        if hasattr(self, "_internal_links"):
            old_list = self._internal_links.get(old_uid)
            if old_list and comp_item.model.uid in old_list:
                old_list.remove(comp_item.model.uid)
            self._internal_links.setdefault(new_uid, []).append(comp_item.model.uid)
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
                     "Trafo": TrafoBranch, "Impedance": ImpedanceBranch}
        containers = {"Bus": self.network.buses, "Gen": self.network.gens,
                      "Load": self.network.loads,
                      "Trafo": self.network.trafos,
                      "Impedance": self.network.impedances}
        uid_map: Dict[str, str] = {}
        new_items: List[BaseComponent] = []
        for kind, mdict in self._clipboard["components"]:
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
            if kind in ("Gen", "Load"):
                model.bus_uid = uid_map.get(model.bus_uid, model.bus_uid)
            container[new_uid] = model
            item = item_cls(model)
            item.setPos(model.x, model.y)
            self.addItem(item)
            self._comp_by_uid[new_uid] = item
            if kind in ("Gen", "Load"):
                self._register_internal_link(item, model.bus_uid)
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
                if hasattr(self, "_internal_links") and uid in self._internal_links:
                    for child_uid in list(self._internal_links[uid]):
                        self._remove_component(child_uid)
                    self._internal_links.pop(uid, None)
                # 以该母线为端点的线路/变压器/阻抗: model 和图形项一起删
                self._purge_branches_on_bus(uid)
            elif kind == "Gen":
                self.network.gens.pop(uid, None)
            elif kind == "Load":
                self.network.loads.pop(uid, None)
            elif kind == "Trafo":
                self.network.trafos.pop(uid, None)
            elif kind == "Impedance":
                self.network.impedances.pop(uid, None)
            self._comp_by_uid.pop(uid, None)
            self.removeItem(item)
        elif isinstance(item, ConnectionItem):
            item.a_comp.unregister_connection(item)
            item.b_comp.unregister_connection(item)
            if item.uid and item.kind == "Line":
                self.network.lines.pop(item.uid, None)
            # 母线↔LineComp: 解除 LineComp 的对应字段
            if item.kind == "Trafo":
                # 不直接删 trafo, 只清掉它接到的母线 uid(避免变成悬空)
                comp = item.a_comp if isinstance(item.a_comp, TrafoItem) else item.b_comp
                bus = item.a_comp if isinstance(item.a_comp, BusItem) else item.b_comp
                if comp.model.hv_bus == bus.model.uid:
                    comp.model.hv_bus = ""
                if comp.model.lv_bus == bus.model.uid:
                    comp.model.lv_bus = ""
            elif item.kind == "Impedance":
                comp = item.a_comp if isinstance(item.a_comp, ImpedanceItem) else item.b_comp
                bus = item.a_comp if isinstance(item.a_comp, BusItem) else item.b_comp
                if comp.model.from_bus == bus.model.uid:
                    comp.model.from_bus = ""
                if comp.model.to_bus == bus.model.uid:
                    comp.model.to_bus = ""
            self.removeItem(item)
            if item in self._connections:
                self._connections.remove(item)

    def _remove_component(self, comp_uid: str) -> None:
        """删掉一个挂接元件(gen/load/trafo/imp): model 与图形项一起清理。

        只删 model 不删图形项会留下"幽灵元件"——还能选中/编辑,
        却不参与计算, 保存后凭空消失。
        """
        for d in (self.network.gens, self.network.loads,
                  self.network.trafos, self.network.impedances):
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
                c.refresh()
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
                c.refresh()
