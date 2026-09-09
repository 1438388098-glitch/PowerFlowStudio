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
import csv
import json
import logging
import os
import sys
import tempfile
import time
from dataclasses import asdict, fields as dc_fields

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QAction, QMessageBox, QSplitter,
    QStatusBar, QShortcut, QToolBar, QFileDialog, QDockWidget,
    QWidget, QVBoxLayout, QTabWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QUndoStack
)

from solver import (
    Network, run_power_flow, _RESULT_FIELDS,
    BusNode, GenUnit, LoadUnit, LineBranch, TrafoBranch, ImpedanceBranch,
)
from canvas import CircuitScene, CircuitView, BaseComponent, ConnectionItem
from palette import ComponentPalette
from properties import PropertiesPanel
from undocmds import SnapshotCommand


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
# 结果总览 (docking panel + CSV)
# -------------------------------------------------------------
def _fmt(v, nd=3):
    return f"{v:.{nd}f}" if isinstance(v, (int, float)) else "—"


class ResultsPanel(QWidget):
    """结果总览: 母线表 + 支路表, 运行潮流后填充"""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        self.tabs = QTabWidget()
        self.bus_table = QTableWidget()
        self.branch_table = QTableWidget()
        for tbl in (self.bus_table, self.branch_table):
            tbl.setEditTriggers(QTableWidget.NoEditTriggers)
            tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            tbl.verticalHeader().setVisible(False)
        self.tabs.addTab(self.bus_table, "母线")
        self.tabs.addTab(self.branch_table, "支路")
        layout.addWidget(self.tabs)
        # 电压柱状图 (pyqtgraph 可选依赖, 没装则隐藏该页)
        try:
            import pyqtgraph as pg
            self._pg = pg
            self.plot = pg.PlotWidget()
            self.plot.setBackground("w")
            self.plot.showGrid(y=True, alpha=0.3)
            self.tabs.addTab(self.plot, "电压图")
            self._has_pg = True
        except Exception:
            self._has_pg = False

    def _refresh_plot(self, rows):
        """rows: [(name, v_pu, ...)] — 画各母线电压柱状图, 0.95/1.05 限值虚线"""
        pg = self._pg
        self.plot.clear()
        self.plot.addLine(y=0.95, pen=pg.mkPen("#c04040", style=Qt.DashLine))
        self.plot.addLine(y=1.05, pen=pg.mkPen("#c04040", style=Qt.DashLine))
        vals = [r[1] for r in rows
                if isinstance(r[1], (int, float)) and r[1] == r[1]]  # 剔除 NaN
        if not vals:
            return
        names = [r[0] for r in rows if isinstance(r[1], (int, float))]
        bar = pg.BarGraphItem(x=list(range(len(vals))), height=vals,
                              width=0.6, brush="#3c78c8")
        self.plot.addItem(bar)
        self.plot.getAxis("bottom").setTicks([list(enumerate(names))])
        self.plot.setYRange(min(0.85, min(vals) - 0.05),
                            max(1.15, max(vals) + 0.05))

    def refresh(self, net: Network):
        # 母线表
        rows = [(b.name,
                 net.bus_voltage_pu.get(uid),
                 net.bus_voltage_kv.get(uid),
                 net.bus_va_degree.get(uid))
                for uid, b in net.buses.items()]
        rows.sort(key=lambda r: r[0])
        if self._has_pg:
            self._refresh_plot(rows)
        self.bus_table.clear()
        self.bus_table.setColumnCount(4)
        self.bus_table.setHorizontalHeaderLabels(["母线", "V (pu)", "V (kV)", "相角 (°)"])
        self.bus_table.setRowCount(len(rows))
        for i, (name, v, kv, a) in enumerate(rows):
            for j, val in enumerate((name, _fmt(v, 4), _fmt(kv, 2), _fmt(a))):
                self.bus_table.setItem(i, j, QTableWidgetItem(str(val)))
        # 支路表
        brows = []
        for uid, ln in net.lines.items():
            brows.append(("线路", ln.name,
                          net.line_p_from_mw.get(uid), net.line_q_from_mvar.get(uid),
                          net.line_loading_percent.get(uid)))
        for uid, tr in net.trafos.items():
            brows.append(("变压器", tr.name,
                          net.trafo_p_hv_mw.get(uid), net.trafo_q_hv_mvar.get(uid),
                          net.trafo_loading_percent.get(uid)))
        for uid, im in net.impedances.items():
            brows.append(("阻抗", im.name,
                          net.impedance_p_from_mw.get(uid),
                          net.impedance_q_from_mvar.get(uid), None))
        brows.sort(key=lambda r: r[1])
        self.branch_table.clear()
        self.branch_table.setColumnCount(5)
        self.branch_table.setHorizontalHeaderLabels(
            ["类型", "名称", "P (MW)", "Q (Mvar)", "负载率 (%)"])
        self.branch_table.setRowCount(len(brows))
        for i, (kind, name, p, q, loading) in enumerate(brows):
            vals = (kind, name, _fmt(p, 2), _fmt(q, 2),
                    _fmt(loading, 1) if loading is not None else "—")
            for j, val in enumerate(vals):
                self.branch_table.setItem(i, j, QTableWidgetItem(str(val)))


def export_results_csv(net: Network, base_path: str):
    """把潮流结果导出成两个 CSV(母线/支路), 返回实际写出的文件路径。
    用 utf-8-sig 编码, Excel 直接打开不乱码。"""
    base = base_path[:-4] if base_path.lower().endswith(".csv") else base_path
    bus_csv, branch_csv = base + "_母线.csv", base + "_支路.csv"
    with open(bus_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["母线", "V(pu)", "V(kV)", "相角(°)"])
        for uid, b in sorted(net.buses.items(), key=lambda kv: kv[1].name):
            w.writerow([b.name,
                        _fmt(net.bus_voltage_pu.get(uid), 4),
                        _fmt(net.bus_voltage_kv.get(uid), 2),
                        _fmt(net.bus_va_degree.get(uid))])
    with open(branch_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["类型", "名称", "P(MW)", "Q(Mvar)", "负载率(%)"])
        for uid, ln in net.lines.items():
            w.writerow(["线路", ln.name,
                        _fmt(net.line_p_from_mw.get(uid), 2),
                        _fmt(net.line_q_from_mvar.get(uid), 2),
                        _fmt(net.line_loading_percent.get(uid), 1)])
        for uid, tr in net.trafos.items():
            w.writerow(["变压器", tr.name,
                        _fmt(net.trafo_p_hv_mw.get(uid), 2),
                        _fmt(net.trafo_q_hv_mvar.get(uid), 2),
                        _fmt(net.trafo_loading_percent.get(uid), 1)])
        for uid, im in net.impedances.items():
            w.writerow(["阻抗", im.name,
                        _fmt(net.impedance_p_from_mw.get(uid), 2),
                        _fmt(net.impedance_q_from_mvar.get(uid), 2), "—"])
    return bus_csv, branch_csv


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

        # Toolbar
        toolbar = QToolBar()
        self.addToolBar(toolbar)
        self.act_run = QAction("▶ 运行潮流", self)
        self.act_run.triggered.connect(self._run_power_flow)
        toolbar.addAction(self.act_run)
        self.act_dc = QAction("DC 直流模式", self)
        self.act_dc.setCheckable(True)
        self.act_dc.setToolTip("勾选后用直流潮流(DC)求解: 只算有功与相角, 速度更快")
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
        act_undo.triggered.connect(self.undo_stack.undo)
        act_undo.setEnabled(False)
        self.undo_stack.canUndoChanged.connect(act_undo.setEnabled)
        toolbar.addAction(act_undo)
        act_redo = QAction("↪ 重做", self)
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
        menu_file.addAction(act_save)
        act_save_as = QAction("另存为(&A)...", self)
        act_save_as.triggered.connect(self._save_topology_as)
        menu_file.addAction(act_save_as)
        menu_file.addAction(act_load)
        menu_file.addAction(act_export)
        menu_file.addSeparator()
        act_quit = QAction("退出(&Q)", self)
        act_quit.triggered.connect(self.close)
        menu_file.addAction(act_quit)
        menu_run = self.menuBar().addMenu("计算(&R)")
        menu_run.addAction(self.act_run)
        menu_run.addAction(self.act_dc)
        menu_run.addSeparator()
        for case_name, case_label in (("case14", "IEEE 14 母线"),
                                      ("case30", "IEEE 30 母线")):
            act_case = QAction(case_label, self)
            act_case.triggered.connect(
                lambda checked=False, cn=case_name: self._load_ieee_case(cn))
            menu_run.addAction(act_case)
        menu_view = self.menuBar().addMenu("视图(&V)")
        menu_view.addAction(act_fit)
        menu_view.addAction(self.results_dock.toggleViewAction())
        menu_help = self.menuBar().addMenu("帮助(&H)")
        act_about = QAction("关于(&A)", self)
        act_about.triggered.connect(
            lambda: QMessageBox.about(
                self, "关于 PowerFlowStudio",
                "潮流计算 GUI · Power Flow Studio\n"
                "PyQt5 画布 + pandapower 牛顿-拉夫逊/直流潮流内核\n"
                "拖拽搭建电网, 一键计算, 电压着色与结果总览。"))
        menu_help.addAction(act_about)

        # Status bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Ready — drag a component from the left into the canvas")

        # Shortcuts
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self._run_power_flow)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self._load_demo)
        QShortcut(QKeySequence("Ctrl+0"), self, activated=self.view.fit_view)
        QShortcut(QKeySequence.Undo, self, activated=self.undo_stack.undo)
        QShortcut(QKeySequence.Redo, self, activated=self.undo_stack.redo)

    # ---------- 状态: 标题 / 脏标记 / 撤销 ----------
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

    # ---------- Toolbar actions ----------
    ASYNC_PF_THRESHOLD = 200   # 母线数超过该值时后台计算, 避免 UI 冻结

    def _run_power_flow(self):
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
        if self.properties.current_item is not None:
            self.properties.refresh_results()
        self.results_panel.refresh(self.network)
        if not self._results_ever_shown:
            self.results_dock.show()
            self._results_ever_shown = True
        mode = "DC" if algorithm == "dc" else "AC"
        n_bus = len(self.network.buses)
        n_line = len(self.network.lines) + len(self.network.trafos) + len(self.network.impedances)
        self.status.showMessage(
            f"✅ 收敛 [{mode}] — 母线 {n_bus}, 支路 {n_line}, 耗时 {elapsed_ms:.0f} ms",
            5000,
        )
        return True, ""

    def _export_csv(self):
        if not self.network.bus_voltage_pu:
            self.status.showMessage("请先运行潮流, 再导出结果", 5000)
            return False
        path, _ = QFileDialog.getSaveFileName(
            self, "导出结果 CSV", "results.csv", "CSV (*.csv)"
        )
        if not path:
            return False
        bus_csv, branch_csv = export_results_csv(self.network, path)
        self.status.showMessage(f"已导出: {bus_csv} 与 {branch_csv}", 6000)
        return True

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
        event.accept()

    def _clear_canvas(self):
        has_any = (self.network.buses or self.network.gens or self.network.loads
                   or self.network.lines or self.network.trafos or self.network.impedances)
        if has_any:
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
        self.status.showMessage("画布已清空", 2000)

    def _save_topology_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "另存为", "topology.json", "JSON (*.json)"
        )
        if path:
            self._save_topology(path)

    def _save_topology(self, path=None):
        if path is None:
            path = self._current_path
        if path is None:
            path, _ = QFileDialog.getSaveFileName(
                self, "保存拓扑", "topology.json", "JSON (*.json)"
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
        self.status.showMessage(f"已保存到 {path}", 4000)
        return path

    def _load_topology(self, path=None):
        if path is None:
            path, _ = QFileDialog.getOpenFileName(
                self, "载入拓扑", "", "JSON (*.json)"
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
        self.status.showMessage(f"已载入 {path}", 4000)
        return True

    def _apply_network(self, net: Network):
        """用解析好的 Network 替换当前网络并重建画布"""
        self._clear_canvas()
        self.network.buses.update(net.buses)
        self.network.gens.update(net.gens)
        self.network.loads.update(net.loads)
        self.network.lines.update(net.lines)
        self.network.trafos.update(net.trafos)
        self.network.impedances.update(net.impedances)
        self._rebuild_scene_from_network()

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
            it.setPos((a.x + b.x) / 2 - 40, (a.y + b.y) / 2 - 25)
            self.scene.addItem(it)
            self.scene._comp_by_uid[uid] = it
        for uid, im in self.network.impedances.items():
            it = ImpedanceItem(im)
            a = self.network.buses[im.from_bus]
            b = self.network.buses[im.to_bus]
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

    def _load_ieee_case(self, name: str):
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

        G1 (slack)                                G2 (PV)
        50+j20 MW   <- L1 ->  <- L2 ->  <- L3 ->  40+j15 MW
        1.05 pu     r=0.04+j0.12 per line, all 100 MVA / 110 kV base
            B1 --- B2 --- B3 --- B4 --- B5
                   |              |
                 Load1          Load2
                30+j10 MW       20+j8 MVAr
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
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
