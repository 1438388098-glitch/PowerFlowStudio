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
    Network, run_power_flow, _RESULT_FIELDS,
    BusNode, GenUnit, LoadUnit, LineBranch, TrafoBranch, ImpedanceBranch,
)
from canvas import CircuitScene, CircuitView, BaseComponent, ConnectionItem
import pandapower as pp
import PyQt5.QtCore

from palette import ComponentPalette
from properties import PropertiesPanel
from undocmds import SnapshotCommand

__version__ = "0.6.0"


class PowerFlowThread(QThread):
    """大网络后台计算: 在副本上跑潮流, 完成后把结果交回 GUI 线程"""
    done = pyqtSignal(object, bool, str, float)   # net_copy, ok, err, elapsed_ms

    def __init__(self, net: Network, algorithm: str, parent=None):
        super().__init__(parent)
        # 深拷贝: 计算期间用户继续编辑不影响输入, 结果也不直接写活网络
        self._net = copy.deepcopy(net)
        self._algorithm = algorithm

    def run(self):
        t0 = time.perf_counter()
        try:
            ok, err = run_power_flow(self._net, algorithm=self._algorithm)
        except Exception as e:   # 后台线程兜底, 不能让异常无声消失
            ok, err = False, f"{type(e).__name__}: {e}"
        self.done.emit(self._net, ok, err, (time.perf_counter() - t0) * 1000.0)


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


# -------------------------------------------------------------
from results import (  # noqa: F401  再导出, 对外接口与拆分前一致
    ResultsPanel, export_results_csv, render_scene_png,
)
# -------------------------------------------------------------
# 拓扑 JSON 解析/校验(独立于 GUI, 方便测试)
# -------------------------------------------------------------
_TOPO_SPEC = [
    ("buses", BusNode), ("gens", GenUnit), ("loads", LoadUnit),
    ("lines", LineBranch), ("trafos", TrafoBranch),
    ("impedances", ImpedanceBranch),
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
    }


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("潮流计算 GUI  ·  pandapower 内核")
        self.resize(1280, 800)

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
        splitter.setSizes([150, 800, 260])
        self.setCentralWidget(splitter)

        # 结果总览 dock (底部, 首次运行潮流时弹出一次, 用户关掉不再打扰)
        self.results_panel = ResultsPanel()
        self.results_dock = QDockWidget("潮流结果总览", self)
        self.results_dock.setWidget(self.results_panel)
        self.results_dock.hide()
        self.addDockWidget(Qt.BottomDockWidgetArea, self.results_dock)
        self._results_ever_shown = False
        self._pf_thread = None
        # 结果表行点击 → 画布选中该元件
        self.results_panel.row_activated.connect(self._on_result_row_activated)

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
        menu_run.addAction("⛏ N-1 校核(逐条开断)", self._run_n_minus_1)
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
        # 上次自动保存时间提示(须在状态栏创建前算好)
        autosave_ts = self._recent_settings().value("autosave_time", "")
        if autosave_ts and os.path.exists(
                os.path.join(tempfile.gettempdir(),
                             "PowerFlowStudio_autosave.json")):
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
            self.restoreGeometry(geometry)
        sizes = s.value("splitter_sizes")
        if sizes:
            try:
                self.centralWidget().setSizes([int(v) for v in sizes])
            except (TypeError, ValueError):
                pass
        if s.value("results_visible", "false") in ("true", True):
            self.results_dock.show()
            self._results_ever_shown = True

        # 编辑快捷键
        QShortcut(QKeySequence.Copy, self, activated=self._copy_selection)
        QShortcut(QKeySequence.Paste, self, activated=self._paste_clipboard)
        # 自动保存(每3分钟, 仅脏画布)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self._autosave)
        self._autosave_timer.start(180_000)

    # ---------- 状态: 标题 / 脏标记 / 撤销 ----------
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
    def _autosave_path(self) -> str:
        return os.path.join(tempfile.gettempdir(), "PowerFlowStudio_autosave.json")

    def _autosave(self):
        if not self._dirty or not self.network.buses:
            return
        try:
            data = network_to_json_dict(self.network)
            with open(self._autosave_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._recent_settings().setValue(
                "autosave_time", time.strftime("%Y-%m-%d %H:%M:%S"))
        except Exception:
            logging.getLogger("powerflow.crash").exception("自动保存失败")

    def _restore_autosave(self) -> bool:
        """从自动保存恢复(不覆盖 current_path)"""
        path = self._autosave_path()
        if not os.path.exists(path):
            self.status.showMessage("没有可恢复的自动保存", 4000)
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                net = parse_topology_json(json.load(f))
        except Exception as e:
            self.status.showMessage(f"自动保存文件损坏: {e}", 5000)
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

    def push_move_undo(self, before: dict, after: dict):
        """拖动结束: 位置变化作为一次撤销入栈 (无变化不入栈)"""
        if self._tracking_suspended or self._in_tracked_op:
            return
        if before == after:
            return
        self._set_dirty(True)
        self.undo_stack.push(SnapshotCommand(self, before, after, "移动元件"))

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

    # ---------- Selection ----------
    def _on_selection_changed(self):
        sel = self.scene.selectedItems()
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
        from canvas import BusItem
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
        """跑 N-1 校核并刷新界面, 失败返回 None"""

        from solver import n_minus_1_check, format_n1_report
        if not self.network.buses or not self.network.gens:
            self.status.showMessage("画布上需要一个可计算的网络(至少母线+发电机)", 5000)
            return None
        algorithm = "dc" if self.act_dc.isChecked() else "nr"
        self.status.showMessage("⏳ N-1 校核计算中...", 0)
        QApplication.processEvents()
        report = n_minus_1_check(self.network, algorithm=algorithm)
        self._refresh_stats()
        if "_base_failed" not in report:
            self.scene.refresh_results()
            self.results_panel.refresh(self.network)
        return format_n1_report(report)

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
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
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
            f"测试: python -m pytest tests -q\n"
            f"异常日志: {log_path}")

    # ---------- Toolbar actions ----------
    ASYNC_PF_THRESHOLD = 200   # 母线数超过该值时后台计算, 避免 UI 冻结

    def _run_power_flow(self) -> tuple:
        algorithm = "dc" if self.act_dc.isChecked() else "nr"
        if (len(self.network.buses) > self.ASYNC_PF_THRESHOLD
                and self._pf_thread is None):
            self.status.showMessage("⏳ 正在后台计算潮流...", 0)
            self._pf_thread = PowerFlowThread(self.network, algorithm, self)
            self._pf_thread.done.connect(self._on_pf_done)
            self._pf_thread.start()
            return True, ""
        t0 = time.perf_counter()
        ok, err = run_power_flow(self.network, algorithm=algorithm)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return self._finish_power_flow(ok, err, elapsed_ms)

    def _on_pf_done(self, net_copy, ok, err, elapsed_ms):
        """后台线程完成: 把副本上的结果字典搬回活动网络"""
        self._pf_thread = None
        for f in _RESULT_FIELDS:
            setattr(self.network, f, dict(getattr(net_copy, f)))
        self._finish_power_flow(ok, err, elapsed_ms)

    def _finish_power_flow(self, ok, err, elapsed_ms):
        algorithm = "dc" if self.act_dc.isChecked() else "nr"
        if not ok:
            if self.isVisible():
                QMessageBox.warning(self, "潮流计算失败", err)
            self.status.showMessage(f"❌ {err}", 5000)
            return False, err
        self.network.converged = True
        self.scene.refresh_results()
        self._refresh_stats()
        if self.properties.current_item is not None:
            self.properties.refresh_results()
        self.results_panel.refresh(self.network)
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

    def _export_csv(self) -> bool:
        if not self.network.bus_voltage_pu:
            self.status.showMessage("请先运行潮流, 再导出结果", 5000)
            return False
        path, _ = QFileDialog.getSaveFileName(
            self, "导出结果 CSV", "results.csv", "CSV (*.csv)"
        )
        if not path:
            return False
        paths = export_results_csv(self.network, path)
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
        s = self._recent_settings()
        s.setValue("window_geometry", self.saveGeometry())
        s.setValue("splitter_sizes", self.centralWidget().sizes())
        s.setValue("results_visible", bool(self.results_dock.isVisible()))
        event.accept()

    def _clear_canvas(self, skip_confirm=False):
        has_any = (self.network.buses or self.network.gens or self.network.loads
                   or self.network.lines or self.network.trafos or self.network.impedances)
        if has_any and not skip_confirm:
            # In headless tests, default to yes to avoid the dialog blocking.
            if self.isVisible():
                r = QMessageBox.question(
                    self, "清空画布", "确认清空当前所有元件?",
                    QMessageBox.Yes | QMessageBox.No
                )
                if r != QMessageBox.Yes:
                    return
        self.network.buses.clear()
        self.network.gens.clear()
        self.network.loads.clear()
        self.network.lines.clear()
        self.network.trafos.clear()
        self.network.impedances.clear()
        self._results_ever_shown = False
        if hasattr(self.scene, "_internal_links"):
            self.scene._internal_links.clear()
        for it in list(self.scene.items()):
            self.scene.removeItem(it)
        self.scene._comp_by_uid.clear()
        self.scene._connections.clear()
        self.properties.clear()
        self._refresh_stats()
        self.status.showMessage("画布已清空", 2000)

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
        # 原子写: 先写临时文件再替换, 中途崩溃不会毁掉旧文件
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
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

    def _apply_network(self, net: Network) -> None:
        """用解析好的 Network 替换当前网络并重建画布"""
        self._clear_canvas()
        self.network.buses.update(net.buses)
        self.network.gens.update(net.gens)
        self.network.loads.update(net.loads)
        self.network.lines.update(net.lines)
        self.network.trafos.update(net.trafos)
        self.network.impedances.update(net.impedances)
        self._rebuild_scene_from_network()
        self._refresh_stats()

    def _rebuild_scene_from_network(self):
        from canvas import BusItem, GenItem, LoadItem, TrafoItem, ImpedanceItem, ConnectionItem
        # Components
        for uid, b in self.network.buses.items():
            it = BusItem(b)
            it.setPos(b.x, b.y)
            self.scene.addItem(it)
            self.scene._comp_by_uid[uid] = it
        for uid, g in self.network.gens.items():
            it = GenItem(g)
            bus = self.network.buses[g.bus_uid]
            # 优先用保存的画布坐标; 老文件(坐标为 0,0)退回母线旁固定偏移
            if g.x or g.y:
                it.setPos(g.x, g.y)
            else:
                it.setPos(bus.x + 20, bus.y - 80)
            self.scene.addItem(it)
            self.scene._comp_by_uid[uid] = it
            self.scene._register_internal_link(it, g.bus_uid)
        for uid, l in self.network.loads.items():
            it = LoadItem(l)
            bus = self.network.buses[l.bus_uid]
            if l.x or l.y:
                it.setPos(l.x, l.y)
            else:
                it.setPos(bus.x + 80, bus.y - 80)
            self.scene.addItem(it)
            self.scene._comp_by_uid[uid] = it
            self.scene._register_internal_link(it, l.bus_uid)
        for uid, t in self.network.trafos.items():
            it = TrafoItem(t)
            a = self.network.buses[t.hv_bus]
            b = self.network.buses[t.lv_bus]
            # 优先用保存的画布坐标; 老文件(0,0)退回两母线中点
            if t.x or t.y:
                it.setPos(t.x, t.y)
            else:
                it.setPos((a.x + b.x) / 2 - 40, (a.y + b.y) / 2 - 25)
            self.scene.addItem(it)
            self.scene._comp_by_uid[uid] = it
        for uid, im in self.network.impedances.items():
            it = ImpedanceItem(im)
            a = self.network.buses[im.from_bus]
            b = self.network.buses[im.to_bus]
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
        try:
            net = load_case(name)
        except Exception as e:
            QMessageBox.warning(self, "加载算例失败", f"{name}: {e}")
            return
        self._tracking_suspended = True
        try:
            self._apply_network(net)
        finally:
            self._tracking_suspended = False
        self.undo_stack.clear()
        self._set_dirty(True)
        self._run_power_flow()
        self.status.showMessage(
            f"已加载 {name}: {len(net.buses)} 母线 / "
            f"{len(net.lines)} 线路 / {len(net.trafos)} 变压器", 5000)

    def _load_demo(self):
        """Load a 3-bus demo: B1(gen)--B2(load1)--B3(load2)."""
        self._tracking_suspended = True
        try:
            self._load_demo_impl()
        finally:
            self._tracking_suspended = False
        self.undo_stack.clear()
        self._set_dirty(True)

    def _load_demo_impl(self):
        self._clear_canvas()
        # 3 buses
        self.scene.add_component("Bus", 200, 300, "B1")
        self.scene.add_component("Bus", 500, 200, "B2")
        self.scene.add_component("Bus", 500, 450, "B3")
        # Generator + loads
        self.scene.add_component("Gen", 250, 200, "G1")
        self.scene.add_component("Load", 600, 150, "L1")
        self.scene.add_component("Load", 600, 400, "L2")
        # Lines: B1-B2, B1-B3
        from canvas import ConnectionItem
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
        self._tracking_suspended = True
        try:
            self._load_two_end_demo_impl()
        finally:
            self._tracking_suspended = False
        self.undo_stack.clear()
        self._set_dirty(True)

    def _load_two_end_demo_impl(self):
        self._clear_canvas()
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
        from canvas import ConnectionItem
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
        sys.stderr.write("UNHANDLED EXCEPTION (also logged):\n")
        traceback.print_exception(exc_type, exc_value, exc_tb)
    sys.excepthook = _excepthook

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    w = MainWindow()
    w.show()
    # 命令行带拓扑文件路径则直接打开: python app.py my_grid.json
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        w._load_topology(sys.argv[1])
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
