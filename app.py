"""
app.py — Main program entry.
Layout: left palette / centre canvas / right properties.
Toolbar: run power flow / clear / load demo / save+load topology.
Component creation happens via drag-and-drop from the palette onto the
canvas (handled in canvas.py: CircuitView.dropEvent). The MainWindow
only wires signals and owns the Network.
"""
from __future__ import annotations
import json
import sys
import time
from dataclasses import asdict, fields as dc_fields

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QAction, QMessageBox, QSplitter,
    QStatusBar, QShortcut, QToolBar, QFileDialog
)

from solver import (
    Network, run_power_flow,
    BusNode, GenUnit, LoadUnit, LineBranch, TrafoBranch, ImpedanceBranch,
)
from canvas import CircuitScene, CircuitView, BaseComponent, ConnectionItem
from palette import ComponentPalette
from properties import PropertiesPanel


# -------------------------------------------------------------
# 拓扑 JSON 解析/校验(独立于 GUI, 方便测试)
# -------------------------------------------------------------
_TOPO_SPEC = [
    ("buses", BusNode), ("gens", GenUnit), ("loads", LoadUnit),
    ("lines", LineBranch), ("trafos", TrafoBranch),
    ("impedances", ImpedanceBranch),
]


def parse_topology_json(data) -> Network:
    """把 json.load 出来的 dict 解析成 Network, 带字段与引用校验。

    旧版本直接 BusNode(**b): 多余/缺失键 TypeError、悬空 bus_uid
    KeyError, 都发生在清空画布之后, 用户面临"存得进读不出"。
    这里提前解析+校验, 坏文件以可读报错拒绝, 不动当前画布。
    """
    if not isinstance(data, dict):
        raise ValueError("顶层必须是 JSON 对象")
    net = Network()
    for key, cls in _TOPO_SPEC:
        entries = data.get(key, []) or []
        if not isinstance(entries, list):
            raise ValueError(f"'{key}' 必须是数组")
        allowed = {f.name for f in dc_fields(cls)}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError(f"'{key}' 里存在非对象条目: {entry!r}")
            if "uid" not in entry or "name" not in entry:
                raise ValueError(f"'{key}' 条目缺少 uid/name: {entry!r}")
            kwargs = {k: v for k, v in entry.items() if k in allowed}
            try:
                obj = cls(**kwargs)
            except TypeError as e:
                raise ValueError(
                    f"'{key}' 条目 {entry.get('name', '?')} 字段无效: {e}")
            getattr(net, key)[obj.uid] = obj
    bus_uids = set(net.buses)

    def _check_ref(ref, what, name):
        if ref not in bus_uids:
            raise ValueError(f"{what} '{name}' 引用了不存在的母线: {ref!r}")

    for g in net.gens.values():
        _check_ref(g.bus_uid, "发电机", g.name)
    for ld in net.loads.values():
        _check_ref(ld.bus_uid, "负荷", ld.name)
    for ln in net.lines.values():
        _check_ref(ln.from_bus, "线路", ln.name)
        _check_ref(ln.to_bus, "线路", ln.name)
    for tr in net.trafos.values():
        _check_ref(tr.hv_bus, "变压器", tr.name)
        _check_ref(tr.lv_bus, "变压器", tr.name)
    for im in net.impedances.values():
        _check_ref(im.from_bus, "阻抗", im.name)
        _check_ref(im.to_bus, "阻抗", im.name)
    return net


def network_to_json_dict(net: Network) -> dict:
    return {
        "buses": [asdict(b) for b in net.buses.values()],
        "gens": [asdict(g) for g in net.gens.values()],
        "loads": [asdict(l) for l in net.loads.values()],
        "lines": [asdict(l) for l in net.lines.values()],
        "trafos": [asdict(t) for t in net.trafos.values()],
        "impedances": [asdict(i) for i in net.impedances.values()],
    }


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
        act_two_end = QAction("⚡ 两端供电", self)
        act_two_end.triggered.connect(self._load_two_end_demo)
        toolbar.addAction(act_two_end)
        toolbar.addSeparator()
        act_save = QAction("💾 保存拓扑", self)
        act_save.triggered.connect(self._save_topology)
        toolbar.addAction(act_save)
        act_load = QAction("📂 载入拓扑", self)
        act_load.triggered.connect(self._load_topology)
        toolbar.addAction(act_load)
        toolbar.addSeparator()
        act_fit = QAction("⤢ 适配视图", self)
        act_fit.triggered.connect(self.view.fit_view)
        toolbar.addAction(act_fit)

        # Status bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Ready — drag a component from the left into the canvas")

        # Shortcuts
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self._run_power_flow)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self._load_demo)
        QShortcut(QKeySequence("Ctrl+0"), self, activated=self.view.fit_view)

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
        t0 = time.perf_counter()
        ok, err = run_power_flow(self.network)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
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
            f"✅ 收敛 — 母线 {n_bus}, 线路 {n_line}, 耗时 {elapsed_ms:.0f} ms",
            5000,
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

    def _save_topology(self, path=None):
        if path is None:
            path, _ = QFileDialog.getSaveFileName(
                self, "保存拓扑", "topology.json", "JSON (*.json)"
            )
            if not path:
                return None
        data = network_to_json_dict(self.network)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self.status.showMessage(f"已保存到 {path}", 4000)
        return path

    def _load_topology(self, path=None):
        if path is None:
            path, _ = QFileDialog.getOpenFileName(
                self, "载入拓扑", "", "JSON (*.json)"
            )
            if not path:
                return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.warning(self, "载入失败", f"无法读取文件: {e}")
            return False
        # 先解析校验, 通过了才动当前画布(坏文件不能毁掉已画的内容)
        try:
            net = parse_topology_json(data)
        except Exception as e:
            QMessageBox.warning(self, "载入失败", f"拓扑数据无效: {e}")
            return False
        self._apply_network(net)
        self.status.showMessage(f"已载入 {path}", 4000)
        return True

    def _apply_network(self, net: Network):
        """用解析好的 Network 替换当前网络并重建画布"""
        self._clear_canvas()
        self.network.buses.update(net.buses)
        self.network.gens.update(net.gens)
        self.network.loads.update(net.loads)
        self.network.lines.update(net.lines)
        self.network.trafos.update(net.trafos)
        self.network.impedances.update(net.impedances)
        self._rebuild_scene_from_network()

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
            # 优先用保存的画布坐标; 老文件(坐标为 0,0)退回母线旁固定偏移
            if g.x or g.y:
                it.setPos(g.x, g.y)
            else:
                it.setPos(bus.x + 20, bus.y - 80)
            self.scene.addItem(it)
            self.scene._comp_by_uid[uid] = it
            self.scene._register_internal_link(it, g.bus_uid)
        for uid, l in self.network.loads.items():
            it = LoadItem(l)
            bus = self.network.buses[l.bus_uid]
            if l.x or l.y:
                it.setPos(l.x, l.y)
            else:
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
        # 变压器/阻抗与母线之间的连线(之前载入后悬空漂浮, 看不出接在哪儿)
        for uid, t in self.network.trafos.items():
            comp = self.scene._comp_by_uid.get(uid)
            for bus_uid in (t.hv_bus, t.lv_bus):
                bus_item = self.scene._comp_by_uid.get(bus_uid)
                if comp is not None and bus_item is not None:
                    self._reconnect_bus_to_linecomp(bus_item, comp)
        for uid, im in self.network.impedances.items():
            comp = self.scene._comp_by_uid.get(uid)
            for bus_uid in (im.from_bus, im.to_bus):
                bus_item = self.scene._comp_by_uid.get(bus_uid)
                if comp is not None and bus_item is not None:
                    self._reconnect_bus_to_linecomp(bus_item, comp)
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

    def _reconnect_bus_to_linecomp(self, bus_item, comp_item):
        """重建一条 母线↔变压器/阻抗 的可视化连线(不改动拓扑字段)"""
        from canvas import TrafoItem
        bus_left = bus_item.scenePos().x() <= comp_item.scenePos().x()
        port_bus = bus_item.port_item("right" if bus_left else "left")
        port_comp = comp_item.port_item("p1" if bus_left else "p2")
        if port_bus is None or port_comp is None:
            return
        conn = ConnectionItem(bus_item, port_bus, comp_item, port_comp)
        conn.kind = "Trafo" if isinstance(comp_item, TrafoItem) else "Impedance"
        conn.uid = comp_item.model.uid
        bus_item.register_connection(conn)
        comp_item.register_connection(conn)
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

    def _load_two_end_demo(self):
        """Load a 5-bus two-end supply network:

        G1 (slack)                                G2 (PV)
        50+j20 MW   <- L1 ->  <- L2 ->  <- L3 ->  40+j15 MW
        1.05 pu     r=0.04+j0.12 per line, all 100 MVA / 110 kV base
            B1 --- B2 --- B3 --- B4 --- B5
                   |              |
                 Load1          Load2
                30+j10 MW       20+j8 MVAr
        """
        self._clear_canvas()
        # 5 buses in a horizontal line
        self.scene.add_component("Bus", 150, 300, "B1")
        self.scene.add_component("Bus", 350, 300, "B2")
        self.scene.add_component("Bus", 550, 300, "B3")
        self.scene.add_component("Bus", 750, 300, "B4")
        self.scene.add_component("Bus", 950, 300, "B5")
        # Generators at the two ends (G1 slack 1.05pu, G2 PV 1.05pu)
        g1 = self.scene.add_component("Gen", 200, 150, "G1")
        g1.model.vm_pu = 1.05
        g2 = self.scene.add_component("Gen", 900, 150, "G2")
        g2.model.p_mw = 40
        g2.model.vm_pu = 1.05
        # Loads at B2 and B4: 30+j10 / 20+j8 (与 docstring 描述一致)
        l1 = self.scene.add_component("Load", 400, 450, "L1")
        l1.model.p_mw, l1.model.q_mvar = 30.0, 10.0
        l2 = self.scene.add_component("Load", 700, 450, "L2")
        l2.model.p_mw, l2.model.q_mvar = 20.0, 8.0
        # Lines: 4 segments B1-B2, B2-B3, B3-B4, B4-B5
        b = {}
        for uid, it in self.scene._comp_by_uid.items():
            if uid in self.network.buses:
                b[self.network.buses[uid].name] = it
        from canvas import ConnectionItem
        for a, b_ in [("B1", "B2"), ("B2", "B3"), ("B3", "B4"), ("B4", "B5")]:
            pa = b[a].port_item("right")
            pb = b[b_].port_item("left")
            self.scene.create_connection(b[a], pa, b[b_], pb)
        # Auto-run
        self._run_power_flow()
        self.status.showMessage(
            "已加载两端供电示例: G1-B1-B2-B3-B4-B5-G2 (B2/B4 带负荷) — 可点 ▶ 重跑", 4000)


def main():
    # Install a global exception hook so that crashes during GUI events
    # get written to crash.log instead of vanishing silently on Windows.
    import traceback
    def _excepthook(exc_type, exc_value, exc_tb):
        with open("crash.log", "a", encoding="utf-8") as f:
            f.write("\n=== Unhandled exception ===\n")
            f.write("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
            f.write("\n")
        # Also print to stderr so the console window shows it.
        sys.stderr.write("UNHANDLED EXCEPTION (also written to crash.log):\n")
        traceback.print_exception(exc_type, exc_value, exc_tb)
    sys.excepthook = _excepthook

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
