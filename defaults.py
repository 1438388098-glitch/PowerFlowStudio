"""
defaults.py — 全局默认参数集中定义

之前这些魔法数字散落在 solver/canvas/app 各处(元件默认参数、吸附半径、
画布尺寸...), 改一处要全局搜。现在单一事实来源, 新元件/调默认值只改这里。

FIELD_RANGES 是"属性面板每个数值字段的 (下限, 上限, 小数位)"声明表。
以前这份范围内联在 properties.py 里(如 `0.1, 1000, 1`), defaults.py 名义上是
单一事实来源却完全没被属性面板引用; 现在属性面板按 (kind, attr) 查这张表,
校验范围与求解器口径不会再各写一套。
"""
from __future__ import annotations

# ---- 母线 ----
DEFAULT_VN_KV = 110.0          # 新建母线额定电压 kV

# ---- 发电机 ----
GEN_P_MW = 50.0                # 默认有功出力
GEN_VM_PU = 1.0                # 默认机端电压设定
GEN_XDSS_PERCENT = 18.0        # 次暂态电抗 x''d 默认 (%) — 12~25% 属正常范围
GEN_RDSS_PERCENT = 1.0         # 次暂态电阻 r''d 默认 (%)
GEN_COS_PHI = 0.85             # 额定功率因数默认

# ---- 负荷 ----
LOAD_P_MW = 10.0
LOAD_Q_MVAR = 5.0

# ---- 线路 (110kV 短线路估算值) ----
LINE_R_OHM_PER_KM = 0.4
LINE_X_OHM_PER_KM = 0.4
LINE_C_NF_PER_KM = 0.0
LINE_LENGTH_KM = 10.0
LINE_MAX_I_KA = 0.6

# ---- 变压器 ----
TRAFO_SN_MVA = 63.0
TRAFO_VN_HV_KV = 110.0
TRAFO_VN_LV_KV = 35.0
TRAFO_VK_PERCENT = 10.0
TRAFO_VKR_PERCENT = 0.4
TRAFO_VECTOR_GROUP = "Dyn"
# 联结组别的允许取值: 存档里的乱码一律回落到 TRAFO_VECTOR_GROUP
TRAFO_VECTOR_GROUPS = ("Dyn", "Dyn5", "Dyn11", "Yyn", "YNyn", "Yy", "YNy",
                       "Dd", "Dd6", "YNd", "YNd11")

# ---- 串联阻抗 ----
IMP_RFT_PU = 0.01
IMP_XFT_PU = 0.01
IMP_SN_MVA = 100.0

# ---- 画布交互 ----
CANVAS_WIDTH = 2000            # sceneRect 尺寸
CANVAS_HEIGHT = 1400
NEAREST_BUS_DIST = 200.0       # Gen/Load 放置时吸附最近母线的半径 px
PORT_SNAP_DIST = 80.0          # 连线释放时吸附最近端口的半径 px
AUTO_BUS_OFFSET = 30.0         # 自动建两条母线时相对放置点的偏移

# ---- 计算与结果阈值 ----
ASYNC_PF_THRESHOLD = 200       # 母线数超过该值时后台线程计算
LOADING_WARN_PERCENT = 80.0    # 负载率告警阈值 %
LOADING_CRIT_PERCENT = 100.0   # 负载率越限阈值 %
V_WARN_LOW_PU = 0.90           # 电压越限下限
V_NORMAL_PU = 0.95             # 电压正常下限
V_WARN_HIGH_PU = 1.05          # 电压正常上限
V_HIGH_PU = 1.10               # 电压越限上限


# -------------------------------------------------------------
# 属性面板数值字段范围: (kind, attr) -> (min, max, decimals)
# -------------------------------------------------------------
FIELD_RANGES = {
    ("Bus", "vn_kv"): (0.1, 1000, 1),
    ("Gen", "p_mw"): (0, 5000, 1),
    ("Gen", "vm_pu"): (0.8, 1.2, 4),
    ("Gen", "slack_weight"): (0.0, 10.0, 2),
    ("Gen", "min_p_mw"): (-5000, 5000, 1),
    ("Gen", "max_p_mw"): (0, 5000, 1),
    ("Gen", "cost_per_mw"): (0, 10000, 1),
    ("Gen", "s_sc_max_mva"): (1, 100000, 0),
    ("Gen", "s_sc_min_mva"): (1, 100000, 0),
    ("Gen", "kappa"): (1.0, 2.0, 2),
    ("Gen", "xdss_percent"): (5.0, 60.0, 1),
    ("Gen", "rdss_percent"): (0.0, 20.0, 2),
    ("Gen", "cos_phi"): (0.1, 1.0, 3),
    # 负负荷 = 该点注入功率(等效电源), 允许为负
    ("Load", "p_mw"): (-5000, 5000, 1),
    ("Load", "q_mvar"): (-1000, 1000, 2),
    ("Shunt", "p_mw"): (0, 500, 3),
    ("Shunt", "q_mvar"): (-1000, 1000, 2),
    ("Trafo", "sn_mva"): (0.1, 1000, 1),
    ("Trafo", "vn_hv_kv"): (0.1, 1000, 1),
    ("Trafo", "vn_lv_kv"): (0.1, 1000, 1),
    ("Trafo", "vk_percent"): (0, 30, 2),
    ("Trafo", "vkr_percent"): (0, 30, 3),
    ("Trafo", "pfe_kw"): (0, 1000, 1),
    ("Trafo", "i0_percent"): (0, 10, 3),
    ("Trafo", "shift_degree"): (-60, 60, 1),
    ("Impedance", "rft_pu"): (-10, 10, 4),
    ("Impedance", "xft_pu"): (-10, 10, 4),
    ("Impedance", "sn_mva"): (0.1, 1000, 1),
    ("Line", "length_km"): (0.01, 1000, 2),
    ("Line", "r_ohm_per_km"): (0, 10, 4),
    ("Line", "x_ohm_per_km"): (0, 10, 4),
    ("Line", "max_i_ka"): (0, 10, 3),
}


def field_range(kind: str, attr: str):
    """取 (min, max, decimals); 未登记时返回 None(调用方自行决定兜底)"""
    return FIELD_RANGES.get((kind, attr))
