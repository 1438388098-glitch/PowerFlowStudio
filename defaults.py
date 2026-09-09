"""
defaults.py — 全局默认参数集中定义

之前这些魔法数字散落在 solver/canvas/app 各处(元件默认参数、吸附半径、
画布尺寸...), 改一处要全局搜。现在单一事实来源, 新元件/调默认值只改这里。
"""
from __future__ import annotations

# ---- 母线 ----
DEFAULT_VN_KV = 110.0          # 新建母线额定电压 kV

# ---- 发电机 ----
GEN_P_MW = 50.0                # 默认有功出力
GEN_VM_PU = 1.0                # 默认机端电压设定

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
