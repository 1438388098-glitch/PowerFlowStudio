"""
solver.py — 把画布上的元件/连线拓扑转换成 pandapower 网络, 跑潮流, 把结果回写到元件
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional

import pandapower as pp


# ============================================================
# 1. 拓扑数据模型(画布层使用, 与 QGraphicsItem 解耦)
# ============================================================

@dataclass
class BusNode:
    """母线节点"""
    uid: str                       # 全局唯一 id
    name: str                      # 显示名, 例 "B1"
    x: float                       # 画布坐标
    y: float
    vn_kv: float = 110.0           # 额定电压(kV)


@dataclass
class GenUnit:
    """发电机 / 电源 (挂在母线上)"""
    uid: str
    name: str
    bus_uid: str                   # 挂接的母线 uid
    p_mw: float = 50.0             # 有功 MW
    vm_pu: float = 1.0             # 电压设定值 pu
    x: float = 0.0                 # 相对母线的偏移
    y: float = 0.0


@dataclass
class LoadUnit:
    """负荷 (挂在母线上)"""
    uid: str
    name: str
    bus_uid: str
    p_mw: float = 10.0
    q_mvar: float = 5.0
    x: float = 0.0
    y: float = 0.0


@dataclass
class LineBranch:
    """输电线路 (两个母线之间)"""
    uid: str
    name: str
    from_bus: str                  # 母线 uid
    to_bus: str
    r_ohm_per_km: float = 0.4
    x_ohm_per_km: float = 0.4
    c_nf_per_km: float = 0.0
    length_km: float = 10.0
    max_i_ka: float = 0.6


@dataclass
class TrafoBranch:
    """双绕组变压器 (两个母线之间, 电压等级不同)"""
    uid: str
    name: str
    hv_bus: str                    # 高压侧母线 uid
    lv_bus: str                    # 低压侧母线 uid
    sn_mva: float = 63.0
    vn_hv_kv: float = 110.0
    vn_lv_kv: float = 35.0
    vkr_percent: float = 0.4
    vk_percent: float = 10.0
    pfe_kw: float = 0.0
    i0_percent: float = 0.0
    shift_degree: float = 0.0


@dataclass
class ImpedanceBranch:
    """串联阻抗 (两个母线之间)"""
    uid: str
    name: str
    from_bus: str
    to_bus: str
    rft_pu: float = 0.01           # R 从 from 视角的 pu
    xft_pu: float = 0.01
    sn_mva: float = 100.0


@dataclass
class Network:
    """整张电网的拓扑(画布层)"""
    buses: Dict[str, BusNode] = field(default_factory=dict)
    gens: Dict[str, GenUnit] = field(default_factory=dict)
    loads: Dict[str, LoadUnit] = field(default_factory=dict)
    lines: Dict[str, LineBranch] = field(default_factory=dict)
    trafos: Dict[str, TrafoBranch] = field(default_factory=dict)
    impedances: Dict[str, ImpedanceBranch] = field(default_factory=dict)

    # 结果缓存(由 solver 写, 由画布层读)
    bus_voltage_pu: Dict[str, float] = field(default_factory=dict)
    bus_voltage_kv: Dict[str, float] = field(default_factory=dict)
    bus_va_degree: Dict[str, float] = field(default_factory=dict)
    line_loading_percent: Dict[str, float] = field(default_factory=dict)
    line_p_from_mw: Dict[str, float] = field(default_factory=dict)
    line_q_from_mvar: Dict[str, float] = field(default_factory=dict)
    line_p_to_mw: Dict[str, float] = field(default_factory=dict)
    line_q_to_mvar: Dict[str, float] = field(default_factory=dict)
    trafo_loading_percent: Dict[str, float] = field(default_factory=dict)
    trafo_p_hv_mw: Dict[str, float] = field(default_factory=dict)
    trafo_q_hv_mvar: Dict[str, float] = field(default_factory=dict)
    trafo_p_lv_mw: Dict[str, float] = field(default_factory=dict)
    trafo_q_lv_mvar: Dict[str, float] = field(default_factory=dict)
    converged: bool = False
    error_msg: str = ""


# ============================================================
# 2. 拓扑 -> pandapower
# ============================================================

def build_pandapower(net: Network) -> pp.pandapowerNet:
    """把画布拓扑转成 pandapower 网络对象"""
    pnet = pp.create_empty_network(name="gui_circuit")

    # 必须有至少一个外部电网(平衡节点) + 一个母线, 否则 PP 抛错
    has_slack = False
    bus_id_map: Dict[str, int] = {}

    for uid, b in net.buses.items():
        pp_bus = pp.create_bus(pnet, vn_kv=b.vn_kv, name=b.name)
        bus_id_map[uid] = pp_bus

    # 平衡节点: 第一个 Gen 当 ext_grid(slack), 其余 Gen 当 gen(PV 节点)
    gen_uids = list(net.gens.keys())
    if gen_uids:
        first_gen_uid = gen_uids[0]
        first_gen = net.gens[first_gen_uid]
        pp.create_ext_grid(
            pnet,
            bus=bus_id_map[first_gen.bus_uid],
            vm_pu=first_gen.vm_pu,
            name=first_gen.name,
            max_p_mw=first_gen.p_mw * 2,
            min_p_mw=0.0,
        )
        has_slack = True
        for uid in gen_uids[1:]:
            g = net.gens[uid]
            pp.create_gen(
                pnet,
                bus=bus_id_map[g.bus_uid],
                p_mw=g.p_mw,
                vm_pu=g.vm_pu,
                name=g.name,
                controllable=False,
            )

    # 如果没有发电机, 用第一条母线当 ext_grid, 避免 PP 报错
    if not has_slack and net.buses:
        first_bus_uid = next(iter(net.buses))
        pp.create_ext_grid(
            pnet,
            bus=bus_id_map[first_bus_uid],
            vm_pu=1.0,
            name="自动平衡节点",
        )

    # 负荷
    for uid, ld in net.loads.items():
        pp.create_load(
            pnet,
            bus=bus_id_map[ld.bus_uid],
            p_mw=ld.p_mw,
            q_mvar=ld.q_mvar,
            name=ld.name,
        )

    # 自定义一个 GUI 用的线路/变压器型号
    pp.create_std_type(
        pnet,
        {"r_ohm_per_km": 0.4, "x_ohm_per_km": 0.4, "c_nf_per_km": 0.0,
         "max_i_ka": 0.6, "type": "cs"},
        name="GUI_LINE",
        element="line",
    )
    pp.create_std_type(
        pnet,
        {"sn_mva": 63.0, "vn_hv_kv": 110.0, "vn_lv_kv": 35.0,
         "vk_percent": 10.0, "vkr_percent": 0.4, "pfe_kw": 0.0,
         "i0_percent": 0.0, "shift_degree": 0.0,
         "vector_group": "Dyn", "tap_side": "hv", "tap_neutral": 0,
         "tap_min": -2, "tap_max": 2, "tap_step_percent": 2.5,
         "tap_pos": 0, "type": "transformer"},
        name="GUI_TRAFO",
        element="trafo",
    )

    # 线路
    for uid, ln in net.lines.items():
        pp.create_line(
            pnet,
            from_bus=bus_id_map[ln.from_bus],
            to_bus=bus_id_map[ln.to_bus],
            length_km=ln.length_km,
            std_type="GUI_LINE",
            name=ln.name,
        )
        idx = pnet.line.index[-1]
        pnet.line.at[idx, "r_ohm_per_km"] = ln.r_ohm_per_km
        pnet.line.at[idx, "x_ohm_per_km"] = ln.x_ohm_per_km
        pnet.line.at[idx, "c_nf_per_km"] = ln.c_nf_per_km
        pnet.line.at[idx, "max_i_ka"] = ln.max_i_ka

    # 变压器
    for uid, tr in net.trafos.items():
        pp.create_transformer(
            pnet,
            hv_bus=bus_id_map[tr.hv_bus],
            lv_bus=bus_id_map[tr.lv_bus],
            std_type="GUI_TRAFO",
            name=tr.name,
        )
        idx = pnet.trafo.index[-1]
        pnet.trafo.at[idx, "sn_mva"] = tr.sn_mva
        pnet.trafo.at[idx, "vn_hv_kv"] = tr.vn_hv_kv
        pnet.trafo.at[idx, "vn_lv_kv"] = tr.vn_lv_kv
        pnet.trafo.at[idx, "vk_percent"] = tr.vk_percent
        pnet.trafo.at[idx, "vkr_percent"] = tr.vkr_percent
        pnet.trafo.at[idx, "pfe_kw"] = tr.pfe_kw
        pnet.trafo.at[idx, "i0_percent"] = tr.i0_percent
        pnet.trafo.at[idx, "shift_degree"] = tr.shift_degree

    # 串联阻抗
    for uid, imp in net.impedances.items():
        pp.create_impedance(
            pnet,
            from_bus=bus_id_map[imp.from_bus],
            to_bus=bus_id_map[imp.to_bus],
            rft_pu=imp.rft_pu,
            xft_pu=imp.xft_pu,
            sn_mva=imp.sn_mva,
            name=imp.name,
        )

    return pnet


# ============================================================
# 3. 跑潮流 + 回写结果到 Network
# ============================================================

def run_power_flow(net: Network) -> Tuple[bool, str]:
    """
    跑潮流, 把结果写回 net.bus_voltage_pu 等字段
    返回 (success, error_msg)
    """
    net.bus_voltage_pu.clear()
    net.bus_voltage_kv.clear()
    net.bus_va_degree.clear()
    net.line_loading_percent.clear()
    net.line_p_from_mw.clear()
    net.line_q_from_mvar.clear()
    net.line_p_to_mw.clear()
    net.line_q_to_mvar.clear()
    net.trafo_loading_percent.clear()
    net.trafo_p_hv_mw.clear()
    net.trafo_q_hv_mvar.clear()
    net.trafo_p_lv_mw.clear()
    net.trafo_q_lv_mvar.clear()
    net.converged = False
    net.error_msg = ""

    if len(net.buses) < 1:
        return False, "画布上没有母线"
    if len(net.gens) < 1:
        return False, "至少需要一个电源(发电机)作为平衡节点"

    try:
        pnet = build_pandapower(net)
        pp.runpp(pnet, algorithm="nr", init="flat", numba=False)
    except pp.LoadflowNotConverged as e:
        return False, f"潮流不收敛: {e}"
    except Exception as e:
        return False, f"计算错误: {type(e).__name__}: {e}"

    # 把 pp 结果按 uid 反向索引: pp.bus 的 index 即 pp bus id, 但我们画布的 uid 用 name 标识
    # res_bus 与 bus 同 index, 所以先建立 pp_bus_id -> uid, 再查 res_bus
    bus_name_to_uid = {b.name: uid for uid, b in net.buses.items()}
    line_name_to_uid = {ln.name: uid for uid, ln in net.lines.items()}
    trafo_name_to_uid = {tr.name: uid for uid, tr in net.trafos.items()}

    pp_bus_to_uid = {}
    for pp_id, b in pnet.bus.iterrows():
        uid = bus_name_to_uid.get(b["name"])
        if uid is not None:
            pp_bus_to_uid[pp_id] = uid

    pp_line_to_uid = {}
    for pp_id, ln in pnet.line.iterrows():
        uid = line_name_to_uid.get(ln["name"])
        if uid is not None:
            pp_line_to_uid[pp_id] = uid

    pp_trafo_to_uid = {}
    for pp_id, tr in pnet.trafo.iterrows():
        uid = trafo_name_to_uid.get(tr["name"])
        if uid is not None:
            pp_trafo_to_uid[pp_id] = uid

    try:
        for pp_id, b in pnet.res_bus.iterrows():
            uid = pp_bus_to_uid.get(pp_id)
            if uid is not None:
                vm_pu = float(b["vm_pu"])
                net.bus_voltage_pu[uid] = vm_pu
                net.bus_voltage_kv[uid] = vm_pu * net.buses[uid].vn_kv
                net.bus_va_degree[uid] = float(b["va_degree"])

        for pp_id, ln in pnet.res_line.iterrows():
            uid = pp_line_to_uid.get(pp_id)
            if uid is not None:
                net.line_loading_percent[uid] = float(ln["loading_percent"])
                net.line_p_from_mw[uid] = float(ln["p_from_mw"])
                net.line_q_from_mvar[uid] = float(ln["q_from_mvar"])
                net.line_p_to_mw[uid] = float(ln["p_to_mw"])
                net.line_q_to_mvar[uid] = float(ln["q_to_mvar"])

        for pp_id, tr in pnet.res_trafo.iterrows():
            uid = pp_trafo_to_uid.get(pp_id)
            if uid is not None:
                net.trafo_loading_percent[uid] = float(tr["loading_percent"])
                net.trafo_p_hv_mw[uid] = float(tr["p_hv_mw"])
                net.trafo_q_hv_mvar[uid] = float(tr["q_hv_mvar"])
                net.trafo_p_lv_mw[uid] = float(tr["p_lv_mw"])
                net.trafo_q_lv_mvar[uid] = float(tr["q_lv_mvar"])
    except Exception as e:
        return False, f"读取结果错误: {type(e).__name__}: {e}"

    net.converged = True
    return True, ""
