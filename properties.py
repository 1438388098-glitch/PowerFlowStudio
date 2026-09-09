"""
properties.py — 右侧属性编辑面板
选中元件/连线时显示其字段, 修改即时写回 model
"""
from PyQt5.QtWidgets import (
    QWidget, QFormLayout, QLineEdit, QDoubleSpinBox, QLabel,
    QVBoxLayout, QPushButton, QGroupBox
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
        self._editing = False        # 本次参数编辑是否已开始(首个改动发生)
        self._edit_before = None     # 首个改动前的网络快照

    def show_component(self, comp_item) -> None:
        self._commit_pending_edit()
        self.current_item = comp_item
        # 清空旧字段
        self._clear_form()
        model = comp_item.model
        # kind 判断统一走 canvas.kind_of (单一来源)
        from canvas import kind_of
        kind = kind_of(comp_item)
        self.title.setText(f"元件: {kind}  {model.name}")
        self.title.setToolTip(f"uid: {model.uid}")

        if kind == "Bus":
            self._add_name_field()
            self._add_float(model, "vn_kv", "额定电压 (kV)", model.vn_kv, 0.1, 1000, 1)
        elif kind == "Gen":
            self._add_name_field()
            self._add_combo_bus(model, "bus_uid", "挂接母线", model.bus_uid)
            self._add_float(model, "p_mw", "有功 P (MW)", model.p_mw, 0, 5000, 1)
            self._add_float(model, "vm_pu", "电压 V (pu)", model.vm_pu, 0.8, 1.2, 4)
            self._add_check(model, "is_slack", "平衡节点 (Slack)")
            self._add_float(model, "min_p_mw", "OPF 出力下限 (MW)", model.min_p_mw, -5000, 5000, 1)
            self._add_float(model, "max_p_mw", "OPF 出力上限 (MW)", model.max_p_mw, 0, 5000, 1)
            self._add_float(model, "cost_per_mw", "发电成本 (元/MWh)", model.cost_per_mw, 0, 10000, 1)
            self._add_float(model, "s_sc_max_mva", "短路容量最大 (MVA)", model.s_sc_max_mva, 1, 100000, 0)
            self._add_float(model, "s_sc_min_mva", "短路容量最小 (MVA)", model.s_sc_min_mva, 1, 100000, 0)
            self._add_float(model, "kappa", "峰值系数 κ", model.kappa, 1.0, 2.0, 2)
        elif kind == "Load":
            self._add_name_field()
            self._add_combo_bus(model, "bus_uid", "挂接母线", model.bus_uid)
            # 负负荷 = 该点注入功率(等效电源), 允许为负
            self._add_float(model, "p_mw", "有功 P (MW)", model.p_mw, -5000, 5000, 1)
            self._add_float(model, "q_mvar", "无功 Q (Mvar)", model.q_mvar, -1000, 1000, 2)
        elif kind == "Shunt":
            self._add_name_field()
            self._add_combo_bus(model, "bus_uid", "挂接母线", model.bus_uid)
            self._add_float(model, "p_mw", "有功损耗 (MW)", model.p_mw, 0, 500, 3)
            self._add_float(model, "q_mvar", "无功 (Mvar) 正电抗/负电容", model.q_mvar, -1000, 1000, 2)
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
            self._add_int(model, "tap_pos", "分接头位置 (-2..2)", model.tap_pos, -2, 2)
        elif kind == "Impedance":
            self._add_name_field()
            self._add_combo_bus(model, "from_bus", "首端母线", model.from_bus)
            self._add_combo_bus(model, "to_bus", "末端母线", model.to_bus)
            self._add_float(model, "rft_pu", "R (pu)", model.rft_pu, -10, 10, 4)
            self._add_float(model, "xft_pu", "X (pu)", model.xft_pu, -10, 10, 4)
            self._add_float(model, "sn_mva", "基准容量 (MVA)", model.sn_mva, 0.1, 1000, 1)

        self.refresh_results()

    def show_connection(self, conn_item) -> None:
        self._commit_pending_edit()
        self.current_item = conn_item
        self._clear_form()
        net = self._scene().network
        self.title.setText(self._connection_title(conn_item, net))
        if conn_item.kind == "Line" and conn_item.uid:
            model = net.lines[conn_item.uid]
            self._show_line_form(model)
        self.refresh_results()

    def _connection_title(self, conn_item, net):
        """连线面板标题: 尽量带上两端母线名, 例 '线路 L1: B1 → B2'"""
        kind = conn_item.kind
        uid = conn_item.uid
        try:
            if kind == "Line" and uid in net.lines:
                m = net.lines[uid]
                a = net.buses.get(m.from_bus)
                b = net.buses.get(m.to_bus)
                return f"线路 {m.name}: {a.name if a else '?'} → {b.name if b else '?'}"
            if kind == "Trafo" and uid in net.trafos:
                m = net.trafos[uid]
                a = net.buses.get(m.hv_bus)
                b = net.buses.get(m.lv_bus)
                return f"变压器 {m.name}: {a.name if a else '?'} ↔ {b.name if b else '?'}"
            if kind == "Impedance" and uid in net.impedances:
                m = net.impedances[uid]
                a = net.buses.get(m.from_bus)
                b = net.buses.get(m.to_bus)
                return f"阻抗 {m.name}: {a.name if a else '?'} ↔ {b.name if b else '?'}"
        except AttributeError:
            pass
        return f"连线: {kind}"

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
        sb.editingFinished.connect(self._commit_pending_edit)
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
            lambda i: (self._set_attr(model, attr, cb.itemData(i)),
                       self._commit_pending_edit())
        )
        self.form_layout.addRow(label, cb)
        self._fields[attr] = cb

    def _add_int(self, model, attr: str, label: str, value: int,
                 mn: int, mx: int) -> None:
        from PyQt5.QtWidgets import QSpinBox
        sb = QSpinBox()
        sb.setRange(mn, mx)
        sb.setValue(int(value))
        sb.valueChanged.connect(lambda v: self._set_attr(model, attr, int(v)))
        sb.editingFinished.connect(self._commit_pending_edit)
        self.form_layout.addRow(label, sb)
        self._fields[attr] = sb

    def _add_check(self, model, attr: str, label: str) -> None:
        from PyQt5.QtWidgets import QCheckBox
        cb = QCheckBox()
        cb.setChecked(bool(getattr(model, attr, False)))
        cb.toggled.connect(
            lambda v: (self._set_attr(model, attr, bool(v)),
                       self._commit_pending_edit()))
        self.form_layout.addRow(label, cb)
        self._fields[attr] = cb

    def _main_window(self):
        scene = self._scene()
        if scene is not None and getattr(scene, "_view", None) is not None:
            return scene._view.window()
        return None

    def _commit_pending_edit(self):
        """把本次参数编辑(首个改动前快照 -> 当前)作为一条撤销记录提交"""
        if self._editing and self._edit_before is not None:
            win = self._main_window()
            if hasattr(win, "push_move_undo"):
                win.push_move_undo(self._edit_before,
                                   win.snapshot_network(), "修改参数")
        self._editing = False
        self._edit_before = None

    def _set_attr(self, model, attr: str, value) -> None:
        if getattr(model, attr) == value:
            return
        if not self._editing:
            # 首个改动: 记录改动前快照, 直到提交前都算同一次编辑
            win = self._main_window()
            self._edit_before = (win.snapshot_network()
                                 if hasattr(win, "snapshot_network") else None)
            self._editing = True
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
        from canvas import (BaseComponent, ConnectionItem, BusItem, GenItem,
                            LoadItem, TrafoItem, ImpedanceItem, ShuntItem)
        if isinstance(item, BaseComponent):
            m = item.model
            if isinstance(item, BusItem):
                v = net.bus_voltage_pu.get(m.uid)
                a = net.bus_va_degree.get(m.uid)
                vk = net.bus_voltage_kv.get(m.uid)
                self._add_result("电压 (pu)", f"{v:.4f}" if v is not None else "—")
                self._add_result("电压 (kV)", f"{vk:.2f}" if vk is not None else "—")
                self._add_result("相角 (°)", f"{a:.3f}" if a is not None else "—")
                ik = net.bus_ikss_ka.get(m.uid)
                if ik is not None:
                    self._add_result("短路 Ikss (kA)", f"{ik:.3f}")
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
            elif isinstance(item, ShuntItem):
                p = net.shunt_p_mw.get(m.uid)
                q = net.shunt_q_mvar.get(m.uid)
                v = net.bus_voltage_pu.get(m.bus_uid)
                self._add_result("注入有功 (MW)", f"{p:+.3f}" if p is not None else "—")
                self._add_result("注入无功 (Mvar)", f"{q:+.2f}" if q is not None else "—")
                self._add_result("母线电压 (pu)", f"{v:.4f}" if v is not None else "—")
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
        from PyQt5.QtGui import QPalette
        light = self.palette().color(QPalette.Window).lightness()
        color = "#005500" if light > 128 else "#7fd67f"   # 深色主题用亮绿
        l.setStyleSheet(f"color: {color}; font-family: monospace;")
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
        self._commit_pending_edit()
        self.current_item = None
        self.title.setText("未选中任何元件")
        self._clear_form()
