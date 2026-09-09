"""
canvas.py — 画布(QGraphicsView + QGraphicsScene)
支持元件拖拽, 连线, 删除, 选中, 属性编辑联动
"""
from __future__ import annotations
import uuid
from typing import Dict, Optional, List

from PyQt5.QtCore import Qt, QPointF, QRectF, pyqtSignal
from PyQt5.QtGui import (
    QBrush, QPen, QColor, QPainter, QPainterPath, QFont, QPolygonF
)
from PyQt5.QtWidgets import (
    QGraphicsScene, QGraphicsView, QGraphicsItem,
    QGraphicsEllipseItem, QGraphicsRectItem, QGraphicsPathItem,
    QGraphicsTextItem
)

from solver import (
    Network, BusNode, GenUnit, LoadUnit,
    LineBranch, TrafoBranch, ImpedanceBranch
)


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


def voltage_color(v_pu: Optional[float]) -> QColor:
    if v_pu is None:
        return COLOR_BUS_FILL
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
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsScenePositionChanges, True)
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
        # 移动元件时, 同步所有连接线
        if change == QGraphicsItem.ItemPositionHasChanged:
            for c in self._connections:
                c.refresh()
        return super().itemChange(change, value)

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
        pen = QPen(COLOR_BUS, 2)
        if self.isSelected():
            pen = QPen(Qt.red, 3)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, 6, 6)
        # 画 "≡" 母线符号
        painter.setPen(QPen(Qt.black, 2))
        for i in range(3):
            y = self.H * 0.3 + i * (self.H * 0.2)
            painter.drawLine(self.W * 0.15, y, self.W * 0.85, y)
        # 电压数值
        if v_pu is not None:
            txt = f"{v_pu:.3f} pu"
        else:
            txt = f"{self.model.vn_kv:.0f} kV"
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
        pen = QPen(COLOR_GEN, 2)
        if self.isSelected():
            pen = QPen(Qt.red, 3)
        painter.setPen(pen)
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
        rect = QRectF(4, 4, self.W - 8, self.H - 8)
        painter.setBrush(QBrush(COLOR_LOAD_FILL))
        pen = QPen(COLOR_LOAD, 2)
        if self.isSelected():
            pen = QPen(Qt.red, 3)
        painter.setPen(pen)
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
        painter.drawLine(0, self.H / 2, self.W, self.H / 2)
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
            scene_pos = self.mapToScene(event.pos())
            x, y = float(scene_pos.x()), float(scene_pos.y())
            if not (x == x and y == y):  # NaN guard
                event.ignore()
                return
            comp = self.scene().add_component(kind, x, y)
        except Exception:
            import traceback
            with open("crash.log", "a", encoding="utf-8") as f:
                f.write("\n=== dropEvent exception ===\n")
                traceback.print_exc(file=f)
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

    # ---- Mouse interaction ----
    def mousePressEvent(self, event):
        # Left-click on a port -> start drawing a connection
        if event.button() == Qt.LeftButton:
            item = self.itemAt(event.pos())
            if isinstance(item, PortItem):
                self._start_connection_from_port(item, event)
                event.accept()
                return
        super().mousePressEvent(event)

    def _start_connection_from_port(self, port, event):
        """Initialise rubber-band state for drawing a connection from `port`."""
        self._pending_port = port
        path = QPainterPath(port.scenePos())
        path.lineTo(self.mapToScene(event.pos()))
        self._rubber_line = QGraphicsPathItem(path)
        self._rubber_line.setPen(QPen(Qt.darkGray, 1.5))
        self._rubber_line.setZValue(-2)
        self.scene().addItem(self._rubber_line)

    def mouseMoveEvent(self, event):
        if self._pending_port and self._rubber_line:
            path = QPainterPath(self._pending_port.scenePos())
            path.lineTo(self.mapToScene(event.pos()))
            self._rubber_line.setPath(path)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._pending_port and self._rubber_line:
            # Find the item under the cursor, but make sure we hit an
            # actual port — PortItem mouseMove events from dragging the
            # cursor over the source port itself should not self-connect.
            target = self.itemAt(event.pos())
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
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
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
        self.setSceneRect(0, 0, 2000, 1400)
        self._comp_by_uid: Dict[str, BaseComponent] = {}
        self._connections: List[ConnectionItem] = []
        self.selection_changed_handler = None
        self._view = None  # set by set_view() after construction

    def set_view(self, view):
        """Optional back-reference so drop / scene events can reach the view
        (and through it, the status bar)."""
        self._view = view

    def _on_drop(self, comp, kind):
        """Hook called by CircuitView.dropEvent after a successful drop."""
        name = getattr(comp.model, "name", "?")
        if self._view is not None and hasattr(self._view, "window"):
            win = self._view.window()
            if hasattr(win, "status"):
                win.status.showMessage(f"已创建 {kind}  {name}", 3000)

    # ------- 元件创建 / 删除 -------
    def add_component(self, kind: str, x: float, y: float, name: Optional[str] = None):
        uid = uuid.uuid4().hex[:8]
        if kind == "Bus":
            name = name or f"B{len(self.network.buses)+1}"
            model = BusNode(uid=uid, name=name, x=x, y=y, vn_kv=110.0)
            self.network.buses[uid] = model
            item = BusItem(model)
        elif kind == "Gen":
            name = name or f"G{len(self.network.gens)+1}"
            # 默认挂到最近母线
            bus_uid = self._nearest_bus(x, y)
            if bus_uid is None:
                bus_uid = self._ensure_first_bus(x, y)
            model = GenUnit(uid=uid, name=name, bus_uid=bus_uid, p_mw=50, vm_pu=1.0)
            self.network.gens[uid] = model
            item = GenItem(model)
        elif kind == "Load":
            name = name or f"L{len(self.network.loads)+1}"
            bus_uid = self._nearest_bus(x, y)
            if bus_uid is None:
                bus_uid = self._ensure_first_bus(x, y)
            model = LoadUnit(uid=uid, name=name, bus_uid=bus_uid, p_mw=10, q_mvar=5)
            self.network.loads[uid] = model
            item = LoadItem(model)
        elif kind == "Trafo":
            name = name or f"T{len(self.network.trafos)+1}"
            uid_a = self._nearest_bus(x, y - 30)
            uid_b = self._nearest_bus(x, y + 30)
            if uid_a is None or uid_b is None:
                a, b = self._ensure_two_buses(x, y)
                uid_a, uid_b = a, b
            model = TrafoBranch(
                uid=uid, name=name, hv_bus=uid_a, lv_bus=uid_b,
                sn_mva=63, vn_hv_kv=110, vn_lv_kv=35
            )
            self.network.trafos[uid] = model
            item = TrafoItem(model)
        elif kind == "Impedance":
            name = name or f"Z{len(self.network.impedances)+1}"
            uid_a = self._nearest_bus(x, y - 30)
            uid_b = self._nearest_bus(x, y + 30)
            if uid_a is None or uid_b is None:
                a, b = self._ensure_two_buses(x, y)
                uid_a, uid_b = a, b
            model = ImpedanceBranch(
                uid=uid, name=name, from_bus=uid_a, to_bus=uid_b
            )
            self.network.impedances[uid] = model
            item = ImpedanceItem(model)
        else:
            raise ValueError(f"未知元件种类: {kind}")

        item.setPos(x, y)
        self.addItem(item)
        self._comp_by_uid[uid] = item

        # gen / load 自动创建到母线的"内部连接"记录(只是拓扑关系, 不画线)
        if kind in ("Gen", "Load"):
            self._register_internal_link(item, model.bus_uid)

        return item

    def _ensure_first_bus(self, x, y):
        # 没有任何母线时自动建一个
        return self.add_component("Bus", x, y, name="B1").model.uid

    def _ensure_two_buses(self, x, y):
        a = self.add_component("Bus", x - 30, y - 30, name=f"B{len(self.network.buses)+1}").model.uid
        b = self.add_component("Bus", x + 30, y + 30, name=f"B{len(self.network.buses)+1}").model.uid
        return a, b

    def _nearest_bus(self, x, y, max_dist=200):
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
                          b_item: BaseComponent, b_port: PortItem):
        # 只支持两个母线/线路元件之间的连接
        if not isinstance(a_item, (BusItem, LineCompItem)) or \
           not isinstance(b_item, (BusItem, LineCompItem)):
            return
        # 母线对母线 → 线路
        if isinstance(a_item, BusItem) and isinstance(b_item, BusItem):
            uid = uuid.uuid4().hex[:8]
            model = LineBranch(
                uid=uid, name=f"L{len(self.network.lines)+1}",
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
        # 母线 ↔ LineComp(变压器/阻抗) 视为连接到 LineComp 的一端, 不建新数据, 但记录端口映射
        elif isinstance(a_item, BusItem) and isinstance(b_item, LineCompItem):
            a_item.model.uid  # 母线 uid
            # 这种端口连接只是把"哪端挂到哪条母线"信息固化下来
            comp = b_item
            # 左端 p1 -> hv, 右端 p2 -> lv (trafo) / from(imp)
            if a_port.port_id == "p1" or a_port.port_id == "left" or a_port.port_id == "top":
                # 母线接到了 comp 的左侧/上方
                if comp.model.KIND == "Trafo" if hasattr(comp.model, "KIND") else False:
                    pass
                # 直接通过端口位置判断: a 在 comp 左边就接到 comp 的 p1 端口
                # 我们把母线 uid 写进 model 字段
                if comp.model.KIND == "Trafo" or isinstance(comp, TrafoItem):
                    if a_port.scenePos().x() < comp.scenePos().x() + comp.W / 2:
                        comp.model.hv_bus = a_item.model.uid
                    else:
                        comp.model.lv_bus = a_item.model.uid
                else:
                    if a_port.scenePos().x() < comp.scenePos().x() + comp.W / 2:
                        comp.model.from_bus = a_item.model.uid
                    else:
                        comp.model.to_bus = a_item.model.uid
            else:
                if isinstance(comp, TrafoItem):
                    if a_port.scenePos().x() < comp.scenePos().x() + comp.W / 2:
                        comp.model.hv_bus = a_item.model.uid
                    else:
                        comp.model.lv_bus = a_item.model.uid
                else:
                    if a_port.scenePos().x() < comp.scenePos().x() + comp.W / 2:
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
        else:
            return

        self.addItem(conn)
        a_item.register_connection(conn)
        b_item.register_connection(conn)
        self._connections.append(conn)

    def delete_item(self, item):
        if isinstance(item, BaseComponent):
            uid = item.model.uid
            # 按 item 类型判断, 不要读 model.KIND (dataclass 上没有这个属性)
            if isinstance(item, BusItem):
                kind = "Bus"
            elif isinstance(item, GenItem):
                kind = "Gen"
            elif isinstance(item, LoadItem):
                kind = "Load"
            elif isinstance(item, TrafoItem):
                kind = "Trafo"
            elif isinstance(item, ImpedanceItem):
                kind = "Impedance"
            else:
                kind = "Base"
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
                # 关联到这个母线的 gen/load 也要删
                if hasattr(self, "_internal_links") and uid in self._internal_links:
                    for child_uid in self._internal_links[uid]:
                        for d in (self.network.gens, self.network.loads, self.network.trafos, self.network.impedances):
                            if child_uid in d:
                                d.pop(child_uid)
                                break
                # 关联到这个母线的线路/变压器/阻抗也要删
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

    def _purge_branches_on_bus(self, bus_uid):
        for d in (self.network.lines, self.network.trafos, self.network.impedances):
            to_del = [uid for uid, br in d.items()
                      if (getattr(br, "from_bus", "") == bus_uid or
                          getattr(br, "to_bus", "") == bus_uid or
                          getattr(br, "hv_bus", "") == bus_uid or
                          getattr(br, "lv_bus", "") == bus_uid)]
            for uid in to_del:
                d.pop(uid, None)

    # ------- 结果可视化刷新 -------
    def refresh_results(self):
        # 重画所有 BusItem (更新电压色)
        for uid, item in self._comp_by_uid.items():
            if isinstance(item, BusItem):
                item.update()
        # 更新所有连线标签(显示 P/Q / loading)
        for c in self._connections:
            if c.kind == "Line" and c.uid and c.uid in self.network.lines:
                ln = self.network.lines[c.uid]
                loading = self.network.line_loading_percent.get(c.uid)
                p_from = self.network.line_p_from_mw.get(c.uid)
                q_from = self.network.line_q_from_mvar.get(c.uid)
                if loading is not None and p_from is not None:
                    txt = f"{p_from:.1f}MW\n{loading:.0f}%"
                else:
                    txt = ln.name
                c._label.setPlainText(txt)
                # 重定位标签
                c.refresh()
