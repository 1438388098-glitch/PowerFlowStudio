"""
ux.py — 编辑效率增强件 (二期拓展)

- SearchDialog: 元件搜索定位 (Ctrl+F)
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

from canvas import kind_of


class SearchDialog(QDialog):
    """元件搜索: 按名称/类型过滤, 双击或回车定位选中"""
    item_selected = pyqtSignal(str)   # uid

    def __init__(self, scene, parent=None):
        super().__init__(parent)
        self.setWindowTitle("搜索元件 (Ctrl+F)")
        self.resize(260, 360)
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
        for uid, item in self._scene._comp_by_uid.items():
            kind = kind_of(item)
            name = getattr(item.model, "name", "?")
            li = QListWidgetItem(f"[{kind}] {name}")
            li.setData(Qt.UserRole, uid)
            self.listw.addItem(li)
        self._filter(self.edit.text())

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
        self.indicator = None   # 显示主视图可视范围的虚线框
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
        # 指示框
        if self.indicator is not None:
            sc.removeItem(self.indicator)
            self.indicator = None
        if self._main is not None and self._main.scene() is sc:
            visible = self._main.mapToScene(self._main.viewport().rect()).boundingRect()
            from PyQt5.QtCore import QPointF
            from PyQt5.QtGui import QPolygonF
            poly = QPolygonF(visible)
            self.indicator = sc.addPath(_poly_to_path(poly),
                                        QPen(Qt.red, 0, Qt.DashLine))
            self.indicator.setZValue(50)

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
    box.addWidget(view)
    dock.setWidget(inner)
    dock._minimap_view = view   # 供外部定时刷新
    return dock
