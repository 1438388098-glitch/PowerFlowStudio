"""
solver.py — 把画布上的元件/连线拓扑转换成 pandapower 网络, 跑潮流, 把结果回写到元件
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Tuple

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
    # Per-generator / per-load results (PV node's actual Q output, etc.)
    gen_p_mw: Dict[str, float] = field(default_factory=dict)
    gen_q_mvar: Dict[str, float] = field(default_factory=dict)
    gen_vm_pu: Dict[str, float] = field(default_factory=dict)
    load_p_mw: Dict[str, float] = field(default_factory=dict)
    load_q_mvar: Dict[str, float] = field(default_factory=dict)
    converged: bool = False
    error_msg: str = ""


# ============================================================
# 2. 拓扑 -> pandapower
# ============================================================

# 结果回写字段清单(清空旧结果 / 批量操作时遍历用)
_RESULT_FIELDS = (
    "bus_voltage_pu", "bus_voltage_kv", "bus_va_degree",
    "line_loading_percent", "line_p_from_mw", "line_q_from_mvar",
    "line_p_to_mw", "line_q_to_mvar",
    "trafo_loading_percent", "trafo_p_hv_mw", "trafo_q_hv_mvar",
    "trafo_p_lv_mw", "trafo_q_lv_mvar",
    "impedance_p_from_mw", "impedance_q_from_mvar",
    "impedance_p_to_mw", "impedance_q_to_mvar",
    "gen_p_mw", "gen_q_mvar", "gen_vm_pu",
    "load_p_mw", "load_q_mvar",
)


def _clear_results(net: Network):
    for name in _RESULT_FIELDS:
        getattr(net, name).clear()
    net.converged = False
    net.error_msg = ""


def build_pandapower(net: Network) -> Tuple[pp.pandapowerNet, Dict[str, Dict]]:
    """
    把画布拓扑转成 pandapower 网络对象。

    返回 (pnet, id_maps)。id_maps 供 run_power_flow 把 pandapower 结果按
    元件 uid 反向索引 —— 全程走 pp 内部 id, 与显示名无关, 元件重名不影响
    结果归属:
      id_maps["bus"]       : uid -> pp bus id
      id_maps["line"]      : uid -> pp line id
      id_maps["trafo"]     : uid -> pp trafo id
      id_maps["impedance"] : uid -> pp impedance id
      id_maps["ext"]       : pp ext_grid id -> gen uid (平衡节点)
      id_maps["gen"]       : pp gen id -> gen uid (PV 节点)
      id_maps["load"]      : pp load id -> load uid
    要求 net 至少有一条母线和一个发电机(平衡节点), run_power_flow 已先行校验。
    ext_grid 和 gen 在 pandapower 是分开的两张表, 各自从 0 计数, 所以分开映射。
    """
    pnet = pp.create_empty_network(name="gui_circuit")

    id_maps: Dict[str, Dict] = {
        "bus": {}, "line": {}, "trafo": {}, "impedance": {},
        "ext": {}, "gen": {}, "load": {},
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
        id_maps["ext"][pp_id] = first_gen_uid
        for uid in ordered[1:]:
            g = net.gens[uid]
            pp_id = pp.create_gen(
                pnet,
                bus=bus_id_map[g.bus_uid],
                p_mw=g.p_mw,
                vm_pu=g.vm_pu,
                name=g.name,
                controllable=False,
            )
            id_maps["gen"][pp_id] = uid

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
    pp.create_std_type(
        pnet,
        {"r_ohm_per_km": D.LINE_R_OHM_PER_KM, "x_ohm_per_km": D.LINE_X_OHM_PER_KM,
         "c_nf_per_km": D.LINE_C_NF_PER_KM, "max_i_ka": D.LINE_MAX_I_KA,
         "type": "cs"},
        name="GUI_LINE",
        element="line",
    )
    pp.create_std_type(
        pnet,
        {"sn_mva": D.TRAFO_SN_MVA, "vn_hv_kv": D.TRAFO_VN_HV_KV,
         "vn_lv_kv": D.TRAFO_VN_LV_KV, "vk_percent": D.TRAFO_VK_PERCENT,
         "vkr_percent": D.TRAFO_VKR_PERCENT, "pfe_kw": 0.0,
         "i0_percent": 0.0, "shift_degree": 0.0,
         "vector_group": "Dyn", "tap_side": "hv", "tap_neutral": 0,
         "tap_min": -2, "tap_max": 2, "tap_step_percent": 2.5,
         "tap_pos": 0, "type": "transformer"},
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

    return pnet, id_maps


# ============================================================
# 3. 跑潮流 + 回写结果到 Network
# ============================================================

def _validate_topology(net: Network) -> str:
    """跑潮流前校验拓扑引用, 返回空串表示通过, 否则返回可读的错误说明。

    悬空引用(元件挂接的母线已被删除)直接进 pandapower 会变成
    KeyError: '' 之类的天书, 这里提前拦下并告诉用户该修哪里。
    """
    bus_uids = set(net.buses)
    for g in net.gens.values():
        if g.bus_uid not in bus_uids:
            return f"发电机 {g.name} 挂接的母线不存在(可能已被删除), 请重新连接"
    for ld in net.loads.values():
        if ld.bus_uid not in bus_uids:
            return f"负荷 {ld.name} 挂接的母线不存在(可能已被删除), 请重新连接"
    for ln in net.lines.values():
        if ln.from_bus not in bus_uids or ln.to_bus not in bus_uids:
            return f"线路 {ln.name} 的端点母线不存在(可能已被删除), 请重新连接"
    for tr in net.trafos.values():
        if tr.hv_bus not in bus_uids or tr.lv_bus not in bus_uids:
            return f"变压器 {tr.name} 的端点母线不存在(可能已被删除), 请重新连接"
        if tr.hv_bus == tr.lv_bus:
            return (f"变压器 {tr.name} 两侧接在同一条母线上, "
                    "请把高压/低压侧分别连到两条不同母线")
    for im in net.impedances.values():
        if im.from_bus not in bus_uids or im.to_bus not in bus_uids:
            return f"阻抗 {im.name} 的端点母线不存在(可能已被删除), 请重新连接"
    return ""


def run_power_flow(net: Network, algorithm: str = "nr") -> Tuple[bool, str]:
    """
    跑潮流, 把结果写回 net.bus_voltage_pu 等字段
    algorithm: "nr" 牛顿-拉夫逊(默认) 或 "dc" 直流潮流(只算 P/相角, 电压全为 1.0)
    返回 (success, error_msg)
    """
    _clear_results(net)

    if len(net.buses) < 1:
        _fail(net, "画布上没有母线")
        return False, net.error_msg
    if len(net.gens) < 1:
        _fail(net, "至少需要一个电源(发电机)作为平衡节点")
        return False, net.error_msg
    topo_err = _validate_topology(net)
    if topo_err:
        _fail(net, topo_err)
        return False, net.error_msg

    try:
        pnet, id_maps = build_pandapower(net)
        if algorithm == "dc":
            # pandapower 3.x: 直流潮流是独立入口 rundcpp,
            # runpp(algorithm="dc") 会 KeyError
            pp.rundcpp(pnet, numba=False)
        else:
            pp.runpp(pnet, algorithm=algorithm, init="flat", numba=False)
    except pp.LoadflowNotConverged as e:
        _fail(net, f"潮流不收敛: {e}")
        return False, net.error_msg
    except Exception as e:
        _fail(net, f"计算错误: {type(e).__name__}: {e}")
        return False, net.error_msg

    # 结果按 uid 归属: bus/line/trafo 用 build 阶段记录的 uid -> pp id 映射,
    # gen/ext/load 用 pp id -> uid 映射。与元件显示名完全无关, 重名不错乱。
    try:
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

        # PV gens — actual Q output (the result, not an input)
        for pp_id, uid in id_maps["gen"].items():
            if pp_id in pnet.res_gen.index:
                net.gen_p_mw[uid] = float(pnet.res_gen.at[pp_id, "p_mw"])
                net.gen_q_mvar[uid] = float(pnet.res_gen.at[pp_id, "q_mvar"])
                net.gen_vm_pu[uid] = float(pnet.res_gen.at[pp_id, "vm_pu"])
        # Slack ext_grid — same fields, separate id space. The slack
        # bus's V is an input, not a result, so we don't write vm_pu
        # here (it stays at whatever the user set).
        for pp_id, uid in id_maps["ext"].items():
            if pp_id in pnet.res_ext_grid.index:
                net.gen_p_mw[uid] = float(pnet.res_ext_grid.at[pp_id, "p_mw"])
                net.gen_q_mvar[uid] = float(pnet.res_ext_grid.at[pp_id, "q_mvar"])
        # Loads — confirmed P/Q (pandapower passes through, but useful for
        # comparing against net.loads and detecting scaling issues)
        for pp_id, uid in id_maps["load"].items():
            if pp_id in pnet.res_load.index:
                net.load_p_mw[uid] = float(pnet.res_load.at[pp_id, "p_mw"])
                net.load_q_mvar[uid] = float(pnet.res_load.at[pp_id, "q_mvar"])
    except Exception as e:
        _fail(net, f"读取结果错误: {type(e).__name__}: {e}")
        return False, net.error_msg

    net.converged = True
    return True, ""


def _fail(net: Network, msg: str):
    """统一失败出口: 既返回给调用方, 也留在 net.error_msg 里供界面轮询"""
    net.converged = False
    net.error_msg = msg


# ============================================================
# 4. N-1 校核 (逐条开断线路/变压器重跑潮流)
# ============================================================

def n_minus_1_check(net: Network, algorithm: str = "nr",
                    loading_limit: float = 100.0) -> dict:
    """
    N-1 校核: 开断每条线路/变压器后重跑潮流, 报告越限与孤立母线。

    返回 {支路uid: entry}; entry 字段:
      kind/name  被开断支路类型与名称
      ok         开断后潮流是否收敛
      error      不收敛/失败时的错误信息
      overloads  [(元件描述, 负载率%)] 超过 loading_limit 的支路
      isolated   [母线名] 开断后孤立的母线
    基线未收敛时会先跑一次基线; 基线失败返回 {"_base_failed": 错误}。
    """
    import copy as _copy
    if not net.converged:
        ok, err = run_power_flow(net, algorithm=algorithm)
        if not ok:
            return {"_base_failed": err}

    branches = [("线路", uid, net.lines) for uid in net.lines]
    branches += [("变压器", uid, net.trafos) for uid in net.trafos]

    report: Dict[str, dict] = {}
    for kind, uid, container in branches:
        name = container[uid].name
        trial = _copy.deepcopy(net)
        (trial.lines if kind == "线路" else trial.trafos).pop(uid, None)
        ok, err = run_power_flow(trial, algorithm=algorithm)
        entry = {"kind": kind, "name": name, "ok": ok,
                 "error": "", "overloads": [], "isolated": []}
        if not ok:
            entry["error"] = err
        else:
            for lid, loading in trial.line_loading_percent.items():
                if loading == loading and loading > loading_limit:
                    ln = trial.lines.get(lid)
                    entry["overloads"].append(
                        (f"线路 {ln.name if ln else lid}", round(loading, 1)))
            for tid, loading in trial.trafo_loading_percent.items():
                if loading == loading and loading > loading_limit:
                    tr = trial.trafos.get(tid)
                    entry["overloads"].append(
                        (f"变压器 {tr.name if tr else tid}", round(loading, 1)))
            for bid, v in trial.bus_voltage_pu.items():
                if v != v:   # NaN → 孤立
                    b = trial.buses.get(bid)
                    entry["isolated"].append(b.name if b else bid)
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
