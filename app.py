"""
app.py — Main program entry.
Layout: left palette / centre canvas / right properties.
Toolbar: run power flow / clear / load demo / save+load topology.
Component creation happens via drag-and-drop from the palette onto the
canvas (handled in canvas.py: CircuitView.dropEvent). The MainWindow
only wires signals and owns the Network.
"""
from __future__ import annotations
import sys

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QAction, QMessageBox, QSplitter,
    QStatusBar, QShortcut, QToolBar, QFileDialog
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

        # Network + scene + view
        self.network = Network()
        self.scene = CircuitScene(self.network)
        self.view = CircuitView(self.scene)
        self.scene.set_view(self.view)  # optional; absent in older canvas

        self.palette = ComponentPalette()
        self.properties = PropertiesPanel()
        self.properties.attach_scene(self.scene)

        # Selection -> property panel
        self.scene.selectionChanged.connect(self._on_selection_changed)

        # Layout
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.palette)
        splitter.addWidget(self.view)
        splitter.addWidget(self.properties)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([150, 800, 260])
        self.setCentralWidget(splitter)

        # Toolbar
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

        # Status bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Ready — drag a component from the left into the canvas")

        # Shortcuts
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self._run_power_flow)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self._load_demo)

    # ---------- Selection ----------
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

    # ---------- Toolbar actions ----------
    def _run_power_flow(self):
        ok, err = run_power_flow(self.network)
        if not ok:
            if self.isVisible():
                QMessageBox.warning(self, "潮流计算失败", err)
            self.status.showMessage(f"❌ {err}", 5000)
            return False, err
        self.scene.refresh_results()
        if self.properties.current_item is not None:
            self.properties.refresh_results()
        n_bus = len(self.network.buses)
        n_line = len(self.network.lines)
        self.status.showMessage(
            f"✅ Converged — buses {n_bus}, lines {n_line}", 5000
        )
        return True, ""

    def _clear_canvas(self):
        has_any = (self.network.buses or self.network.gens or self.network.loads
                   or self.network.lines or self.network.trafos or self.network.impedances)
        if has_any:
            # In headless tests, default to yes to avoid the dialog blocking.
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
        # Rebuild canvas
        self._rebuild_scene_from_network()
        self.status.showMessage(f"已载入 {path}", 4000)

    def _rebuild_scene_from_network(self):
        from canvas import BusItem, GenItem, LoadItem, TrafoItem, ImpedanceItem, ConnectionItem
        # Components
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
        # Lines between buses
        for uid, ln in self.network.lines.items():
            a = self.scene._comp_by_uid.get(ln.from_bus)
            b = self.scene._comp_by_uid.get(ln.to_bus)
            if a is None or b is None:
                continue
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
        """Load a 3-bus demo: B1(gen)--B2(load1)--B3(load2)."""
        self._clear_canvas()
        # 3 buses
        self.scene.add_component("Bus", 200, 300, "B1")
        self.scene.add_component("Bus", 500, 200, "B2")
        self.scene.add_component("Bus", 500, 450, "B3")
        # Generator + loads
        self.scene.add_component("Gen", 250, 200, "G1")
        self.scene.add_component("Load", 600, 150, "L1")
        self.scene.add_component("Load", 600, 400, "L2")
        # Lines: B1-B2, B1-B3
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
        # Auto-run power flow once
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
