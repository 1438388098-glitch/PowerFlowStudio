# PowerFlowStudio 协作指南

> 潮流计算可视化编辑器 — 一个用 PyQt5 + pandapower 实现的轻量级电力系统仿真 GUI。
> 目标用户：电气工程学生 / 教学 / 简单电网仿真。

---

## 一、项目现状

### 已实现（v0.x）

- **画布**：自由拖拽的 QGraphicsView 元件编辑器
  - 5 类元件：Bus / Gen / Load / Trafo / Impedance
  - 元件库面板（左侧 5 个 QPushButton，按住拖出）
  - 拖放 → 端口连线 → 拓扑建立
  - 选中元件显示「参数」+「潮流结果」面板（右侧）
  - 元件按 |V| 着色（bus 填色按电压标幺值变化）
  - 删除（删除选中 / Delete / Backspace 键）

- **求解器**（`solver.py`）：基于 pandapower 的 Newton-Raphson
  - 线路、变压器、阻抗
  - 多发电机（第一个 = slack/ext_grid，其余 = PV）
  - 结果反向索引回我们的 uid：bus V/θ、line loading、trafo loading、**gen P/Q/V、load P/Q**

- **拓扑 IO**：保存/载入 JSON
- **示例**：3 母线单端供电、5 母线两端供电
- **打包**：`build_windows.bat` 一次性产出 `dist\PowerFlowStudio.exe`

### 技术栈

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | 3.11+ | 主语言 |
| PyQt5 | 5.15 | GUI |
| pyqtgraph | 0.14 | （已装但暂未使用，预留给图表） |
| pandapower | 3.5 | NR 潮流计算 |
| numpy | 2.x | pandapower 依赖 |
| pyinstaller | 6.22 | Windows 打包 |

---

## 二、文件结构

```
PowerFlowStudio/
├── app.py          — 主入口 (MainWindow, 工具栏, IO, demo)
├── canvas.py       — 画布 (QGraphicsView/Scene, BaseComponent, PortItem, ConnectionItem)
├── palette.py      — 元件库面板 (5 个 QPushButton + QDrag)
├── properties.py   — 属性编辑面板 (QFormLayout, 左侧参数 + 右侧潮流结果)
├── solver.py       — 拓扑↔pandapower 转换 + Newton-Raphson
├── build_windows.bat  — Windows 一键打包
└── README.md       — 用户使用说明
```

**核心数据流**：用户在 canvas 上拖元件 → `add_component` 把 model 写入 `Network` → 用户连端口 → `create_connection` 更新拓扑 → 点 ▶ 运行 → `run_power_flow` 把 Network 转成 pandapower，NR 求解，结果反向索引回 Network → canvas 用 `bus_voltage_pu` 重画 fill 色 → properties 面板用 `gen_p_mw / gen_q_mvar` 等字典显示实际功率。

---

## 三、已知 bug 与待修（按优先级）

### 🔴 P1 — 修一下体验会好很多

1. **vm_pu 没有真传到 pandapower**
   - `canvas.py:595` `GenUnit(uid=uid, name=name, bus_uid=bus_uid, p_mw=50, vm_pu=1.0)`
   - `add_component('Gen', ...)` 写死 `vm_pu=1.0` —— 用户在属性面板改了 vm_pu 也无效
   - 修：`add_component` 第三个参数应该传 dict 而不是固定值，或者 `GenItem` 持有 default 字段，add 时读 model 字段

2. **变压器 / 阻抗结果在结果面板显示 "暂未提取"**
   - `properties.py:208-211`
   - solver 里有 `trafo_loading_percent / trafo_p_hv_mw` 等字典，但 properties 没读
   - 修：跟 GenItem 的写法一致，加 4-5 行

3. **save/load JSON 时 vm_pu 丢失**
   - `app.py:177-188` `BusNode(**b)` `GenUnit(**g)` 用 `**` 解包，但用户改了 vm_pu 后没存进 JSON
   - 看 `app.py:_save_topology` 是否把所有字段都序列化

### 🟡 P2 — 锦上添花

4. **线路默认参数是 r=0.4, x=0.4, max_i=0.6 kA**
   - `solver.py:189-195` `GUI_LINE` std_type
   - 这是 110 kV 短线路估算值，但用户没法在 UI 里调（除非编辑 properties 后让用户**重新运行潮流**看效果；目前看上去 props 改了不影响 solver——再确认）
   - 改：让用户能在 properties 调 length_km / r / x / max_i，**并把这些值通过 std_type 传到 pp**——目前用固定 std_type，所以用户改的参数被忽略了

5. **没有 OPF / 短路 / 时域仿真**（仅稳态潮流）— README 已经说明
6. **没有取消选中的方法**（点空白处应清空 selection）
7. **撤销/重做**（Ctrl+Z）— 现在没有，UI 操作不可逆

### 🟢 P3 — 大改动 / 长期

8. **没有缩放/平移**（画布小，看大网络困难）—— QGraphicsView 自带 `setDragMode(ScrollHandDrag)` 但要先关 rubber-band selection
9. **没有对齐网格**（用户拖元件是自由坐标）
10. **没有复制/粘贴**
11. **没有 IEEE 标准测试用例库**（IEEE 14/30/57/118 节点 — pandapower 自带但没集成进来）
12. **没有图表显示**（电压分布、潮流分布、PV 曲线）— pyqtgraph 装好了没用在画图上

---

## 四、架构上的可优化方向

### A. 数据模型与画布解耦（高 ROI）

**现状**：`solver.py` 的 `Network` dataclass 和 `canvas.py` 的 `BaseComponent` 强耦合。Bus/Gen/Load/Trafo/Impedance 五个 dataclass 既存拓扑也存 GUI 字段（`x, y`）。

**建议**：拆成「拓扑层」+「视图层」
- 拓扑层（`solver.py`）：纯拓扑，不含坐标
- 视图层（`canvas.py`）：单独的 `Layout` dict，存 `uid -> (x, y)`，保存到 JSON 时也独立存

**好处**：保存/载入更干净；以后加 Undo/Redo 时只动 Layout 不动拓扑；做优化算法（如自动布局）只动 Layout。

### B. 求解器抽接口

**现状**：`run_power_flow` 直接调 `build_pandapower + pp.runpp`。

**建议**：抽 `Backend` 接口，未来可以加 MATPOWER / PYPOWER 后端而不只是 pandapower。

```python
class PowerFlowBackend(Protocol):
    def build(self, net: Network) -> Any: ...
    def solve(self, backend_net: Any) -> Tuple[bool, str]: ...
    def extract_results(self, backend_net: Any, net: Network) -> None: ...

class PandaPowerBackend: ...  # 当前实现
class MatpowerBackend: ...    # 未来
```

### C. 配置外置

**现状**：`solver.py:189-195` 的 `GUI_LINE` 硬编码阻抗参数。

**建议**：抽到 `config.yaml`，用户能改默认线路参数、改基准容量、改 init 算法等。

### D. 测试

**现状**：solver 单元测试是 ad-hoc 脚本，没有 CI。

**建议**：
- 至少给 `solver.py` 写 pytest，覆盖：
  - 3 母线例题（已知电压/相角）
  - 5 母线两端供电（功率平衡）
  - 收敛失败（阻抗过大）
- GUI 测试用 `QT_QPA_PLATFORM=offscreen` + pytest-qt
- CI：GitHub Actions 跑 pytest + Windows 打包

### E. 可视化

**现状**：只显示 bus 电压填色 + 线路箭头。

**建议**（pyqtgraph 装好了没用）：
- 实时电压曲线（NR 迭代过程）
- 功率流向 Sankey 图
- 电压等高线 / 拓扑图导出 PNG

---

## 五、协作约定

### 提交前必做

1. **跑通两端供电 demo** —— 5 母线收敛是最低标准
2. **不引入新 import 而不更新 `build_windows.bat`** —— pyinstaller 漏 hidden import 会导致运行时 NameError（`QLineF` 那次教训）
3. **改 canvas.py 时 WSL offscreen 跑过** —— `QT_QPA_PLATFORM=offscreen python -u test.py` 测 paint + 拖放 + 连线

### Commit message 风格

- `Fix <具体问题>` 修 bug
- `Add <功能>` 新功能
- `Refactor <范围>` 重构
- 描述里**写清楚为什么**和**怎么测的**（参见最近几次 commit：commit message 里带 WSL 测试输出是好的）

### 不破坏的接口

- `solver.py:Network` 的字段名（bus_voltage_pu / line_p_from_mw 等）—— 已被 `properties.py` 用
- `canvas.py:BaseComponent.W / H / KIND / HAS_PORTS` —— 已被 `app.py`/`properties.py` 反射
- `solver.py:run_power_flow(net) -> (bool, str)` 返回值签名

---

## 六、快速上手

### 1. 装环境（WSL）

```bash
cd /mnt/c/Users/Admin/PowerFlowStudio
uv venv .venv
source .venv/bin/activate
uv pip install PyQt5 pyqtgraph pandapower numpy pyinstaller
```

### 2. 跑

```bash
QT_QPA_PLATFORM=offscreen python -u app.py
```

### 3. 测试 solver 单独

```python
from solver import run_power_flow, Network, BusNode, GenUnit, LoadUnit, LineBranch
n = Network()
# 构造网络
ok, err = run_power_flow(n)
print(n.bus_voltage_pu)
```

### 4. Windows 打包

```cmd
cd C:\Users\Admin\PowerFlowStudio
build_windows.bat
dist\PowerFlowStudio.exe
```

---

## 七、给协作者的常见问题清单

**Q: 加新元件类型怎么改？**
A: 三处都要改：
1. `solver.py` 加 dataclass + `build_pandapower` 里加 pp.create_*
2. `canvas.py` 加 Item 类继承 `BaseComponent`（覆写 `_init_ports` 和 `paint`）
3. `app.py:550` `component_class_map` 加映射 + `properties.py:62` 加表单字段

**Q: 改了 solver 字段名，要同步改 properties.py 吗？**
A: 是。`properties.py:refresh_results` 直接读 `net.xxx` 字典。

**Q: 为什么我加了 widget 在 build 时崩？**
A: pyinstaller 默认不打 hidden import。**在 import 章节补 `from PyQt5.QtXXX import YYY`** —— 改完跑一遍 offscreen 验证（WSL offscreen paint 跳过，但 import 阶段会跑；production 在 Windows 才暴露）。

**Q: 我想加 IEEE 14 母线预置**
A: pandapower 自带：`pp.networks.case14()`。在 `app.py:_load_demo` 加按钮调 `build_pandapower_from_pp(pp.networks.case14())` —— 但要写个反向转换 pandapower → 我们 Network 的函数（参考 `build_pandapower` 倒着写）。

---

## 八、版本

最后同步：2026-09-09（commit `4859b2e`）。

**已知稳定 commit**：`4859b2e` 之后
- ✅ 元件之间能两两连接
- ✅ 潮流能跑通
- ✅ PV 节点显示实际 Q
- ✅ 打包在 Windows 成功

如果接手的协作者从 main 拉下来后跑不通，**先切到 `4859b2e` 验证基线**。
