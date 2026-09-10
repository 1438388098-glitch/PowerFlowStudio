"""
ux.py — 编辑效率增强件 (二期拓展)

- SearchDialog: 元件 + 支路搜索定位 (Ctrl+F)
- MiniMapView + make_minimap_dock: 小地图 (共享 scene 的缩略视图)
"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt, QRectF, pyqtSignal
from PyQt5.QtGui import QPen
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
    QGraphicsView, QDockWidget, QWidget, QVBoxLayout as QVBox
)

import theme
from canvas import kind_of


class SearchDialog(QDialog):
    """元件搜索: 按名称/类型过滤, 双击或回车定位选中。

    搜索源同时覆盖元件与支路连线 —— 以前只遍历 ``_comp_by_uid``,
    线路没有独立图形项, 于是"搜 L1 线路"永远搜不到。
    """
    item_selected = pyqtSignal(str)   # uid

    def __init__(self, scene, parent=None):
        super().__init__(parent)
        s = theme.ui_scale()
        self.setWindowTitle("搜索元件 / 支路 (Ctrl+F)")
        self.resize(int(300 * s), int(420 * s))
        self.setMinimumSize(int(240 * s), int(240 * s))
        self._scene = scene
        layout = QVBoxLayout(self)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("输入名称或类型关键字…")
        self.edit.textChanged.connect(self._filter)
        layout.addWidget(self.edit)
        self.listw = QListWidget()
        self.listw.itemActivated.connect(self._activate)
        self.listw.itemDoubleClicked.connect(self._activate)
        layout.addWidget(self.listw)
        self._populate()

    def _populate(self):
        self.listw.clear()
        net = getattr(self._scene, "network", None)
        seen = set()
        for uid, item in self._scene._comp_by_uid.items():
            kind = kind_of(item)
            name = getattr(item.model, "name", "?")
            li = QListWidgetItem(f"[{kind}] {name}")
            li.setData(Qt.UserRole, uid)
            self.listw.addItem(li)
            seen.add(uid)
        # 支路连线(线路/变压器/阻抗): 名称取自 network, 画布上它们是连线
        for c in list(getattr(self._scene, "_connections", [])):
            uid = getattr(c, "uid", "") or ""
            if not uid or uid in seen:
                continue
            name = self._branch_name(net, c.kind, uid)
            li = QListWidgetItem(f"[{c.kind}] {name}")
            li.setData(Qt.UserRole, uid)
            self.listw.addItem(li)
            seen.add(uid)
        self._filter(self.edit.text())

    @staticmethod
    def _branch_name(net, kind: str, uid: str) -> str:
        if net is None:
            return uid
        try:
            if kind == "Line":
                return net.lines[uid].name
            if kind == "Trafo":
                return net.trafos[uid].name
            if kind == "Impedance":
                return net.impedances[uid].name
        except (KeyError, AttributeError):
            pass
        return uid

    def _filter(self, text):
        text = (text or "").strip().lower()
        for i in range(self.listw.count()):
            li = self.listw.item(i)
            li.setHidden(bool(text) and text not in li.text().lower())

    def _activate(self, li: QListWidgetItem):
        uid = li.data(Qt.UserRole)
        if uid:
            self.item_selected.emit(uid)
            self.accept()

    def refresh(self):
        self._populate()


class MiniMapView(QGraphicsView):
    """小地图: 共享主场景, 只读缩略; 点击/拖动可导航主视图"""
    def __init__(self, scene, main_view, parent=None):
        super().__init__(scene, parent)
        from PyQt5.QtGui import QPainter
        self._main = main_view
        self.setInteractive(False)
        self.setRenderHint(QPainter.Antialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._indicator_poly = None   # 主视图可视范围, drawForeground 叠加绘制
        self.refresh()

    def refresh(self):
        """适配场景内容 + 更新主视图可视范围指示框"""
        sc = self.scene()
        if sc is None:
            return
        self.resetTransform()
        content = sc.itemsBoundingRect().adjusted(-80, -80, 80, 80)
        if content.width() <= 0 or content.height() <= 0:
            content = QRectF(0, 0, 800, 600)
        self.setSceneRect(content)
        self.fitInView(content, Qt.KeepAspectRatio)
        # 指示框存为多边形, 在本视图 drawForeground 叠加绘制:
        # 不进共享场景 → 导出 PNG/SVG 与适配视图不受红框污染,
        # 也不必每 600ms 从场景增删图形项
        if self._main is not None and self._main.scene() is sc:
            visible = self._main.mapToScene(self._main.viewport().rect()).boundingRect()
            from PyQt5.QtGui import QPolygonF
            self._indicator_poly = QPolygonF(visible)
        else:
            self._indicator_poly = None
        self.viewport().update()

    def drawForeground(self, painter, rect):
        from PyQt5.QtGui import QPainter
        poly = self._indicator_poly
        if poly is not None and poly.count():
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(QPen(Qt.red, 0, Qt.DashLine))
            painter.drawPath(_poly_to_path(poly))
            painter.restore()

    def mousePressEvent(self, event):
        if self._main is not None:
            self._main.centerOn(self.mapToScene(event.pos()))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and self._main is not None:
            self._main.centerOn(self.mapToScene(event.pos()))
        super().mouseMoveEvent(event)


def _poly_to_path(poly):
    from PyQt5.QtGui import QPainterPath
    path = QPainterPath()
    path.addPolygon(poly)
    path.closeSubpath()
    return path


def make_minimap_dock(scene, main_view, parent) -> QDockWidget:
    dock = QDockWidget("小地图", parent)
    inner = QWidget()
    box = QVBox(inner)
    box.setContentsMargins(0, 0, 0, 0)
    view = MiniMapView(scene, main_view)
    # 给一个随界面倍数缩放的最小尺寸, 否则 4K 上小地图会被压成一条缝
    s = theme.ui_scale()
    view.setMinimumSize(int(200 * s), int(140 * s))
    dock.setMinimumWidth(int(210 * s))
    box.addWidget(view)
    dock.setWidget(inner)
    dock._minimap_view = view   # 供外部定时刷新
    return dock
