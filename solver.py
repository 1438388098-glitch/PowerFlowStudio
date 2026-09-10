"""
solver.py — 把画布上的元件/连线拓扑转换成 pandapower 网络, 跑潮流, 把结果回写到元件
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import pandapower as pp

import defaults as D


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
    vn_kv: float = D.DEFAULT_VN_KV # 额定电压(kV)


@dataclass
class GenUnit:
    """发电机 / 电源 (挂在母线上)"""
    uid: str
    name: str
    bus_uid: str                   # 挂接的母线 uid
    p_mw: float = D.GEN_P_MW       # 有功 MW
    vm_pu: float = D.GEN_VM_PU     # 电压设定值 pu
    x: float = 0.0                 # 相对母线的偏移
    y: float = 0.0
    is_slack: bool = False         # 勾选后该机作为平衡节点(默认仍取第一台)
    gen_mode: str = "PV"           # "PV" 恒电压机端 / "PQ" 定功率(静默发电)
    slack_weight: float = 1.0      # 分布式松弛分摊权重
    # ---- OPF 最优潮流 ----
    min_p_mw: float = 0.0          # OPF 出力下限
    max_p_mw: float = 100.0        # OPF 出力上限
    cost_per_mw: float = 20.0      # 线性发电成本 (OPF 目标: 总成本最小)
    # ---- 三相短路计算 (仅平衡节点参数进入 pandapower) ----
    s_sc_max_mva: float = 5000.0   # 最大短路容量
    s_sc_min_mva: float = 3000.0   # 最小短路容量
    rx_max: float = 0.1            # R/X 比 (最大)
    rx_min: float = 0.1
    kappa: float = 1.5             # 峰值系数
    # ---- 机组短路铭牌参数 (PV 机组参与短路计算时使用) ----
    # 以前写死 xdss=18%/rdss=1%/cosφ=0.85, 真实 x''d 在 12%~25% 波动,
    # 会让 Ikss 出现 10%~30% 偏差, 而短路计算正是用来校验遮断能力的。
    xdss_percent: float = D.GEN_XDSS_PERCENT   # 次暂态电抗 x''d (%)
    rdss_percent: float = D.GEN_RDSS_PERCENT   # 次暂态电阻 r''d (%)
    cos_phi: float = D.GEN_COS_PHI             # 额定功率因数


@dataclass
class LoadUnit:
    """负荷 (挂在母线上)"""
    uid: str
    name: str
    bus_uid: str
    p_mw: float = D.LOAD_P_MW
    q_mvar: float = D.LOAD_Q_MVAR
    x: float = 0.0
    y: float = 0.0


@dataclass
class ShuntUnit:
    """并联电容/电抗器 (挂在母线上)"""
    uid: str
    name: str
    bus_uid: str
    p_mw: float = 0.0              # 有功损耗
    q_mvar: float = 10.0           # 正=电抗器(吸收无功), 负=电容器(发出无功)
    x: float = 0.0
    y: float = 0.0


@dataclass
class LineBranch:
    """输电线路 (两个母线之间)"""
    uid: str
    name: str
    from_bus: str                  # 母线 uid
    to_bus: str
    r_ohm_per_km: float = D.LINE_R_OHM_PER_KM
    x_ohm_per_km: float = D.LINE_X_OHM_PER_KM
    c_nf_per_km: float = D.LINE_C_NF_PER_KM
    length_km: float = D.LINE_LENGTH_KM
    max_i_ka: float = D.LINE_MAX_I_KA


@dataclass
class TrafoBranch:
    """双绕组变压器 (两个母线之间, 电压等级不同)"""
    uid: str
    name: str
    hv_bus: str                    # 高压侧母线 uid
    lv_bus: str                    # 低压侧母线 uid
    sn_mva: float = D.TRAFO_SN_MVA
    vn_hv_kv: float = D.TRAFO_VN_HV_KV
    vn_lv_kv: float = D.TRAFO_VN_LV_KV
    vkr_percent: float = D.TRAFO_VKR_PERCENT
    vk_percent: float = D.TRAFO_VK_PERCENT
    pfe_kw: float = 0.0
    i0_percent: float = 0.0
    shift_degree: float = 0.0
    tap_pos: int = 0               # 分接头位置 (-2..2, 0 为中性)
    vector_group: str = "Dyn"      # 联结组别 (Dyn/Yy/Yd 等, 影响零序与相移)
    x: float = 0.0                 # 画布坐标 (0,0 表示未记录, 载入时按中点摆放)
    y: float = 0.0


@dataclass
class ImpedanceBranch:
    """串联阻抗 (两个母线之间)"""
    uid: str
    name: str
    from_bus: str
    to_bus: str
    rft_pu: float = D.IMP_RFT_PU   # R 从 from 视角的 pu
    xft_pu: float = D.IMP_XFT_PU
    sn_mva: float = D.IMP_SN_MVA
    x: float = 0.0                 # 画布坐标 (同 TrafoBranch)
    y: float = 0.0


@dataclass
class Network:
    """整张电网的拓扑(画布层)"""
    buses: Dict[str, BusNode] = field(default_factory=dict)
    gens: Dict[str, GenUnit] = field(default_factory=dict)
    loads: Dict[str, LoadUnit] = field(default_factory=dict)
    lines: Dict[str, LineBranch] = field(default_factory=dict)
    trafos: Dict[str, TrafoBranch] = field(default_factory=dict)
    impedances: Dict[str, ImpedanceBranch] = field(default_factory=dict)
    shunts: Dict[str, ShuntUnit] = field(default_factory=dict)

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
    # 串联阻抗结果 (res_impedance: 两侧 P/Q)
    impedance_p_from_mw: Dict[str, float] = field(default_factory=dict)
    impedance_q_from_mvar: Dict[str, float] = field(default_factory=dict)
    impedance_p_to_mw: Dict[str, float] = field(default_factory=dict)
    impedance_q_to_mvar: Dict[str, float] = field(default_factory=dict)
    # 并联电容/电抗器结果
    shunt_p_mw: Dict[str, float] = field(default_factory=dict)
    shunt_q_mvar: Dict[str, float] = field(default_factory=dict)
    # 三相短路结果 (母线->Ikss kA)
    bus_ikss_ka: Dict[str, float] = field(default_factory=dict)
    # Per-generator / per-load results (PV node's actual Q output, etc.)
    gen_p_mw: Dict[str, float] = field(default_factory=dict)
    gen_q_mvar: Dict[str, float] = field(default_factory=dict)
    gen_vm_pu: Dict[str, float] = field(default_factory=dict)
    load_p_mw: Dict[str, float] = field(default_factory=dict)
    load_q_mvar: Dict[str, float] = field(default_factory=dict)
    # 汇总指标
    total_loss_mw: float = 0.0
    total_loss_q_mvar: float = 0.0
    converged: bool = False
    error_msg: str = ""
    # 结果来源: "" / "ac" / "dc" / "opf" / "sc"
    # DC 模式下 vm_pu 恒为 1.0、Q 恒为 0, 界面必须据此改换着色与标注,
    # 否则用户会误以为"所有母线电压都正好是额定值"。
    result_kind: str = ""
    # 非致命提示(孤立母线等): 求解仍成功, 但需要提醒用户注意
    warnings: List[str] = field(default_factory=list)


# ============================================================
# 2. 拓扑 -> pandapower
# ============================================================

# 元件类型 -> 该类型引用了母线的字段。
# 校验 / 解析 / 序列化全部以此为准: 新增元件类型只改这一处,
# 不会再出现"某个入口忘了校验某类元件"的漏网(P0-1 的根因)。
# 结构: (Network 属性名, 中文标签, 引用母线的字段名元组)
BUS_REF_SPEC = (
    ("gens", "发电机", ("bus_uid",)),
    ("loads", "负荷", ("bus_uid",)),
    ("lines", "线路", ("from_bus", "to_bus")),
    ("trafos", "变压器", ("hv_bus", "lv_bus")),
    ("impedances", "阻抗", ("from_bus", "to_bus")),
    ("shunts", "电容/电抗", ("bus_uid",)),
)

# 结果回写字段清单(清空旧结果 / 批量操作时遍历用)
_RESULT_FIELDS = (
    "bus_voltage_pu", "bus_voltage_kv", "bus_va_degree",
    "line_loading_percent", "line_p_from_mw", "line_q_from_mvar",
    "line_p_to_mw", "line_q_to_mvar",
    "trafo_loading_percent", "trafo_p_hv_mw", "trafo_q_hv_mvar",
    "trafo_p_lv_mw", "trafo_q_lv_mvar",
    "impedance_p_from_mw", "impedance_q_from_mvar",
    "impedance_p_to_mw", "impedance_q_to_mvar",
    "shunt_p_mw", "shunt_q_mvar",
    "bus_ikss_ka",
    "gen_p_mw", "gen_q_mvar", "gen_vm_pu",
    "load_p_mw", "load_q_mvar",
)


def _clear_results(net: Network):
    for name in _RESULT_FIELDS:
        getattr(net, name).clear()
    net.total_loss_mw = 0.0
    net.total_loss_q_mvar = 0.0
    net.converged = False
    net.error_msg = ""
    net.result_kind = ""
    net.warnings = []


def build_pandapower(net: Network, for_opf: bool = False) -> Tuple[pp.pandapowerNet, Dict[str, Dict]]:
    """
    把画布拓扑转成 pandapower 网络对象。

    for_opf=True 时: 发电机按可调度(带 min/max 出力与线性成本)建模,
    供 run_opf 做最优潮流; 默认 False 保持纯潮流语义。

    返回 (pnet, id_maps)。id_maps 供结果回写按元件 uid 反向索引:
      id_maps["bus"]       : uid -> pp bus id
      id_maps["line"]      : uid -> pp line id
      id_maps["trafo"]     : uid -> pp trafo id
      id_maps["impedance"] : uid -> pp impedance id
      id_maps["shunt"]     : uid -> pp shunt id
      id_maps["ext"]       : pp ext_grid id -> gen uid (平衡节点)
      id_maps["gen"]       : pp gen id -> gen uid (PV 节点)
      id_maps["load"]      : pp load id -> load uid
    要求 net 至少有一条母线和一个发电机(平衡节点), run_power_flow 已先行校验。
    ext_grid 和 gen 在 pandapower 是分开的两张表, 各自从 0 计数, 所以分开映射。
    """
    pnet = pp.create_empty_network(name="gui_circuit")

    id_maps: Dict[str, Dict] = {
        "bus": {}, "line": {}, "trafo": {}, "impedance": {},
        "shunt": {}, "ext": {}, "gen": {}, "load": {},
    }
    bus_id_map = id_maps["bus"]

    for uid, b in net.buses.items():
        bus_id_map[uid] = pp.create_bus(pnet, vn_kv=b.vn_kv, name=b.name)

    # 平衡节点: 勾选了 is_slack 的发电机优先, 否则第一台; 其余当 gen(PV 节点)
    gen_uids = list(net.gens.keys())
    if gen_uids:
        slack_uids = [u for u in gen_uids if net.gens[u].is_slack]
        ordered = slack_uids + [u for u in gen_uids if u not in slack_uids]
        first_gen_uid = ordered[0]
        first_gen = net.gens[first_gen_uid]
        pp_id = pp.create_ext_grid(
            pnet,
            bus=bus_id_map[first_gen.bus_uid],
            vm_pu=first_gen.vm_pu,
            name=first_gen.name,
        )
        # 短路计算参数 (潮流不用, calc_sc 需要)
        pnet.ext_grid.at[pp_id, "s_sc_max_mva"] = first_gen.s_sc_max_mva
        pnet.ext_grid.at[pp_id, "s_sc_min_mva"] = first_gen.s_sc_min_mva
        pnet.ext_grid.at[pp_id, "rx_max"] = first_gen.rx_max
        pnet.ext_grid.at[pp_id, "rx_min"] = first_gen.rx_min
        pnet.ext_grid.at[pp_id, "kappa"] = first_gen.kappa
        pnet.ext_grid.at[pp_id, "slack_weight"] = first_gen.slack_weight
        id_maps["ext"][pp_id] = first_gen_uid
        for uid in ordered[1:]:
            g = net.gens[uid]
            if g.gen_mode == "PQ" and not for_opf:
                # PQ 机组: 定功率注入, 用 sgen 建模 (无电压控制)
                pp_id = pp.create_sgen(
                    pnet,
                    bus=bus_id_map[g.bus_uid],
                    p_mw=g.p_mw,
                    q_mvar=0.0,
                    name=g.name,
                )
                # calc_sc 强制要求 sgen 的短路列(sn_mva/k/kappa), 缺了会
                # 直接 ValueError; 按与 gen 相同的经验近似补齐
                sn = max(abs(g.p_mw) / 0.85, 10.0)
                pnet.sgen.at[pp_id, "sn_mva"] = sn
                pnet.sgen.at[pp_id, "k"] = 1.2
                pnet.sgen.at[pp_id, "kappa"] = 1.0
                id_maps.setdefault("sgen", {})[pp_id] = uid
                continue
            if for_opf and g.gen_mode != "PQ":
                pp_id = pp.create_gen(
                    pnet,
                    bus=bus_id_map[g.bus_uid],
                    p_mw=g.p_mw,
                    vm_pu=g.vm_pu,
                    name=g.name,
                    controllable=True,
                    min_p_mw=g.min_p_mw,
                    max_p_mw=g.max_p_mw,
                )
            elif for_opf:
                # PQ 机组在 OPF 里也不可调度: 定功率注入, 不虚拟成 PV
                pp_id = pp.create_sgen(
                    pnet,
                    bus=bus_id_map[g.bus_uid],
                    p_mw=g.p_mw,
                    q_mvar=0.0,
                    name=g.name,
                    controllable=False,
                )
                id_maps.setdefault("sgen", {})[pp_id] = uid
                continue
            else:
                pp_id = pp.create_gen(
                    pnet,
                    bus=bus_id_map[g.bus_uid],
                    p_mw=g.p_mw,
                    vm_pu=g.vm_pu,
                    name=g.name,
                    controllable=False,
                )
            pnet.gen.at[pp_id, "slack_weight"] = g.slack_weight
            id_maps["gen"][pp_id] = uid
        if for_opf:
            # OPF 要求所有可调度元件都有成本函数(线性成本 cp1)
            for pp_id, uid in id_maps["ext"].items():
                pp.create_poly_cost(pnet, pp_id, et="ext_grid",
                                    cp1_eur_per_mw=net.gens[uid].cost_per_mw)
            for pp_id, uid in id_maps["gen"].items():
                pp.create_poly_cost(pnet, pp_id, et="gen",
                                    cp1_eur_per_mw=net.gens[uid].cost_per_mw)

    # 负荷
    for uid, ld in net.loads.items():
        pp_id = pp.create_load(
            pnet,
            bus=bus_id_map[ld.bus_uid],
            p_mw=ld.p_mw,
            q_mvar=ld.q_mvar,
            name=ld.name,
        )
        id_maps["load"][pp_id] = uid

    # 自定义一个 GUI 用的线路/变压器型号
    # (去重: 同一 pnet 被重复填充时不再重复注册, N-1/OPF 重跑路径上省一遍开销)
    if "GUI_LINE" not in pnet.std_types.get("line", {}):
        pp.create_std_type(
            pnet,
            {"r_ohm_per_km": D.LINE_R_OHM_PER_KM, "x_ohm_per_km": D.LINE_X_OHM_PER_KM,
             "c_nf_per_km": D.LINE_C_NF_PER_KM, "max_i_ka": D.LINE_MAX_I_KA,
             "type": "cs"},
            name="GUI_LINE",
            element="line",
        )
    if "GUI_TRAFO" not in pnet.std_types.get("trafo", {}):
        pp.create_std_type(
            pnet,
            {"sn_mva": D.TRAFO_SN_MVA, "vn_hv_kv": D.TRAFO_VN_HV_KV,
             "vn_lv_kv": D.TRAFO_VN_LV_KV, "vk_percent": D.TRAFO_VK_PERCENT,
             "vkr_percent": D.TRAFO_VKR_PERCENT, "pfe_kw": 0.0,
             "i0_percent": 0.0, "shift_degree": 0.0,
             "vector_group": D.TRAFO_VECTOR_GROUP, "tap_side": "hv",
             "tap_neutral": 0,
             "tap_min": -2, "tap_max": 2, "tap_step_percent": 2.5,
             "tap_pos": 0, "tap_changer_type": "Ratio",
             "type": "transformer"},
            name="GUI_TRAFO",
            element="trafo",
        )

    # 线路 — create_line 的返回值就是 pp id, 无需再去取 index[-1]
    for uid, ln in net.lines.items():
        idx = pp.create_line(
            pnet,
            from_bus=bus_id_map[ln.from_bus],
            to_bus=bus_id_map[ln.to_bus],
            length_km=ln.length_km,
            std_type="GUI_LINE",
            name=ln.name,
        )
        pnet.line.at[idx, "r_ohm_per_km"] = ln.r_ohm_per_km
        pnet.line.at[idx, "x_ohm_per_km"] = ln.x_ohm_per_km
        pnet.line.at[idx, "c_nf_per_km"] = ln.c_nf_per_km
        pnet.line.at[idx, "max_i_ka"] = ln.max_i_ka
        pnet.line.at[idx, "endtemp_degree"] = 70.0   # 短路计算(case=min)需要
        id_maps["line"][uid] = idx

    # 变压器
    for uid, tr in net.trafos.items():
        idx = pp.create_transformer(
            pnet,
            hv_bus=bus_id_map[tr.hv_bus],
            lv_bus=bus_id_map[tr.lv_bus],
            std_type="GUI_TRAFO",
            name=tr.name,
        )
        pnet.trafo.at[idx, "sn_mva"] = tr.sn_mva
        pnet.trafo.at[idx, "vn_hv_kv"] = tr.vn_hv_kv
        pnet.trafo.at[idx, "vn_lv_kv"] = tr.vn_lv_kv
        pnet.trafo.at[idx, "vk_percent"] = tr.vk_percent
        pnet.trafo.at[idx, "vkr_percent"] = tr.vkr_percent
        pnet.trafo.at[idx, "pfe_kw"] = tr.pfe_kw
        pnet.trafo.at[idx, "i0_percent"] = tr.i0_percent
        pnet.trafo.at[idx, "shift_degree"] = tr.shift_degree
        pnet.trafo.at[idx, "tap_pos"] = int(tr.tap_pos)
        # 联结组别只放行已知取值, 免得手改存档里的乱码让 calc_sc 崩掉
        vg = str(getattr(tr, "vector_group", "") or "")
        pnet.trafo.at[idx, "vector_group"] = (
            vg if vg in D.TRAFO_VECTOR_GROUPS else D.TRAFO_VECTOR_GROUP)
        id_maps["trafo"][uid] = idx

    # 串联阻抗
    for uid, imp in net.impedances.items():
        idx = pp.create_impedance(
            pnet,
            from_bus=bus_id_map[imp.from_bus],
            to_bus=bus_id_map[imp.to_bus],
            rft_pu=imp.rft_pu,
            xft_pu=imp.xft_pu,
            sn_mva=imp.sn_mva,
            name=imp.name,
        )
        id_maps["impedance"][uid] = idx

    # 并联电容/电抗器
    for uid, sh in net.shunts.items():
        idx = pp.create_shunt(
            pnet,
            bus=bus_id_map[sh.bus_uid],
            p_mw=sh.p_mw,
            q_mvar=sh.q_mvar,
            name=sh.name,
        )
        id_maps["shunt"][uid] = idx

    return pnet, id_maps


# ============================================================
# 3. 跑潮流 + 回写结果到 Network
# ============================================================

# ============================================================
# 3. 跑潮流 + 回写结果到 Network
# ============================================================

def _solve_pnet(pnet, algorithm: str = "nr", distributed_slack: bool = False):
    """对已构建好的 pandapower 网络求解一次潮流 (NR / DC)。

    单独抽出来是因为 N-1 校核要在同一个 pnet 上反复求解(逐条开断),
    而只读结果的调用方(OPF 预热、短路预热)也需要同一套口径。
    """
    if algorithm == "dc":
        # pandapower 3.x: 直流潮流是独立入口 rundcpp,
        # runpp(algorithm="dc") 会 KeyError
        pp.rundcpp(pnet, numba=False)
    elif distributed_slack:
        pp.runpp(pnet, algorithm=algorithm, init="flat", numba=False,
                 distributed_slack=True)
    else:
        pp.runpp(pnet, algorithm=algorithm, init="flat", numba=False)


def _validate_topology(net: Network) -> str:
    """跑潮流前校验拓扑引用, 返回空串表示通过, 否则返回可读的错误说明。

    悬空引用(元件挂接的母线已被删除)直接进 pandapower 会变成
    KeyError: '' 之类的天书, 这里提前拦下并告诉用户该修哪里。
    遍历口径由 BUS_REF_SPEC 驱动, 新增元件类型自动纳入, 不会再漏。
    """
    bus_uids = set(net.buses)
    for key, label, fields in BUS_REF_SPEC:
        single = len(fields) == 1
        for obj in getattr(net, key).values():
            for fname in fields:
                if getattr(obj, fname, "") not in bus_uids:
                    where = "挂接的母线" if single else "的端点母线"
                    return (f"{label} {obj.name} {where}不存在(可能已被删除), "
                            "请重新连接")
    # 自环与零长度: trafo 的 hv==lv 早就查了, line 的自环以前漏查 —— 不对称
    for tr in net.trafos.values():
        if tr.hv_bus == tr.lv_bus:
            return (f"变压器 {tr.name} 两侧接在同一条母线上, "
                    "请把高压/低压侧分别连到两条不同母线")
    for ln in net.lines.values():
        if ln.from_bus == ln.to_bus:
            return (f"线路 {ln.name} 两端接在同一条母线上(自环), "
                    "请把两端分别连到两条不同母线")
        if ln.length_km <= 0:
            return f"线路 {ln.name} 的长度必须大于 0 (当前 {ln.length_km:g} km)"
    for im in net.impedances.values():
        if im.from_bus == im.to_bus:
            return (f"阻抗 {im.name} 两端接在同一条母线上(自环), "
                    "请把两端分别连到两条不同母线")
    return ""


def _collect_warnings(net: Network) -> List[str]:
    """求解成功后仍值得提醒的非致命问题(孤立母线)。

    孤立母线不会让潮流失败(结果该点是 NaN), 但用户往往以为是软件出错了,
    所以主动说明"这些母线没接进电网"。
    """
    degree: Dict[str, int] = {uid: 0 for uid in net.buses}
    for key, _label, fields in BUS_REF_SPEC:
        for obj in getattr(net, key).values():
            for fname in fields:
                ref = getattr(obj, fname, "")
                if ref in degree:
                    degree[ref] += 1
    isolated = [net.buses[uid].name for uid, d in degree.items() if d == 0]
    if isolated:
        shown = ", ".join(isolated[:8]) + ("…" if len(isolated) > 8 else "")
        return [f"{len(isolated)} 条母线未接入电网(电压显示为 NaN): {shown}"]
    return []


def run_power_flow(net: Network, algorithm: str = "nr",
                   distributed_slack: bool = False) -> Tuple[bool, str]:
    """
    跑潮流, 把结果写回 net.bus_voltage_pu 等字段
    algorithm: "nr" 牛顿-拉夫逊(默认) 或 "dc" 直流潮流(只算 P/相角, 电压全为 1.0)
    distributed_slack: 按 gen/ext_grid 的 slack_weight 把松弛功率分摊到多机
    返回 (success, error_msg)
    """
    _clear_results(net)

    topo_err = _validate_for_solve(net)
    if topo_err:
        _fail(net, topo_err)
        return False, net.error_msg

    try:
        pnet, id_maps = build_pandapower(net)
        _solve_pnet(pnet, algorithm, distributed_slack)
    except pp.LoadflowNotConverged as e:
        _fail(net, f"潮流不收敛: {e}")
        return False, net.error_msg
    except Exception as e:
        _fail(net, f"计算错误: {type(e).__name__}: {e}")
        return False, net.error_msg

    # 结果按 uid 归属: bus/line/trafo 用 build 阶段记录的 uid -> pp id 映射,
    # gen/ext/load 用 pp id -> uid 映射。与元件显示名完全无关, 重名不错乱。
    try:
        _extract_ac_results(net, pnet, id_maps)
    except Exception as e:
        _fail(net, f"读取结果错误: {type(e).__name__}: {e}")
        return False, net.error_msg

    net.result_kind = "dc" if algorithm == "dc" else "ac"
    net.warnings = _collect_warnings(net)
    net.converged = True
    return True, ""


def _fail(net: Network, msg: str):
    """统一失败出口: 既返回给调用方, 也留在 net.error_msg 里供界面轮询"""
    net.converged = False
    net.error_msg = msg


# ============================================================
# 4. N-1 校核 (逐条开断线路/变压器重跑潮流)
# ============================================================

def _n1_overloads(pnet, id_maps, net, loading_limit: float) -> list:
    """从一次求解结果里挑出越限支路 (线路 + 变压器)"""
    out = []
    res_line = getattr(pnet, "res_line", None)
    if res_line is not None and "loading_percent" in set(res_line.columns):
        for uid, pp_id in id_maps["line"].items():
            if pp_id not in res_line.index:
                continue
            loading = float(res_line.at[pp_id, "loading_percent"])
            if loading == loading and loading > loading_limit:
                ln = net.lines.get(uid)
                out.append((f"线路 {ln.name if ln else uid}", round(loading, 1)))
    res_trafo = getattr(pnet, "res_trafo", None)
    if res_trafo is not None and "loading_percent" in set(res_trafo.columns):
        for uid, pp_id in id_maps["trafo"].items():
            if pp_id not in res_trafo.index:
                continue
            loading = float(res_trafo.at[pp_id, "loading_percent"])
            if loading == loading and loading > loading_limit:
                tr = net.trafos.get(uid)
                out.append((f"变压器 {tr.name if tr else uid}", round(loading, 1)))
    return out


def _n1_isolated(pnet, id_maps, net) -> list:
    """开断后电压为 NaN 的母线 = 被孤立的母线"""
    res_bus = getattr(pnet, "res_bus", None)
    if res_bus is None or "vm_pu" not in set(res_bus.columns):
        return []
    out = []
    for uid, pp_id in id_maps["bus"].items():
        if pp_id not in res_bus.index:
            continue
        v = float(res_bus.at[pp_id, "vm_pu"])
        if v != v:
            b = net.buses.get(uid)
            out.append(b.name if b else uid)
    return out


def n_minus_1_check(net: Network, algorithm: str = "nr",
                    loading_limit: float = 100.0, progress=None,
                    distributed_slack: bool = False) -> dict:
    """
    N-1 校核: 开断每条线路/变压器/串联阻抗后重跑潮流, 报告越限与孤立母线。
    distributed_slack 与 run_power_flow 同义, 逐条开断的计算沿用同一松弛
    口径, 避免基线用分布式松弛、开断校核却按单松弛算的结论偏差。

    返回 {支路uid: entry}; entry 字段:
      kind/name  被开断支路类型与名称
      ok         开断后潮流是否收敛
      error      不收敛/失败时的错误信息
      overloads  [(元件描述, 负载率%)] 超过 loading_limit 的支路
      isolated   [母线名] 开断后孤立的母线
    基线未收敛时会先跑一次基线; 基线失败返回 {"_base_failed": 错误}。

    实现要点: 不再对每条支路 deepcopy 整张 Network 再重建 pandapower。
    旧写法在 case118 上要 179 次整网深拷贝 + 179 次 build + 179 次求解,
    是 O(n²); 现在只建一次 pnet, 逐条把 in_service 置 False/True 即可,
    语义等价(pandapower 本就把 in_service=False 的元件排除在计算外)。
    """
    if not net.converged:
        ok, err = run_power_flow(net, algorithm=algorithm,
                                 distributed_slack=distributed_slack)
        if not ok:
            return {"_base_failed": err}

    # (中文类型名, uid, pandapower 表名)
    branches = [("线路", uid, "line") for uid in net.lines]
    branches += [("变压器", uid, "trafo") for uid in net.trafos]
    branches += [("阻抗", uid, "impedance") for uid in net.impedances]
    total = len(branches)
    if total == 0:
        return {}

    try:
        pnet, id_maps = build_pandapower(net)
    except Exception as e:
        return {"_base_failed": f"{type(e).__name__}: {e}"}

    containers = {"线路": net.lines, "变压器": net.trafos, "阻抗": net.impedances}
    report: Dict[str, dict] = {}
    for done, (kind, uid, table) in enumerate(branches):
        if progress is not None:
            progress(done, total)
        entry = {"kind": kind, "name": containers[kind][uid].name, "ok": False,
                 "error": "", "overloads": [], "isolated": []}
        pp_id = id_maps[table].get(uid)
        if pp_id is None:
            entry["error"] = "内部索引缺失, 无法校核"
            report[uid] = entry
            continue
        try:
            pnet[table].at[pp_id, "in_service"] = False
            try:
                _solve_pnet(pnet, algorithm, distributed_slack)
            except pp.LoadflowNotConverged as e:
                entry["error"] = f"潮流不收敛: {e}"
            except Exception as e:
                entry["error"] = f"{type(e).__name__}: {e}"
            else:
                entry["ok"] = True
                entry["overloads"] = _n1_overloads(pnet, id_maps, net,
                                                   loading_limit)
                entry["isolated"] = _n1_isolated(pnet, id_maps, net)
        finally:
            pnet[table].at[pp_id, "in_service"] = True
        report[uid] = entry
    return report


def format_n1_report(report: dict, loading_limit: float = 100.0) -> str:
    """把 n_minus_1_check 的报告排版成人话(供对话框/文件)"""
    if "_base_failed" in report:
        return f"基线潮流失败, 无法校核: {report['_base_failed']}"
    problem_lines = []
    n_problem = 0
    for e in report.values():
        if not e["ok"]:
            n_problem += 1
            problem_lines.append(
                f"✗ 开断{e['kind']} {e['name']}: 潮流不收敛 ({e['error']})")
        elif e["overloads"] or e["isolated"]:
            n_problem += 1
            parts = [f"△ 开断{e['kind']} {e['name']}:" ]
            parts += [f"    越限 {desc} {loading}%" for desc, loading in e["overloads"]]
            parts += [f"    孤立母线 {name}" for name in e["isolated"]]
            problem_lines.append("\n".join(parts))
    header = (f"N-1 校核: 共 {len(report)} 条支路, "
              f"{n_problem} 条开断后出现问题 "
              f"(负载率限值 {loading_limit:.0f}%)\n")
    if not problem_lines:
        return header + "✓ 全部开断方式下无越限、无孤立母线。"
    return header + "\n".join(problem_lines)


def _validate_for_solve(net: Network) -> str:
    """run_power_flow / run_opf / run_short_circuit 共用的前置校验"""
    if len(net.buses) < 1:
        return "画布上没有母线"
    if len(net.gens) < 1:
        return "至少需要一个电源(发电机)作为平衡节点"
    return _validate_topology(net)


def _extract_ac_results(net: Network, pnet, id_maps):
    """NR/OPF 共用的 AC 结果回写 (结果表列两模式一致)"""
    res_bus = pnet.res_bus
    for uid, pp_id in id_maps["bus"].items():
        if pp_id in res_bus.index:
            vm_pu = float(res_bus.at[pp_id, "vm_pu"])
            net.bus_voltage_pu[uid] = vm_pu
            net.bus_voltage_kv[uid] = vm_pu * net.buses[uid].vn_kv
            net.bus_va_degree[uid] = float(res_bus.at[pp_id, "va_degree"])

    res_line = pnet.res_line
    for uid, pp_id in id_maps["line"].items():
        if pp_id in res_line.index:
            net.line_loading_percent[uid] = float(res_line.at[pp_id, "loading_percent"])
            net.line_p_from_mw[uid] = float(res_line.at[pp_id, "p_from_mw"])
            net.line_q_from_mvar[uid] = float(res_line.at[pp_id, "q_from_mvar"])
            net.line_p_to_mw[uid] = float(res_line.at[pp_id, "p_to_mw"])
            net.line_q_to_mvar[uid] = float(res_line.at[pp_id, "q_to_mvar"])

    res_trafo = pnet.res_trafo
    for uid, pp_id in id_maps["trafo"].items():
        if pp_id in res_trafo.index:
            net.trafo_loading_percent[uid] = float(res_trafo.at[pp_id, "loading_percent"])
            net.trafo_p_hv_mw[uid] = float(res_trafo.at[pp_id, "p_hv_mw"])
            net.trafo_q_hv_mvar[uid] = float(res_trafo.at[pp_id, "q_hv_mvar"])
            net.trafo_p_lv_mw[uid] = float(res_trafo.at[pp_id, "p_lv_mw"])
            net.trafo_q_lv_mvar[uid] = float(res_trafo.at[pp_id, "q_lv_mvar"])

    res_imp = getattr(pnet, "res_impedance", None)
    if res_imp is not None:
        for uid, pp_id in id_maps["impedance"].items():
            if pp_id in res_imp.index:
                net.impedance_p_from_mw[uid] = float(res_imp.at[pp_id, "p_from_mw"])
                net.impedance_q_from_mvar[uid] = float(res_imp.at[pp_id, "q_from_mvar"])
                net.impedance_p_to_mw[uid] = float(res_imp.at[pp_id, "p_to_mw"])
                net.impedance_q_to_mvar[uid] = float(res_imp.at[pp_id, "q_to_mvar"])

    res_sh = getattr(pnet, "res_shunt", None)
    if res_sh is not None:
        for uid, pp_id in id_maps["shunt"].items():
            if pp_id in res_sh.index:
                net.shunt_p_mw[uid] = float(res_sh.at[pp_id, "p_mw"])
                net.shunt_q_mvar[uid] = float(res_sh.at[pp_id, "q_mvar"])

    for pp_id, uid in id_maps["gen"].items():
        if pp_id in pnet.res_gen.index:
            net.gen_p_mw[uid] = float(pnet.res_gen.at[pp_id, "p_mw"])
            net.gen_q_mvar[uid] = float(pnet.res_gen.at[pp_id, "q_mvar"])
            net.gen_vm_pu[uid] = float(pnet.res_gen.at[pp_id, "vm_pu"])
    for pp_id, uid in id_maps["ext"].items():
        if pp_id in pnet.res_ext_grid.index:
            net.gen_p_mw[uid] = float(pnet.res_ext_grid.at[pp_id, "p_mw"])
            net.gen_q_mvar[uid] = float(pnet.res_ext_grid.at[pp_id, "q_mvar"])
    for pp_id, uid in id_maps.get("sgen", {}).items():
        if pp_id in pnet.res_sgen.index:
            net.gen_p_mw[uid] = float(pnet.res_sgen.at[pp_id, "p_mw"])
            net.gen_q_mvar[uid] = float(pnet.res_sgen.at[pp_id, "q_mvar"])
    for pp_id, uid in id_maps["load"].items():
        if pp_id in pnet.res_load.index:
            net.load_p_mw[uid] = float(pnet.res_load.at[pp_id, "p_mw"])
            net.load_q_mvar[uid] = float(pnet.res_load.at[pp_id, "q_mvar"])

    # 网损汇总 (缺列防御)
    loss_mw = 0.0
    loss_q = 0.0
    line_cols = set(pnet.res_line.columns)
    if "pl_mw" in line_cols:
        loss_mw += float(pnet.res_line["pl_mw"].fillna(0).sum())
    if {"q_from_mvar", "q_to_mvar"} <= line_cols:
        loss_q += float((pnet.res_line["q_from_mvar"].fillna(0)
                         + pnet.res_line["q_to_mvar"].fillna(0)).sum())
    if hasattr(pnet, "res_trafo"):
        tcols = set(pnet.res_trafo.columns)
        if "pl_mw" in tcols:
            loss_mw += float(pnet.res_trafo["pl_mw"].fillna(0).sum())
        if "ql_mvar" in tcols:
            loss_q += float(pnet.res_trafo["ql_mvar"].fillna(0).sum())
    net.total_loss_mw = loss_mw
    net.total_loss_q_mvar = loss_q


def run_opf(net: Network) -> Tuple[bool, str]:
    """
    最优潮流(OPF): 以线性发电成本最小为目标, 求各可控机组出力。
    结果同样回写 net.gen_p_mw 等字段 (实际出力即优化解)。
    """
    _clear_results(net)

    topo_err = _validate_for_solve(net)
    if topo_err:
        _fail(net, topo_err)
        return False, net.error_msg

    try:
        pnet, id_maps = build_pandapower(net, for_opf=True)
        # 先跑一次收敛潮流: 结果既可作 OPF 初值(results), 也让用户在
        # OPF 失败时看到基线潮流状态。
        # 初值双保险: flat 对低阻抗环网实测不收敛, results 更稳; 反过来
        # 简单网络 results 会发散。这里与下面的 runopp 保持同一策略 —— 都试。
        try:
            pp.runpp(pnet, algorithm="nr", init="flat", numba=False)
        except pp.LoadflowNotConverged:
            pnet, id_maps = build_pandapower(net, for_opf=True)
            pp.runpp(pnet, algorithm="nr", init="results", numba=False)
        # 初值双保险: 低阻抗并联环网用 flat 实测不收敛, results 更稳;
        # 但部分简单网络 results 反而发散。两种初值都试, 任一收敛即成
        try:
            pp.runopp(pnet, init="results", verbose=False)
        except pp.OPFNotConverged:
            pnet, id_maps = build_pandapower(net, for_opf=True)
            pp.runopp(pnet, init="flat", verbose=False)
    except pp.LoadflowNotConverged as e:
        _fail(net, f"OPF 不收敛: {e}")
        return False, net.error_msg
    except Exception as e:
        _fail(net, f"OPF 计算错误: {type(e).__name__}: {e}")
        return False, net.error_msg

    try:
        _extract_ac_results(net, pnet, id_maps)
    except Exception as e:
        _fail(net, f"读取 OPF 结果错误: {type(e).__name__}: {e}")
        return False, net.error_msg

    net.result_kind = "opf"
    net.warnings = _collect_warnings(net)
    net.converged = True
    return True, ""


def run_short_circuit(net: Network, case: str = "max") -> Tuple[bool, str]:
    """
    三相对称短路计算: 母线短路电流 Ikss (kA) 回写到 net.bus_ikss_ka。
    case: "max" 最大运行方式 / "min" 最小运行方式。
    平衡节点发电机属性里有短路容量与 R/X、峰值系数参数。
    """
    _clear_results(net)

    topo_err = _validate_for_solve(net)
    if topo_err:
        _fail(net, topo_err)
        return False, net.error_msg

    try:
        from pandapower.shortcircuit import calc_sc
        pnet, id_maps = build_pandapower(net)
        # 短路计算自身要做一次潮流初始化
        pp.runpp(pnet, algorithm="nr", init="flat", numba=False)
        # PV 发电机参与短路: 用机组自身的次暂态参数(属性面板可改)。
        # 旧版把 x''d=18%/r''d=1%/cosφ=0.85 写死, 与铭牌无关 ——
        # 真实 x''d 在 12%~25% 波动, Ikss 会有 10%~30% 偏差, 而短路
        # 计算恰恰是用来校验遮断容量的。额定容量优先取 OPF 出力上限。
        for pp_id, uid in id_maps["gen"].items():
            g = net.gens[uid]
            vn = net.buses[g.bus_uid].vn_kv
            cos_phi = min(max(float(getattr(g, "cos_phi", 0.85) or 0.85), 0.1), 1.0)
            p_ref = abs(float(getattr(g, "max_p_mw", 0.0) or 0.0)) or abs(g.p_mw)
            sn = max(p_ref / cos_phi, 10.0)
            xdss = float(getattr(g, "xdss_percent", 18.0))
            rdss = float(getattr(g, "rdss_percent", 1.0))
            base_z = vn * vn / sn
            pnet.gen.at[pp_id, "vn_kv"] = vn
            pnet.gen.at[pp_id, "sn_mva"] = sn
            pnet.gen.at[pp_id, "xdss_percent"] = xdss
            pnet.gen.at[pp_id, "rdss_percent"] = rdss
            pnet.gen.at[pp_id, "xdss_ohm"] = xdss / 100.0 * base_z
            pnet.gen.at[pp_id, "rdss_ohm"] = rdss / 100.0 * base_z
            pnet.gen.at[pp_id, "xdss_pu"] = xdss / 100.0
            pnet.gen.at[pp_id, "rdss_pu"] = rdss / 100.0
            pnet.gen.at[pp_id, "cos_phi"] = cos_phi
            pnet.gen.at[pp_id, "generator_type"] = "PV"
        calc_sc(pnet, case=case, ip=False, topology="auto", lv_tol_percent=10)
    except pp.LoadflowNotConverged as e:
        _fail(net, f"短路计算前潮流不收敛: {e}")
        return False, net.error_msg
    except Exception as e:
        _fail(net, f"短路计算错误: {type(e).__name__}: {e}")
        return False, net.error_msg

    try:
        res_sc = pnet.res_bus_sc
        for uid, pp_id in id_maps["bus"].items():
            if pp_id in res_sc.index:
                net.bus_ikss_ka[uid] = float(res_sc.at[pp_id, "ikss_ka"])
        # 回填短路前的运行电压/相角: calc_sc 内部已经算过潮流, 不回填的话
        # 切到电压视图看到的是上一次 AC 的脏数据(或空白)。注意只回填母线,
        # 不碰 line_*/trafo_*(短路结果与潮流结果互斥显示)。
        res_bus = getattr(pnet, "res_bus", None)
        if res_bus is not None and "vm_pu" in set(res_bus.columns):
            for uid, pp_id in id_maps["bus"].items():
                if pp_id not in res_bus.index:
                    continue
                vm = float(res_bus.at[pp_id, "vm_pu"])
                if vm != vm:      # NaN: 孤立母线, 不写入
                    continue
                net.bus_voltage_pu[uid] = vm
                net.bus_voltage_kv[uid] = vm * net.buses[uid].vn_kv
                va = float(res_bus.at[pp_id, "va_degree"])
                if va == va:
                    net.bus_va_degree[uid] = va
    except Exception as e:
        _fail(net, f"读取短路结果错误: {type(e).__name__}: {e}")
        return False, net.error_msg

    net.result_kind = "sc"
    net.warnings = _collect_warnings(net)
    net.converged = True
    return True, ""
