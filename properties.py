"""
properties.py — 右侧属性编辑面板
选中元件/连线时显示其字段, 修改即时写回 model

数值字段的校验范围来自 defaults.FIELD_RANGES(单一事实来源), 面板不再
各自内联一套魔法数字; 存档里的越界值会被钳制并同步回写 model。
"""
import math

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QWidget, QFormLayout, QLineEdit, QDoubleSpinBox, QLabel,
    QVBoxLayout, QPushButton, QGroupBox, QScrollArea, QFrame
)

import defaults as D
import theme


def _is_bad(v) -> bool:
    """None / NaN / inf / 非数值 —— 都视为"没有有效数值" """
    if v is None:
        return True
    try:
        f = float(v)
    except (TypeError, ValueError):
        return True
    return math.isnan(f) or math.isinf(f)


def _fmt_num(v, nd: int = 4, sign: bool = False) -> str:
    """数值 → 显示字符串: 空值/NaN/inf 一律显示 '—'。

    孤岛母线电压、直流潮流的电压幅值都是 NaN。以前直接 f"{v:.4f}" 会在
    属性面板打印字面 "nan", 而结果表里同一个 NaN 却是"无着色"的空单元格
    —— 同一份数据两种表现。统一收口到 '—'。
    """
    if _is_bad(v):
        return "—"
    f = float(v)
    return f"{f:+.{nd}f}" if sign else f"{f:.{nd}f}"


def _pair(p, q) -> str:
    """一对方程结果 'P / Q'; 任一缺失就整体显示 '—'"""
    if _is_bad(p) and _is_bad(q):
        return "—"
    return f"{_fmt_num(p, 2, sign=True)} / {_fmt_num(q, 2, sign=True)}"


def _drop_layout(layout) -> None:
    """清空一个布局, 并**立即**把控件从父级摘下来。

    只调 ``deleteLater()`` 是不够的: 延迟删除要等事件循环回到空闲才执行,
    而 ``takeAt`` 仅仅把控件移出布局、并不会解除父子关系 —— 在同一个事件
    处理里"清空 → 重建"时, 旧控件仍然挂在父窗口上、仍带旧坐标, 于是和新
    建的行画在同一个位置, 属性面板会出现两层文字叠在一起的乱码。先
    ``setParent(None)`` 立刻隐藏并脱离, 再排队删除。
    """
    while layout.count():
        child = layout.takeAt(0)
        w = child.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        else:
            sub = child.layout()
            if sub is not None:
                _drop_layout(sub)


class PropertiesPanel(QWidget):
    """选中元件后, 在这里编辑它的字段"""
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        s = theme.ui_scale()
        # 用最小/最大宽度而非 setFixedWidth: 面板在 QSplitter 里, 固定宽度
        # 会连带把分隔条也锁死。最小值直接取自 theme.splitter_minimums()
        # 的第三栏, 保证"S 分栏最小宽度"与"表单放得下"是同一个数字。
        self.setMinimumWidth(theme.splitter_minimums()[2])
        self.setMaximumWidth(int(560 * s))
        self.current_item = None
        self._current_kind = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(int(8 * s), int(8 * s),
                                  int(8 * s), int(8 * s))
        layout.setSpacing(int(6 * s))
        self.title = QLabel("未选中任何元件")
        self.title.setWordWrap(True)
        self.title.setStyleSheet(
            f"font-weight:bold; font-size:{theme.base_font_point_size() + 1.5:.1f}pt;"
            f" color:{theme.TEXT};")
        layout.addWidget(self.title)

        self.form_host = QWidget()
        self.form_layout = QFormLayout(self.form_host)
        self.form_layout.setContentsMargins(0, 0, 0, 0)
        # 长标签(如"无功 (Mvar) 正电抗/负电容")在窄面板上会把输入框挤到
        # 只剩几十像素, 数字被截成 "1.05C"。WrapLongRows 让放不下的标签
        # 换到自己一行, 输入框始终拿到整行宽度; 短标签仍保持左右布局。
        self.form_layout.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.form_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.form_layout.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        # 结果区
        self.result_box = QGroupBox("潮流结果")
        self.result_layout = QFormLayout(self.result_box)
        self.result_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        # 结果标签偏长("实际无功 Q (Mvar)"), 窄面板上同样要换行而不是挤压数值
        self.result_layout.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.result_layout.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        # 表单必须放进滚动区: 发电机有 17 个字段, 在 4K / 放大字号下
        # 纵向一屏放不下, 以前 QFormLayout 会被强行压缩, 行与行直接叠在
        # 一起变成一团乱码。放进 QScrollArea 后放不下就滚动。
        self._form_scroll = QScrollArea()
        self._form_scroll.setWidgetResizable(True)
        self._form_scroll.setFrameShape(QFrame.NoFrame)
        self._form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        inner = QWidget()
        inner_box = QVBoxLayout(inner)
        inner_box.setContentsMargins(0, 0, 0, 0)
        inner_box.setSpacing(int(6 * s))
        inner_box.addWidget(self.form_host)
        inner_box.addWidget(self.result_box)
        inner_box.addStretch(1)
        self._form_scroll.setWidget(inner)
        layout.addWidget(self._form_scroll, 1)

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
        self._current_kind = kind
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
            self._add_combo_choice(model, "gen_mode", "节点类型",
                                   ["PV", "PQ"], model.gen_mode)
            self._add_float(model, "slack_weight", "松弛分摊权重",
                            model.slack_weight, 0.0, 10.0, 2)
            self._add_float(model, "min_p_mw", "OPF 出力下限 (MW)", model.min_p_mw, -5000, 5000, 1)
            self._add_float(model, "max_p_mw", "OPF 出力上限 (MW)", model.max_p_mw, 0, 5000, 1)
            self._add_float(model, "cost_per_mw", "发电成本 (元/MWh)", model.cost_per_mw, 0, 10000, 1)
            self._add_float(model, "s_sc_max_mva", "短路容量最大 (MVA)", model.s_sc_max_mva, 1, 100000, 0)
            self._add_float(model, "s_sc_min_mva", "短路容量最小 (MVA)", model.s_sc_min_mva, 1, 100000, 0)
            self._add_float(model, "kappa", "峰值系数 κ", model.kappa, 1.0, 2.0, 2)
            self._add_float(model, "xdss_percent", "次暂态电抗 x''d (%)",
                            model.xdss_percent, 5.0, 60.0, 1)
            self._add_float(model, "rdss_percent", "次暂态电阻 r''d (%)",
                            model.rdss_percent, 0.0, 20.0, 2)
            self._add_float(model, "cos_phi", "额定功率因数 cosφ",
                            model.cos_phi, 0.1, 1.0, 3)
            self._add_note("短路电流: PV 机组按上面的铭牌参数(x''d/r''d/cosφ)"
                           "与 max(S=|P|/cosφ, 10MVA) 估算, 额定容量优先生效; "
                           "PQ 机组按 sgen 电流源参与短路。")
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
            self._add_float(model, "q_mvar", "无功 Q (Mvar)",
                            model.q_mvar, -1000, 1000, 2)
            self._add_note("Q 的符号约定与 pandapower 一致: "
                           "正 = 电抗器(吸收无功), 负 = 电容器(发出无功)。")
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
            self._add_float(model, "shift_degree", "相移 (°)", model.shift_degree,
                            -60, 60, 1)
            # 联结组别以前在 solver 里写死 "Dyn", Yy/Yd 电网无法表达
            self._add_combo_choice(model, "vector_group", "联结组别",
                                   list(D.TRAFO_VECTOR_GROUPS),
                                   getattr(model, "vector_group", "Dyn"))
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
        self._current_kind = conn_item.kind or ""
        if conn_item.kind == "Line" and conn_item.uid:
            self._show_line_form(net.lines[conn_item.uid])
        elif conn_item.kind == "Trafo" and conn_item.uid in net.trafos:
            # 变压器/阻抗连线以前在属性面板完全不可编辑(点上去只有标题),
            # 用户只能删掉重画 —— 与"线路可编辑"明显不对称。现在补上。
            self._show_trafo_form(net.trafos[conn_item.uid])
        elif conn_item.kind == "Impedance" and conn_item.uid in net.impedances:
            self._show_impedance_form(net.impedances[conn_item.uid])
        # 结束: 上面的表单一律先经过 _current_kind 设定, 保证
        # _add_float 能从 defaults.FIELD_RANGES 取到正确的范围
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

    def _field_min_width(self, sample: str = "0000.0000") -> int:
        """输入类控件的最小宽度: 按当前字体实测 "样例文本" 的像素宽度。

        写死像素在放大字号/高分屏下必然截断, 按字体度量算才能跟着走。
        额外留出上下箭头(SpinBox)与内边距的宽度。
        """
        fm = self.fontMetrics()
        try:
            text_w = fm.horizontalAdvance(sample)
        except AttributeError:      # Qt < 5.11
            text_w = fm.width(sample)
        return text_w + int(38 * theme.ui_scale())

    def _fit_field(self, w, sample: str = "0000.0000") -> None:
        """给输入控件设一个不会被截断的最小宽度。

        注意别再去调 setSizePolicy: 字段列本来就走 AllNonFixedFieldsGrow,
        重复设一次同样的策略没有意义。
        """
        w.setMinimumWidth(self._field_min_width(sample))

    def _show_line_form(self, model) -> None:
        self._add_name_field_line(model)
        self._add_combo_bus(model, "from_bus", "首端母线", model.from_bus)
        self._add_combo_bus(model, "to_bus", "末端母线", model.to_bus)
        self._add_float(model, "length_km", "长度 (km)", model.length_km, 0.01, 1000, 2)
        self._add_float(model, "r_ohm_per_km", "R (Ω/km)", model.r_ohm_per_km, 0, 10, 4)
        self._add_float(model, "x_ohm_per_km", "X (Ω/km)", model.x_ohm_per_km, 0, 10, 4)
        self._add_float(model, "max_i_ka", "载流量 (kA)", model.max_i_ka, 0, 10, 3)

    def _show_trafo_form(self, model) -> None:
        """变压器连线: 与元件面板同一套字段(点连线也能改参数)"""
        self._add_name_field_line(model)
        self._add_combo_bus(model, "hv_bus", "高压母线", model.hv_bus)
        self._add_combo_bus(model, "lv_bus", "低压母线", model.lv_bus)
        self._add_float(model, "sn_mva", "额定容量 (MVA)", model.sn_mva, 0.1, 1000, 1)
        self._add_float(model, "vn_hv_kv", "高压侧额定电压 (kV)",
                        model.vn_hv_kv, 0.1, 1000, 1)
        self._add_float(model, "vn_lv_kv", "低压侧额定电压 (kV)",
                        model.vn_lv_kv, 0.1, 1000, 1)
        self._add_float(model, "vk_percent", "短路电压 (%)", model.vk_percent, 0, 30, 2)
        self._add_float(model, "vkr_percent", "电阻压降 (%)",
                        model.vkr_percent, 0, 30, 3)
        self._add_float(model, "pfe_kw", "铁损 (kW)", model.pfe_kw, 0, 1000, 1)
        self._add_float(model, "i0_percent", "空载电流 (%)",
                        model.i0_percent, 0, 10, 3)
        self._add_int(model, "tap_pos", "分接头位置 (-2..2)", model.tap_pos, -2, 2)
        self._add_float(model, "shift_degree", "相移 (°)", model.shift_degree,
                        -60, 60, 1)
        self._add_combo_choice(model, "vector_group", "联结组别",
                               list(D.TRAFO_VECTOR_GROUPS),
                               getattr(model, "vector_group", "Dyn"))

    def _show_impedance_form(self, model) -> None:
        """串联阻抗连线: 同上"""
        self._add_name_field_line(model)
        self._add_combo_bus(model, "from_bus", "首端母线", model.from_bus)
        self._add_combo_bus(model, "to_bus", "末端母线", model.to_bus)
        self._add_float(model, "rft_pu", "R (pu)", model.rft_pu, -10, 10, 4)
        self._add_float(model, "xft_pu", "X (pu)", model.xft_pu, -10, 10, 4)
        self._add_float(model, "sn_mva", "基准容量 (MVA)", model.sn_mva, 0.1, 1000, 1)

    def _add_name_field_line(self, model) -> None:
        e = QLineEdit(model.name)
        e.editingFinished.connect(lambda: self._set_attr(model, "name", e.text()))
        self._fit_field(e, "元件名称")
        self.form_layout.addRow("名称", e)

    def _add_note(self, text: str) -> None:
        """灰色小字说明行(不入 _fields, 不参与编辑)"""
        l = QLabel(text)
        l.setWordWrap(True)
        # 字号跟着界面倍数走: 写死 11px 时, 4K/放大字号下这行会明显比其他
        # 文字小一圈甚至看不清
        l.setStyleSheet(
            f"color:{theme.MUTED};"
            f" font-size:{max(7.0, theme.base_font_point_size() - 1.5):.1f}pt;")
        self.form_layout.addRow(l)

    def _clear_form(self) -> None:
        _drop_layout(self.form_layout)
        self._fields.clear()
        _drop_layout(self.result_layout)
        # 空的结果分组框以前会占掉一大块空白(标题下面什么都没有),
        # 没有结果数据时直接收起来
        self.result_box.setVisible(False)

    def _add_name_field(self) -> None:
        model = self.current_item.model
        e = QLineEdit(model.name)
        e.editingFinished.connect(lambda: (
            self._set_attr(model, "name", e.text()),
            self.current_item.update_label(e.text()),
        ))
        self._fit_field(e, "元件名称")
        self.form_layout.addRow("名称", e)

    def _add_float(self, model, attr: str, label: str, value: float,
                   mn: float, mx: float, decimals: int) -> None:
        # 范围以 defaults.FIELD_RANGES 为单一事实来源; 形参只在字段未登记时兜底
        rng = D.field_range(self._current_kind, attr)
        if rng is not None:
            mn, mx, decimals = rng
        sb = QDoubleSpinBox()
        sb.setDecimals(decimals)
        sb.setRange(mn, mx)
        sb.setSingleStep(0.01 if mx - mn < 10 else 1.0)
        # 关闭键盘跟踪: 以前敲 "123" 会触发 3 次 valueChanged(3 次整网快照
        # 比对), 中间态还会被写进 model(敲 "1" 就被钳到下限)。回车/失焦才提交。
        sb.setKeyboardTracking(False)
        try:
            current = float(value)
        except (TypeError, ValueError):
            current = mn
        clamped = min(max(current, mn), mx)
        sb.setValue(clamped)
        if clamped != current:
            # 存档里的越界值(旧版本写的、手工改的): 以前 QDoubleSpinBox 只
            # 钳制显示, model 仍留原值 —— 用户不动这个字段就永远发现不了,
            # 界面与模型长期不一致。这里同步回写, 保证两边同值。
            self._sync_clamped(model, attr, clamped)
        sb.valueChanged.connect(lambda v: self._set_attr(model, attr, v))
        sb.editingFinished.connect(self._commit_pending_edit)
        # 样例按小数位构造, 位数越多越宽
        self._fit_field(sb, "0" * 4 + "." + "0" * min(max(decimals, 0), 6))
        self.form_layout.addRow(label, sb)
        self._fields[attr] = sb

    def _sync_clamped(self, model, attr: str, value) -> None:
        """把被钳制进合法范围的值写回 model(并标脏)"""
        if getattr(model, attr, None) == value:
            return
        try:
            setattr(model, attr, value)
        except Exception:
            return
        win = self._main_window()
        if hasattr(win, "_set_dirty"):
            win._set_dirty(True)

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
        self._fit_field(cb, "B1 母线")      # 母线下拉实际就显示 "B1" 这类短名
        self.form_layout.addRow(label, cb)
        self._fields[attr] = cb

    def _add_combo_choice(self, model, attr: str, label: str,
                          choices, current) -> None:
        from PyQt5.QtWidgets import QComboBox
        cb = QComboBox()
        cb.addItems([str(c) for c in choices])
        idx = list(choices).index(current) if current in choices else 0
        cb.setCurrentIndex(idx)
        cb.currentIndexChanged.connect(
            lambda i: (self._set_attr(model, attr, choices[i]),
                       self._commit_pending_edit())
        )
        self._fit_field(cb, "选择项")
        self.form_layout.addRow(label, cb)
        self._fields[attr] = cb

    def _add_int(self, model, attr: str, label: str, value: int,
                 mn: int, mx: int) -> None:
        from PyQt5.QtWidgets import QSpinBox
        sb = QSpinBox()
        sb.setRange(mn, mx)
        sb.setValue(int(value))
        sb.setKeyboardTracking(False)   # 同 _add_float: 回车/失焦才提交
        sb.valueChanged.connect(lambda v: self._set_attr(model, attr, int(v)))
        sb.editingFinished.connect(self._commit_pending_edit)
        self._fit_field(sb, "-0000")
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
        _drop_layout(self.result_layout)
        self.result_box.setVisible(False)   # 没有结果就不显示空分组框
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
                self._add_result("电压 (pu)", _fmt_num(v, 4))
                self._add_result("电压 (kV)", _fmt_num(vk, 2))
                self._add_result("相角 (°)", _fmt_num(a, 3))
                ik = net.bus_ikss_ka.get(m.uid)
                if ik is not None:
                    self._add_result("短路 Ikss (kA)", _fmt_num(ik, 3))
            elif isinstance(item, GenItem):
                # PV node: P is input, V is input, Q is the result.
                # Slack node: V and θ are input, P and Q are results.
                p = net.gen_p_mw.get(m.uid)
                q = net.gen_q_mvar.get(m.uid)
                # vm_pu is filled for PV nodes (res_gen has it) but not
                # slack (res_ext_grid doesn't). Fall back to model input.
                vm = net.gen_vm_pu.get(m.uid, m.vm_pu)
                self._add_result("实际有功 P (MW)", _fmt_num(p, 2, sign=True))
                self._add_result("实际无功 Q (Mvar)", _fmt_num(q, 2, sign=True))
                self._add_result("机端电压 (pu)", _fmt_num(vm, 4))
                if vm is not None and not _is_bad(vm):
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
                self._add_result("负荷有功 P (MW)", _fmt_num(p_actual, 2))
                self._add_result("负荷无功 Q (Mvar)", _fmt_num(q_actual, 2))
                self._add_result("母线电压 (pu)", _fmt_num(v, 4))
                self._add_result("母线相角 (°)", _fmt_num(a, 3))
            elif isinstance(item, TrafoItem):
                self._show_trafo_results(m.uid)
            elif isinstance(item, ImpedanceItem):
                self._show_impedance_results(m.uid)
            elif isinstance(item, ShuntItem):
                p = net.shunt_p_mw.get(m.uid)
                q = net.shunt_q_mvar.get(m.uid)
                v = net.bus_voltage_pu.get(m.bus_uid)
                self._add_result("注入有功 (MW)", _fmt_num(p, 3, sign=True))
                self._add_result("注入无功 (Mvar)", _fmt_num(q, 2, sign=True))
                self._add_result("母线电压 (pu)", _fmt_num(v, 4))
        elif isinstance(item, ConnectionItem):
            if item.kind == "Line" and item.uid in net.lines:
                l = net.line_loading_percent.get(item.uid)
                p = net.line_p_from_mw.get(item.uid)
                q = net.line_q_from_mvar.get(item.uid)
                self._add_result("首端 P (MW)", _fmt_num(p, 2))
                self._add_result("首端 Q (Mvar)", _fmt_num(q, 2))
                self._add_result("负载率 (%)", _fmt_num(l, 1))
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
        self._add_result("负载率 (%)", _fmt_num(l, 1))
        self._add_result("高压侧 P/Q", _pair(ph, qh))
        self._add_result("低压侧 P/Q", _pair(pl, ql))

    def _show_impedance_results(self, uid: str) -> None:
        """串联阻抗潮流结果 (net.impedance_* 字典, solver 回写)"""
        net = self._scene().network
        pf = net.impedance_p_from_mw.get(uid)
        qf = net.impedance_q_from_mvar.get(uid)
        pt = net.impedance_p_to_mw.get(uid)
        qt = net.impedance_q_to_mvar.get(uid)
        self._add_result("首端 P/Q", _pair(pf, qf))
        self._add_result("末端 P/Q", _pair(pt, qt))

    def _add_result(self, label: str, value: str) -> None:
        self.result_box.setVisible(True)   # 有数据才显示"潮流结果"分组
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
