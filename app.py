"""
app.py — 主程序入口
- 左侧: 元件库
- 中间: 画布 (拖拽/连线/选中/删除)
- 右侧: 属性面板 + 潮流结果
- 顶部: 工具栏(运行潮流/清空)
"""
from __future__ import annotations
import sys
from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QAction, QMessageBox, QSplitter, QLabel, QStatusBar, QShortcut,
    QPushButton, QToolBar, QFileDialog
)

from solver import Network, run_power_flow
from canvas import CircuitScene, CircuitView, BaseComponent, ConnectionItem
from palette import ComponentPalette
from properties import PropertiesPanel


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("潮流计算 GUI  ·  pandapower 内核")
        self.resize(1280, 800)

        # 网络 + 场景
        self.network = Network()
        self.scene = CircuitScene(self.network)
        self.view = CircuitView(self.scene)

        self.palette = ComponentPalette()
        self.properties = PropertiesPanel()
        self.properties.attach_scene(self.scene)

        # 选中变化 -> 刷新属性面板
        self.scene.selectionChanged.connect(self._on_selection_changed)

        # 元件库点击 -> 进入"待放置"模式
        self._pending_kind: Optional[str] = None
        self.palette.component_chosen.connect(self._on_choose_component)

        # 画布点击 -> 如果有待放置元件, 在点击位置创建
        self.view.mousePressEvent_orig = self.view.mousePressEvent
        self.view.mousePressEvent = self._on_canvas_press

        # 布局
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.palette)
        splitter.addWidget(self.view)
        splitter.addWidget(self.properties)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([150, 800, 260])
        self.setCentralWidget(splitter)

        # 工具栏
        toolbar = QToolBar()
        self.addToolBar(toolbar)
        act_run = QAction("▶ 运行潮流", self)
        act_run.triggered.connect(self._run_power_flow)
        toolbar.addAction(act_run)
        act_clear = QAction("✖ 清空画布", self)
        act_clear.triggered.connect(self._clear_canvas)
        toolbar.addAction(act_clear)
        toolbar.addSeparator()
        act_demo = QAction("★ 加载示例", self)
        act_demo.triggered.connect(self._load_demo)
        toolbar.addAction(act_demo)
        toolbar.addSeparator()
        act_save = QAction("💾 保存拓扑", self)
        act_save.triggered.connect(self._save_topology)
        toolbar.addAction(act_save)
        act_load = QAction("📂 载入拓扑", self)
        act_load.triggered.connect(self._load_topology)
        toolbar.addAction(act_load)

        # 状态栏
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("就绪 — 从左侧拖入元件到画布")

        # 快捷键
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self._run_power_flow)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self._load_demo)

        # 提示标签: 中央叠加显示"放置中: 元件名"
        self._place_hint = QLabel("", self)
        self._place_hint.setStyleSheet(
            "background:#fff8d8; color:#806000; padding:6px 12px; border:1px solid #d0c080;"
            "border-radius:6px; font-weight:bold;"
        )
        self._place_hint.hide()

    # ---------- 元件放置 ----------
    def _on_choose_component(self, kind: str):
        self._pending_kind = kind
        self._place_hint.setText(f"放置中: {kind} — 在画布上点击要放置的位置 (再次点击元件库取消)")
        self._place_hint.adjustSize()
        self._place_hint.move(20, 20)
        self._place_hint.show()

    def _on_canvas_press(self, event):
        # 拦截画布按下: 如果有待放置元件, 在该点创建
        if event.button() == Qt.LeftButton and self._pending_kind is not None:
            # 必须点中空白, 而不是已有的元件 / 端口
            scene_pos = self.view.mapToScene(event.pos())
            item = self.view.itemAt(event.pos())
            from canvas import PortItem, BaseComponent
            if isinstance(item, (PortItem, BaseComponent)):
                # 落到已有元件上 → 走默认逻辑 (可能要连线)
                self._pending_kind = None
                self._place_hint.hide()
                self.view.mousePressEvent_orig(event)
                return
            kind = self._pending_kind
            self._pending_kind = None
            self._place_hint.hide()
            comp = self.scene.add_component(kind, scene_pos.x(), scene_pos.y())
            self.scene.clearSelection()
            comp.setSelected(True)
            event.accept()
            self.status.showMessage(f"已创建 {kind}  {comp.model.name}", 3000)
            return
        self.view.mousePressEvent_orig(event)

    # ---------- 选中事件 ----------
    def _on_selection_changed(self):
        sel = self.scene.selectedItems()
        if not sel:
            self.properties.clear()
            return
        item = sel[0]
        if isinstance(item, BaseComponent):
            self.properties.show_component(item)
        elif isinstance(item, ConnectionItem):
            self.properties.show_connection(item)

    # ---------- 工具栏动作 ----------
    def _run_power_flow(self):
        ok, err = run_power_flow(self.network)
        if not ok:
            if self.isVisible():
                QMessageBox.warning(self, "潮流计算失败", err)
            self.status.showMessage(f"❌ {err}", 5000)
            return False, err
        self.scene.refresh_results()
        # 更新属性面板的结果区
        if self.properties.current_item is not None:
            self.properties.refresh_results()
        n_bus = len(self.network.buses)
        n_line = len(self.network.lines)
        self.status.showMessage(
            f"✅ 潮流收敛 — 母线 {n_bus}, 线路 {n_line}", 5000
        )
        return True, ""

    def _clear_canvas(self):
        has_any = (self.network.buses or self.network.gens or self.network.loads
                   or self.network.lines or self.network.trafos or self.network.impedances)
        if has_any:
            # 在无头环境(CI/offscreen 测试)下默认 yes, 避免弹窗阻塞
            if self.isVisible():
                r = QMessageBox.question(
                    self, "清空画布", "确认清空当前所有元件?",
                    QMessageBox.Yes | QMessageBox.No
                )
                if r != QMessageBox.Yes:
                    return
        self.network.buses.clear()
        self.network.gens.clear()
        self.network.loads.clear()
        self.network.lines.clear()
        self.network.trafos.clear()
        self.network.impedances.clear()
        if hasattr(self.scene, "_internal_links"):
            self.scene._internal_links.clear()
        for it in list(self.scene.items()):
            self.scene.removeItem(it)
        self.scene._comp_by_uid.clear()
        self.scene._connections.clear()
        self.properties.clear()
        self.status.showMessage("画布已清空", 2000)

    def _save_topology(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存拓扑", "topology.json", "JSON (*.json)"
        )
        if not path:
            return
        import json
        from dataclasses import asdict
        data = {
            "buses": [asdict(b) for b in self.network.buses.values()],
            "gens": [asdict(g) for g in self.network.gens.values()],
            "loads": [asdict(l) for l in self.network.loads.values()],
            "lines": [asdict(l) for l in self.network.lines.values()],
            "trafos": [asdict(t) for t in self.network.trafos.values()],
            "impedances": [asdict(i) for i in self.network.impedances.values()],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self.status.showMessage(f"已保存到 {path}", 4000)

    def _load_topology(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "载入拓扑", "", "JSON (*.json)"
        )
        if not path:
            return
        import json
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.warning(self, "载入失败", f"无法读取文件: {e}")
            return
        self._clear_canvas()
        from solver import BusNode, GenUnit, LoadUnit, LineBranch, TrafoBranch, ImpedanceBranch
        for b in data.get("buses", []):
            self.network.buses[b["uid"]] = BusNode(**b)
        for g in data.get("gens", []):
            self.network.gens[g["uid"]] = GenUnit(**g)
        for l in data.get("loads", []):
            self.network.loads[l["uid"]] = LoadUnit(**l)
        for l in data.get("lines", []):
            self.network.lines[l["uid"]] = LineBranch(**l)
        for t in data.get("trafos", []):
            self.network.trafos[t["uid"]] = TrafoBranch(**t)
        for i in data.get("impedances", []):
            self.network.impedances[i["uid"]] = ImpedanceBranch(**i)
        # 重建画布
        self._rebuild_scene_from_network()
        self.status.showMessage(f"已载入 {path}", 4000)

    def _rebuild_scene_from_network(self):
        from canvas import BusItem, GenItem, LoadItem, TrafoItem, ImpedanceItem, ConnectionItem
        # 元件
        for uid, b in self.network.buses.items():
            it = BusItem(b)
            it.setPos(b.x, b.y)
            self.scene.addItem(it)
            self.scene._comp_by_uid[uid] = it
        for uid, g in self.network.gens.items():
            it = GenItem(g)
            bus = self.network.buses[g.bus_uid]
            it.setPos(bus.x + 20, bus.y - 80)
            self.scene.addItem(it)
            self.scene._comp_by_uid[uid] = it
            self.scene._register_internal_link(it, g.bus_uid)
        for uid, l in self.network.loads.items():
            it = LoadItem(l)
            bus = self.network.buses[l.bus_uid]
            it.setPos(bus.x + 80, bus.y - 80)
            self.scene.addItem(it)
            self.scene._comp_by_uid[uid] = it
            self.scene._register_internal_link(it, l.bus_uid)
        for uid, t in self.network.trafos.items():
            it = TrafoItem(t)
            a = self.network.buses[t.hv_bus]
            b = self.network.buses[t.lv_bus]
            it.setPos((a.x + b.x) / 2 - 40, (a.y + b.y) / 2 - 25)
            self.scene.addItem(it)
            self.scene._comp_by_uid[uid] = it
        for uid, im in self.network.impedances.items():
            it = ImpedanceItem(im)
            a = self.network.buses[im.from_bus]
            b = self.network.buses[im.to_bus]
            it.setPos((a.x + b.x) / 2 - 40, (a.y + b.y) / 2 - 25)
            self.scene.addItem(it)
            self.scene._comp_by_uid[uid] = it
        # 线路: 母线之间画连线
        for uid, ln in self.network.lines.items():
            a = self.scene._comp_by_uid.get(ln.from_bus)
            b = self.scene._comp_by_uid.get(ln.to_bus)
            if a is None or b is None:
                continue
            # 取左右最近端口
            pa = a.port_item("right")
            pb = b.port_item("left")
            if pa is None or pb is None:
                continue
            conn = ConnectionItem(a, pa, b, pb)
            conn.kind = "Line"
            conn.uid = uid
            a.register_connection(conn)
            b.register_connection(conn)
            self.scene.addItem(conn)
            self.scene._connections.append(conn)

    def _load_demo(self):
        """加载一个 3 母线示例: B1(电源)--B2(负荷1)--B3(负荷2)"""
        self._clear_canvas()
        # 3 个母线
        self.scene.add_component("Bus", 200, 300, "B1")
        self.scene.add_component("Bus", 500, 200, "B2")
        self.scene.add_component("Bus", 500, 450, "B3")
        # 电源 + 负荷
        self.scene.add_component("Gen", 250, 200, "G1")
        self.scene.add_component("Load", 600, 150, "L1")
        self.scene.add_component("Load", 600, 400, "L2")
        # 线路: B1-B2, B1-B3
        from canvas import ConnectionItem
        b1 = next(it for uid, it in self.scene._comp_by_uid.items()
                  if self.network.buses[uid].name == "B1")
        b2 = next(it for uid, it in self.scene._comp_by_uid.items()
                  if self.network.buses[uid].name == "B2")
        b3 = next(it for uid, it in self.scene._comp_by_uid.items()
                  if self.network.buses[uid].name == "B3")
        for a, b in [(b1, b2), (b1, b3)]:
            pa = a.port_item("right")
            pb = b.port_item("left")
            self.scene.create_connection(a, pa, b, pb)
        # 自动跑一次潮流
        self._run_power_flow()
        self.status.showMessage("已加载 3 母线示例 — 可点 ▶ 重新运行", 4000)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
