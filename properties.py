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
    def __init__(self, parent=None):
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

    def show_component(self, comp_item):
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
            self._add_float("vn_kv", "额定电压 (kV)", model.vn_kv, 0.1, 1000, 1)
        elif kind == "Gen":
            self._add_name_field()
            self._add_combo_bus("bus_uid", "挂接母线", model.bus_uid)
            self._add_float("p_mw", "有功 P (MW)", model.p_mw, 0, 5000, 1)
            self._add_float("vm_pu", "电压 V (pu)", model.vm_pu, 0.8, 1.2, 4)
        elif kind == "Load":
            self._add_name_field()
            self._add_combo_bus("bus_uid", "挂接母线", model.bus_uid)
            self._add_float("p_mw", "有功 P (MW)", model.p_mw, 0, 5000, 1)
            self._add_float("q_mvar", "无功 Q (Mvar)", model.q_mvar, -1000, 1000, 2)
        elif kind == "Trafo":
            self._add_name_field()
            self._add_combo_bus("hv_bus", "高压母线", model.hv_bus)
            self._add_combo_bus("lv_bus", "低压母线", model.lv_bus)
            self._add_float("sn_mva", "额定容量 (MVA)", model.sn_mva, 0.1, 1000, 1)
            self._add_float("vn_hv_kv", "高压侧额定电压 (kV)", model.vn_hv_kv, 0.1, 1000, 1)
            self._add_float("vn_lv_kv", "低压侧额定电压 (kV)", model.vn_lv_kv, 0.1, 1000, 1)
            self._add_float("vk_percent", "短路电压 (%)", model.vk_percent, 0, 30, 2)
            self._add_float("vkr_percent", "电阻压降 (%)", model.vkr_percent, 0, 30, 3)
            self._add_float("pfe_kw", "铁损 (kW)", model.pfe_kw, 0, 1000, 1)
            self._add_float("i0_percent", "空载电流 (%)", model.i0_percent, 0, 10, 3)
        elif kind == "Impedance":
            self._add_name_field()
            self._add_combo_bus("from_bus", "首端母线", model.from_bus)
            self._add_combo_bus("to_bus", "末端母线", model.to_bus)
            self._add_float("rft_pu", "R (pu)", model.rft_pu, -10, 10, 4)
            self._add_float("xft_pu", "X (pu)", model.xft_pu, -10, 10, 4)
            self._add_float("sn_mva", "基准容量 (MVA)", model.sn_mva, 0.1, 1000, 1)
        elif kind == "Line":
            self._add_name_field()
            self._add_combo_bus("from_bus", "首端母线", model.from_bus)
            self._add_combo_bus("to_bus", "末端母线", model.to_bus)
            self._add_float("length_km", "长度 (km)", model.length_km, 0.01, 1000, 2)
            self._add_float("r_ohm_per_km", "R (Ω/km)", model.r_ohm_per_km, 0, 10, 4)
            self._add_float("x_ohm_per_km", "X (Ω/km)", model.x_ohm_per_km, 0, 10, 4)
            self._add_float("max_i_ka", "载流量 (kA)", model.max_i_ka, 0, 10, 3)

        self.refresh_results()

    def show_connection(self, conn_item):
        self.current_item = conn_item
        self._clear_form()
        self.title.setText(f"连线: {conn_item.kind}")
        if conn_item.kind == "Line" and conn_item.uid:
            model = self._scene().network.lines[conn_item.uid]
            self._show_line_form(model)
        self.refresh_results()

    def _scene(self):
        if hasattr(self, "_scene_ref"):
            return self._scene_ref
        return None

    def attach_scene(self, scene):
        self._scene_ref = scene

    def _show_line_form(self, model):
        self._add_name_field_line(model)
        self._add_combo_bus("from_bus", "首端母线", model.from_bus)
        self._add_combo_bus("to_bus", "末端母线", model.to_bus)
        self._add_float("length_km", "长度 (km)", model.length_km, 0.01, 1000, 2)
        self._add_float("r_ohm_per_km", "R (Ω/km)", model.r_ohm_per_km, 0, 10, 4)
        self._add_float("x_ohm_per_km", "X (Ω/km)", model.x_ohm_per_km, 0, 10, 4)
        self._add_float("max_i_ka", "载流量 (kA)", model.max_i_ka, 0, 10, 3)

    def _add_name_field_line(self, model):
        e = QLineEdit(model.name)
        e.editingFinished.connect(lambda: self._set_attr(model, "name", e.text()))
        self.form_layout.addRow("名称", e)

    def _clear_form(self):
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

    def _add_name_field(self):
        model = self.current_item.model
        e = QLineEdit(model.name)
        e.editingFinished.connect(lambda: (
            self._set_attr(model, "name", e.text()),
            self.current_item.update_label(e.text()),
        ))
        self.form_layout.addRow("名称", e)

    def _add_float(self, attr, label, value, mn, mx, decimals):
        sb = QDoubleSpinBox()
        sb.setDecimals(decimals)
        sb.setRange(mn, mx)
        sb.setSingleStep(0.01 if mx - mn < 10 else 1.0)
        sb.setValue(value)
        model = self.current_item.model
        sb.valueChanged.connect(lambda v: self._set_attr(model, attr, v))
        self.form_layout.addRow(label, sb)
        self._fields[attr] = sb

    def _add_combo_bus(self, attr, label, current_uid):
        from PyQt5.QtWidgets import QComboBox
        cb = QComboBox()
        scene = self._scene()
        for uid, b in scene.network.buses.items():
            cb.addItem(b.name, uid)
        # 选中当前
        idx = cb.findData(current_uid)
        if idx >= 0:
            cb.setCurrentIndex(idx)
        model = self.current_item.model
        cb.currentIndexChanged.connect(
            lambda i: self._set_attr(model, attr, cb.itemData(i))
        )
        self.form_layout.addRow(label, cb)
        self._fields[attr] = cb

    def _set_attr(self, model, attr, value):
        if getattr(model, attr) != value:
            setattr(model, attr, value)

    def refresh_results(self):
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
                    if vm < 0.95:
                        self._add_result("状态", "⚠ 电压偏低")
                    elif vm > 1.05:
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
                self._add_result("变压器结果", "暂未提取 (仅作连线占位)")
            elif isinstance(item, ImpedanceItem):
                self._add_result("阻抗结果", "暂未提取")
        elif isinstance(item, ConnectionItem):
            if item.kind == "Line" and item.uid in net.lines:
                l = net.line_loading_percent.get(item.uid)
                p = net.line_p_from_mw.get(item.uid)
                q = net.line_q_from_mvar.get(item.uid)
                self._add_result("首端 P (MW)", f"{p:.2f}" if p is not None else "—")
                self._add_result("首端 Q (Mvar)", f"{q:.2f}" if q is not None else "—")
                self._add_result("负载率 (%)", f"{l:.1f}" if l is not None else "—")
            elif item.kind == "Trafo" and item.uid in net.trafos:
                self._add_result("变压器结果", "暂未提取 (仅作连线占位)")

    def _add_result(self, label, value):
        l = QLabel(value)
        l.setStyleSheet("color: #005500; font-family: monospace;")
        self.result_layout.addRow(label, l)

    def _on_delete(self):
        scene = self._scene()
        if scene is None or self.current_item is None:
            return
        scene.delete_item(self.current_item)
        self.current_item = None
        self.title.setText("未选中任何元件")
        self._clear_form()

    def clear(self):
        self.current_item = None
        self.title.setText("未选中任何元件")
        self._clear_form()
