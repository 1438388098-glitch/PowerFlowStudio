"""
ieee_cases.py — pandapower 标准测试系统 → Network 反向转换

README 扩展方向 #3: 一键加载 IEEE 标准算例。pandapower 自带这些系统,
转成画布的 Network 后即可用 run_power_flow 求解。
"""
from __future__ import annotations

from solver import (
    Network, BusNode, GenUnit, LoadUnit,
    LineBranch, TrafoBranch, ImpedanceBranch,
)

SUPPORTED_CASES = ("case14", "case24_ieee_rts", "case30", "case39",
                   "case57", "case118")


def load_case(name: str) -> Network:
    """加载标准算例并转成画布 Network。name 见 SUPPORTED_CASES。"""
    if name not in SUPPORTED_CASES:
        raise ValueError(f"不支持的算例: {name} (可选: {', '.join(SUPPORTED_CASES)})")
    import pandapower.networks as ppn
    pnet = getattr(ppn, name)()

    net = Network()

    # 母线: 网格布局摆放 (case 自带坐标不可靠)
    bus_uid: dict = {}
    n = len(pnet.bus)
    cols = max(1, round(n ** 0.5))
    for i, row in pnet.bus.iterrows():
        i = int(i)
        uid = f"b{i}"
        bus_uid[i] = uid
        net.buses[uid] = BusNode(
            uid=uid,
            name=str(row.get("name", "") or f"B{i + 1}"),
            x=150.0 + (i % cols) * 190.0,
            y=150.0 + (i // cols) * 180.0,
            vn_kv=float(row["vn_kv"]),
        )

    def _f(row, key, default=0.0):
        v = row.get(key, default)
        try:
            v = float(v)
        except (TypeError, ValueError):
            return default
        return default if v != v else v   # NaN -> default

    # 发电机: ext_grid 与 gen 表 (先插 ext_grid, 第一个 Gen 即平衡节点)
    for i, row in pnet.ext_grid.iterrows():
        uid = f"gs{int(i)}"
        net.gens[uid] = GenUnit(
            uid=uid, name=f"Slack{int(i) + 1}",
            bus_uid=bus_uid[int(row["bus"])], vm_pu=_f(row, "vm_pu", 1.0))
    for i, row in pnet.gen.iterrows():
        uid = f"gp{int(i)}"
        net.gens[uid] = GenUnit(
            uid=uid, name=f"G{int(i) + 1}",
            bus_uid=bus_uid[int(row["bus"])],
            p_mw=_f(row, "p_mw", 0.0), vm_pu=_f(row, "vm_pu", 1.0))

    # 负荷
    for i, row in pnet.load.iterrows():
        uid = f"l{int(i)}"
        net.loads[uid] = LoadUnit(
            uid=uid, name=f"Load{int(i) + 1}",
            bus_uid=bus_uid[int(row["bus"])],
            p_mw=_f(row, "p_mw"), q_mvar=_f(row, "q_mvar"))

    # 线路
    for i, row in pnet.line.iterrows():
        uid = f"ln{int(i)}"
        net.lines[uid] = LineBranch(
            uid=uid, name=f"Line{int(i) + 1}",
            from_bus=bus_uid[int(row["from_bus"])],
            to_bus=bus_uid[int(row["to_bus"])],
            r_ohm_per_km=_f(row, "r_ohm_per_km", 0.4),
            x_ohm_per_km=_f(row, "x_ohm_per_km", 0.4),
            c_nf_per_km=_f(row, "c_nf_per_km", 0.0),
            length_km=_f(row, "length_km", 1.0) or 1.0,
            max_i_ka=_f(row, "max_i_ka", 0.6) or 0.6,
        )

    # 变压器 (电压等级取两侧母线的 vn_kv)
    for i, row in pnet.trafo.iterrows():
        uid = f"t{int(i)}"
        hv = int(row["hv_bus"])
        lv = int(row["lv_bus"])
        net.trafos[uid] = TrafoBranch(
            uid=uid, name=f"Trafo{int(i) + 1}",
            hv_bus=bus_uid[hv], lv_bus=bus_uid[lv],
            sn_mva=_f(row, "sn_mva", 60.0) or 60.0,
            vn_hv_kv=float(pnet.bus.at[hv, "vn_kv"]),
            vn_lv_kv=float(pnet.bus.at[lv, "vn_kv"]),
            vk_percent=_f(row, "vk_percent", 10.0),
            vkr_percent=_f(row, "vkr_percent", 0.4),
            pfe_kw=_f(row, "pfe_kw", 0.0),
            i0_percent=_f(row, "i0_percent", 0.0),
        )

    # 串联阻抗 (部分算例没有)
    for i, row in pnet.impedance.iterrows():
        uid = f"z{int(i)}"
        net.impedances[uid] = ImpedanceBranch(
            uid=uid, name=f"Z{int(i) + 1}",
            from_bus=bus_uid[int(row["from_bus"])],
            to_bus=bus_uid[int(row["to_bus"])],
            rft_pu=_f(row, "rft_pu", 0.01),
            xft_pu=_f(row, "xft_pu", 0.01),
            sn_mva=_f(row, "sn_mva", 100.0) or 100.0,
        )

    return net
