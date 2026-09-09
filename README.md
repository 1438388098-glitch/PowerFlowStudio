# 潮流计算 GUI · Power Flow Studio

一个基于 PyQt5 的电力系统潮流计算可视化工具。左侧元件库, 中间画布自由拖拽搭建电网, 右侧编辑参数和查看结果, 内核使用成熟的 pandapower(牛顿-拉夫逊法)。

## 功能

- **元件**: 母线 / 电源(发电机) / 负荷 / 变压器 / 串联阻抗
- **自由拖拽**: 元件放到画布任意位置; 端口之间拖拽连线建立连接
- **属性编辑**: 选中元件, 右侧面板改参数(实时生效)
- **潮流计算**: 点击工具栏 ▶ 运行潮流, 用 pandapower 牛顿-拉夫逊法
- **结果可视化**: 母线按电压标幺着色(绿/黄/红), 线路显示 P/Q 和负载率
- **拓扑保存/加载**: 整张电网保存为 JSON, 可重新载入
- **示例一键加载**: 工具栏 "★ 加载示例" 直接放一个 3 母线测试网

## 安装

```bash
# 推荐: 使用 uv
uv venv .venv
source .venv/bin/activate      # Linux/WSL/macOS
# .venv\Scripts\activate       # Windows
uv pip install PyQt5 pyqtgraph pandapower numpy
```

## 运行

```bash
source .venv/bin/activate
python app.py
```

启动后:
1. 工具栏点 "★ 加载示例", 看到 3 母线示例自动跑一次潮流
2. 或从左侧元件库点击元件, 然后在画布上点击放置
3. 把鼠标放到元件边缘的黑色小圆点(端口)上, 拖到另一个元件的端口建立连接
4. 选中元件, 在右侧面板修改参数
5. 工具栏点 "▶ 运行潮流"

## 打包成 exe

```bash
source .venv/bin/activate
uv pip install pyinstaller
pyinstaller --onefile --windowed --name PowerFlowStudio app.py
# 产物: dist/PowerFlowStudio.exe (Windows) 或 dist/PowerFlowStudio (Linux/macOS)
```

注意:
- 第一次打包比较慢(数十秒到几分钟)
- 单文件 exe 体积 80-150MB(包含 pandapower + numpy + PyQt5)
- Windows: 直接双击 dist\PowerFlowStudio.exe 运行
- Linux: `./dist/PowerFlowStudio`; 若提示缺 libxcb 等, `sudo apt install libxcb-xinerama0 libxkbcommon-x11-0`

## 文件结构

```
PowerFlowGUI/
├── app.py          # 主入口, 工具栏/菜单/快捷键
├── canvas.py       # QGraphicsView 画布, 元件与连线
├── palette.py      # 左侧元件库面板
├── properties.py   # 右侧属性编辑面板
├── solver.py       # 拓扑 ↔ pandapower 转换 + 潮流计算
└── README.md
```

## 键盘快捷键

- `Ctrl+R`: 运行潮流
- `Ctrl+L`: 加载示例
- `Delete` / `Backspace`: 删除选中元件或连线

## 当前限制

- 单平衡节点(第一个发电机自动当 ext_grid)
- 不支持 OPF / 短路 / 时域仿真(只做稳态潮流)
- 变压器两端必须接到两个不同母线
- 保存 JSON 时不含计算结果(只存拓扑)

## 扩展方向

- 多平衡节点 / 发电机 PV 节点类型切换
- 结果表格导出 CSV
- 引入 IEEE 标准测试系统(case14/case30/case39)
- DC 潮流模式
- 内嵌时域仿真接口
