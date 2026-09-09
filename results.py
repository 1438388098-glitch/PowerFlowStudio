"""
results.py — 潮流结果展示与导出

从 app.py 拆出: 结果总览 dock(母线表/支路表/电压图/相角图)、CSV 导出、
画布 PNG 渲染。app.py 保留同名的再导出(re-export), 对外接口不变。
"""
from __future__ import annotations

import csv

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QTabWidget, QTableWidget, QTableWidgetItem,
    QHeaderView
)

from solver import Network


def _fmt(v, nd=3):
    return f"{v:.{nd}f}" if isinstance(v, (int, float)) else "—"


class ResultsPanel(QWidget):
    """结果总览: 母线表 + 支路表 + 电压图 + 相角图, 运行潮流后填充。
    点击表格行会发出 row_activated, 由主窗口联动画布选中。"""
    row_activated = pyqtSignal(str, str)   # (kind: bus/branch, uid)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bus_row_uids = []
        self._branch_row_uids = []
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
        self.bus_table.cellClicked.connect(self._on_bus_row)
        self.branch_table.cellClicked.connect(self._on_branch_row)
        # 图表页 (pyqtgraph 可选依赖, 没装则隐藏)
        self._pg = None
        try:
            import pyqtgraph as pg
            self._pg = pg
            self.plot = pg.PlotWidget()
            self.plot.setBackground("w")
            self.plot.showGrid(y=True, alpha=0.3)
            self.tabs.addTab(self.plot, "电压图")
            self.plot_va = pg.PlotWidget()
            self.plot_va.setBackground("w")
            self.plot_va.showGrid(y=True, alpha=0.3)
            self.tabs.addTab(self.plot_va, "相角图")
            self._has_pg = True
        except Exception:
            self._has_pg = False

    # ---- 交互 ----
    def _on_bus_row(self, row, _col):
        if 0 <= row < len(self._bus_row_uids):
            self.row_activated.emit("bus", self._bus_row_uids[row])

    def _on_branch_row(self, row, _col):
        if 0 <= row < len(self._branch_row_uids):
            self.row_activated.emit("branch", self._branch_row_uids[row])

    # ---- 单元格着色 ----
    def _v_cell_color(self, v):
        from canvas import voltage_color
        if not isinstance(v, (int, float)) or v != v:
            return None
        return voltage_color(v)

    def _loading_cell_color(self, loading):
        from canvas import loading_color
        if not isinstance(loading, (int, float)) or loading != loading:
            return None
        return loading_color(loading)

    # ---- 数据填充 ----
    def refresh(self, net: Network):
        rows = [(b.name,
                 net.bus_voltage_pu.get(uid),
                 net.bus_voltage_kv.get(uid),
                 net.bus_va_degree.get(uid))
                for uid, b in net.buses.items()]
        rows.sort(key=lambda r: r[0])
        if self._has_pg:
            self._refresh_plot(rows)
            self._refresh_va_plot(rows)
        self._fill_bus_table(net, rows)
        self._fill_branch_table(net)

    def _fill_bus_table(self, net: Network, rows):
        self.bus_table.clear()
        self.bus_table.setColumnCount(4)
        self.bus_table.setHorizontalHeaderLabels(["母线", "V (pu)", "V (kV)", "相角 (°)"])
        self.bus_table.setRowCount(len(rows))
        self._bus_row_uids = []
        for i, (name, v, kv, a) in enumerate(rows):
            for j, val in enumerate((name, _fmt(v, 4), _fmt(kv, 2), _fmt(a))):
                item = QTableWidgetItem(str(val))
                if j == 1:
                    bg = self._v_cell_color(v)
                    if bg is not None:
                        item.setBackground(bg)
                self.bus_table.setItem(i, j, item)
            uid = next((u for u, b in net.buses.items() if b.name == name), None)
            self._bus_row_uids.append(uid or "")

    def _fill_branch_table(self, net: Network):
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
        self._branch_row_uids = []
        for i, (kind, name, p, q, loading) in enumerate(brows):
            vals = (kind, name, _fmt(p, 2), _fmt(q, 2),
                    _fmt(loading, 1) if loading is not None else "—")
            for j, val in enumerate(vals):
                item = QTableWidgetItem(str(val))
                if j == 4:
                    bg = self._loading_cell_color(loading)
                    if bg is not None:
                        item.setBackground(bg)
                self.branch_table.setItem(i, j, item)
            container = {"线路": net.lines, "变压器": net.trafos, "阻抗": net.impedances}[kind]
            uid = next((u for u, b in container.items() if b.name == name), None)
            self._branch_row_uids.append(uid or "")

    # ---- 图表 ----
    def _bar_plot(self, plot, names, vals, limits=()):
        """清空并重画一张柱状图; limits 为参考虚线 y 值"""
        pg = self._pg
        plot.clear()
        for y in limits:
            plot.addLine(y=y, pen=pg.mkPen("#c04040", style=Qt.DashLine))
        if not vals:
            return
        bar = pg.BarGraphItem(x=list(range(len(vals))), height=vals,
                              width=0.6, brush="#3c78c8")
        plot.addItem(bar)
        plot.getAxis("bottom").setTicks([list(enumerate(names))])
        lo = min([0.0] + vals)
        hi = max(vals)
        pad = max(0.1, (hi - lo) * 0.1)
        plot.setYRange(lo - pad, hi + pad)

    def _refresh_plot(self, rows):
        vals = [r[1] for r in rows
                if isinstance(r[1], (int, float)) and r[1] == r[1]]  # 剔除 NaN
        names = [r[0] for r in rows
                 if isinstance(r[1], (int, float)) and r[1] == r[1]]
        self._bar_plot(self.plot, names, vals, limits=(0.95, 1.05))

    def _refresh_va_plot(self, rows):
        vals = [r[3] for r in rows
                if isinstance(r[3], (int, float)) and r[3] == r[3]]
        names = [r[0] for r in rows
                 if isinstance(r[3], (int, float)) and r[3] == r[3]]
        self._bar_plot(self.plot_va, names, vals, limits=(0.0,))


def export_results_csv(net: Network, base_path: str) -> dict:
    """把潮流结果导出成 CSV(母线/支路/电源与负荷), 返回 {kind: path}。
    用 utf-8-sig 编码, Excel 直接打开不乱码。"""
    base = base_path[:-4] if base_path.lower().endswith(".csv") else base_path
    bus_csv = base + "_母线.csv"
    branch_csv = base + "_支路.csv"
    genload_csv = base + "_电源与负荷.csv"
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
    with open(genload_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["类型", "名称", "挂接母线", "P设定(MW)", "V设定(pu)", "P实际(MW)", "Q实际(Mvar)"])
        for uid, g in net.gens.items():
            bus = net.buses.get(g.bus_uid)
            w.writerow(["发电机", g.name, bus.name if bus else "?",
                        _fmt(g.p_mw, 2), _fmt(g.vm_pu, 4),
                        _fmt(net.gen_p_mw.get(uid), 2),
                        _fmt(net.gen_q_mvar.get(uid), 2)])
        w.writerow([])
        w.writerow(["类型", "名称", "挂接母线", "P设定(MW)", "Q设定(Mvar)", "P实际(MW)", "Q实际(Mvar)"])
        for uid, ld in net.loads.items():
            bus = net.buses.get(ld.bus_uid)
            w.writerow(["负荷", ld.name, bus.name if bus else "?",
                        _fmt(ld.p_mw, 2), _fmt(ld.q_mvar, 2),
                        _fmt(net.load_p_mw.get(uid), 2),
                        _fmt(net.load_q_mvar.get(uid), 2)])
    return {"bus": bus_csv, "branch": branch_csv, "genload": genload_csv}


def render_scene_png(scene, path: str, scale: float = 2.0) -> bool:
    """把画布渲染成 PNG(白底, 2x 缩放), 供报告/作业插图"""
    from PyQt5.QtCore import QRectF
    from PyQt5.QtGui import QImage, QPainter
    rect = scene.itemsBoundingRect().adjusted(-40, -40, 40, 40)
    img = QImage(max(1, int(rect.width() * scale)),
                 max(1, int(rect.height() * scale)),
                 QImage.Format_ARGB32)
    img.fill(Qt.white)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing)
    scene.render(painter, target=QRectF(img.rect()), source=rect)
    painter.end()
    return img.save(path)
