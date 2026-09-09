"""
tests/test_gui_smoke.py — GUI 层 offscreen 冒烟测试
用 QT_QPA_PLATFORM=offscreen 直接调方法, 不需要真实点击与显示器。
覆盖: 增删元件/连线、属性面板、存取 roundtrip、删除级联、缩放、demo 潮流。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from solver import Network, run_power_flow


@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def scene(qapp):
    from canvas import CircuitScene
    net = Network()
    return CircuitScene(net)


def _bus_items(scene):
    from canvas import BusItem
    return [it for it in scene._comp_by_uid.values() if isinstance(it, BusItem)]


class TestComponents:
    def test_add_all_kinds(self, scene):
        for kind in ("Bus", "Gen", "Load", "Trafo", "Impedance"):
            item = scene.add_component(kind, 100, 100)
            assert item is not None
        assert len(scene.network.buses) >= 1
        assert len(scene.network.gens) == 1
        assert len(scene.network.loads) == 1
        assert len(scene.network.trafos) == 1
        assert len(scene.network.impedances) == 1

    def test_names_unique_after_delete_recreate(self, scene):
        """回归: 建 B1/B2, 删 B1, 再建应得 B3 而不是又一个 B2"""
        b1 = scene.add_component("Bus", 100, 100)
        scene.add_component("Bus", 400, 100)
        scene.delete_item(b1)
        b3 = scene.add_component("Bus", 700, 100)
        names = [b.name for b in scene.network.buses.values()]
        assert len(names) == len(set(names)), f"显示名重名: {names}"
        assert b3.model.name == "B3"

    def test_trafo_creation_two_distinct_buses(self, scene):
        """回归: 只有近处一条母线时, 变压器两侧不得接到同一条母线"""
        scene.add_component("Bus", 200, 300)
        t = scene.add_component("Trafo", 300, 300)
        assert t.model.hv_bus != t.model.lv_bus

    def test_move_bus_position_writeback(self, scene):
        """回归: 拖动(改 pos)后坐标必须回写 model, 否则存/载丢布局"""
        b = scene.add_component("Bus", 100, 100)
        b.setPos(333.0, 444.0)
        assert scene.network.buses[b.model.uid].x == pytest.approx(333.0)
        assert scene.network.buses[b.model.uid].y == pytest.approx(444.0)

    def test_move_trafo_no_attribute_error(self, scene):
        """回归: 变压器/阻抗 model 没有 x/y, 拖动不得崩"""
        t = scene.add_component("Trafo", 300, 300)
        t.setPos(350.0, 320.0)   # 触发 itemChange 位置回写路径


class TestConnections:
    def test_bus_bus_line_created(self, scene):
        a = scene.add_component("Bus", 100, 100)
        b = scene.add_component("Bus", 400, 100)
        scene.create_connection(a, a.port_item("right"), b, b.port_item("left"))
        assert len(scene.network.lines) == 1
        assert len(scene._connections) == 1
        assert scene._connections[0].kind == "Line"

    def test_line_names_unique_after_delete_recreate(self, scene):
        def mkline():
            a = scene.add_component("Bus", 100, 100)
            b = scene.add_component("Bus", 400, 100)
            scene.create_connection(a, a.port_item("right"), b, b.port_item("left"))
        mkline()
        first = next(iter(scene.network.lines.values()))
        scene.network.lines.pop(first.uid)
        scene._connections.clear()
        mkline()
        names = [l.name for l in scene.network.lines.values()]
        assert len(names) == len(set(names)), f"线路名重名: {names}"

    def test_delete_bus_cascades_graphical_items(self, scene):
        """回归: 删母线只删 model 不删图形项 → 幽灵元件"""
        bus = scene.add_component("Bus", 200, 200)
        scene.add_component("Gen", 230, 120)     # 自动挂到母线
        scene.add_component("Load", 280, 130)
        assert len(scene._comp_by_uid) == 3
        scene.delete_item(bus)
        assert len(scene.network.buses) == 0
        assert len(scene.network.gens) == 0
        assert len(scene.network.loads) == 0
        assert len(scene._comp_by_uid) == 0, "画布上残留幽灵元件"
        assert len(scene.items()) == 0, "scene 上残留图形项"


class TestPropertiesPanel:
    def test_show_connection_does_not_crash(self, scene, qapp):
        """回归: 选中一条连线, 属性面板曾因 ConnectionItem 无 .model 必崩"""
        from properties import PropertiesPanel
        a = scene.add_component("Bus", 100, 100)
        b = scene.add_component("Bus", 400, 100)
        scene.create_connection(a, a.port_item("right"), b, b.port_item("left"))
        panel = PropertiesPanel()
        panel.attach_scene(scene)
        panel.show_connection(scene._connections[0])   # 不应抛异常
        assert "连线" in panel.title.text()

    def test_trafo_results_shown_not_placeholder(self, scene, qapp):
        """回归: 变压器结果曾显示'暂未提取'"""
        from properties import PropertiesPanel
        t = scene.add_component("Trafo", 300, 300)   # 自动带两条母线
        # 低压侧母线电压等级调成与变压器 vn_lv_kv 一致, 避免离谱变比
        scene.network.buses[t.model.lv_bus].vn_kv = 35.0
        scene.add_component("Gen", 150, 150)
        scene.add_component("Load", 450, 450)
        ok, msg = run_power_flow(scene.network)
        assert ok, msg
        panel = PropertiesPanel()
        panel.attach_scene(scene)
        panel.show_component(t)
        texts = []
        for i in range(panel.result_layout.count()):
            w = panel.result_layout.itemAt(i).widget()
            if w is not None:
                texts.append(w.text())
        assert not any("暂未提取" in t for t in texts), texts


class TestTopologyIO:
    def test_parse_rejects_dangling_ref(self):
        from app import parse_topology_json
        data = {"buses": [{"uid": "b1", "name": "B1", "x": 0, "y": 0}],
                "gens": [{"uid": "g1", "name": "G1", "bus_uid": "nope"}]}
        with pytest.raises(ValueError, match="发电机"):
            parse_topology_json(data)

    def test_parse_rejects_missing_uid(self):
        from app import parse_topology_json
        data = {"buses": [{"name": "B1", "x": 0, "y": 0}]}
        with pytest.raises(ValueError, match="uid"):
            parse_topology_json(data)

    def test_parse_tolerates_unknown_fields(self):
        from app import parse_topology_json
        data = {"buses": [{"uid": "b1", "name": "B1", "x": 0, "y": 0,
                           "future_field": 123}]}
        net = parse_topology_json(data)
        assert len(net.buses) == 1

    def test_save_load_roundtrip_keeps_positions_and_trafo_wires(self, qapp, tmp_path):
        """回归: 保存/载入保留布局, 且变压器与母线的连线被重建"""
        from app import MainWindow
        w = MainWindow()
        w.scene.add_component("Bus", 200, 300)
        w.scene.add_component("Gen", 150, 150)
        t = w.scene.add_component("Trafo", 300, 300)   # 触发新建第二条母线
        t.setPos(320, 330)                              # 移动, 测坐标回写+保存
        path = str(tmp_path / "topo.json")
        assert w._save_topology(path) == path

        w2 = MainWindow()
        assert w2._load_topology(path) is True
        # 布局保留
        saved_bus = next(iter(w2.network.buses.values()))
        orig_bus = next(b for b in w.network.buses.values()
                        if b.uid == saved_bus.uid)
        assert (saved_bus.x, saved_bus.y) == (orig_bus.x, orig_bus.y)
        # 变压器图形项存在, 且与母线有连线(kind == "Trafo")
        kinds = [c.kind for c in w2.scene._connections]
        assert "Trafo" in kinds, f"载入后变压器连线未重建: {kinds}"
        assert w2.scene._comp_by_uid.get(t.model.uid) is not None

    def test_load_bad_file_reports_not_crashes(self, qapp, tmp_path, monkeypatch):
        from app import MainWindow
        bad = tmp_path / "bad.json"
        bad.write_text('{"buses": [{"uid": "b1", "name": "B1", "x": 0, "y": 0}], '
                       '"gens": [{"uid": "g1", "name": "G1", "bus_uid": "ghost"}]}',
                       encoding="utf-8")
        w = MainWindow()
        w.scene.add_component("Bus", 100, 100)   # 载入失败时不能毁掉现有画布
        warned = {}
        monkeypatch.setattr(
            "app.QMessageBox.warning",
            lambda *a, **k: warned.setdefault("msg", a[2] if len(a) > 2 else ""))
        assert w._load_topology(str(bad)) is False
        assert warned.get("msg"), "坏文件应给出可读警告"
        assert len(w.network.buses) == 1, "坏文件不应清空当前画布"


class TestViewInteraction:
    def test_escape_clears_selection(self, scene, qapp):
        from canvas import CircuitView
        from PyQt5.QtCore import Qt, QEvent
        from PyQt5.QtGui import QKeyEvent
        b = scene.add_component("Bus", 100, 100)
        b.setSelected(True)
        view = CircuitView(scene)
        ev = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
        view.keyPressEvent(ev)
        assert not scene.selectedItems()

    def test_wheel_zoom_and_fit(self, qapp):
        from canvas import CircuitScene, CircuitView
        from PyQt5.QtCore import Qt, QPoint, QPointF
        from PyQt5.QtGui import QWheelEvent
        scene = CircuitScene(Network())
        scene.add_component("Bus", 100, 100)
        scene.add_component("Bus", 500, 500)
        view = CircuitView(scene)
        view.resize(800, 600)
        m11_before = view.transform().m11()
        ev = QWheelEvent(QPointF(200, 200), QPointF(200, 200),
                         QPoint(0, 0), QPoint(0, 120), Qt.NoButton,
                         Qt.NoModifier, Qt.ScrollUpdate, False)
        view.wheelEvent(ev)
        assert view.transform().m11() > m11_before
        view.fit_view()
        visible = view.mapToScene(view.viewport().rect()).boundingRect()
        assert visible.contains(scene.itemsBoundingRect()), \
            "适配视图后仍看不到全部元件"


class TestDemos:
    def test_three_bus_demo_converges(self, qapp):
        from app import MainWindow
        w = MainWindow()
        w._load_demo()
        assert w.network.converged is True

    def test_two_end_demo_converges_and_matches_docstring(self, qapp):
        """COLLABORATION 铁律: 5 母线两端供电必须收敛; 参数与 docstring 一致"""
        from app import MainWindow
        w = MainWindow()
        w._load_two_end_demo()
        assert w.network.converged is True
        loads = {l.name: (l.p_mw, l.q_mvar) for l in w.network.loads.values()}
        assert loads["L1"] == (30.0, 10.0)
        assert loads["L2"] == (20.0, 8.0)
        gens = {g.name: g.vm_pu for g in w.network.gens.values()}
        assert gens["G1"] == 1.05 and gens["G2"] == 1.05


class TestRound3Features:
    def test_drag_gen_line_to_bus_rebinds(self, scene):
        """Gen 拖线连到另一条母线: bus_uid 真正改挂 (不再是视觉装饰)"""
        b1 = scene.add_component("Bus", 100, 300)
        b2 = scene.add_component("Bus", 600, 300)
        g = scene.add_component("Gen", 130, 180)   # 自动挂到 b1
        assert g.model.bus_uid == b1.model.uid
        scene.create_connection(g, g.port_item("out"), b2, b2.port_item("left"))
        assert g.model.bus_uid == b2.model.uid, "拖线换母线未转正"
        assert g.model.uid in scene._internal_links.get(b2.model.uid, [])
        assert g.model.uid not in scene._internal_links.get(b1.model.uid, [])

    def test_export_results_csv(self, qapp, tmp_path):
        from app import export_results_csv
        from solver import (
            run_power_flow, BusNode, GenUnit, LoadUnit, LineBranch)
        net = Network()
        for uid, x in (("b1", 0), ("b2", 300)):
            net.buses[uid] = BusNode(uid=uid, name=uid.upper(), x=x, y=0)
        net.gens["g1"] = GenUnit(uid="g1", name="G1", bus_uid="b1")
        net.loads["l1"] = LoadUnit(uid="l1", name="L1", bus_uid="b2")
        net.lines["ln1"] = LineBranch(uid="ln1", name="L1", from_bus="b1", to_bus="b2")
        ok, msg = run_power_flow(net)
        assert ok, msg
        base = str(tmp_path / "out")
        bus_csv, branch_csv = export_results_csv(net, base)
        assert bus_csv.endswith("_母线.csv") and branch_csv.endswith("_支路.csv")
        bus_text = open(bus_csv, encoding="utf-8-sig").read()
        assert "B1" in bus_text and "V(pu)" in bus_text
        branch_text = open(branch_csv, encoding="utf-8-sig").read()
        assert "线路" in branch_text and "负载率" in branch_text

    def test_results_panel_fills_tables(self, qapp):
        from app import ResultsPanel
        from canvas import CircuitScene
        from solver import run_power_flow
        scene = CircuitScene(Network())
        scene.add_component("Bus", 100, 100)
        scene.add_component("Bus", 400, 100)
        a, b = _bus_items(scene)
        scene.create_connection(a, a.port_item("right"), b, b.port_item("left"))
        scene.add_component("Gen", 130, 180)
        ok, msg = run_power_flow(scene.network)
        assert ok, msg
        panel = ResultsPanel()
        panel.refresh(scene.network)
        assert panel.bus_table.rowCount() == 2
        assert panel.branch_table.rowCount() == 1
        assert panel.branch_table.item(0, 0).text() == "线路"

    def test_dc_toggle_changes_algorithm(self, qapp):
        from app import MainWindow
        w = MainWindow()
        assert w.act_dc.isChecked() is False
        w._load_demo()
        v_ac = dict(w.network.bus_voltage_pu)
        assert w.network.converged
        # 打开 DC 再跑
        w.act_dc.setChecked(True)
        ok, err = w._run_power_flow()
        assert ok, err
        assert "DC" in w.status.currentMessage()


class TestRound4Features:
    def test_undo_redo_add_delete(self, qapp):
        """快照式撤销: 添加→撤销→画布空, 重做→元件回来"""
        from app import MainWindow
        w = MainWindow()
        w.scene.add_component("Bus", 100, 100)
        assert len(w.network.buses) == 1
        assert w.undo_stack.canUndo()
        w.undo_stack.undo()
        assert len(w.network.buses) == 0
        assert len(w.scene._comp_by_uid) == 0
        w.undo_stack.redo()
        assert len(w.network.buses) == 1
        assert len(w.scene._comp_by_uid) == 1

    def test_undo_delete_restores_element(self, qapp):
        from app import MainWindow
        w = MainWindow()
        w.scene.add_component("Bus", 100, 100)
        w.scene.add_component("Gen", 130, 150)
        uid = next(iter(w.network.gens))
        w.scene.delete_item(w.scene._comp_by_uid[uid])
        assert len(w.network.gens) == 0
        w.undo_stack.undo()
        assert uid in w.network.gens, "删除的发电机应被撤销恢复"

    def test_dirty_flag_and_title(self, qapp):
        from app import MainWindow
        w = MainWindow()
        assert w._dirty is False
        assert "*" not in w.windowTitle()
        w.scene.add_component("Bus", 100, 100)
        assert w._dirty is True
        assert "*" in w.windowTitle()
        w._set_dirty(False)
        assert "*" not in w.windowTitle()

    def test_confirm_discard_changes(self, qapp, monkeypatch):
        from app import MainWindow
        from PyQt5.QtWidgets import QMessageBox
        w = MainWindow()
        w._set_dirty(True)
        answers = {"No": QMessageBox.No, "Cancel": QMessageBox.Cancel}
        for label, expected_ok in (("No", True), ("Cancel", False)):
            monkeypatch.setattr(
                "app.QMessageBox.question",
                lambda *a, **k: answers[label])
            assert w.confirm_discard_changes() is expected_ok

    def test_atomic_save_no_tmp_leftover(self, qapp, tmp_path):
        from app import MainWindow
        w = MainWindow()
        w.scene.add_component("Bus", 100, 100)
        path = str(tmp_path / "t.json")
        w._save_topology(path)
        assert os.path.exists(path)
        assert not os.path.exists(path + ".tmp"), "原子写的临时文件应被替换掉"

    def test_pf_thread_runs_in_copy(self, qapp):
        from app import PowerFlowThread
        from test_solver import build_3bus_network
        net = build_3bus_network()
        t = PowerFlowThread(net, "nr")
        results = []
        t.done.connect(lambda c, ok, err, ms: results.append(ok))
        t.run()   # 同步调用 run, 不开事件循环
        assert results and results[0] is True
        # 原网络未被后台计算污染 (结果在副本上)
        assert len(net.bus_voltage_pu) == 0

    def test_results_plot_handles_nan(self, qapp):
        from app import ResultsPanel
        from solver import run_power_flow, BusNode
        from test_solver import build_3bus_network
        net = build_3bus_network()
        net.buses["iso"] = BusNode(uid="iso", name="ISO", x=900, y=900)
        ok, msg = run_power_flow(net)
        assert ok, msg
        panel = ResultsPanel()
        panel.refresh(net)   # NaN 电压不得让绘图崩掉
        assert panel.bus_table.rowCount() == 4

    def test_ieee_case14_loads_and_converges(self, qapp):
        """README 扩展方向 #3: IEEE 标准算例一键加载"""
        from app import MainWindow
        w = MainWindow()
        w._load_ieee_case("case14")
        assert len(w.network.buses) == 14
        assert w.network.converged, "case14 应收敛"
        assert len(w.network.trafos) >= 1
        assert len(w.network.gens) >= 1

    def test_ieee_case30_converges(self):
        from ieee_cases import load_case
        from solver import run_power_flow
        net = load_case("case30")
        ok, msg = run_power_flow(net)
        assert ok, f"case30 不收敛: {msg}"

    def test_load_case_rejects_unknown(self):
        from ieee_cases import load_case
        import pytest
        with pytest.raises(ValueError):
            load_case("case999")
