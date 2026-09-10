"""
app.py — Main program entry.
Layout: left palette / centre canvas / right properties.
Toolbar & menus: run power flow (AC/DC) / results table / CSV export /
clear / load demo / save+load topology.
Component creation happens via drag-and-drop from the palette onto the
canvas (handled in canvas.py: CircuitView.dropEvent). The MainWindow
only wires signals and owns the Network.
"""
from __future__ import annotations
import copy
import inspect
import json
import logging
import os
import sys
import tempfile
import time
from dataclasses import asdict, fields as dc_fields

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QAction, QMessageBox, QSplitter,
    QStatusBar, QShortcut, QToolBar, QFileDialog, QDockWidget, QMenu,
    QUndoStack, QLabel
)

from solver import (
    Network, run_power_flow, run_opf, run_short_circuit, _RESULT_FIELDS,
    _clear_results,
    BusNode, GenUnit, LoadUnit, LineBranch, TrafoBranch, ImpedanceBranch,
    ShuntUnit,
)
from canvas import CircuitScene, CircuitView, BaseComponent, ConnectionItem
import pandapower as pp
import PyQt5.QtCore

import theme
from palette import ComponentPalette
from properties import PropertiesPanel
from results import (  # noqa: F401  再导出, 对外接口与拆分前一致
    ResultsPanel, export_results_csv, render_scene_png,
    render_scene_svg,
)
from topo_io import (  # noqa: F401  再导出, 兼容旧导入路径
    parse_topology_json, network_to_json_dict, _TOPO_SPEC,
)
from undocmds import SnapshotCommand
from ux import SearchDialog, make_minimap_dock

__version__ = "0.7.1"


class PowerFlowThread(QThread):
    """大网络后台计算: 在副本上跑潮流, 完成后把结果交回 GUI 线程"""
    done = pyqtSignal(object, bool, str, float)   # net_copy, ok, err, elapsed_ms

    def __init__(self, net: Network, algorithm: str,
                 distributed_slack: bool = False, parent=None):
        super().__init__(parent)
        # 深拷贝: 计算期间用户继续编辑不影响输入, 结果也不直接写活网络
        self._net = copy.deepcopy(net)
        self._algorithm = algorithm
        self._distributed_slack = distributed_slack

    def run(self):
        t0 = time.perf_counter()
        try:
            ok, err = run_power_flow(self._net, algorithm=self._algorithm,
                                     distributed_slack=self._distributed_slack)
        except Exception as e:   # 后台线程兜底, 不能让异常无声消失
            ok, err = False, f"{type(e).__name__}: {e}"
        self.done.emit(self._net, ok, err, (time.perf_counter() - t0) * 1000.0)


class ComputeThread(QThread):
    """通用后台计算: 在活网络的一份深拷贝上执行 work(net), 完成后把
    (net_copy, outcome, 耗时ms) 交回 GUI 线程。

    OPF / 短路 / N-1 / 118 节点潮流以前全在主线程同步跑, 界面直接冻住
    (118 节点 N-1 要跑上百次潮流), 而且计算期间工具栏不禁用, 用户可以
    再点一次造成重入。统一走这个线程 + 禁用计算类动作即可根治。

    outcome 的约定: work() 的返回值原样带回; 抛异常时带回
    ("__error__", "异常信息")。work 若接受第二个参数, 会收到一个
    进度回调 progress(done, total)。
    """
    done = pyqtSignal(object, object, float)
    progress = pyqtSignal(int, int)

    def __init__(self, net: Network, work, parent=None):
        super().__init__(parent)
        # 深拷贝: 计算期间用户继续编辑不影响输入, 结果也不直接写活网络
        self._net = copy.deepcopy(net)
        self._work = work
        try:
            self._wants_progress = len(
                inspect.signature(work).parameters) >= 2
        except (TypeError, ValueError):
            self._wants_progress = False

    def run(self):
        t0 = time.perf_counter()
        try:
            if self._wants_progress:
                outcome = self._work(self._net, self.progress.emit)
            else:
                outcome = self._work(self._net)
        except Exception as e:
            outcome = ("__error__", f"{type(e).__name__}: {e}")
        self.done.emit(self._net, outcome, (time.perf_counter() - t0) * 1000.0)


def _work_opf(net: Network, progress=None) -> tuple:
    """后台线程里执行的 OPF(独立函数: 便于测试与复用)。

    第二个参数是 ComputeThread 注入的进度回调, OPF 单次求解用不上, 收下
    即可 —— 签名有两个参数是有意的, 否则 ComputeThread 会把进度回调
    当成第一个业务参数传错位置。
    """
    return run_opf(net)


def setup_crash_logger():
    """统一异常日志: 写系统临时目录(只读目录兜底 stderr), 三处手写
    crash.log 由此收口。"""
    lg = logging.getLogger("powerflow.crash")
    if not lg.handlers:
        lg.setLevel(logging.ERROR)
        try:
            log_path = os.path.join(tempfile.gettempdir(), "PowerFlowStudio.log")
            handler = logging.FileHandler(log_path, encoding="utf-8")
        except Exception:
            handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s  %(levelname)s  %(message)s"))
        lg.addHandler(handler)
        lg.propagate = False
    return lg


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # 统一外观(风格/调色板/字号/样式表)。幂等, 测试里也走同一套 UI。
        theme.apply_theme()
        self.setWindowTitle("潮流计算 GUI  ·  pandapower 内核")
        # 分辨率适配: 默认尺寸按当前屏幕可用区域算, 并给出小屏也能容纳的
        # 最小尺寸 —— 以前写死 1280×800, 在 1366×768 上会顶到屏幕边缘,
        # 在 4K 上又只占中间一小块。
        self.resize(theme.default_window_size())
        self.setMinimumSize(theme.minimum_window_size())

        # 文件状态 / 撤销
        self._current_path = None
        self._dirty = False
        self._in_tracked_op = False       # 嵌套调用不重复快照
        self._tracking_suspended = False  # demo/载入等整体替换不进撤销栈
        self.undo_stack = QUndoStack(self)

        # Network + scene + view
        self.network = Network()
        self.scene = CircuitScene(self.network)
        self.view = CircuitView(self.scene)
        self.scene.set_view(self.view)  # optional; absent in older canvas

        self.palette = ComponentPalette()
        self.properties = PropertiesPanel()
        self.properties.attach_scene(self.scene)

        # Selection -> property panel
        self.scene.selectionChanged.connect(self._on_selection_changed)

        # 增删/连线入口包上 标脏+撤销快照
        self._install_tracking()
        self._update_title()

        # Layout
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.palette)
        splitter.addWidget(self.view)
        splitter.addWidget(self.properties)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        # 三栏比例按窗口宽度算(元件库 ~13% / 属性面板 ~21%), 而不是写死像素
        splitter.setSizes(theme.splitter_sizes(self.width()))
        self.setCentralWidget(splitter)

        # 结果总览 dock (底部, 首次运行潮流时弹出一次, 用户关掉不再打扰)
        self.results_panel = ResultsPanel()
        self.results_dock = QDockWidget("潮流结果总览", self)
        self.results_dock.setWidget(self.results_panel)
        self.results_dock.hide()
        self.addDockWidget(Qt.BottomDockWidgetArea, self.results_dock)
        self._results_ever_shown = False
        self._pf_thread = None
        self._compute_thread = None      # OPF / 短路 / N-1 的后台线程
        self._compute_actions = []       # 计算期间要禁用的动作(避免重入)
        self._compute_fingerprint = None  # 后台计算开始时的拓扑指纹
        # 结果表行点击 → 画布选中该元件
        self.results_panel.row_activated.connect(self._on_result_row_activated)

        # 小地图 (右侧, 定时刷新缩略与视口指示框)
        self.minimap_dock = make_minimap_dock(self.scene, self.view, self)
        self.addDockWidget(Qt.RightDockWidgetArea, self.minimap_dock)
        self.minimap_dock.hide()
        self._minimap_timer = QTimer(self)
        self._minimap_timer.timeout.connect(self._refresh_minimap)
        self._minimap_timer.start(600)
        self._search_dialog = None

        # Toolbar
        toolbar = QToolBar()
        self.addToolBar(toolbar)
        self.act_run = QAction("▶ 运行潮流", self)
        self.act_run.setStatusTip("运行潮流计算 (Ctrl+R)")
        self.act_run.triggered.connect(self._run_power_flow)
        toolbar.addAction(self.act_run)
        self.act_dc = QAction("DC 直流模式", self)
        self.act_dc.setCheckable(True)
        self.act_dc.setToolTip("勾选后用直流潮流(DC)求解: 只算有功与相角, 速度更快")
        self.act_dc.toggled.connect(
            lambda c: self.mode_label.setText("DC" if c else "AC"))
        toolbar.addAction(self.act_dc)
        self.act_dslack = QAction("分布式松弛", self)
        self.act_dslack.setCheckable(True)
        self.act_dslack.setStatusTip("按各发电机松弛权重分摊网损功率 (属性面板可设权重)")
        toolbar.addAction(self.act_dslack)
        act_clear = QAction("✖ 清空画布", self)
        act_clear.triggered.connect(self._clear_canvas)
        toolbar.addAction(act_clear)
        toolbar.addSeparator()
        act_demo = QAction("★ 加载示例", self)
        act_demo.triggered.connect(self._load_demo)
        toolbar.addAction(act_demo)
        act_two_end = QAction("⚡ 两端供电", self)
        act_two_end.triggered.connect(self._load_two_end_demo)
        toolbar.addAction(act_two_end)
        toolbar.addSeparator()
        act_save = QAction("💾 保存拓扑", self)
        act_save.setStatusTip("保存拓扑到当前文件 (Ctrl+S); 首次保存会询问路径")
        act_save.triggered.connect(self._save_topology)
        toolbar.addAction(act_save)
        act_load = QAction("📂 载入拓扑", self)
        act_load.triggered.connect(self._load_topology)
        toolbar.addAction(act_load)
        act_export = QAction("📄 导出CSV", self)
        act_export.triggered.connect(self._export_csv)
        toolbar.addAction(act_export)
        toolbar.addSeparator()
        act_undo = QAction("↩ 撤销", self)
        act_undo.setStatusTip("撤销上一次增删/连线/移动/粘贴 (Ctrl+Z)")
        act_undo.triggered.connect(self.undo_stack.undo)
        act_undo.setEnabled(False)
        self.undo_stack.canUndoChanged.connect(act_undo.setEnabled)
        toolbar.addAction(act_undo)
        act_redo = QAction("↪ 重做", self)
        act_redo.setStatusTip("重做被撤销的操作 (Ctrl+Shift+Z)")
        act_redo.triggered.connect(self.undo_stack.redo)
        act_redo.setEnabled(False)
        self.undo_stack.canRedoChanged.connect(act_redo.setEnabled)
        toolbar.addAction(act_redo)
        toolbar.addSeparator()
        act_fit = QAction("⤢ 适配视图", self)
        act_fit.triggered.connect(self.view.fit_view)
        toolbar.addAction(act_fit)

        # 菜单栏 (工具栏之外的第二入口, 提高可发现性)
        menu_file = self.menuBar().addMenu("文件(&F)")
        act_new = QAction("新建(&N)", self)
        act_new.triggered.connect(self._new_file)
        menu_file.addAction(act_new)
        menu_file.addAction(act_save)
        act_save_as = QAction("另存为(&A)...", self)
        act_save_as.triggered.connect(self._save_topology_as)
        menu_file.addAction(act_save_as)
        menu_file.addAction(act_load)
        act_png = QAction("导出画布 PNG(&P)...", self)
        act_png.triggered.connect(self._export_png)
        menu_file.addAction(act_png)
        act_svg = QAction("导出画布 SVG(&G)...", self)
        act_svg.triggered.connect(self._export_svg)
        menu_file.addAction(act_svg)
        act_export2 = QAction("导出结果 CSV(&C)...", self)
        act_export2.triggered.connect(self._export_csv)
        menu_file.addAction(act_export2)
        menu_file.addSeparator()
        act_autosave = QAction("恢复自动保存(&V)", self)
        act_autosave.setStatusTip("从临时目录里最近一次的自动保存恢复画布")
        act_autosave.triggered.connect(self._restore_autosave)
        menu_file.addAction(act_autosave)
        menu_file.addSeparator()
        self.recent_menu = QMenu("最近文件(&R)", self)
        menu_file.addMenu(self.recent_menu)
        self._rebuild_recent_menu()
        menu_file.addSeparator()
        act_quit = QAction("退出(&Q)", self)
        act_quit.triggered.connect(self.close)
        menu_file.addAction(act_quit)
        menu_run = self.menuBar().addMenu("计算(&R)")
        menu_run.addAction(self.act_run)
        menu_run.addAction(self.act_dc)
        menu_run.addSeparator()
        menu_run.addAction("★ 3 母线示例", self._load_demo)
        menu_run.addAction("⚡ 两端供电示例", self._load_two_end_demo)
        # 注意不能直接把 self._run_n_minus_1 接给 triggered: QAction.triggered
        # 会传一个 checked(bool), 而签名 (self, interactive=True) 正好能收下,
        # 于是菜单点出来永远是 interactive=False —— 报告根本不弹。用 lambda 收口。
        self._act_n1 = QAction("⛏ N-1 校核(逐条开断)", self)
        self._act_n1.triggered.connect(lambda checked=False: self._run_n_minus_1())
        menu_run.addAction(self._act_n1)
        act_opf = QAction("🎯 OPF 最优潮流", self)
        act_opf.setStatusTip("以发电成本最小为目标优化各机组出力 (属性面板可设成本与出力上下限)")
        act_opf.triggered.connect(self._run_opf)
        menu_run.addAction(act_opf)
        menu_sc = menu_run.addMenu("三相短路计算")
        act_sc_max = QAction("最大运行方式", self)
        act_sc_max.triggered.connect(lambda: self._run_short_circuit("max"))
        menu_sc.addAction(act_sc_max)
        act_sc_min = QAction("最小运行方式", self)
        act_sc_min.triggered.connect(lambda: self._run_short_circuit("min"))
        menu_sc.addAction(act_sc_min)
        menu_run.addSeparator()
        for case_name, case_label in (("case14", "IEEE 14 母线"),
                                      ("case24_ieee_rts", "IEEE RTS-24 母线"),
                                      ("case30", "IEEE 30 母线"),
                                      ("case39", "IEEE 39 母线"),
                                      ("case57", "IEEE 57 母线"),
                                      ("case118", "IEEE 118 母线")):
            act_case = QAction(case_label, self)
            act_case.triggered.connect(
                lambda checked=False, cn=case_name: self._load_ieee_case(cn))
            menu_run.addAction(act_case)
        menu_view = self.menuBar().addMenu("视图(&V)")
        act_zoom_in = QAction("放大(&I)", self)
        act_zoom_in.triggered.connect(self.view.zoom_in)
        menu_view.addAction(act_zoom_in)
        act_zoom_out = QAction("缩小(&O)", self)
        act_zoom_out.triggered.connect(self.view.zoom_out)
        menu_view.addAction(act_zoom_out)
        menu_view.addAction(act_fit)
        menu_view.addAction(self.results_dock.toggleViewAction())
        act_snap = QAction("网格对齐(新元件)", self)
        act_snap.setCheckable(True)
        act_snap.triggered.connect(self._toggle_snap)
        menu_view.addAction(act_snap)
        self.act_minimap = QAction("小地图", self)
        self.act_minimap.setCheckable(True)
        self.act_minimap.setChecked(False)
        self.act_minimap.triggered.connect(
            lambda c: self.minimap_dock.setVisible(c))
        menu_view.addAction(self.act_minimap)
        self.act_kv = QAction("母线电压标 kV", self)
        self.act_kv.setCheckable(True)
        self.act_kv.setStatusTip("切换母线上方电压标签的显示单位 (pu / kV)")
        self.act_kv.triggered.connect(self._toggle_v_label)
        menu_view.addAction(self.act_kv)
        menu_help = self.menuBar().addMenu("帮助(&H)")
        act_help = QAction("使用说明(&H)", self)
        act_help.triggered.connect(self._show_help)
        menu_help.addAction(act_help)
        act_sysinfo = QAction("系统信息(&S)", self)
        act_sysinfo.triggered.connect(self._show_sysinfo)
        menu_help.addAction(act_sysinfo)
        act_about = QAction("关于(&A)", self)
        act_about.triggered.connect(
            lambda: QMessageBox.about(
                self, "关于 PowerFlowStudio",
                f"潮流计算 GUI · Power Flow Studio  v{__version__}\n"
                "PyQt5 画布 + pandapower 牛顿-拉夫逊/直流潮流内核\n"
                "拖拽搭建电网, 一键计算, 电压着色、结果总览与 N-1 校核。"))
        menu_help.addAction(act_about)

        # Status bar
        # 计算类动作: 计算进行中统一禁用, 杜绝"算着算着又点一次"的重入
        # (主线程同步分支与后台线程会并发改写同一份结果字典)。
        self._compute_actions = [self.act_run, act_opf, self._act_n1,
                                 act_sc_max, act_sc_min]
        # 上次自动保存时间提示(须在状态栏创建前算好)
        autosave_ts = self._recent_settings().value("autosave_time", "")
        if autosave_ts and any(os.path.exists(p) for p in self._autosave_paths()):
            self._startup_autosave_hint = (
                f"上次自动保存: {autosave_ts} (文件-恢复自动保存 可取回)")
        else:
            self._startup_autosave_hint = None
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage(
            self._startup_autosave_hint
            or "Ready — drag a component from the left into the canvas")
        self.mode_label = QLabel(" AC ")
        self.mode_label.setToolTip("当前求解模式: AC=牛顿-拉夫逊, DC=直流潮流")
        self.status.addPermanentWidget(self.mode_label)
        self.stats_label = QLabel("")
        self.stats_label.setToolTip("画布元件统计")
        self.status.addPermanentWidget(self.stats_label)
        self.coord_label = QLabel("")
        self.coord_label.setToolTip("光标画布坐标")
        self.status.addPermanentWidget(self.coord_label)
        self._refresh_stats()

        # Shortcuts
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self._run_power_flow)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self._load_demo)
        QShortcut(QKeySequence("Ctrl+0"), self, activated=self.view.fit_view)
        QShortcut(QKeySequence.Undo, self, activated=self.undo_stack.undo)
        QShortcut(QKeySequence.Redo, self, activated=self.undo_stack.redo)
        QShortcut(QKeySequence.Save, self, activated=self._save_topology)
        QShortcut(QKeySequence.New, self, activated=self._new_file)

        # 恢复上次的窗口几何与面板布局
        s = self._recent_settings()
        geometry = s.value("window_geometry")
        if geometry is not None:
            try:
                self.restoreGeometry(geometry)
                # 存档可能来自另一台显示器/更高的分辨率, 直接恢复会得到
                # "窗口跑到屏幕外"或"比屏幕还大"的哑状态, 这里收敛一次
                if not self.isMaximized():
                    theme.clamp_to_screen(self)
            except Exception:
                logging.getLogger("powerflow.crash").exception("恢复窗口几何失败")
        sizes = s.value("splitter_sizes")
        if sizes:
            # 存档里的三栏宽度可能来自旧版本(那时面板宽度是写死的), 也可能
            # 来自另一块屏幕。逐项 clamp 是不够的 —— 只要三栏之和远小于
            # 当前窗口, 富余宽度会被 stretch=1 的画布全部吃掉, 用户看到
            # "属性面板卡在最小值"的哑状态且每次启动都复现。这里交给
            # theme 做完整校验, 不可信就按当前分辨率重算。
            vals = theme.sanitize_splitter_sizes(sizes, self.width())
            try:
                self.centralWidget().setSizes(vals)
            except (TypeError, ValueError):
                pass
        if s.value("results_visible", "false") in ("true", True):
            self.results_dock.show()
            self._results_ever_shown = True

        # 编辑快捷键
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._open_search)
        QShortcut(QKeySequence.Copy, self, activated=self._copy_selection)
        QShortcut(QKeySequence.Paste, self, activated=self._paste_clipboard)
        # 自动保存(每3分钟, 仅脏画布)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self._autosave)
        self._autosave_timer.start(180_000)

    # ---------- 状态: 标题 / 脏标记 / 撤销 ----------
    def _open_search(self):
        if self._search_dialog is None:
            self._search_dialog = SearchDialog(self.scene, self)
            self._search_dialog.item_selected.connect(self._on_search_selected)
        self._search_dialog.refresh()
        self._search_dialog.show()
        self._search_dialog.raise_()
        self._search_dialog.edit.setFocus()

    def _on_search_selected(self, uid: str) -> None:
        """搜索框选中一项: 元件与支路连线都要能定位。

        以前固定按 "bus" 查, 而支路(线路/变压器/阻抗)没有独立图形项,
        点搜索结果没有任何反应。
        """
        item = self.scene._comp_by_uid.get(uid)
        if item is None:
            for c in self.scene._connections:
                if c.uid == uid:
                    item = c
                    break
        if item is None:
            return
        self.scene.clearSelection()
        item.setSelected(True)
        self.view.centerOn(item.scenePos())

    def _update_coords(self, x: float, y: float):
        self.coord_label.setText(f"({x:.0f}, {y:.0f})")

    def _refresh_minimap(self):
        if self.minimap_dock.isVisible():
            self.minimap_dock._minimap_view.refresh()

    def _copy_selection(self):
        n = self.scene.copy_selection()
        self.status.showMessage(
            f"已复制 {n} 个元件 (Ctrl+V 粘贴)" if n else "未选中任何元件", 3000)
        return n

    def _paste_clipboard(self):
        before = self.snapshot_network()
        n = self.scene.paste_clipboard()
        after = self.snapshot_network()
        if n and after != before:
            self._set_dirty(True)
            self._refresh_stats()
            self.undo_stack.push(SnapshotCommand(self, before, after, "粘贴元件"))
            self.status.showMessage(f"已粘贴 {n} 个元件", 3000)
        return n

    # ---------- 自动保存 ----------
    AUTOSAVE_SLOTS = 3   # 轮转保留最近 3 份, 防止恰好存了坏状态

    def _autosave_paths(self):
        return [os.path.join(tempfile.gettempdir(),
                             f"PowerFlowStudio_autosave_{i}.json")
                for i in range(self.AUTOSAVE_SLOTS)]

    def _autosave_path(self) -> str:
        """兼容旧接口: 返回最新一份自动保存文件路径"""
        existing = [p for p in self._autosave_paths() if os.path.exists(p)]
        if existing:
            return max(existing, key=os.path.getmtime)
        return self._autosave_paths()[0]

    def _autosave(self):
        if not self._dirty or not self.network.buses:
            return
        try:
            s = self._recent_settings()
            idx = int(s.value("autosave_idx", 0) or 0) % self.AUTOSAVE_SLOTS
            path = self._autosave_paths()[idx]
            data = network_to_json_dict(self.network)
            # 与手动保存同口径的原子写: 以前直接 open(path,"w"), 写到一半
            # 崩溃/断电会留下半截 JSON, 而恢复逻辑优先挑 mtime 最新的那份,
            # 恰好就是这份坏文件。
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            s.setValue("autosave_idx", idx + 1)
            s.setValue("autosave_time", time.strftime("%Y-%m-%d %H:%M:%S"))
            self._autosave_failed = False
        except Exception as e:
            logging.getLogger("powerflow.crash").exception("自动保存失败")
            # 自动保存失败以前只写日志 —— 用户以为有备份, 其实没有。
            # 每次连续失败只提示一次, 避免每 3 分钟弹一次状态栏。
            if not getattr(self, "_autosave_failed", False):
                self._autosave_failed = True
                self.status.showMessage(
                    f"⚠ 自动保存失败({type(e).__name__}), 请手动 Ctrl+S 保存", 8000)

    def _restore_autosave(self) -> bool:
        """从自动保存恢复(不覆盖 current_path)。

        按修改时间从新到旧遍历全部轮转槽位, 取第一份能解析成功的 ——
        最新那份可能正好是坏状态(半截写入/磁盘满), 旧写法就此直接失败,
        而旁边明明还躺着两份完好的存档。
        """
        candidates = sorted(
            (p for p in self._autosave_paths() if os.path.exists(p)),
            key=os.path.getmtime, reverse=True)
        if not candidates:
            self.status.showMessage("没有可恢复的自动保存", 4000)
            return False
        net = None
        errors = []
        for path in candidates:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    net = parse_topology_json(json.load(f))
                break
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {e}")
        if net is None:
            self.status.showMessage(
                "自动保存文件均无法解析: " + "; ".join(errors), 6000)
            return False
        self._tracking_suspended = True
        try:
            self._apply_network(net)
        finally:
            self._tracking_suspended = False
        self.undo_stack.clear()
        self._set_dirty(True)
        self.status.showMessage("已从自动保存恢复", 5000)
        return True

    def snapshot_network(self) -> dict:
        """当前网络快照(供画布拖动撤销取用)"""
        return network_to_json_dict(self.network)

    def _refresh_stats(self):
        n = self.network
        n_branch = len(n.lines) + len(n.trafos) + len(n.impedances)
        self.stats_label.setText(
            f"母线{len(n.buses)} 机{len(n.gens)} 负荷{len(n.loads)} 支路{n_branch} ")

    def push_move_undo(self, before: dict, after: dict, label: str = "移动元件"):
        """拖动/参数修改: 变化作为一次撤销入栈 (无变化不入栈)"""
        if self._tracking_suspended or self._in_tracked_op:
            return
        if before == after:
            return
        self._set_dirty(True)
        self.undo_stack.push(SnapshotCommand(self, before, after, label))

    def _set_dirty(self, dirty: bool):
        self._dirty = dirty
        self._update_title()

    def _update_title(self):
        name = os.path.basename(self._current_path) if self._current_path else "未命名"
        star = " *" if self._dirty else ""
        self.setWindowTitle(f"{name}{star} — 潮流计算 GUI · Power Flow Studio")

    def _install_tracking(self):
        """包装 scene 的增删/连线入口: 每次真实变更 标脏 + 压入撤销快照"""
        scene = self.scene
        for name, label in (("add_component", "添加元件"),
                            ("create_connection", "建立连线"),
                            ("delete_item", "删除")):
            original = getattr(scene, name)
            setattr(scene, name, self._wrap_mutation(original, label))

    def _wrap_mutation(self, original, label):
        mw = self

        def wrapper(*args, **kwargs):
            if mw._in_tracked_op or mw._tracking_suspended:
                return original(*args, **kwargs)
            before = network_to_json_dict(mw.network)
            mw._in_tracked_op = True
            try:
                result = original(*args, **kwargs)
            finally:
                mw._in_tracked_op = False
            after = network_to_json_dict(mw.network)
            if after != before:
                mw._set_dirty(True)
                mw._refresh_stats()
                mw.undo_stack.push(SnapshotCommand(mw, before, after, label))
            return result

        return wrapper

    def _restore_snapshot(self, state: dict):
        """撤销/重做: 用快照整体替换网络并重建画布"""
        try:
            net = parse_topology_json(json.loads(json.dumps(state)))
        except Exception:
            logging.getLogger("powerflow.crash").exception("快照恢复失败")
            return
        was_suspended = self._tracking_suspended
        self._tracking_suspended = True
        try:
            self._apply_network(net)
        finally:
            self._tracking_suspended = was_suspended
        self._set_dirty(True)

    # ---------- 计算状态(防重入 / 后台线程) ----------
    def _compute_busy(self) -> bool:
        return self._pf_thread is not None or self._compute_thread is not None

    def _compute_should_async(self) -> bool:
        """本次计算是否该放到后台线程。

        与潮流共用母线数阈值; 另加支路数条件 —— N-1 的代价随支路数平方
        增长, 支路多于 400 时即使母线不多也会明显卡顿。
        """
        n_branch = (len(self.network.lines) + len(self.network.trafos)
                    + len(self.network.impedances))
        return (len(self.network.buses) > self.ASYNC_PF_THRESHOLD
                or n_branch > 400)

    def _begin_compute(self, label: str) -> bool:
        """一次计算开始前的闸门: 已在算则拒绝。

        以前后台潮流进行中, 阈值判断(and self._pf_thread is None)为假就
        落到主线程同步分支 —— 既冻结界面, 又与后台线程并发改写同一份结果
        字典。这里统一收口, 并把计算类动作禁掉。
        """
        if self._compute_busy():
            self.status.showMessage(
                f"⏳ 已有计算在进行中, 本次{label}已忽略, 请稍候…", 5000)
            return False
        self._set_compute_enabled(False)
        return True

    def _end_compute(self) -> None:
        self._set_compute_enabled(True)

    def _set_compute_enabled(self, enabled: bool) -> None:
        for act in self._compute_actions:
            act.setEnabled(enabled)

    def _topology_fingerprint(self) -> tuple:
        """拓扑指纹: 判断后台计算期间用户是否改过网络。

        后台线程在深拷贝上算, 算完把结果搬回活网络; 若这期间拓扑变了,
        搬回来的结果描述的就是另一个网络 —— 直接覆盖等于交付错数据。
        """
        n = self.network
        return (tuple(sorted(n.buses)), tuple(sorted(n.gens)),
                tuple(sorted(n.loads)), tuple(sorted(n.lines)),
                tuple(sorted(n.trafos)), tuple(sorted(n.impedances)),
                tuple(sorted(n.shunts)))

    def _start_compute_thread(self, work, on_done, label: str) -> None:
        self._compute_thread = ComputeThread(self.network, work, parent=self)
        self._compute_fingerprint = self._topology_fingerprint()
        self._compute_thread.done.connect(on_done)
        self._compute_thread.start()

    def _async_result_usable(self) -> bool:
        """后台结果是否仍对应当前拓扑"""
        if self._topology_fingerprint() == self._compute_fingerprint:
            return True
        self.status.showMessage(
            "⚠ 计算期间拓扑已被修改, 本次结果已丢弃 — 请重新计算", 8000)
        return False

    def _copy_results_into_network(self, net_copy) -> None:
        """把后台副本上的结果字段搬回活网络"""
        for f in _RESULT_FIELDS:
            setattr(self.network, f, dict(getattr(net_copy, f)))
        for f in ("total_loss_mw", "total_loss_q_mvar", "converged",
                  "error_msg", "result_kind"):
            setattr(self.network, f, getattr(net_copy, f))
        self.network.warnings = list(getattr(net_copy, "warnings", []))

    def _show_warnings(self) -> None:
        """把 solver 的非致命提示(孤立母线等)显示到状态栏"""
        warns = getattr(self.network, "warnings", None)
        if warns:
            self.status.showMessage("⚠ " + "; ".join(warns), 8000)

    # ---------- Selection ----------
    def _on_selection_changed(self):
        # 关闭窗口时场景可能已被销毁而信号尚未断开, 直接访问 self.scene
        # 会抛 RuntimeError: wrapped C/C++ object ... has been deleted
        # (被 conftest 的 os._exit 长期掩盖)。这里守卫一次。
        try:
            sel = self.scene.selectedItems()
        except RuntimeError:
            return
        if not sel:
            self.properties.clear()
            return
        item = sel[0]
        if isinstance(item, BaseComponent):
            self.properties.show_component(item)
        elif isinstance(item, ConnectionItem):
            self.properties.show_connection(item)

    def _on_result_row_activated(self, kind: str, uid: str):
        """结果总览表点击行 → 画布选中并居中对应元件"""
        if not uid:
            return
        item = self.scene._comp_by_uid.get(uid)
        if item is None and kind == "branch":
            # 线路没有独立图形项, 选中对应连线
            for c in self.scene._connections:
                if c.uid == uid:
                    item = c
                    break
        if item is None:
            return
        self.scene.clearSelection()
        item.setSelected(True)
        self.view.centerOn(item.scenePos())

    def _run_opf(self):
        """OPF 最优潮流: 成本最小化调度, 结果覆盖实际出力显示。

        大网络转后台线程(118 节点 OPF 会冻结界面数秒); 小网络同步执行,
        保持 (ok, err) 返回契约不变(测试与脚本依赖它)。
        """
        if not self._begin_compute("OPF 计算"):
            return False, "已有计算在进行中"
        if self._compute_should_async():
            self._start_compute_thread(_work_opf, self._on_opf_done, "OPF 计算")
            self.status.showMessage("⏳ 正在后台计算 OPF (最优潮流)...", 0)
            return True, ""
        t0 = time.perf_counter()
        try:
            ok, err = run_opf(self.network)
        except Exception as e:   # solver 已兜底, 这里是最后一道防线
            ok, err = False, f"{type(e).__name__}: {e}"
        finally:
            self._end_compute()
        return self._finish_opf(ok, err, (time.perf_counter() - t0) * 1000.0)

    def _finish_opf(self, ok, err, elapsed_ms):
        if not ok:
            if self.isVisible():
                QMessageBox.warning(self, "OPF 计算失败", err)
            self.status.showMessage(f"❌ {err}", 5000)
            return False, err
        self.network.converged = True
        self._refresh_compute_views()
        cost = sum((g.cost_per_mw or 0) * self.network.gen_p_mw.get(u, 0.0)
                   for u, g in self.network.gens.items())
        self.status.showMessage(
            f"🎯 OPF 收敛 — 总发电成本 ≈ {cost:.0f}, 耗时 {elapsed_ms:.0f} ms "
            "(机组实际出力即优化结果)", 6000)
        return True, ""

    def _on_opf_done(self, net_copy, outcome, elapsed_ms):
        self._compute_thread = None
        try:
            if isinstance(outcome, tuple) and outcome and outcome[0] == "__error__":
                self._finish_opf(False, outcome[1], elapsed_ms)
                return
            ok, err = outcome
            if not ok:
                self._finish_opf(False, err, elapsed_ms)
                return
            if not self._async_result_usable():
                return
            self._copy_results_into_network(net_copy)
            self._show_warnings()
            self._finish_opf(True, "", elapsed_ms)
        finally:
            self._end_compute()

    def _run_short_circuit(self, case: str = "max"):
        """三相短路计算: Ikss 写入母线结果 (电压等潮流结果会被清空)"""
        if not self._begin_compute("短路计算"):
            return False, "已有计算在进行中"
        if self._compute_should_async():
            work = (lambda n, progress=None: run_short_circuit(n, case=case))
            self._start_compute_thread(work, self._on_sc_done, "短路计算")
            self.status.showMessage("⏳ 正在后台计算三相短路...", 0)
            return True, ""
        t0 = time.perf_counter()
        try:
            ok, err = run_short_circuit(self.network, case=case)
        except Exception as e:
            ok, err = False, f"{type(e).__name__}: {e}"
        finally:
            self._end_compute()
        return self._finish_sc(ok, err, case,
                               (time.perf_counter() - t0) * 1000.0)

    def _finish_sc(self, ok, err, case, elapsed_ms):
        if not ok:
            if self.isVisible():
                QMessageBox.warning(self, "短路计算失败", err)
            self.status.showMessage(f"❌ {err}", 5000)
            return False, err
        self.network.converged = True
        self._refresh_compute_views()
        ik = [v for v in self.network.bus_ikss_ka.values() if v == v]
        if ik:
            self.status.showMessage(
                f"⚡ 短路计算[{case}] 完成 — Ikss 最大 {max(ik):.2f} kA "
                f"(母线 {len(ik)} 条), 耗时 {elapsed_ms:.0f} ms "
                "— 各母线属性面板可查", 8000)
        return True, ""

    def _on_sc_done(self, net_copy, outcome, elapsed_ms):
        self._compute_thread = None
        try:
            if isinstance(outcome, tuple) and outcome and outcome[0] == "__error__":
                self._finish_sc(False, outcome[1], "", elapsed_ms)
                return
            ok, err = outcome
            if not ok:
                self._finish_sc(False, err, "", elapsed_ms)
                return
            if not self._async_result_usable():
                return
            self._copy_results_into_network(net_copy)
            self._finish_sc(True, "", "后台", elapsed_ms)
        finally:
            self._end_compute()

    def _refresh_compute_views(self) -> None:
        """一次计算成功后统一刷新: 画布着色 / 属性面板结果 / 结果总览"""
        try:
            self.scene.refresh_results()
        except RuntimeError:
            return          # 场景已随窗口销毁(退出过程中)
        if self.properties.current_item is not None:
            self.properties.refresh_results()
        self.results_panel.refresh(self.network)
        self._refresh_stats()

    def _export_svg(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出画布 SVG", "grid.svg", "SVG (*.svg)"
        )
        if not path:
            return False
        if not self.scene.items():
            self.status.showMessage("画布是空的, 没有可导出的内容", 4000)
            return False
        if render_scene_svg(self.scene, path):
            self.status.showMessage(f"已导出 {path}", 5000)
            return True
        self.status.showMessage("导出失败", 4000)
        return False

    def _run_n_minus_1(self, interactive: bool = True):
        """N-1 校核: 逐条开断线路/变压器重跑潮流, 报告越限与孤立母线。

        interactive=False 只算并返回报告文本(供测试/后续自动化),
        不弹任何对话框。"""
        text = self._compute_n1_report()
        if text is None:
            return None
        if interactive:
            self._show_n1_report(text)
        self.status.showMessage("N-1 校核完成", 5000)
        return text

    def _compute_n1_report(self):
        """跑 N-1 校核并刷新界面, 失败返回 None

        N-1 逐条开断必须串行(每条都依赖同一份 pnet 的 in_service 状态),
        因此这里保持在主线程执行, 但用模态进度框 + 重入门闸兜住:
        计算期间所有计算类动作被禁用, 不会出现"算着又点一次"。
        """
        from solver import n_minus_1_check, format_n1_report
        if not self.network.buses or not self.network.gens:
            self.status.showMessage("画布上需要一个可计算的网络(至少母线+发电机)", 5000)
            return None
        if not self._begin_compute("N-1 校核"):
            return None
        try:
            algorithm = "dc" if self.act_dc.isChecked() else "nr"
            n_branch = (len(self.network.lines) + len(self.network.trafos)
                        + len(self.network.impedances))
            progress = None
            if n_branch > 30:
                from PyQt5.QtWidgets import QProgressDialog
                progress = QProgressDialog("N-1 校核计算中…", "取消", 0, n_branch,
                                           self)
                progress.setWindowModality(Qt.WindowModal)
                progress.setMinimumDuration(0)

            def _on_progress(done, total):
                if progress is not None:
                    progress.setValue(done)
                    QApplication.processEvents()

            self.status.showMessage("⏳ N-1 校核计算中...", 0)
            QApplication.processEvents()
            report = n_minus_1_check(self.network, algorithm=algorithm,
                                     distributed_slack=self.act_dslack.isChecked(),
                                     progress=_on_progress)
            if progress is not None:
                progress.setValue(n_branch)
                progress.close()
            self._refresh_stats()
            if "_base_failed" not in report:
                self._refresh_compute_views()
            return format_n1_report(report)
        finally:
            self._end_compute()

    def _show_n1_report(self, text: str):
        """弹 N-1 报告对话框, 支持另存 txt"""
        msg = QMessageBox(self)
        msg.setWindowTitle("N-1 校核报告")
        if len(text) > 4000:
            msg.setText(text.splitlines()[0] + " (详情见下方详细内容)")
            msg.setDetailedText(text)
        else:
            msg.setText(text)
        msg.setTextInteractionFlags(Qt.TextSelectableByMouse)
        save_btn = msg.addButton("保存报告...", QMessageBox.ActionRole)
        msg.addButton("关闭", QMessageBox.RejectRole)
        msg.exec_()
        if msg.clickedButton() is save_btn:
            path, _ = QFileDialog.getSaveFileName(
                self, "保存 N-1 报告", "n1_report.txt", "文本 (*.txt)")
            if path:
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(text)
                except OSError as e:
                    QMessageBox.warning(self, "保存失败", f"无法写入 {path}:\n{e}")
                    return
                self.status.showMessage(f"N-1 报告已保存到 {path}", 5000)

    def _export_png(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出画布 PNG", "grid.png", "PNG (*.png)"
        )
        if not path:
            return False
        if not self.scene.items():
            self.status.showMessage("画布是空的, 没有可导出的内容", 4000)
            return False
        if render_scene_png(self.scene, path):
            self.status.showMessage(f"已导出 {path}", 5000)
            return True
        self.status.showMessage("导出失败", 4000)
        return False

    def _toggle_snap(self, checked: bool) -> None:
        self.scene.snap_enabled = checked
        self.scene.update()

    def _toggle_v_label(self, checked: bool) -> None:
        self.scene.v_label_mode = "kv" if checked else "pu"
        self.scene.refresh_results()
        self.scene.update()
        self.status.showMessage(
            "网格对齐: 开 (影响新放置的元件)" if checked else "网格对齐: 关",
            3000)

    def _show_help(self):
        QMessageBox.information(
            self, "使用说明",
            "基本操作\n"
            "  · 左侧元件库按住拖到画布放置; 端口小圆点拖到另一元件建立连线\n"
            "  · Gen/Load 拖线连到母线 = 改挂接母线\n"
            "  · 滚轮缩放 / 中键拖拽平移 / Ctrl+0 适配视图\n"
            "  · 点空白或 ESC 取消选中; ESC 取消正在拖的连线\n"
            "  · 右键元件: 重命名 / 删除\n\n"
            "计算与结果\n"
            "  · Ctrl+R 或 ▶ 运行潮流; 勾选 DC 切换直流潮流\n"
            "  · 计算菜单可一键加载 IEEE 14/30/39 标准算例\n"
            "  · 底部结果总览: 点表格行可在画布上定位对应元件\n"
            "  · 文件菜单可导出结果 CSV 和画布 PNG\n\n"
            "快捷键\n"
            "  Ctrl+Z 撤销 / Ctrl+Shift+Z 重做 / Ctrl+S 保存 / Delete 删除选中\n")

    def _show_sysinfo(self):
        import platform as _p
        log_path = os.path.join(tempfile.gettempdir(), "PowerFlowStudio.log")
        QMessageBox.information(
            self, "系统信息",
            f"PowerFlowStudio v{__version__}\n"
            f"Python {_p.python_version()}\n"
            f"PyQt5 {PyQt5.QtCore.PYQT_VERSION_STR}\n"
            f"pandapower {pp.__version__}\n"
            f"numpy {__import__('numpy').__version__}\n"
            f"{theme.describe_environment()}\n"
            f"界面倍数可用环境变量 POWERFLOW_UI_SCALE 覆盖\n"
            f"测试: python -m pytest tests -q\n"
            f"异常日志: {log_path}")

    # ---------- Toolbar actions ----------
    ASYNC_PF_THRESHOLD = 200   # 母线数超过该值时后台计算, 避免 UI 冻结

    def _run_power_flow(self) -> tuple:
        if not self._begin_compute("潮流计算"):
            return False, "已有计算在进行中"
        algorithm = "dc" if self.act_dc.isChecked() else "nr"
        if self._compute_should_async():
            self._pf_thread = PowerFlowThread(
                self.network, algorithm,
                distributed_slack=self.act_dslack.isChecked(), parent=self)
            self._pf_thread.done.connect(self._on_pf_done)
            self._pf_thread.start()
            self.status.showMessage("⏳ 正在后台计算潮流...", 0)
            return True, ""
        t0 = time.perf_counter()
        try:
            ok, err = run_power_flow(
                self.network, algorithm=algorithm,
                distributed_slack=self.act_dslack.isChecked())
        except Exception as e:
            ok, err = False, f"{type(e).__name__}: {e}"
        finally:
            self._end_compute()
        return self._finish_power_flow(ok, err,
                                       (time.perf_counter() - t0) * 1000.0)

    def _on_pf_done(self, net_copy, ok, err, elapsed_ms):
        """后台线程完成: 把副本上的结果字典搬回活动网络"""
        self._pf_thread = None
        try:
            if ok:
                if not self._async_result_usable():
                    return
                self._copy_results_into_network(net_copy)
                self._show_warnings()
            self._finish_power_flow(ok, err, elapsed_ms)
        finally:
            self._end_compute()

    def _finish_power_flow(self, ok, err, elapsed_ms):
        algorithm = "dc" if self.act_dc.isChecked() else "nr"
        if not ok:
            if self.isVisible():
                QMessageBox.warning(self, "潮流计算失败", err)
            self.status.showMessage(f"❌ {err}", 5000)
            return False, err
        self.network.converged = True
        self._refresh_compute_views()
        if not self._results_ever_shown                 and os.environ.get("POWERFLOW_NO_AUTOSHOW") != "1":
            # offscreen 测试环境下 pyqtgraph 实际绘屏会触发原生崩溃,
            # 测试通过环境变量关掉自动弹出(图表逻辑仍有用例覆盖)
            self.results_dock.show()
            self._results_ever_shown = True
        mode = "DC" if algorithm == "dc" else "AC"
        self.mode_label.setText(mode)
        n_bus = len(self.network.buses)
        n_line = len(self.network.lines) + len(self.network.trafos) + len(self.network.impedances)
        self.status.showMessage(
            f"✅ 收敛 [{mode}] — 母线 {n_bus}, 支路 {n_line}, 耗时 {elapsed_ms:.0f} ms",
            5000,
        )
        return True, ""

    def _has_results(self) -> bool:
        """是否有可导出的结果: 潮流 / OPF / 短路 任一种算过即可。

        以前闸门只看 ``bus_voltage_pu``, 于是"只跑了短路(结果只填
        ``bus_ikss_ka``)"的用户明明有结果却被拦住导不出来。
        """
        n = self.network
        return bool(n.converged or n.bus_voltage_pu or n.bus_ikss_ka
                    or n.line_p_from_mw or n.gen_p_mw)

    def _export_csv(self) -> bool:
        if not self._has_results():
            self.status.showMessage("请先运行潮流/短路, 再导出结果", 5000)
            return False
        path, _ = QFileDialog.getSaveFileName(
            self, "导出结果 CSV", "results.csv", "CSV (*.csv)"
        )
        if not path:
            return False
        try:
            paths = export_results_csv(self.network, path)
        except OSError as e:
            # 只读目录 / 磁盘满 / 文件被 Excel 占用 —— 以前直接抛到事件
            # 循环外层, 用户看到的是"点了没反应"或整体崩溃
            QMessageBox.warning(self, "导出失败",
                                f"无法写入文件:\n{e}\n\n"
                                "请确认目标目录可写、文件未被其他程序占用。")
            self.status.showMessage(f"❌ 导出失败: {e}", 6000)
            return False
        except Exception as e:
            logging.getLogger("powerflow.crash").exception("导出 CSV 失败")
            QMessageBox.warning(self, "导出失败", f"{type(e).__name__}: {e}")
            return False
        self.status.showMessage(
            f"已导出: {paths['bus']} / {paths['branch']} / {paths['genload']}", 6000)
        return True

    def _new_file(self) -> None:
        """新建: 走未保存确认, 清空画布并解除文件关联"""
        if not self.confirm_discard_changes():
            return
        self._clear_canvas(skip_confirm=True)
        self._current_path = None
        self._set_dirty(False)
        self.undo_stack.clear()
        self.status.showMessage("已新建空白画布", 3000)

    # ---------- 最近文件 ----------
    RECENT_KEY = "recent_files"
    RECENT_MAX = 8

    def _recent_settings(self):
        from PyQt5.QtCore import QSettings
        return QSettings("PowerFlowStudio", "PowerFlowStudio")

    def _last_dir(self) -> str:
        return self._recent_settings().value("last_dir", "") or ""

    def _set_last_dir(self, path: str):
        d = os.path.dirname(path)
        if d:
            self._recent_settings().setValue("last_dir", d)

    def _remember_recent(self, path: str):
        s = self._recent_settings()
        files = s.value(self.RECENT_KEY, []) or []
        if isinstance(files, str):
            files = [files]
        files = [f for f in files if f != path]
        files.insert(0, path)
        s.setValue(self.RECENT_KEY, files[:self.RECENT_MAX])
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self):
        self.recent_menu.clear()
        files = self._recent_settings().value(self.RECENT_KEY, []) or []
        if isinstance(files, str):
            files = [files]
        if not files:
            act = self.recent_menu.addAction("(空)")
            act.setEnabled(False)
            return
        for f in files:
            act = self.recent_menu.addAction(f)
            act.triggered.connect(
                lambda checked=False, p=f: self._load_topology(p)
                if os.path.exists(p)
                else self.status.showMessage(f"文件不存在: {p}", 4000))

    # ---------- 关闭确认 ----------
    def confirm_discard_changes(self) -> bool:
        """有未保存改动时询问; 返回 False 表示用户想留下"""
        if not self._dirty:
            return True
        r = QMessageBox.question(
            self, "未保存的改动",
            "当前画布有未保存的改动, 保存后再退出吗?",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
        )
        if r == QMessageBox.Yes:
            return self._save_topology() is not None
        return r == QMessageBox.No

    def closeEvent(self, event):
        if not self.confirm_discard_changes():
            event.ignore()
            return
        # 断开场景信号: 场景的 C++ 对象比窗口先销毁时, 仍挂在
        # selectionChanged 上的回调会去访问已释放对象并抛
        # RuntimeError(实测: app.py _on_selection_changed)。
        try:
            self.scene.selectionChanged.disconnect(self._on_selection_changed)
        except (TypeError, RuntimeError):
            pass
        # 后台计算线程还在跑就先等它收尾, 避免退出时被强杀
        for th in (self._pf_thread, self._compute_thread):
            if th is not None and th.isRunning():
                th.wait(3000)
        self._pf_thread = None
        self._compute_thread = None
        s = self._recent_settings()
        s.setValue("window_geometry", self.saveGeometry())
        s.setValue("splitter_sizes", self.centralWidget().sizes())
        s.setValue("results_visible", bool(self.results_dock.isVisible()))
        event.accept()

    def _clear_canvas(self, skip_confirm=False) -> bool:
        """清空画布。返回 False 表示用户在确认框里选了取消。

        调用方**必须**检查返回值: 内部示例加载一旦忽略它, 用户点"否"
        之后示例元件会叠加到旧画布上, 新旧连线错乱。
        """
        has_any = (self.network.buses or self.network.gens or self.network.loads
                   or self.network.lines or self.network.trafos
                   or self.network.impedances or self.network.shunts)
        if has_any and not skip_confirm:
            # In headless tests, default to yes to avoid the dialog blocking.
            if self.isVisible():
                r = QMessageBox.question(
                    self, "清空画布", "确认清空当前所有元件?",
                    QMessageBox.Yes | QMessageBox.No
                )
                if r != QMessageBox.Yes:
                    return False
        self.network.buses.clear()
        self.network.gens.clear()
        self.network.loads.clear()
        self.network.lines.clear()
        self.network.trafos.clear()
        self.network.impedances.clear()
        self.network.shunts.clear()
        # 结果字典必须一起清空: 它们从不参与上面的清空, 而导出闸门/母线
        # 着色看的就是这些字典 —— 只置 converged=False 的话, 新建/载入
        # 之后导出得到的是**上一个拓扑**的潮流结果(贴着新文件名)。
        _clear_results(self.network)
        self._results_ever_shown = False
        # 剪贴板里的 uid 已全部失效, 留着会被 Ctrl+V 粘成死引用
        if hasattr(self.scene, "_clipboard"):
            self.scene._clipboard = None
        # 只移除顶层项: scene.items() 含端口/标签等子项, 父项移除时
        # 子项的 C++ 对象已被一并销毁, 再对子项 removeItem 会访问已释放内存
        for it in list(self.scene.items()):
            if it.parentItem() is None:
                self.scene.removeItem(it)
        self.scene._comp_by_uid.clear()
        self.scene._connections.clear()
        self.properties.clear()
        self._refresh_stats()
        self.status.showMessage("画布已清空", 2000)
        return True

    def _save_topology_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "另存为", "topology.json", "JSON (*.json)"
        )
        if path:
            self._save_topology(path)

    def _save_topology(self, path: str | None = None):
        if path is None:
            path = self._current_path
        if path is None:
            start = os.path.join(self._last_dir(), "topology.json")                 if self._last_dir() else "topology.json"
            path, _ = QFileDialog.getSaveFileName(
                self, "保存拓扑", start, "JSON (*.json)"
            )
            if not path:
                return None
        data = network_to_json_dict(self.network)
        # 原子写: 先写临时文件再替换, 中途崩溃不会毁掉旧文件。
        # 整体包异常: 只读目录 / 磁盘满 / 网络盘掉线时以前会一路冒泡到
        # closeEvent, 绕开 excepthook, 用户看到的就是"关不掉窗口"。
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except OSError as e:
            try:
                os.remove(tmp_path)      # 别留下半截临时文件
            except OSError:
                pass
            logging.getLogger("powerflow.crash").exception("保存拓扑失败")
            QMessageBox.warning(
                self, "保存失败",
                f"无法写入 {path}:\n{e}\n\n请确认目录可写、磁盘空间充足。")
            self.status.showMessage(f"❌ 保存失败: {e}", 6000)
            return None
        except Exception as e:
            logging.getLogger("powerflow.crash").exception("保存拓扑失败")
            QMessageBox.warning(self, "保存失败", f"{type(e).__name__}: {e}")
            return None
        self._current_path = path
        self._set_dirty(False)
        self._remember_recent(path)
        self._set_last_dir(path)
        self.status.showMessage(f"已保存到 {path}", 4000)
        return path

    def _load_topology(self, path: str | None = None) -> bool:
        if path is None:
            path, _ = QFileDialog.getOpenFileName(
                self, "载入拓扑", self._last_dir(), "JSON (*.json)"
            )
            if not path:
                return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.warning(self, "载入失败", f"无法读取文件: {e}")
            return False
        # 先解析校验, 通过了才动当前画布(坏文件不能毁掉已画的内容)
        try:
            net = parse_topology_json(data)
        except Exception as e:
            QMessageBox.warning(self, "载入失败", f"拓扑数据无效: {e}")
            return False
        self._apply_network(net)
        self._current_path = path
        self._set_dirty(False)
        self.undo_stack.clear()   # 载入后旧撤销历史失效
        self._remember_recent(path)
        self._set_last_dir(path)
        self.status.showMessage(f"已载入 {path}", 4000)
        return True

    def _apply_network(self, net: Network) -> bool:
        """用解析好的 Network 替换当前网络并重建画布。

        内部批量替换(载入/撤销恢复)不弹清空确认 —— 入口本身已经过用户确认。
        整体包了异常处理: "解析成功"不等于"重建一定成功"(悬空引用、异常
        坐标); 一旦重建抛错就回滚到替换前的画布。以前 _apply_network 在
        try 之外, 结果是"已清空 + 重建抛 KeyError"的中间态直接崩给用户,
        app.py 注释里"坏文件不能毁掉已画的内容"的承诺并未生效。

        返回 True 表示替换成功。
        """
        backup = network_to_json_dict(self.network)
        self.scene._suspend_writeback = True     # 重建期间不回写坐标(防漂移)
        try:
            self._clear_canvas(skip_confirm=True)
            self.network.buses.update(net.buses)
            self.network.gens.update(net.gens)
            self.network.loads.update(net.loads)
            self.network.lines.update(net.lines)
            self.network.trafos.update(net.trafos)
            self.network.impedances.update(net.impedances)
            self.network.shunts.update(net.shunts)
            self._rebuild_scene_from_network()
        except Exception as e:
            logging.getLogger("powerflow.crash").exception("画布重建失败")
            self._rollback_to(backup)
            if self.isVisible():
                QMessageBox.warning(
                    self, "载入失败",
                    f"拓扑无法重建, 已回滚到之前的画布:\n{e}")
            return False
        finally:
            self.scene._suspend_writeback = False
        self._refresh_stats()
        if self._rebuild_skipped:
            shown = ", ".join(self._rebuild_skipped[:6])
            more = "…" if len(self._rebuild_skipped) > 6 else ""
            self.status.showMessage(
                f"⚠ 有 {len(self._rebuild_skipped)} 个元件引用的母线不存在, "
                f"已跳过显示: {shown}{more}", 8000)
        return True

    def _rollback_to(self, backup: dict) -> None:
        """把画布回滚到备份快照(重建路径已尽力不再抛异常)"""
        try:
            net = parse_topology_json(json.loads(json.dumps(backup)))
        except Exception:
            logging.getLogger("powerflow.crash").exception("回滚快照解析失败")
            net = Network()
        self.scene._suspend_writeback = True
        try:
            self._clear_canvas(skip_confirm=True)
            for key in ("buses", "gens", "loads", "lines", "trafos",
                        "impedances", "shunts"):
                getattr(self.network, key).update(getattr(net, key))
            try:
                self._rebuild_scene_from_network()
            except Exception:
                logging.getLogger("powerflow.crash").exception("回滚重建失败")
        finally:
            self.scene._suspend_writeback = False
        self._refresh_stats()

    def _bus_of(self, uid: str, owner: str):
        """按 uid 取母线; 缺失时记一条告警并返回 None。

        画布重建以前直接 self.network.buses[uid], 悬空引用就是 KeyError
        崩溃(P0-1)。改成"跳过该元件 + 汇总告警", 坏存档最多少显示几个
        元件, 不会把整个载入流程炸掉。
        """
        bus = self.network.buses.get(uid)
        if bus is None:
            self._rebuild_skipped.append(owner)
        return bus

    def _rebuild_scene_from_network(self):
        from canvas import (BusItem, GenItem, LoadItem, TrafoItem,
                            ImpedanceItem, ShuntItem, ConnectionItem)
        self._rebuild_skipped = []
        # Components
        for uid, b in self.network.buses.items():
            it = BusItem(b)
            it.setPos(b.x, b.y)
            self.scene.addItem(it)
            self.scene._comp_by_uid[uid] = it
        for uid, g in self.network.gens.items():
            bus = self._bus_of(g.bus_uid, f"发电机 {g.name}")
            if bus is None:
                continue
            it = GenItem(g)
            # 优先用保存的画布坐标; 老文件(坐标为 0,0)退回母线旁固定偏移
            if g.x or g.y:
                it.setPos(g.x, g.y)
            else:
                it.setPos(bus.x + 20, bus.y - 80)
            self.scene.addItem(it)
            self.scene._comp_by_uid[uid] = it
        for uid, l in self.network.loads.items():
            bus = self._bus_of(l.bus_uid, f"负荷 {l.name}")
            if bus is None:
                continue
            it = LoadItem(l)
            if l.x or l.y:
                it.setPos(l.x, l.y)
            else:
                it.setPos(bus.x + 80, bus.y - 80)
            self.scene.addItem(it)
            self.scene._comp_by_uid[uid] = it
        for uid, sh in self.network.shunts.items():
            bus = self._bus_of(sh.bus_uid, f"电容/电抗 {sh.name}")
            if bus is None:
                continue
            it = ShuntItem(sh)
            if sh.x or sh.y:
                it.setPos(sh.x, sh.y)
            else:
                it.setPos(bus.x + 140, bus.y - 80)
            self.scene.addItem(it)
            self.scene._comp_by_uid[uid] = it
        for uid, t in self.network.trafos.items():
            a = self._bus_of(t.hv_bus, f"变压器 {t.name}")
            b = self._bus_of(t.lv_bus, f"变压器 {t.name}")
            if a is None or b is None:
                continue
            it = TrafoItem(t)
            # 优先用保存的画布坐标; 老文件(0,0)退回两母线中点
            if t.x or t.y:
                it.setPos(t.x, t.y)
            else:
                it.setPos((a.x + b.x) / 2 - 40, (a.y + b.y) / 2 - 25)
            self.scene.addItem(it)
            self.scene._comp_by_uid[uid] = it
        for uid, im in self.network.impedances.items():
            a = self._bus_of(im.from_bus, f"阻抗 {im.name}")
            b = self._bus_of(im.to_bus, f"阻抗 {im.name}")
            if a is None or b is None:
                continue
            it = ImpedanceItem(im)
            if im.x or im.y:
                it.setPos(im.x, im.y)
            else:
                it.setPos((a.x + b.x) / 2 - 40, (a.y + b.y) / 2 - 25)
            self.scene.addItem(it)
            self.scene._comp_by_uid[uid] = it
        # 变压器/阻抗与母线之间的连线(之前载入后悬空漂浮, 看不出接在哪儿)
        for uid, t in self.network.trafos.items():
            comp = self.scene._comp_by_uid.get(uid)
            for bus_uid in (t.hv_bus, t.lv_bus):
                bus_item = self.scene._comp_by_uid.get(bus_uid)
                if comp is not None and bus_item is not None:
                    self._reconnect_bus_to_linecomp(bus_item, comp)
        for uid, im in self.network.impedances.items():
            comp = self.scene._comp_by_uid.get(uid)
            for bus_uid in (im.from_bus, im.to_bus):
                bus_item = self.scene._comp_by_uid.get(bus_uid)
                if comp is not None and bus_item is not None:
                    self._reconnect_bus_to_linecomp(bus_item, comp)
        # Lines between buses
        for uid, ln in self.network.lines.items():
            a = self.scene._comp_by_uid.get(ln.from_bus)
            b = self.scene._comp_by_uid.get(ln.to_bus)
            if a is None or b is None:
                continue
            pa = a.port_item("right")
            pb = b.port_item("left")
            if pa is None or pb is None:
                continue
            conn = ConnectionItem(a, pa, b, pb)
            conn.kind = "Line"
            conn.uid = uid
            a.register_connection(conn)
            b.register_connection(conn)
            self.scene.addItem(conn)
            self.scene._connections.append(conn)

    def _reconnect_bus_to_linecomp(self, bus_item, comp_item):
        """重建一条 母线↔变压器/阻抗 的可视化连线(不改动拓扑字段)"""
        from canvas import TrafoItem
        bus_left = bus_item.scenePos().x() <= comp_item.scenePos().x()
        port_bus = bus_item.port_item("right" if bus_left else "left")
        port_comp = comp_item.port_item("p1" if bus_left else "p2")
        if port_bus is None or port_comp is None:
            return
        conn = ConnectionItem(bus_item, port_bus, comp_item, port_comp)
        conn.kind = "Trafo" if isinstance(comp_item, TrafoItem) else "Impedance"
        conn.uid = comp_item.model.uid
        bus_item.register_connection(conn)
        comp_item.register_connection(conn)
        self.scene.addItem(conn)
        self.scene._connections.append(conn)

    def _load_ieee_case(self, name: str) -> None:
        """一键加载 IEEE 标准算例 (pandapower 自带) 并自动跑潮流"""
        from ieee_cases import load_case
        # 加载算例会整体替换画布 —— 必须与"新建"走同一套未保存确认,
        # 否则用户辛苦搭好的未保存拓扑会被无声覆盖。
        if not self.confirm_discard_changes():
            return
        try:
            net = load_case(name)
        except Exception as e:
            QMessageBox.warning(self, "加载算例失败", f"{name}: {e}")
            return
        self._tracking_suspended = True
        try:
            ok = self._apply_network(net)
        finally:
            self._tracking_suspended = False
        if not ok:
            return
        self.undo_stack.clear()
        self._set_dirty(True)
        self._run_power_flow()
        self.status.showMessage(
            f"已加载 {name}: {len(net.buses)} 母线 / "
            f"{len(net.lines)} 线路 / {len(net.trafos)} 变压器", 5000)

    def _load_demo(self):
        """Load a 3-bus demo: B1(gen)--B2(load1)--B3(load2)."""
        if not self.confirm_discard_changes():
            return
        self._tracking_suspended = True
        try:
            self._load_demo_impl()
        finally:
            self._tracking_suspended = False
        self.undo_stack.clear()
        self._set_dirty(True)

    def _load_demo_impl(self):
        # skip_confirm: 是否覆盖旧画布已由外层 _load_demo 的
        # confirm_discard_changes 统一决定。这里再弹一次确认框的话,
        # 用户点"否"之后本函数仍会继续 add_component, 示例元件就叠到
        # 旧画布上了(新旧元件混在一起、连线错乱)。
        if not self._clear_canvas(skip_confirm=True):
            return
        # 3 buses
        self.scene.add_component("Bus", 200, 300, "B1")
        self.scene.add_component("Bus", 500, 200, "B2")
        self.scene.add_component("Bus", 500, 450, "B3")
        # Generator + loads
        self.scene.add_component("Gen", 250, 200, "G1")
        self.scene.add_component("Load", 600, 150, "L1")
        self.scene.add_component("Load", 600, 400, "L2")
        # Lines: B1-B2, B1-B3
        b1 = next(it for uid, it in self.scene._comp_by_uid.items()
                  if self.network.buses[uid].name == "B1")
        b2 = next(it for uid, it in self.scene._comp_by_uid.items()
                  if self.network.buses[uid].name == "B2")
        b3 = next(it for uid, it in self.scene._comp_by_uid.items()
                  if self.network.buses[uid].name == "B3")
        for a, b in [(b1, b2), (b1, b3)]:
            pa = a.port_item("right")
            pb = b.port_item("left")
            self.scene.create_connection(a, pa, b, pb)
        # Auto-run power flow once
        self._run_power_flow()
        self.status.showMessage("已加载 3 母线示例 — 可点 ▶ 重新运行", 4000)

    def _load_two_end_demo(self):
        """Load a 5-bus two-end supply network:

        G1 (slack, 1.05pu, P设定50)          G2 (PV, 1.05pu, P设定40)
            B1 --- B2 --- B3 --- B4 --- B5
                    |              |
                  Load1          Load2
                  30+j10          20+j8 (MVA)
        4 段线路均为默认参数: 10 km, r=0.4 Ω/km, x=0.4 Ω/km (约 4+j4 Ω/段)
        """
        if not self.confirm_discard_changes():
            return
        self._tracking_suspended = True
        try:
            self._load_two_end_demo_impl()
        finally:
            self._tracking_suspended = False
        self.undo_stack.clear()
        self._set_dirty(True)

    def _load_two_end_demo_impl(self):
        # skip_confirm: 覆盖确认由外层 _load_two_end_demo 统一处理(理由同 3 母线示例)
        if not self._clear_canvas(skip_confirm=True):
            return
        # 5 buses in a horizontal line
        self.scene.add_component("Bus", 150, 300, "B1")
        self.scene.add_component("Bus", 350, 300, "B2")
        self.scene.add_component("Bus", 550, 300, "B3")
        self.scene.add_component("Bus", 750, 300, "B4")
        self.scene.add_component("Bus", 950, 300, "B5")
        # Generators at the two ends (G1 slack 1.05pu, G2 PV 1.05pu)
        g1 = self.scene.add_component("Gen", 200, 150, "G1")
        g1.model.vm_pu = 1.05
        g2 = self.scene.add_component("Gen", 900, 150, "G2")
        g2.model.p_mw = 40
        g2.model.vm_pu = 1.05
        # Loads at B2 and B4: 30+j10 / 20+j8 (与 docstring 描述一致)
        l1 = self.scene.add_component("Load", 400, 450, "L1")
        l1.model.p_mw, l1.model.q_mvar = 30.0, 10.0
        l2 = self.scene.add_component("Load", 700, 450, "L2")
        l2.model.p_mw, l2.model.q_mvar = 20.0, 8.0
        # Lines: 4 segments B1-B2, B2-B3, B3-B4, B4-B5
        b = {}
        for uid, it in self.scene._comp_by_uid.items():
            if uid in self.network.buses:
                b[self.network.buses[uid].name] = it
        for a, b_ in [("B1", "B2"), ("B2", "B3"), ("B3", "B4"), ("B4", "B5")]:
            pa = b[a].port_item("right")
            pb = b[b_].port_item("left")
            self.scene.create_connection(b[a], pa, b[b_], pb)
        # Auto-run
        self._run_power_flow()
        self.status.showMessage(
            "已加载两端供电示例: G1-B1-B2-B3-B4-B5-G2 (B2/B4 带负荷) — 可点 ▶ 重跑", 4000)


def main():
    # 全局异常钩子: GUI 事件里的崩溃写日志(系统临时目录)而不是无声消失
    import traceback
    crash_log = setup_crash_logger()

    def _excepthook(exc_type, exc_value, exc_tb):
        crash_log.critical(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_tb))
        # Also print to stderr so the console window shows it.
        # pythonw 启动时 sys.stderr 为 None, 直接写会二次崩
        if sys.stderr is not None:
            sys.stderr.write("UNHANDLED EXCEPTION (also logged):\n")
            traceback.print_exception(exc_type, exc_value, exc_tb)
    sys.excepthook = _excepthook

    # 高分屏支持必须在 QApplication 实例化**之前**打开, 否则在 4K 屏上
    # 界面只有邮票大小(Windows 上 Qt5 默认不启用 DPI 缩放)。
    theme.enable_high_dpi()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")     # MainWindow 里再套一层统一主题(palette+样式表)
    w = MainWindow()
    w.show()
    # 命令行带拓扑文件路径则直接打开: python app.py my_grid.json
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        w._load_topology(sys.argv[1])
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
