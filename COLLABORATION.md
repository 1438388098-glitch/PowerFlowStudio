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
| Python | 3.11+（建议 3.13，仓库 `.venv` 即 3.13） | 主语言 |
| PyQt5 | 5.15 | GUI |
| pandapower | 3.5 | NR 潮流计算 |
| numpy | 2.x | pandapower 依赖 |
| pytest | 8.x | 测试（`tests/` 目录，`python -m pytest tests -q`） |
| pyinstaller | 6.22 | Windows 打包（仅打包需要） |

> pyqtgraph 曾被列为依赖，但全项目从未 import——已从依赖清单移除，等真做图表再加回来。

---

## 二、文件结构

```
PowerFlowStudio/
├── app.py          — 主入口 (MainWindow, 工具栏, IO, demo)
├── canvas.py       — 画布 (QGraphicsView/Scene, BaseComponent, PortItem, ConnectionItem)
├── palette.py      — 元件库面板 (5 个 QPushButton + QDrag)
├── properties.py   — 属性编辑面板 (QFormLayout, 左侧参数 + 右侧潮流结果)
├── solver.py       — 拓扑↔pandapower 转换 + Newton-Raphson
├── tests/          — pytest: test_solver.py(17用例) + test_gui_smoke.py(offscreen)
├── requirements.txt / requirements-dev.txt — 锁版本依赖
├── build_windows.bat  — Windows 一键打包
└── README.md       — 用户使用说明
```

**核心数据流**：用户在 canvas 上拖元件 → `add_component` 把 model 写入 `Network` → 用户连端口 → `create_connection` 更新拓扑 → 点 ▶ 运行 → `run_power_flow` 把 Network 转成 pandapower，NR 求解，结果反向索引回 Network → canvas 用 `bus_voltage_pu` 重画 fill 色 → properties 面板用 `gen_p_mw / gen_q_mvar` 等字典显示实际功率。

---

## 三、已知 bug 与待修（按优先级，2026-09-10 复核）

> 复核说明：老清单里 P1#1（vm_pu 不生效）与 P1#3（存取丢 vm_pu）经查证**并不成立**
> ——属性面板经 `_set_attr` 实时写回 model，`_save_topology` 用 `asdict` 全量序列化，
> solver 每次运行都读取 `model.vm_pu`。P2#4（改线路参数不生效）同样不成立：
> `build_pandapower` 创建线路后会把用户的 r/x/c/max_i 覆写回 pandapower 表。
> 请勿按过时情报返工。

### ✅ 已修复（2026-09-09/10 通宵迭代，详见 git log optimize(round-N)）

1. ~~变压器结果显示"暂未提取"~~ — 变压器/阻抗结果均已提取并展示
2. ~~点空白无法取消选中~~ — 点空白 / ESC 均可取消选中，ESC 还能取消正在拖的连线
3. ~~画布无缩放~~ — 滚轮以光标为锚缩放，工具栏 ⤢ / Ctrl+0 适配视图
4. ~~结果显示按 name 反查，重名元件结果串位~~ — solver 全程走 uid↔pp_id 映射
5. ~~画布移动元件位置不回写 model，存/载丢布局~~ — `itemChange` 回写（Bus/Gen/Load）
6. ~~删除母线留下幽灵图形项~~ — 级联删除挂接的 Gen/Load/Trafo/Impedance 图形项
7. ~~选中连线属性面板必崩~~ — `_add_float/_add_combo_bus` 显式接收 model
8. ~~坏 JSON 载入即崩 / 载入后变压器悬空~~ — `parse_topology_json` 先校验后替换，
   重建时补齐 Bus↔LineComp 连线
9. ~~删除后再建元件显示名重名~~ — `_next_name` 计数器跳过占用名
10. ~~变压器自动创建可能 hv==lv 同母线~~ — 创建与校验两处都拦截
11. ~~Gen/Load 拖线只是视觉装饰~~ — 拖线连母线即转正改挂 bus_uid
12. ~~无撤销/重做~~ — 快照式 QUndoStack（Ctrl+Z/Ctrl+Shift+Z），增删/连线可撤销
13. ~~无 DC 潮流~~ — 计算→DC 直流模式（pandapower 3.x 用 `pp.rundcpp`，注意
    `runpp(algorithm="dc")` 已不可用，会 KeyError）
14. ~~无结果总览/CSV~~ — 底部结果 dock（母线表/支路表/电压柱状图）+ 一键导出双 CSV
15. ~~无 IEEE 标准算例~~ — 计算菜单一键加载 case14/case30（转换器 `ieee_cases.py`）
16. ~~关闭窗口丢工作/保存写半截文件~~ — closeEvent 未保存提醒 + 原子写

### 🔴 P1 — 修一下体验会好很多

（当前无 P1 级已知 bug）

### 🟡 P2 — 锦上添花

1. **没有 OPF / 短路 / 时域仿真**（仅稳态潮流）— README 已经说明
2. **撤销粒度**：拖动位置、属性面板连续编辑不在快照内

### 🟢 P3 — 大改动 / 长期

1. ~~没有对齐网格~~ — 视图菜单"网格对齐(新元件)" + 背景参考线
2. **没有复制/粘贴**
3. **拓扑/视图解耦**、**求解器抽 Backend 接口**（见第四节）
4. **发电机 PV/PQ 类型切换**（is_slack 已支持, 全类型切换未做）

### 其他 2026-09-10 新增能力速查

- N-1 校核：`solver.n_minus_1_check` + 计算菜单入口（逐条开断报告越限/孤立）
- IEEE 14/30/39/57/118 一键加载：`ieee_cases.py`
- 拖动位置已纳入撤销快照（`CircuitView` 按压/释放对比 + `push_move_undo`）
- 生成 README 截图：`python tools/render_screenshot.py`（勿用 offscreen, 见脚本注释）

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
