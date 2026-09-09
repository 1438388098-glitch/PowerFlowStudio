"""
properties.py — 右侧属性编辑面板
选中元件/连线时显示其字段, 修改即时写回 model
"""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QWidget, QFormLayout, QLineEdit, QDoubleSpinBox, QLabel,
    QVBoxLayout, QPushButton, QHBoxLayout, QGroupBox
)


class PropertiesPanel(QWidget):
    """选中元件后, 在这里编辑它的字段"""
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedWidth(260)
        self.current_item = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.title = QLabel("未选中任何元件")
        self.title.setStyleSheet("font-weight:bold; font-size:11pt;")
        layout.addWidget(self.title)

        self.form_host = QWidget()
        self.form_layout = QFormLayout(self.form_host)
        self.form_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.form_host)

        # 结果区
        self.result_box = QGroupBox("潮流结果")
        self.result_layout = QFormLayout(self.result_box)
        layout.addWidget(self.result_box)
        layout.addStretch(1)

        self.delete_btn = QPushButton("删除选中")
        self.delete_btn.clicked.connect(self._on_delete)
        layout.addWidget(self.delete_btn)

        self._fields = {}   # attr_name -> input widget

    def show_component(self, comp_item) -> None:
        self.current_item = comp_item
        # 清空旧字段
        self._clear_form()
        model = comp_item.model
        # 从 item 类型判断 kind, 而不是从 model.KIND (dataclass 没这个字段)
        from canvas import BusItem, GenItem, LoadItem, TrafoItem, ImpedanceItem
        if isinstance(comp_item, BusItem):
            kind = "Bus"
        elif isinstance(comp_item, GenItem):
            kind = "Gen"
        elif isinstance(comp_item, LoadItem):
            kind = "Load"
        elif isinstance(comp_item, TrafoItem):
            kind = "Trafo"
        elif isinstance(comp_item, ImpedanceItem):
            kind = "Impedance"
        else:
            kind = "Line"
        self.title.setText(f"元件: {kind}  {model.name}")

        if kind == "Bus":
            self._add_name_field()
            self._add_float(model, "vn_kv", "额定电压 (kV)", model.vn_kv, 0.1, 1000, 1)
        elif kind == "Gen":
            self._add_name_field()
            self._add_combo_bus(model, "bus_uid", "挂接母线", model.bus_uid)
            self._add_float(model, "p_mw", "有功 P (MW)", model.p_mw, 0, 5000, 1)
            self._add_float(model, "vm_pu", "电压 V (pu)", model.vm_pu, 0.8, 1.2, 4)
        elif kind == "Load":
            self._add_name_field()
            self._add_combo_bus(model, "bus_uid", "挂接母线", model.bus_uid)
            self._add_float(model, "p_mw", "有功 P (MW)", model.p_mw, 0, 5000, 1)
            self._add_float(model, "q_mvar", "无功 Q (Mvar)", model.q_mvar, -1000, 1000, 2)
        elif kind == "Trafo":
            self._add_name_field()
            self._add_combo_bus(model, "hv_bus", "高压母线", model.hv_bus)
            self._add_combo_bus(model, "lv_bus", "低压母线", model.lv_bus)
            self._add_float(model, "sn_mva", "额定容量 (MVA)", model.sn_mva, 0.1, 1000, 1)
            self._add_float(model, "vn_hv_kv", "高压侧额定电压 (kV)", model.vn_hv_kv, 0.1, 1000, 1)
            self._add_float(model, "vn_lv_kv", "低压侧额定电压 (kV)", model.vn_lv_kv, 0.1, 1000, 1)
            self._add_float(model, "vk_percent", "短路电压 (%)", model.vk_percent, 0, 30, 2)
            self._add_float(model, "vkr_percent", "电阻压降 (%)", model.vkr_percent, 0, 30, 3)
            self._add_float(model, "pfe_kw", "铁损 (kW)", model.pfe_kw, 0, 1000, 1)
            self._add_float(model, "i0_percent", "空载电流 (%)", model.i0_percent, 0, 10, 3)
        elif kind == "Impedance":
            self._add_name_field()
            self._add_combo_bus(model, "from_bus", "首端母线", model.from_bus)
            self._add_combo_bus(model, "to_bus", "末端母线", model.to_bus)
            self._add_float(model, "rft_pu", "R (pu)", model.rft_pu, -10, 10, 4)
            self._add_float(model, "xft_pu", "X (pu)", model.xft_pu, -10, 10, 4)
            self._add_float(model, "sn_mva", "基准容量 (MVA)", model.sn_mva, 0.1, 1000, 1)

        self.refresh_results()

    def show_connection(self, conn_item) -> None:
        self.current_item = conn_item
        self._clear_form()
        self.title.setText(f"连线: {conn_item.kind}")
        if conn_item.kind == "Line" and conn_item.uid:
            model = self._scene().network.lines[conn_item.uid]
            self._show_line_form(model)
        self.refresh_results()

    def _scene(self):  # -> CircuitScene | None
        if hasattr(self, "_scene_ref"):
            return self._scene_ref
        return None

    def attach_scene(self, scene) -> None:
        self._scene_ref = scene

    def _show_line_form(self, model) -> None:
        self._add_name_field_line(model)
        self._add_combo_bus(model, "from_bus", "首端母线", model.from_bus)
        self._add_combo_bus(model, "to_bus", "末端母线", model.to_bus)
        self._add_float(model, "length_km", "长度 (km)", model.length_km, 0.01, 1000, 2)
        self._add_float(model, "r_ohm_per_km", "R (Ω/km)", model.r_ohm_per_km, 0, 10, 4)
        self._add_float(model, "x_ohm_per_km", "X (Ω/km)", model.x_ohm_per_km, 0, 10, 4)
        self._add_float(model, "max_i_ka", "载流量 (kA)", model.max_i_ka, 0, 10, 3)

    def _add_name_field_line(self, model) -> None:
        e = QLineEdit(model.name)
        e.editingFinished.connect(lambda: self._set_attr(model, "name", e.text()))
        self.form_layout.addRow("名称", e)

    def _clear_form(self) -> None:
        while self.form_layout.count():
            child = self.form_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self._fields.clear()
        # 清空结果区
        while self.result_layout.count():
            child = self.result_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    def _add_name_field(self) -> None:
        model = self.current_item.model
        e = QLineEdit(model.name)
        e.editingFinished.connect(lambda: (
            self._set_attr(model, "name", e.text()),
            self.current_item.update_label(e.text()),
        ))
        self.form_layout.addRow("名称", e)

    def _add_float(self, model, attr: str, label: str, value: float,
                   mn: float, mx: float, decimals: int) -> None:
        sb = QDoubleSpinBox()
        sb.setDecimals(decimals)
        sb.setRange(mn, mx)
        sb.setSingleStep(0.01 if mx - mn < 10 else 1.0)
        sb.setValue(value)
        sb.valueChanged.connect(lambda v: self._set_attr(model, attr, v))
        self.form_layout.addRow(label, sb)
        self._fields[attr] = sb

    def _add_combo_bus(self, model, attr: str, label: str, current_uid: str) -> None:
        from PyQt5.QtWidgets import QComboBox
        cb = QComboBox()
        scene = self._scene()
        for uid, b in scene.network.buses.items():
            cb.addItem(b.name, uid)
        # 选中当前
        idx = cb.findData(current_uid)
        if idx >= 0:
            cb.setCurrentIndex(idx)
        cb.currentIndexChanged.connect(
            lambda i: self._set_attr(model, attr, cb.itemData(i))
        )
        self.form_layout.addRow(label, cb)
        self._fields[attr] = cb

    def _set_attr(self, model, attr: str, value) -> None:
        if getattr(model, attr) != value:
            setattr(model, attr, value)

    def refresh_results(self) -> None:
        while self.result_layout.count():
            child = self.result_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        scene = self._scene()
        if scene is None or self.current_item is None:
            return
        net = scene.network
        item = self.current_item
        from canvas import BaseComponent, ConnectionItem, BusItem, GenItem, LoadItem, TrafoItem, ImpedanceItem
        if isinstance(item, BaseComponent):
            m = item.model
            if isinstance(item, BusItem):
                v = net.bus_voltage_pu.get(m.uid)
                a = net.bus_va_degree.get(m.uid)
                vk = net.bus_voltage_kv.get(m.uid)
                self._add_result("电压 (pu)", f"{v:.4f}" if v is not None else "—")
                self._add_result("电压 (kV)", f"{vk:.2f}" if vk is not None else "—")
                self._add_result("相角 (°)", f"{a:.3f}" if a is not None else "—")
            elif isinstance(item, GenItem):
                # PV node: P is input, V is input, Q is the result.
                # Slack node: V and θ are input, P and Q are results.
                p = net.gen_p_mw.get(m.uid)
                q = net.gen_q_mvar.get(m.uid)
                # vm_pu is filled for PV nodes (res_gen has it) but not
                # slack (res_ext_grid doesn't). Fall back to model input.
                vm = net.gen_vm_pu.get(m.uid, m.vm_pu)
                self._add_result("实际有功 P (MW)", f"{p:+.2f}" if p is not None else "—")
                self._add_result("实际无功 Q (Mvar)", f"{q:+.2f}" if q is not None else "—")
                self._add_result("机端电压 (pu)", f"{vm:.4f}" if vm is not None else "—")
                if vm is not None:
                    # 阈值与画布着色同一来源, 避免两处定义漂移
                    from canvas import V_NORMAL, V_WARN_HIGH
                    if vm < V_NORMAL:
                        self._add_result("状态", "⚠ 电压偏低")
                    elif vm > V_WARN_HIGH:
                        self._add_result("状态", "⚠ 电压偏高")
                    else:
                        self._add_result("状态", "✓ 正常")
            elif isinstance(item, LoadItem):
                # PQ node: P and Q are inputs; voltage and angle are results.
                v = net.bus_voltage_pu.get(m.bus_uid)
                a = net.bus_va_degree.get(m.bus_uid)
                p_actual = net.load_p_mw.get(m.uid, m.p_mw)
                q_actual = net.load_q_mvar.get(m.uid, m.q_mvar)
                self._add_result("负荷有功 P (MW)", f"{p_actual:.2f}")
                self._add_result("负荷无功 Q (Mvar)", f"{q_actual:.2f}")
                self._add_result("母线电压 (pu)", f"{v:.4f}" if v is not None else "—")
                self._add_result("母线相角 (°)", f"{a:.3f}" if a is not None else "—")
            elif isinstance(item, TrafoItem):
                self._show_trafo_results(m.uid)
            elif isinstance(item, ImpedanceItem):
                self._show_impedance_results(m.uid)
        elif isinstance(item, ConnectionItem):
            if item.kind == "Line" and item.uid in net.lines:
                l = net.line_loading_percent.get(item.uid)
                p = net.line_p_from_mw.get(item.uid)
                q = net.line_q_from_mvar.get(item.uid)
                self._add_result("首端 P (MW)", f"{p:.2f}" if p is not None else "—")
                self._add_result("首端 Q (Mvar)", f"{q:.2f}" if q is not None else "—")
                self._add_result("负载率 (%)", f"{l:.1f}" if l is not None else "—")
            elif item.kind == "Trafo" and item.uid in net.trafos:
                self._show_trafo_results(item.uid)
            elif item.kind == "Impedance" and item.uid in net.impedances:
                self._show_impedance_results(item.uid)

    def _show_trafo_results(self, uid: str) -> None:
        """变压器潮流结果 (solver 已把 res_trafo 回写到 net.trafo_* 字典)"""
        net = self._scene().network
        l = net.trafo_loading_percent.get(uid)
        ph = net.trafo_p_hv_mw.get(uid)
        qh = net.trafo_q_hv_mvar.get(uid)
        pl = net.trafo_p_lv_mw.get(uid)
        ql = net.trafo_q_lv_mvar.get(uid)
        self._add_result("负载率 (%)", f"{l:.1f}" if l is not None else "—")
        self._add_result(
            "高压侧 P/Q",
            f"{ph:+.2f} / {qh:+.2f}" if ph is not None else "—")
        self._add_result(
            "低压侧 P/Q",
            f"{pl:+.2f} / {ql:+.2f}" if pl is not None else "—")

    def _show_impedance_results(self, uid: str) -> None:
        """串联阻抗潮流结果 (net.impedance_* 字典, solver 回写)"""
        net = self._scene().network
        pf = net.impedance_p_from_mw.get(uid)
        qf = net.impedance_q_from_mvar.get(uid)
        pt = net.impedance_p_to_mw.get(uid)
        qt = net.impedance_q_to_mvar.get(uid)
        self._add_result(
            "首端 P/Q",
            f"{pf:+.2f} / {qf:+.2f}" if pf is not None else "—")
        self._add_result(
            "末端 P/Q",
            f"{pt:+.2f} / {qt:+.2f}" if pt is not None else "—")

    def _add_result(self, label: str, value: str) -> None:
        l = QLabel(value)
        l.setStyleSheet("color: #005500; font-family: monospace;")
        self.result_layout.addRow(label, l)

    def _on_delete(self) -> None:
        scene = self._scene()
        if scene is None or self.current_item is None:
            return
        scene.delete_item(self.current_item)
        self.current_item = None
        self.title.setText("未选中任何元件")
        self._clear_form()

    def clear(self) -> None:
        self.current_item = None
        self.title.setText("未选中任何元件")
        self._clear_form()
