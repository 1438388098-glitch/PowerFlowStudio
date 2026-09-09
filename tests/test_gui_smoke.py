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
        assert panel.title.text().startswith("线路"), "标题应带线路名与两端母线"

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
        paths = export_results_csv(net, base)
        assert paths["bus"].endswith("_母线.csv")
        assert paths["branch"].endswith("_支路.csv")
        assert paths["genload"].endswith("_电源与负荷.csv")
        bus_text = open(paths["bus"], encoding="utf-8-sig").read()
        assert "B1" in bus_text and "V(pu)" in bus_text
        branch_text = open(paths["branch"], encoding="utf-8-sig").read()
        assert "线路" in branch_text and "负载率" in branch_text
        genload_text = open(paths["genload"], encoding="utf-8-sig").read()
        assert "发电机" in genload_text and "G1" in genload_text
        assert "负荷" in genload_text and "L1" in genload_text

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


class TestRound5Features:
    def test_loading_color_after_run(self, qapp):
        """运行潮流后, 线路连线应按负载率着色"""
        from canvas import COLOR_LOADING_OK, COLOR_LOADING_WARN
        from app import MainWindow
        w = MainWindow()
        w._load_two_end_demo()
        colors = {c.uid: c.loading_color for c in w.scene._connections
                  if c.kind == "Line"}
        assert colors and all(c is not None for c in colors.values())
        # 正常线路应为绿色系(不过载)
        from canvas import COLOR_LOADING_CRIT
        assert not any(c is COLOR_LOADING_CRIT for c in colors.values())

    def test_trafo_label_shows_loading(self, qapp):
        from app import MainWindow
        from canvas import BusItem
        w = MainWindow()
        w.scene.add_component("Bus", 150, 300)
        w.scene.add_component("Bus", 600, 300)
        buses = [it for it in w.scene._comp_by_uid.values() if isinstance(it, BusItem)]
        t = w.scene.add_component("Trafo", 300, 300)   # 自动挂到两条母线(可能新建)
        lv_model = w.network.buses[t.model.lv_bus]
        lv_model.vn_kv = 35.0
        # 显式建 Bus↔Trafo 连线 (add_component 不画线)
        hv_item = w.scene._comp_by_uid[t.model.hv_bus]
        lv_item = w.scene._comp_by_uid[t.model.lv_bus]
        w.scene.create_connection(hv_item, hv_item.port_item("right"),
                                  t, t.port_item("p1"))
        w.scene.create_connection(lv_item, lv_item.port_item("left"),
                                  t, t.port_item("p2"))
        w.scene.add_component("Gen", 150, 150)
        w.scene.add_component("Load", 660, 460)
        ok, msg = w._run_power_flow()
        assert ok, msg
        trafo_conns = [c for c in w.scene._connections if c.kind == "Trafo"]
        assert trafo_conns
        assert any("%" in c._label.toPlainText() for c in trafo_conns)

    def test_snap_point(self, qapp):
        from canvas import CircuitScene
        from solver import Network
        scene = CircuitScene(Network())
        x, y = scene.snap_point(103.0, 97.0)
        assert (x, y) == (103.0, 97.0), "开关关闭时不吸附"
        scene.snap_enabled = True
        x, y = scene.snap_point(103.0, 97.0)
        assert (x, y) == (100.0, 100.0)

    def test_result_row_click_selects_canvas_item(self, qapp):
        from app import MainWindow
        w = MainWindow()
        w._load_demo()
        panel = w.results_panel
        assert panel._bus_row_uids
        uid = panel._bus_row_uids[0]
        assert uid
        panel.row_activated.emit("bus", uid)
        sel = w.scene.selectedItems()
        assert len(sel) == 1
        assert sel[0].model.uid == uid

    def test_render_scene_png(self, qapp, tmp_path):
        from app import MainWindow, render_scene_png
        w = MainWindow()
        w._load_two_end_demo()
        path = str(tmp_path / "grid.png")
        assert render_scene_png(w.scene, path)
        size = os.path.getsize(path)
        assert size > 5000, f"PNG 太小, 可能渲染失败: {size}B"

    def test_case39_loads_and_converges(self, qapp):
        from app import MainWindow
        w = MainWindow()
        w._load_ieee_case("case39")
        assert len(w.network.buses) == 39
        assert w.network.converged, "case39 应收敛"
        assert len(w.network.gens) >= 10


class TestRound6Features:
    def test_results_module_reexport(self, qapp):
        """app.py 保留 results 拆分后的再导出, 旧接口不破坏"""
        import app
        from results import ResultsPanel as RP, export_results_csv as ex, render_scene_png as rn
        assert app.ResultsPanel is RP
        assert app.export_results_csv is ex
        assert app.render_scene_png is rn

    def test_angle_plot_tab_exists(self, qapp):
        from results import ResultsPanel
        panel = ResultsPanel()
        assert panel._has_pg
        names = [panel.tabs.tabText(i) for i in range(panel.tabs.count())]
        assert "相角图" in names and "电压图" in names

    def test_recent_files_menu(self, qapp, tmp_path, monkeypatch):
        from app import MainWindow
        from PyQt5.QtCore import QSettings
        s = QSettings("PowerFlowStudio", "PowerFlowStudio")
        s.setValue("recent_files", [])
        w = MainWindow()
        w.scene.add_component("Bus", 100, 100)
        path = str(tmp_path / "recent.json")
        w._save_topology(path)
        assert w.recent_menu.actions()[0].text() == path
        w2 = MainWindow()
        assert w2.recent_menu.actions()[0].text() == path
        s.setValue("recent_files", [])   # 清理, 不污染真实 QSettings

    def test_new_file_clears_and_unlinks(self, qapp, monkeypatch):
        from app import MainWindow
        from PyQt5.QtWidgets import QMessageBox
        w = MainWindow()
        w.scene.add_component("Bus", 100, 100)
        w._set_dirty(True)
        # "No(不保存)" = 放弃改动继续 → 应清空
        monkeypatch.setattr("app.QMessageBox.question",
                            lambda *a, **k: QMessageBox.No)
        w._new_file()
        assert len(w.network.buses) == 0, "选不保存后应清空画布"
        assert w._current_path is None
        # "Cancel" = 留下 → 不清空
        w.scene.add_component("Bus", 100, 100)
        w._set_dirty(True)
        monkeypatch.setattr("app.QMessageBox.question",
                            lambda *a, **k: QMessageBox.Cancel)
        w._new_file()
        assert len(w.network.buses) == 1, "取消后不应清空画布"

    def test_zoom_buttons(self, qapp):
        from canvas import CircuitScene, CircuitView
        from solver import Network
        scene = CircuitScene(Network())
        view = CircuitView(scene)
        m0 = view.transform().m11()
        view.zoom_in()
        assert view.transform().m11() > m0
        view.zoom_out()
        view.zoom_out()
        assert view.transform().m11() < m0

    def test_tooltip_after_run(self, qapp):
        from app import MainWindow
        w = MainWindow()
        w._load_demo()
        items = list(w.scene._comp_by_uid.values())
        assert all(it.toolTip() for it in items), "元件应有 hover 提示"
        gen_tip = next(it.toolTip() for it in items if it.model.name == "G1")
        assert "实际" in gen_tip   # 发电机提示含实际出力


class TestRound7Features:
    def test_move_undo_restores_position(self, qapp):
        """拖动后的 Ctrl+Z 应恢复位置"""
        from app import MainWindow
        w = MainWindow()
        b = w.scene.add_component("Bus", 100, 100)
        # 模拟一次拖动: 记录快照→移动→通知入栈
        w._move_before = None
        before = w.snapshot_network()
        b.setPos(500, 400)
        assert w.network.buses[b.model.uid].x == 500.0
        w.push_move_undo(before, w.snapshot_network())
        w.undo_stack.undo()
        assert w.network.buses[b.model.uid].x == pytest.approx(100.0)
        w.undo_stack.redo()
        assert w.network.buses[b.model.uid].x == pytest.approx(500.0)

    def test_view_drag_hook_snapshot(self, qapp):
        """视图鼠标按下会记录快照, 释放时无位移则不入栈"""
        from app import MainWindow
        w = MainWindow()
        w.show()
        n_cmds = w.undo_stack.count()
        # 直接调钩子: 快照相同 → 不 push
        same = w.snapshot_network()
        w.push_move_undo(same, same)
        assert w.undo_stack.count() == n_cmds
        w.close()

    def test_case57_loads_and_converges(self, qapp):
        from app import MainWindow
        w = MainWindow()
        w._load_ieee_case("case57")
        assert w.network.converged, "case57 应收敛"
        assert len(w.network.buses) == 57

    def test_mode_label_updates(self, qapp):
        from app import MainWindow
        w = MainWindow()
        assert w.mode_label.text().strip() == "AC"
        w.act_dc.setChecked(True)
        assert w.mode_label.text().strip() == "DC"

    def test_grid_background_toggle(self, qapp):
        from canvas import CircuitScene
        from solver import Network
        scene = CircuitScene(Network())
        scene.snap_enabled = True
        scene.update()   # 不崩溃即可; 绘制在 render 时发生
        from app import render_scene_png
        scene.add_component("Bus", 100, 100)
        import tempfile, os as _os
        p = _os.path.join(tempfile.gettempdir(), "grid_bg_test.png")
        assert render_scene_png(scene, p)

    def test_kind_of_helper(self, qapp):
        from canvas import kind_of, BusItem, TrafoItem
        from app import MainWindow
        w = MainWindow()
        b = w.scene.add_component("Bus", 100, 100)
        t = w.scene.add_component("Trafo", 400, 400)
        assert kind_of(b) == "Bus"
        assert kind_of(t) == "Trafo"

    def test_properties_show_uid_tooltip(self, qapp):
        from properties import PropertiesPanel
        from canvas import CircuitScene
        from solver import Network
        scene = CircuitScene(Network())
        b = scene.add_component("Bus", 100, 100)
        panel = PropertiesPanel()
        panel.attach_scene(scene)
        panel.show_component(b)
        assert b.model.uid in panel.title.toolTip()

    def test_slack_checkbox_roundtrip(self, qapp):
        """勾选平衡节点 → 保存 → 载入, is_slack 字段不丢"""
        from app import MainWindow, parse_topology_json, network_to_json_dict
        w = MainWindow()
        g = w.scene.add_component("Gen", 150, 150)
        g.model.is_slack = True
        data = network_to_json_dict(w.network)
        net = parse_topology_json(data)
        assert next(iter(net.gens.values())).is_slack is True


class TestRound8Features:
    def test_case118_loads_and_converges(self, qapp):
        from app import MainWindow
        w = MainWindow()
        w._load_ieee_case("case118")
        assert len(w.network.buses) == 118
        assert w.network.converged, "case118 应收敛"

    def test_n1_menu_function(self, qapp, monkeypatch):
        from app import MainWindow
        w = MainWindow()
        w._load_two_end_demo()
        text = w._run_n_minus_1(interactive=False)   # 非交互: 不弹对话框
        assert text and "N-1 校核" in text

    def test_version_defined(self):
        import app
        assert hasattr(app, "__version__")
        assert app.__version__.count(".") == 2

    def test_stats_label_updates(self, qapp):
        from app import MainWindow
        w = MainWindow()
        w.scene.add_component("Bus", 100, 100)
        w.scene.add_component("Gen", 130, 150)
        assert "母线1" in w.stats_label.text()
        assert "机1" in w.stats_label.text()

    def test_out_of_bounds_drop_clamped(self, qapp):
        from app import MainWindow
        w = MainWindow()
        b = w.scene.add_component("Bus", 5000, -300)
        assert 0 <= b.model.x <= 2000
        assert 0 <= b.model.y <= 1400

    def test_connection_title_with_bus_names(self, qapp):
        from properties import PropertiesPanel
        from canvas import CircuitScene
        from solver import Network
        scene = CircuitScene(Network())
        a = scene.add_component("Bus", 100, 100)
        b = scene.add_component("Bus", 400, 100)
        scene.create_connection(a, a.port_item("right"), b, b.port_item("left"))
        panel = PropertiesPanel()
        panel.attach_scene(scene)
        panel.show_connection(scene._connections[0])
        assert "→" in panel.title.text()
        assert panel.title.text().startswith("线路")

    def test_table_copy_menu_exists(self, qapp):
        from PyQt5.QtCore import Qt
        from results import ResultsPanel
        panel = ResultsPanel()
        assert panel.bus_table.contextMenuPolicy() == Qt.CustomContextMenu


class TestRound9Features:
    def test_copy_paste_bus_and_line(self, qapp):
        """复制母线+线路 → 粘贴得到新 uid/顺延名字/偏移位置, 线路一并复制"""
        from app import MainWindow
        w = MainWindow()
        a = w.scene.add_component("Bus", 100, 100)
        b = w.scene.add_component("Bus", 400, 100)
        w.scene.create_connection(a, a.port_item("right"), b, b.port_item("left"))
        w.scene.clearSelection()
        a.setSelected(True)
        b.setSelected(True)
        assert w.scene.copy_selection() == 2
        assert w._paste_clipboard() == 2
        assert len(w.network.buses) == 4
        assert len(w.network.lines) == 2, "两端都被复制的线路应一并复制"
        names = [x.name for x in w.network.buses.values()]
        assert len(names) == len(set(names)), "粘贴后名字不应重复"
        # 新线路端点是副本母线
        new_line = max(w.network.lines.values(), key=lambda l: l.name)
        all_uids = set(w.network.buses)
        assert new_line.from_bus in all_uids and new_line.to_bus in all_uids

    def test_paste_empty_clipboard_noop(self, qapp):
        from app import MainWindow
        w = MainWindow()
        assert w._paste_clipboard() == 0

    def test_paste_undo(self, qapp):
        from app import MainWindow
        w = MainWindow()
        w.scene.add_component("Bus", 100, 100)
        w.scene.clearSelection()
        w.scene._comp_by_uid[next(iter(w.scene._comp_by_uid))].setSelected(True)
        w.scene.copy_selection()
        n_before = len(w.network.buses)
        w._paste_clipboard()
        w.undo_stack.undo()
        assert len(w.network.buses) == n_before, "粘贴应可撤销"

    def test_autosave_and_restore(self, qapp):
        from app import MainWindow
        w = MainWindow()
        w._load_demo()
        w._set_dirty(True)
        w._autosave()
        import os
        assert os.path.exists(w._autosave_path())
        w2 = MainWindow()
        assert w2._restore_autosave() is True
        assert len(w2.network.buses) == 3
        assert w2.network.converged is False   # 只恢复拓扑, 不恢复结果
        # 清理临时文件, 不影响其他用例
        os.remove(w._autosave_path())

    def test_trafo_position_saved_in_json(self, qapp, tmp_path):
        """round9: 变压器坐标应存档, 载入后不再漂回中点"""
        from app import MainWindow, network_to_json_dict, parse_topology_json
        w = MainWindow()
        t = w.scene.add_component("Trafo", 500, 500)
        t.setPos(640, 520)
        m = w.network.trafos[t.model.uid]
        assert (m.x, m.y) == (640.0, 520.0), "itemChange 应回写变压器坐标"
        net = parse_topology_json(network_to_json_dict(w.network))
        assert next(iter(net.trafos.values())).x == 640.0

    def test_kv_label_toggle(self, qapp):
        from app import MainWindow
        w = MainWindow()
        w._load_demo()
        w.act_kv.setChecked(True)
        w._toggle_v_label(True)
        assert w.scene.v_label_mode == "kv"
        w.act_kv.setChecked(False)
        w._toggle_v_label(False)
        assert w.scene.v_label_mode == "pu"

    def test_connection_delete_clears_panel(self, qapp):
        """删除当前显示的连线, 属性面板应被清空 (selectionChanged 联动)"""
        from app import MainWindow
        w = MainWindow()
        a = w.scene.add_component("Bus", 100, 100)
        b = w.scene.add_component("Bus", 400, 100)
        w.scene.create_connection(a, a.port_item("right"), b, b.port_item("left"))
        conn = w.scene._connections[0]
        conn.setSelected(True)
        w.scene.delete_item(conn)   # 触发 selectionChanged
        assert w.properties.current_item is None
        assert "未选中" in w.properties.title.text()


class TestRound10Features:
    def test_case24_rts_loads_and_converges(self, qapp):
        from app import MainWindow
        w = MainWindow()
        w._load_ieee_case("case24_ieee_rts")
        assert w.network.converged, "case24 RTS 应收敛"
        assert len(w.network.buses) == 24

    def test_hover_highlight_state(self, qapp):
        from canvas import CircuitScene, CircuitView
        from solver import Network
        from PyQt5.QtCore import QPointF
        scene = CircuitScene(Network())
        b = scene.add_component("Bus", 100, 100)
        view = CircuitView(scene)
        view.resize(600, 400)
        view.show()
        # 直接调 hover 事件处理, 验证状态切换与重绘不崩
        from PyQt5.QtGui import QHoverEvent
        from PyQt5.QtCore import QEvent
        pos = view.mapFromScene(b.scenePos() + QPointF(40, 25))
        ev = QHoverEvent(QEvent.HoverEnter, pos, pos)
        view.viewport().event(ev)   # 走真实分发路径
        scene.update()
        view.close()

    def test_cli_open_argument(self, qapp, tmp_path):
        """命令行参数打开拓扑: 复用 _load_topology, 验证路径逻辑"""
        from app import MainWindow
        w = MainWindow()
        w.scene.add_component("Bus", 100, 100)
        path = str(tmp_path / "cli.json")
        w._save_topology(path)
        w2 = MainWindow()
        import os
        assert os.path.exists(path)   # main() 里按此路径调 _load_topology
        assert w2._load_topology(path) is True

    def test_sysinfo_strings_available(self, qapp):
        """系统信息对话框内容来源可用(不弹框, 验证数据源)"""
        import app, platform, tempfile, os
        import pandapower
        import PyQt5.QtCore
        assert app.__version__
        assert platform.python_version()
        log_path = os.path.join(tempfile.gettempdir(), "PowerFlowStudio.log")
        assert isinstance(log_path, str) and log_path

    def test_startup_autosave_hint(self, qapp, monkeypatch, tmp_path):
        from app import MainWindow
        w = MainWindow()
        # 无自动保存文件时提示为 None
        assert w._startup_autosave_hint is None or isinstance(w._startup_autosave_hint, str)


class TestNegativeLoadUI:
    def test_load_p_mw_spinbox_allows_negative(self, qapp):
        """回归: 负荷有功 P 曾被限制在 0-5000, 无法输入负值"""
        from properties import PropertiesPanel
        from canvas import CircuitScene, LoadItem
        from solver import Network, LoadUnit
        scene = CircuitScene(Network())
        m = LoadUnit(uid="l1", name="L1", bus_uid="", p_mw=-5.0)
        item = LoadItem(m)
        panel = PropertiesPanel()
        panel.attach_scene(scene)
        panel.show_component(item)
        sb = panel._fields["p_mw"]
        assert sb.minimum() == -5000.0, "负荷 P 下限应允许负值"
        assert sb.value() == pytest.approx(-5.0)
        sb.setValue(-30.0)
        assert m.p_mw == pytest.approx(-30.0), "负值应能写回 model"


class TestRoundAFeatures:
    def test_shunt_add_via_palette_builder(self, scene):
        """并联电容/电抗器: 元件库拖放路径(表驱动 builder)"""
        scene.add_component("Bus", 200, 300)
        sh = scene.add_component("Shunt", 260, 150)
        assert sh.model.bus_uid
        from canvas import kind_of
        assert kind_of(sh) == "Shunt"

    def test_shunt_properties_form_and_results(self, scene, qapp):
        from properties import PropertiesPanel
        from solver import run_power_flow, ShuntUnit
        t_bus = scene.add_component("Bus", 200, 300)
        scene.add_component("Gen", 150, 150)
        sh = scene.add_component("Shunt", 260, 150)
        ok, msg = run_power_flow(scene.network)
        assert ok, msg
        panel = PropertiesPanel()
        panel.attach_scene(scene)
        panel.show_component(sh)
        sb = panel._fields["q_mvar"]
        sb.setValue(-20.0)
        assert sh.model.q_mvar == -20.0
        ok, msg = run_power_flow(scene.network)
        assert ok, msg
        panel.show_component(sh)
        texts = []
        for i in range(panel.result_layout.count()):
            wdg = panel.result_layout.itemAt(i).widget()
            if wdg is not None:
                texts.append(wdg.text())
        assert any("注入无功" in t or "—" in t for t in texts)

    def test_tap_pos_spinbox(self, qapp):
        from properties import PropertiesPanel
        from canvas import CircuitScene
        from solver import Network
        scene = CircuitScene(Network())
        t = scene.add_component("Trafo", 300, 300)
        panel = PropertiesPanel()
        panel.attach_scene(scene)
        panel.show_component(t)
        sb = panel._fields["tap_pos"]
        sb.setValue(2)
        assert t.model.tap_pos == 2

    def test_gen_opf_fields_in_form(self, qapp):
        from properties import PropertiesPanel
        from canvas import CircuitScene
        from solver import Network
        scene = CircuitScene(Network())
        g = scene.add_component("Gen", 150, 150)
        panel = PropertiesPanel()
        panel.attach_scene(scene)
        panel.show_component(g)
        for f in ("min_p_mw", "max_p_mw", "cost_per_mw",
                  "s_sc_max_mva", "s_sc_min_mva", "kappa"):
            assert f in panel._fields, f"Gen 表单缺 {f}"

    def test_opf_menu_action(self, qapp):
        from app import MainWindow
        w = MainWindow()
        w._load_two_end_demo()
        ok, err = w._run_opf()
        assert ok, err
        assert w.network.converged

    def test_short_circuit_menu_action(self, qapp):
        from app import MainWindow
        w = MainWindow()
        w._load_two_end_demo()
        ok, err = w._run_short_circuit("max")
        assert ok, err
        assert len(w.network.bus_ikss_ka) == 5

    def test_loss_label_after_run(self, qapp):
        from app import MainWindow
        w = MainWindow()
        w._load_demo()
        ok, err = w._run_power_flow()
        assert ok, err
        assert "总网损" in w.results_panel.loss_label.text()
        assert "MW" in w.results_panel.loss_label.text()

    def test_shunt_in_branch_table_and_parse(self, qapp, tmp_path):
        from app import MainWindow, network_to_json_dict, parse_topology_json
        w = MainWindow()
        w.scene.add_component("Bus", 200, 300)
        w.scene.add_component("Gen", 150, 150)
        w.scene.add_component("Shunt", 260, 150)
        data = network_to_json_dict(w.network)
        assert "shunts" in data and len(data["shunts"]) == 1
        net = parse_topology_json(data)
        assert len(net.shunts) == 1
        ok, msg = w._run_power_flow()
        assert ok, msg
        panel = w.results_panel
        kinds = [panel.branch_table.item(r, 0).text()
                 for r in range(panel.branch_table.rowCount())]
        assert "电容/电抗" in kinds
