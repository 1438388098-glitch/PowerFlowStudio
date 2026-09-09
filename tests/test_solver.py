"""
tests/test_solver.py — solver 层回归基线
solver.py 不依赖 Qt, 可直接对 Network 数据模型跑潮流断言结果。
运行: .venv/Scripts/python -m pytest tests -q
"""
import math

import pytest

from solver import (
    Network, BusNode, GenUnit, LoadUnit,
    LineBranch, TrafoBranch, ImpedanceBranch,
    run_power_flow,
)


def make_bus(uid, name, x=0.0, y=0.0, vn_kv=110.0):
    return BusNode(uid=uid, name=name, x=x, y=y, vn_kv=vn_kv)


def make_line(uid, name, from_bus, to_bus, length_km=10.0,
              r_ohm_per_km=0.4, x_ohm_per_km=0.4, max_i_ka=0.6):
    return LineBranch(uid=uid, name=name, from_bus=from_bus, to_bus=to_bus,
                      length_km=length_km, r_ohm_per_km=r_ohm_per_km,
                      x_ohm_per_km=x_ohm_per_km, max_i_ka=max_i_ka)


def build_3bus_network():
    """B1(G1, slack) --line--> B2(L1) ; B1 --line--> B3(L2)"""
    net = Network()
    net.buses["b1"] = make_bus("b1", "B1", 0, 0)
    net.buses["b2"] = make_bus("b2", "B2", 300, 0)
    net.buses["b3"] = make_bus("b3", "B3", 300, 250)
    net.gens["g1"] = GenUnit(uid="g1", name="G1", bus_uid="b1",
                             p_mw=50, vm_pu=1.0)
    net.loads["l1"] = LoadUnit(uid="l1", name="L1", bus_uid="b2",
                               p_mw=10, q_mvar=5)
    net.loads["l2"] = LoadUnit(uid="l2", name="L2", bus_uid="b3",
                               p_mw=10, q_mvar=5)
    net.lines["ln1"] = make_line("ln1", "L1线", "b1", "b2")
    net.lines["ln2"] = make_line("ln2", "L2线", "b1", "b3")
    return net


def build_5bus_two_end_network():
    """两端供电: G1(slack)-B1-B2-B3-B4-B5-G2(PV), B2/B4 带负荷"""
    net = Network()
    names = ["b1", "b2", "b3", "b4", "b5"]
    for i, uid in enumerate(names):
        net.buses[uid] = make_bus(uid, "B%d" % (i + 1), i * 200.0, 0)
    net.gens["g1"] = GenUnit(uid="g1", name="G1", bus_uid="b1",
                             p_mw=50, vm_pu=1.05)
    net.gens["g2"] = GenUnit(uid="g2", name="G2", bus_uid="b5",
                             p_mw=40, vm_pu=1.05)
    net.loads["l1"] = LoadUnit(uid="l1", name="L1", bus_uid="b2",
                               p_mw=30, q_mvar=10)
    net.loads["l2"] = LoadUnit(uid="l2", name="L2", bus_uid="b4",
                               p_mw=20, q_mvar=8)
    for i in range(4):
        net.lines["ln%d" % (i + 1)] = make_line(
            "ln%d" % (i + 1), "线%d" % (i + 1), names[i], names[i + 1])
    return net


class TestValidation:
    def test_no_buses(self):
        ok, msg = run_power_flow(Network())
        assert ok is False
        assert msg

    def test_no_gen(self):
        net = Network()
        net.buses["b1"] = make_bus("b1", "B1")
        ok, msg = run_power_flow(net)
        assert ok is False
        assert "电源" in msg or "平衡" in msg


class TestThreeBus:
    def test_converged(self):
        net = build_3bus_network()
        ok, msg = run_power_flow(net)
        assert ok, msg
        assert net.converged is True

    def test_all_bus_results_present(self):
        net = build_3bus_network()
        ok, _ = run_power_flow(net)
        assert ok
        for uid in ("b1", "b2", "b3"):
            assert uid in net.bus_voltage_pu
            assert uid in net.bus_voltage_kv
            assert uid in net.bus_va_degree

    def test_power_balance(self):
        """发电 = 负荷 + 网损, 网损为正"""
        net = build_3bus_network()
        ok, _ = run_power_flow(net)
        assert ok
        gen_p = sum(net.gen_p_mw.values())
        load_p = sum(net.load_p_mw.values())
        loss = gen_p - load_p
        assert loss > 0
        assert loss < 5.0  # 短线路网损应很小

    def test_voltage_drop_towards_load(self):
        """负荷母线电压应低于(或等于)平衡节点"""
        net = build_3bus_network()
        ok, _ = run_power_flow(net)
        assert ok
        assert net.bus_voltage_pu["b2"] <= net.bus_voltage_pu["b1"] + 1e-6
        assert net.bus_voltage_pu["b3"] <= net.bus_voltage_pu["b1"] + 1e-6

    def test_kv_conversion(self):
        net = build_3bus_network()
        ok, _ = run_power_flow(net)
        assert ok
        for uid in net.buses:
            expect = net.bus_voltage_pu[uid] * net.buses[uid].vn_kv
            assert net.bus_voltage_kv[uid] == pytest.approx(expect, rel=1e-6)

    def test_line_results_present(self):
        net = build_3bus_network()
        ok, _ = run_power_flow(net)
        assert ok
        for uid in ("ln1", "ln2"):
            assert uid in net.line_loading_percent
            assert uid in net.line_p_from_mw
            assert uid in net.line_q_from_mvar
            assert net.line_loading_percent[uid] >= 0


class TestTwoEndSupply:
    def test_converged(self):
        net = build_5bus_two_end_network()
        ok, msg = run_power_flow(net)
        assert ok, msg

    def test_gens_share_load(self):
        """两端发电机都出力, 总发电 = 总负荷 + 网损(网损为正, 约1-2MW)"""
        net = build_5bus_two_end_network()
        ok, _ = run_power_flow(net)
        assert ok
        gen_p = sum(net.gen_p_mw.values())
        load_p = sum(net.load_p_mw.values())
        assert gen_p == pytest.approx(load_p, abs=5.0)
        assert gen_p > load_p
        assert net.gen_p_mw["g1"] > 0 and net.gen_p_mw["g2"] > 0

    def test_pv_node_q_result(self):
        """PV 节点 G2 的实际无功应有结果值"""
        net = build_5bus_two_end_network()
        ok, _ = run_power_flow(net)
        assert ok
        assert "g2" in net.gen_q_mvar
        assert math.isfinite(net.gen_q_mvar["g2"])


class TestDuplicateNames:
    def test_same_display_name_results_not_mixed(self):
        """两条母线显示名相同, 结果也不得串位 (回归: name 曾被当 uid 用)"""
        net = Network()
        net.buses["bx1"] = make_bus("bx1", "同名", 0, 0)
        net.buses["bx2"] = make_bus("bx2", "同名", 300, 0)
        net.gens["g1"] = GenUnit(uid="g1", name="G1", bus_uid="bx1",
                                 p_mw=50, vm_pu=1.0)
        net.loads["l1"] = LoadUnit(uid="l1", name="负荷", bus_uid="bx1",
                                   p_mw=1, q_mvar=0)
        net.loads["l2"] = LoadUnit(uid="l2", name="负荷", bus_uid="bx2",
                                   p_mw=60, q_mvar=20)
        net.lines["lnA"] = make_line("lnA", "同名线", "bx1", "bx2")
        ok, msg = run_power_flow(net)
        assert ok, msg
        # 轻载母线电压应接近 1.0, 重载母线明显被拉低 — 两者不能相等
        v_light = net.bus_voltage_pu["bx1"]
        v_heavy = net.bus_voltage_pu["bx2"]
        assert v_light != pytest.approx(v_heavy, abs=1e-3)
        assert v_light > v_heavy

    def test_same_line_name_results_distinct(self):
        """两条线路显示名相同, 各自负载率不得互相覆盖"""
        net = build_3bus_network()
        net.lines["ln1"].name = "同名线"
        net.lines["ln2"].name = "同名线"
        # 把 L2 负荷加大, 使 ln1 与 ln2 负载率必然不同
        net.loads["l2"].p_mw = 40
        net.loads["l2"].q_mvar = 20
        ok, msg = run_power_flow(net)
        assert ok, msg
        assert net.line_loading_percent["ln1"] != pytest.approx(
            net.line_loading_percent["ln2"], abs=1e-3)


class TestIsolatedBus:
    def test_isolated_bus_nan_not_crash(self):
        """孤立母线(无连接)潮流仍收敛, 电压为 NaN — 不得抛异常"""
        net = build_3bus_network()
        net.buses["bx"] = make_bus("bx", "孤立", 900, 900)
        ok, msg = run_power_flow(net)
        assert ok, msg
        assert "bx" in net.bus_voltage_pu
        assert math.isnan(net.bus_voltage_pu["bx"])


class TestTrafoAndImpedance:
    def test_trafo_branch(self):
        net = Network()
        net.buses["hv"] = make_bus("hv", "HV", 0, 0, vn_kv=110.0)
        net.buses["lv"] = make_bus("lv", "LV", 300, 0, vn_kv=35.0)
        net.gens["g1"] = GenUnit(uid="g1", name="G1", bus_uid="hv",
                                 p_mw=50, vm_pu=1.0)
        net.loads["l1"] = LoadUnit(uid="l1", name="L1", bus_uid="lv",
                                   p_mw=20, q_mvar=8)
        net.trafos["t1"] = TrafoBranch(uid="t1", name="T1",
                                       hv_bus="hv", lv_bus="lv")
        ok, msg = run_power_flow(net)
        assert ok, msg
        assert "t1" in net.trafo_loading_percent
        assert net.trafo_loading_percent["t1"] > 0
        assert "t1" in net.trafo_p_hv_mw
        assert "hv" in net.bus_voltage_pu and "lv" in net.bus_voltage_pu

    def test_impedance_branch(self):
        net = Network()
        net.buses["b1"] = make_bus("b1", "B1", 0, 0)
        net.buses["b2"] = make_bus("b2", "B2", 300, 0)
        net.gens["g1"] = GenUnit(uid="g1", name="G1", bus_uid="b1",
                                 p_mw=50, vm_pu=1.0)
        net.loads["l1"] = LoadUnit(uid="l1", name="L1", bus_uid="b2",
                                   p_mw=15, q_mvar=5)
        net.impedances["z1"] = ImpedanceBranch(uid="z1", name="Z1",
                                               from_bus="b1", to_bus="b2")
        ok, msg = run_power_flow(net)
        assert ok, msg
        assert "b2" in net.bus_voltage_pu
        assert net.bus_voltage_pu["b2"] > 0.5
        # 阻抗结果已提取 (round3: impedance P/Q 回写)
        assert "z1" in net.impedance_p_from_mw
        assert "z1" in net.impedance_p_to_mw
        assert abs(net.impedance_p_from_mw["z1"]) > 0


class TestRerunClearsOldResults:
    def test_removed_element_results_cleared(self):
        net = build_3bus_network()
        ok, _ = run_power_flow(net)
        assert ok
        assert "ln2" in net.line_loading_percent
        # 删掉一条线再跑, 旧结果不得残留
        del net.lines["ln2"]
        ok, msg = run_power_flow(net)
        assert ok, msg
        assert "ln2" not in net.line_loading_percent


class TestDcPowerFlow:
    def test_dc_mode_converges(self):
        """DC 直流潮流: 收敛, 相角有结果, 有功近似平衡"""
        net = build_3bus_network()
        ok, msg = run_power_flow(net, algorithm="dc")
        assert ok, msg
        assert len(net.bus_va_degree) == 3
        gen_p = sum(net.gen_p_mw.values())
        load_p = sum(net.load_p_mw.values())
        assert gen_p == pytest.approx(load_p, abs=1.0)

    def test_default_algorithm_is_nr(self):
        net = build_3bus_network()
        ok, _ = run_power_flow(net)
        assert ok
        # NR 模式下电压有真实结果(非恒定 1.0)
        assert any(abs(v - 1.0) > 1e-6 for v in net.bus_voltage_pu.values())


class TestSlackSelection:
    def test_second_gen_marked_slack_becomes_reference(self):
        """勾选 is_slack 的第二台发电机应成为平衡节点 (电压设定生效于其母线)"""
        net = build_3bus_network()
        # G1 在 b1 (vm 1.0), 把 G2 挂到 b3 并设为平衡节点 vm=1.05
        del net.gens["g1"]
        net.gens["g2"] = GenUnit(uid="g2", name="G2", bus_uid="b3",
                                 p_mw=30, vm_pu=1.05, is_slack=True)
        ok, msg = run_power_flow(net)
        assert ok, msg
        assert net.bus_voltage_pu["b3"] == pytest.approx(1.05, abs=1e-6)
        assert net.bus_voltage_pu["b3"] != pytest.approx(1.0, abs=1e-3)

    def test_default_slack_is_first_gen(self):
        net = build_5bus_two_end_network()
        ok, _ = run_power_flow(net)
        assert ok
        # G1 vm=1.05 且是第一台 → b1 电压 = 1.05
        assert net.bus_voltage_pu["b1"] == pytest.approx(1.05, abs=1e-6)


class TestResultDirectionality:
    def test_line_power_flows_toward_load(self):
        """3 母线: 线路从平衡节点流向负荷侧, p_from 为正"""
        net = build_3bus_network()
        ok, _ = run_power_flow(net)
        assert ok
        assert net.line_p_from_mw["ln1"] > 0
        assert net.line_p_from_mw["ln2"] > 0

    def test_trafo_power_hv_to_lv(self):
        net = Network()
        net.buses["hv"] = make_bus("hv", "HV", 0, 0, vn_kv=110.0)
        net.buses["lv"] = make_bus("lv", "LV", 300, 0, vn_kv=35.0)
        net.gens["g1"] = GenUnit(uid="g1", name="G1", bus_uid="hv", p_mw=50)
        net.loads["l1"] = LoadUnit(uid="l1", name="L1", bus_uid="lv",
                                   p_mw=20, q_mvar=8)
        net.trafos["t1"] = TrafoBranch(uid="t1", name="T1",
                                       hv_bus="hv", lv_bus="lv")
        ok, _ = run_power_flow(net)
        assert ok
        assert net.trafo_p_hv_mw["t1"] > 0, "功率应从高压侧流向低压侧"
        assert abs(net.trafo_p_hv_mw["t1"] - 20.0) < 2.0


class TestNMinus1:
    def test_healthy_network_passes(self):
        """3 母线网络任意开断一条线, 另一条线带双倍负荷可能重载 — 但不应崩溃"""
        from solver import n_minus_1_check, format_n1_report
        net = build_3bus_network()
        ok, _ = run_power_flow(net)
        assert ok
        report = n_minus_1_check(net)
        assert len(report) == 2   # 两条线路
        for uid, e in report.items():
            assert e["ok"] is True
            assert isinstance(e["overloads"], list)
        text = format_n1_report(report)
        assert "N-1 校核" in text

    def test_radial_network_isolation_detected(self):
        """开断唯一供电线路 → 对端母线孤立, 应被报告出来"""
        from solver import n_minus_1_check, format_n1_report
        net = build_3bus_network()
        # 改成辐射状: 只留 b1-b2, b3 挂 b2 后面
        del net.lines["ln2"]
        net.lines["ln2b"] = make_line("ln2b", "线2", "b2", "b3")
        ok, _ = run_power_flow(net)
        assert ok
        report = n_minus_1_check(net)
        # 开断 ln1 → b1 孤立但 b1 无源; 开断 ln2b → b3 孤立
        e = report["ln2b"]
        assert e["ok"] is True
        assert "B3" in e["isolated"] or "B2" in e["isolated"], e
        text = format_n1_report(report)
        assert "孤立" in text

    def test_base_failure_reported(self):
        from solver import n_minus_1_check, format_n1_report
        net = Network()
        report = n_minus_1_check(net)
        assert "_base_failed" in report
        assert "失败" in format_n1_report(report)


class TestNegativeLoad:
    def test_negative_load_is_generation(self):
        """负负荷 = 该点注入功率, 潮流应收敛且结果回写负值"""
        net = build_3bus_network()
        net.loads["l1"].p_mw = -5.0   # B2 上等效注入 5 MW
        ok, msg = run_power_flow(net)
        assert ok, msg
        assert net.load_p_mw["l1"] == pytest.approx(-5.0)
        # 平衡节点少带 5MW: 发电总量 = 总负荷(净) + 网损
        gen_p = sum(net.gen_p_mw.values())
        net_load_p = sum(net.load_p_mw.values())
        assert gen_p == pytest.approx(net_load_p, abs=5.0)
        assert gen_p > net_load_p   # 网损仍为正
