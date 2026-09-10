"""
topo_io.py — 拓扑 JSON 解析/校验与序列化 (从 app.py 拆出)

parse_topology_json / network_to_json_dict 独立于 GUI, 方便测试与复用。
app.py 保留同名再导出, 对外接口不变。
"""
from __future__ import annotations
import math
from dataclasses import asdict, fields as dc_fields
from typing import get_type_hints

from solver import (
    Network, BusNode, GenUnit, LoadUnit,
    LineBranch, TrafoBranch, ImpedanceBranch, ShuntUnit,
    BUS_REF_SPEC,
)


# -------------------------------------------------------------
# 拓扑 JSON 解析/校验(独立于 GUI, 方便测试)
# -------------------------------------------------------------
_TOPO_SPEC = [
    ("buses", BusNode), ("gens", GenUnit), ("loads", LoadUnit),
    ("lines", LineBranch), ("trafos", TrafoBranch),
    ("impedances", ImpedanceBranch), ("shunts", ShuntUnit),
]

# 不做数值校验的字段: 标识/名称/引用/枚举
_NON_NUMERIC = {"uid", "name", "bus_uid", "from_bus", "to_bus",
                "hv_bus", "lv_bus", "is_slack", "gen_mode", "vector_group"}


def _check_numeric_fields(cls, kwargs, what: str) -> None:
    """数值字段必须是有限数字, int 字段必须是整数。

    手工编辑的 x="abc"、负长度、NaN 以前会直接进 dataclass,
    直到界面格式化时才炸; 这里在载入入口拦下并指名道姓。"""
    hints = get_type_hints(cls)
    for key, val in kwargs.items():
        if key in _NON_NUMERIC:
            continue
        typ = hints.get(key)
        if typ is float:
            if isinstance(val, bool) or not isinstance(val, (int, float)) \
                    or not math.isfinite(val):
                raise ValueError(
                    f"{what} 字段 '{key}' 必须是有限数字, 实际为 {val!r}")
        elif typ is int:
            # 放宽: JSON 里 "0.0" / 0.0 这种写法应可接受 ——
            # 外部工具(或 json 往返)很容易把整数写成浮点,
            # 以前会误伤, 现在只要求"能安全转成 int"并顺手转掉。
            if isinstance(val, bool) or not isinstance(val, (int, float)) \
                    or not math.isfinite(val) \
                    or float(val) != int(val):
                raise ValueError(
                    f"{what} 字段 '{key}' 必须是整数, 实际为 {val!r}")
            if not isinstance(val, int):
                kwargs[key] = int(val)


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
            what = f"'{key}' 条目 {entry.get('name', '?')}"
            _check_numeric_fields(cls, kwargs, what)
            try:
                obj = cls(**kwargs)
            except TypeError as e:
                raise ValueError(f"{what} 字段无效: {e}")
            container = getattr(net, key)
            if obj.uid in container:
                # 同一数组里 uid 重复: 以前是静默覆盖, 用户拿到的是
                # "少了几个元件却看起来正常"的残缺模型 —— 必须报错。
                raise ValueError(
                    f"'{key}' 里存在重复的 uid: {obj.uid!r} "
                    f"(条目 {obj.name!r}), 每个元件 uid 必须唯一")
            container[obj.uid] = obj
    bus_uids = set(net.buses)

    # 引用校验由 solver.BUS_REF_SPEC 驱动: 元件类型 -> 引用母线的字段。
    # 以前这里手写了 gens/loads/lines/trafos/impedances 五类, 唯独漏了
    # shunts —— 悬空 shunt 会一路穿透到画布重建时 KeyError 崩溃(P0-1)。
    for key, label, fields in BUS_REF_SPEC:
        single = len(fields) == 1
        for obj in getattr(net, key).values():
            for fname in fields:
                ref = getattr(obj, fname, "")
                if ref not in bus_uids:
                    where = "挂接的母线" if single else "的端点母线"
                    raise ValueError(f"{label} '{obj.name}' {where}不存在: {ref!r}")
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
