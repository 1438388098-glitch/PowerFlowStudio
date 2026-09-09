"""
topo_io.py — 拓扑 JSON 解析/校验与序列化 (从 app.py 拆出)

parse_topology_json / network_to_json_dict 独立于 GUI, 方便测试与复用。
app.py 保留同名再导出, 对外接口不变。
"""
from __future__ import annotations
from dataclasses import asdict, fields as dc_fields

from solver import (
    Network, BusNode, GenUnit, LoadUnit,
    LineBranch, TrafoBranch, ImpedanceBranch, ShuntUnit,
)


# -------------------------------------------------------------
# 拓扑 JSON 解析/校验(独立于 GUI, 方便测试)
# -------------------------------------------------------------
_TOPO_SPEC = [
    ("buses", BusNode), ("gens", GenUnit), ("loads", LoadUnit),
    ("lines", LineBranch), ("trafos", TrafoBranch),
    ("impedances", ImpedanceBranch), ("shunts", ShuntUnit),
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
        "shunts": [asdict(s) for s in net.shunts.values()],
    }
