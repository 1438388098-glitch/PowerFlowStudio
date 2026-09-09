"""
tests/test_e2e_journey.py — 端到端用户旅程演练
一次完整链路: 建网→改参→计算→存取→撤销重做→N-1→CSV/PNG→自动保存→复制粘贴。
目的: 捕捉功能之间组合时的回归(单元测试各自通过不代表组合无冲突)。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def test_full_user_journey(qapp, tmp_path):
    from app import (
        MainWindow, export_results_csv, render_scene_png,
        parse_topology_json, network_to_json_dict,
    )
    from results import ResultsPanel
    from canvas import BusItem
    from solver import run_power_flow

    w = MainWindow()
    w.show()

    # 1. 加载两端供电示例, 自动收敛
    w._load_two_end_demo()
    assert w.network.converged
    uid_of = {b.name: u for u, b in w.network.buses.items()}
    v_before = dict(w.network.bus_voltage_pu)

    # 2. 改负荷参数 → 重算 → 结果应变化
    load1 = next(l for l in w.network.loads.values() if l.name == "L1")
    load1.p_mw = 90.0     # 大幅加重 L1
    ok, msg = w._run_power_flow()
    assert ok, msg
    v_after = w.network.bus_voltage_pu
    b2 = uid_of["B2"]
    assert v_after[b2] < v_before[b2] - 1e-4, "加重负荷后 B2 电压应下降"

    # 3. 保存 → 再加元件 → 撤销 → 重做 → 载入
    path = str(tmp_path / "journey.json")
    assert w._save_topology(path) == path
    w.scene.add_component("Bus", 1200, 200)
    w.scene.add_component("Gen", 1230, 100)
    n_after_add = len(w.network.buses)
    assert n_after_add == 6
    w.undo_stack.undo()          # 撤销加 Gen (两端供电原有 2 台)
    assert len(w.network.gens) == 2
    w.undo_stack.undo()          # 撤销加 Bus
    assert len(w.network.buses) == 5
    w.undo_stack.redo()
    assert len(w.network.buses) == 6
    w.undo_stack.undo()          # 回到 5 母线(与保存文件一致)
    assert w._load_topology(path) is True
    assert len(w.network.buses) == 5
    assert w.network.converged is False   # 载入只带拓扑

    # 4. 重算 + N-1 校核(非交互)
    ok, msg = w._run_power_flow()
    assert ok, msg
    text = w._run_n_minus_1(interactive=False)
    assert text and "N-1 校核" in text

    # 5. 导出 CSV 与 PNG
    paths = export_results_csv(w.network, str(tmp_path / "rep"))
    for p in paths.values():
        assert os.path.exists(p) and os.path.getsize(p) > 0
    png = str(tmp_path / "grid.png")
    assert render_scene_png(w.scene, png)

    # 6. 自动保存 → 新窗口恢复
    w._set_dirty(True)
    w._autosave()
    assert os.path.exists(w._autosave_path())
    w2 = MainWindow()
    assert w2._restore_autosave() is True
    assert len(w2.network.buses) == 5

    # 7. 复制粘贴一条"母线+发电机"组合
    a = w2.scene._comp_by_uid[next(u for u, b in w2.network.buses.items()
                                   if b.name == "B1")]
    a.setSelected(True)
    assert w2.scene.copy_selection() == 1
    assert w2._paste_clipboard() == 1
    assert len(w2.network.buses) == 6

    # 8. 结果面板与总览联动
    ok, msg = w2._run_power_flow()
    assert ok, msg
    panel = w2.results_panel
    uid = panel._bus_row_uids[0]
    panel.row_activated.emit("bus", uid)
    sel = w2.scene.selectedItems()
    assert sel and sel[0].model.uid == uid

    # 9. 解析函数与导出的文件往返一致
    net = parse_topology_json(network_to_json_dict(w2.network))
    assert len(net.buses) == 6

    # 无未保存改动的窗口关闭不会弹确认
    w._set_dirty(False)
    w.close()
    w2._set_dirty(False)
    w2.close()
