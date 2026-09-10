"""
theme.py — 全局外观 · 高分屏与分辨率适配

四件事:

1. enable_high_dpi() —— 必须在 ``QApplication`` 实例化**之前**调用。
   打开 Qt 的高分屏缩放(支持 4K 上 125%/150%/175% 这类非整数缩放)与
   高分辨率位图, 否则 4K 屏上界面只有邮票大小。

2. apply_theme() —— Fusion 风格 + 统一调色板 + 字号 + 轻量样式表。
   让菜单/工具栏/表格/停靠面板/分隔条在不同 Windows 主题下观感一致。

3. 分辨率适配 —— default_window_size() / minimum_window_size() /
   clamp_to_screen() / splitter_sizes() 全部按当前屏幕可用区域计算,
   1366×768 不会越界, 3840×2160 也不会只占一小角。

4. 统一缩放入口 —— ui_scale() / px(): 字号与内边距同源缩放, 保证
   控件不因字号变大而被挤坏; 也可用环境变量 ``POWERFLOW_UI_SCALE=1.25``
   手动指定倍率(调试或特殊显示器)。

注意: 全部函数在无 QApplication 时都安全降级为默认值, 因此单元测试
(offscreen、无真实屏幕) 也能正常导入与调用。
"""
from __future__ import annotations

import os
from typing import List, Optional

from PyQt5.QtCore import Qt, QRect, QSize
from PyQt5.QtGui import QColor, QFont, QPalette
from PyQt5.QtWidgets import QApplication

# ------------------------------------------------------------
# 调色板 (浅色专业主题, 与画布元件配色一致)
# ------------------------------------------------------------
BG = "#eef1f6"          # 窗口底色
SURFACE = "#ffffff"     # 卡片/输入底色
SURFACE_ALT = "#e6ecf4"  # 表头/标签页底色
BORDER = "#c9d2de"
BORDER_SOFT = "#dfe6ef"
TEXT = "#1d2531"
MUTED = "#6b7787"
DISABLED = "#a8b2c0"
ACCENT = "#2f6fb5"      # 主色(与母线蓝一致)
ACCENT_LIGHT = "#e3edf9"
ACCENT_PRESSED = "#cfe0f5"

# 基准字号 (96 DPI 下的默认值)
BASE_PT = 9.5

# 字体族: Windows / macOS / Linux 依次回落, 保证中英文都不发虚
FONT_FAMILIES: List[str] = [
    "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI",
    "PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC",
    "Source Han Sans SC", "DejaVu Sans",
]


def resolved_families() -> List[str]:
    """按 FONT_FAMILIES 的顺序, 只留下系统里真实装了的字体。

    实测坑: ``QFont.setFamilies([...])`` 只写了候选列表, **不会**更新
    ``family()`` 这个单数访问器。若只调 setFamilies, 字体最终会回退成
    系统默认(中文 Windows 上是衬线体 SimSun), 整个界面看着发旧发虚。
    这里先筛出真实可用的族, 调用方还会再 setFamily(第一个) 兜底。
    """
    try:
        from PyQt5.QtGui import QFontDatabase
        avail = set(QFontDatabase().families())
    except Exception:
        return list(FONT_FAMILIES)
    present = [name for name in FONT_FAMILIES if name in avail]
    return present or list(FONT_FAMILIES)

_theme_applied = False
_theme_instance = None          # 已应用主题的 QApplication(避免重复 polish)
_scale_cache: Optional[float] = None


# ------------------------------------------------------------
# 1. 高分屏
# ------------------------------------------------------------
def enable_high_dpi() -> bool:
    """打开 Qt 高分屏支持。**必须**在 QApplication 创建前调用。

    返回 True 表示已生效; 若 QApplication 已存在则返回 False
    (此时设置无效, 但也不会报错 —— 测试里 QApplication 由 fixture
    提前创建, 走这条分支)。
    """
    if QApplication.instance() is not None:
        return False
    try:
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    except Exception:
        return False
    # 非整数缩放(125%/150%)按真实比例渲染, 不四舍五入 —— 否则 4K@150%
    # 会被当成 100% 或 200%, 字体与图标尺寸明显不匹配
    policy = getattr(Qt, "HighDpiScaleFactorRoundingPolicy", None)
    if policy is not None:
        try:
            QApplication.setHighDpiScaleFactorRoundingPolicy(
                policy.PassThrough)
        except Exception:
            pass
    return True


# ------------------------------------------------------------
# 2. 缩放系数
# ------------------------------------------------------------
def ui_scale() -> float:
    """全局 UI 缩放系数(结果缓存, 屏幕 DPI 查询在部分平台较慢)。

    - ``POWERFLOW_UI_SCALE`` 环境变量优先(便于临时调试), 不缓存。
    - 系统已做缩放(devicePixelRatio>1)或逻辑 DPI 正常 → 返回 1.0,
      交给 Qt 自己缩放, 避免"双重放大"。
    - 高物理 DPI 但系统未缩放(典型: 4K 显示器保持 100%) → 按 DPI
      比例放大字号, 否则界面会小到看不清。
    """
    global _scale_cache
    env = os.environ.get("POWERFLOW_UI_SCALE", "").strip()
    if env:
        try:
            return max(0.6, min(3.0, float(env)))
        except ValueError:
            pass
    if _scale_cache is not None:
        return _scale_cache
    scr = QApplication.primaryScreen()
    if scr is None:
        return 1.0          # 还没有屏幕: 不缓存, 等 QApplication 就绪后再算
    try:
        dpr = float(scr.devicePixelRatio() or 1.0)
        dpi = float(scr.logicalDotsPerInch() or 96.0)
    except Exception:
        return 1.0
    if dpr > 1.01 or dpi <= 110.0:
        _scale_cache = 1.0
    else:
        _scale_cache = max(1.0, min(1.8, dpi / 96.0))
    return _scale_cache


def px(n: float) -> str:
    """把设计稿像素值换算成当前缩放下的样式表 px 字符串。"""
    return f"{max(1, int(round(n * ui_scale())))}px"


def base_font_point_size() -> float:
    return max(7.0, min(20.0, BASE_PT * ui_scale()))


def build_font() -> QFont:
    fams = resolved_families()
    f = QFont()
    try:
        f.setFamilies(fams)
    except AttributeError:      # Qt < 5.13 没有 setFamilies
        pass
    # 必须再显式 setFamily: setFamilies 不更新单数 family(), 只设列表会
    # 静默回退到系统默认字体(见 resolved_families() 的说明)
    if fams:
        f.setFamily(fams[0])
    f.setPointSizeF(base_font_point_size())
    try:
        f.setHintingPreference(QFont.PreferFullHinting)
    except Exception:
        pass
    return f


# ------------------------------------------------------------
# 3. 分辨率适配
# ------------------------------------------------------------
def available_geometry() -> QRect:
    """当前主屏可用区域(已扣掉任务栏)。无屏幕时给一个保守默认值。"""
    scr = QApplication.primaryScreen()
    if scr is None:
        return QRect(0, 0, 1280, 800)
    return scr.availableGeometry()


def default_window_size() -> QSize:
    """默认窗口尺寸: 铺满可用区域的 ~88%, 但不超过 1920×1200。

    上限是为了超宽屏(3840)上不至于把窗口拉到 3400px 宽 —— 那样
    两侧面板离得太远反而难用; 下限保证 1366×768 上仍然可用。
    """
    avail = available_geometry()
    w = int(min(max(1100, avail.width() * 0.88), min(avail.width(), 1920)))
    h = int(min(max(660, avail.height() * 0.88), min(avail.height(), 1200)))
    return QSize(max(800, w), max(560, h))


def minimum_window_size() -> QSize:
    """最小尺寸: 小屏上要能放得下, 否则窗口一开就超出屏幕。"""
    avail = available_geometry()
    return QSize(min(980, avail.width()), min(620, avail.height()))


def clamp_to_screen(win) -> None:
    """把恢复出来的窗口几何收敛到当前屏幕内。

    QSettings 里存的可能是另一台显示器(或 4K)上的几何, 直接
    restoreGeometry 会得到"窗口在屏幕外/比屏幕还大"的哑状态。
    """
    avail = available_geometry()
    frame = win.frameGeometry()
    too_big = (frame.width() > avail.width() * 1.02
               or frame.height() > avail.height() * 1.02)
    outside = not avail.intersects(frame)
    if not (too_big or outside):
        return
    win.resize(default_window_size())
    win.move(max(avail.left(), avail.center().x() - win.width() // 2),
             max(avail.top(), avail.center().y() - win.height() // 2))


def splitter_minimums() -> List[int]:
    """左元件库 / 画布 / 右属性 三栏的最小宽度。

    属性面板给 300: 发电机表单最宽一行是"短路容量最大 (MVA)"(约 121px)
    加输入框(约 95px), 再加上边距与纵向滚动条(11px), 268 会差几个像素
    导致标签末字被裁、并弹出横向滚动条。
    """
    s = ui_scale()
    return [int(160 * s), 320, int(300 * s)]


def sanitize_splitter_sizes(saved, total_width: Optional[int] = None) -> List[int]:
    """校验从 QSettings 恢复的三栏宽度, 不可信就回落到重新计算的值。

    实测踩过的坑: 上一版把面板宽度写死(150/260), 存档里留下了
    ``['240', '124', '268']`` 这种几何。新版本直接恢复时, 由于画布那栏
    stretch=1, 富余宽度全被画布吃掉, 用户看到的是"画布只剩 124px、
    属性面板卡在最小值"的哑状态 —— 而且此后每次启动都这样, 用户无从
    察觉是历史设置作祟。这里做四项检查:
      1. 必须是 3 个能转成整数的元素
      2. 每栏都不小于当前最小值
      3. 总宽与实际可用宽度相差不超过 12%(窗口换过尺寸就重算)
      4. 中间画布栏不小于最小值
    任一项不过就直接用 splitter_sizes() 重算。
    """
    if not total_width or total_width <= 0:
        total_width = default_window_size().width()
    lo = splitter_minimums()
    fallback = splitter_sizes(total_width)
    if saved is None:
        return fallback
    try:
        vals = [int(v) for v in saved]
    except (TypeError, ValueError):
        return fallback
    if len(vals) != 3:
        return fallback
    if any(v < lo[i] for i, v in enumerate(vals)):
        return fallback
    if abs(sum(vals) - total_width) > max(80, total_width * 0.12):
        return fallback
    return vals


def splitter_sizes(total_width: Optional[int] = None) -> List[int]:
    """三栏初始比例: 元件库 ~13%, 属性面板 ~21%, 其余给画布。"""
    s = ui_scale()
    if not total_width or total_width <= 0:
        total_width = default_window_size().width()
    lo = splitter_minimums()
    pal = int(min(max(lo[0], total_width * 0.13), 250 * s))
    props = int(min(max(lo[2], total_width * 0.21), 400 * s))
    center = max(lo[1], total_width - pal - props)
    return [pal, center, props]


# ------------------------------------------------------------
# 4. 应用主题
# ------------------------------------------------------------
def build_palette() -> QPalette:
    p = QPalette()
    c = QColor
    p.setColor(QPalette.Window, c(BG))
    p.setColor(QPalette.WindowText, c(TEXT))
    p.setColor(QPalette.Base, c(SURFACE))
    p.setColor(QPalette.AlternateBase, c("#f4f7fb"))
    p.setColor(QPalette.ToolTipBase, c("#2b3442"))
    p.setColor(QPalette.ToolTipText, c("#f2f5f9"))
    p.setColor(QPalette.Text, c(TEXT))
    p.setColor(QPalette.Button, c(SURFACE))
    p.setColor(QPalette.ButtonText, c(TEXT))
    p.setColor(QPalette.BrightText, c("#d64545"))
    p.setColor(QPalette.Link, c(ACCENT))
    p.setColor(QPalette.Highlight, c(ACCENT))
    p.setColor(QPalette.HighlightedText, c("#ffffff"))
    p.setColor(QPalette.PlaceholderText, c(DISABLED))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, c(DISABLED))
    return p


def build_stylesheet() -> str:
    """轻量样式表: 只做结构与间距, 颜色尽量交给调色板。"""
    return f"""
QMainWindow, QDialog {{ background: {BG}; }}
QToolBar {{
    background: {SURFACE}; border: 0; border-bottom: 1px solid {BORDER};
    padding: {px(4)} {px(6)}; spacing: {px(4)};
}}
QToolBar QToolButton {{
    padding: {px(5)} {px(10)}; border: 1px solid transparent;
    border-radius: {px(5)};
}}
QToolBar QToolButton:hover {{ background: {ACCENT_LIGHT}; }}
QToolBar QToolButton:pressed {{ background: {ACCENT_PRESSED}; }}
QToolBar QToolButton:checked {{
    background: {ACCENT_PRESSED}; border: 1px solid {ACCENT};
}}
QToolBar::separator {{
    background: {BORDER}; width: 1px; margin: {px(4)} {px(6)};
}}
QMenuBar {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
QMenuBar::item {{ padding: {px(5)} {px(10)}; background: transparent;
                 border-radius: {px(5)}; }}
QMenuBar::item:selected {{ background: {ACCENT_LIGHT}; }}
QMenu {{ background: {SURFACE}; border: 1px solid {BORDER}; padding: {px(4)}; }}
QMenu::item {{ padding: {px(6)} {px(22)} {px(6)} {px(14)};
              border-radius: {px(4)}; }}
QMenu::item:selected {{ background: {ACCENT}; color: #ffffff; }}
QMenu::separator {{ height: 1px; background: {BORDER_SOFT};
                   margin: {px(4)} {px(8)}; }}
QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; }}
QStatusBar QLabel {{ padding: 0 {px(6)}; color: {MUTED}; }}
QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:hover {{ background: {ACCENT}; }}
QDockWidget {{ font-weight: bold; }}
QDockWidget::title {{
    background: {SURFACE_ALT}; padding: {px(5)} {px(8)};
    border-bottom: 1px solid {BORDER};
}}
QTabWidget::pane {{
    border: 1px solid {BORDER}; background: {SURFACE};
    border-radius: {px(6)}; top: -1px;
}}
QTabBar::tab {{
    background: {SURFACE_ALT}; color: {MUTED};
    padding: {px(6)} {px(14)}; border: 1px solid {BORDER};
    border-bottom: 0; margin-right: {px(2)};
    border-top-left-radius: {px(6)}; border-top-right-radius: {px(6)};
}}
QTabBar::tab:selected {{ background: {SURFACE}; color: {TEXT}; }}
QTabBar::tab:hover:!selected {{ background: #d7e0ec; }}
QHeaderView::section {{
    background: {SURFACE_ALT}; color: #33404f;
    padding: {px(5)} {px(6)}; border: 0;
    border-right: 1px solid {BORDER}; border-bottom: 1px solid {BORDER};
}}
QTableWidget, QTableView {{
    background: {SURFACE}; gridline-color: {BORDER_SOFT};
    border: 1px solid {BORDER}; border-radius: {px(6)};
    selection-background-color: {ACCENT_LIGHT}; selection-color: {TEXT};
}}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {SURFACE}; border: 1px solid {BORDER};
    border-radius: {px(5)}; padding: {px(4)} {px(6)};
    min-height: {px(18)};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {ACCENT};
}}
QComboBox::drop-down {{ border: 0; width: {px(18)}; }}
QPushButton {{
    background: {SURFACE}; border: 1px solid {BORDER};
    border-radius: {px(5)}; padding: {px(6)} {px(12)};
}}
QPushButton:hover {{ background: {ACCENT_LIGHT}; border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {ACCENT_PRESSED}; }}
QGroupBox {{
    border: 1px solid {BORDER}; border-radius: {px(6)};
    margin-top: {px(12)}; padding-top: {px(8)};
    background: {SURFACE}; font-weight: bold;
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: {px(10)}; padding: 0 {px(4)};
    color: {MUTED};
}}
QCheckBox {{ spacing: {px(6)}; }}
QScrollBar:vertical {{ background: transparent; width: {px(11)};
                       margin: {px(2)}; }}
QScrollBar::handle:vertical {{ background: #c2ccda;
                               border-radius: {px(5)};
                               min-height: {px(24)}; }}
QScrollBar::handle:vertical:hover {{ background: #a7b5c8; }}
QScrollBar:horizontal {{ background: transparent; height: {px(11)};
                         margin: {px(2)}; }}
QScrollBar::handle:horizontal {{ background: #c2ccda;
                                 border-radius: {px(5)};
                                 min-width: {px(24)}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolTip {{
    background: #2b3442; color: #f2f5f9;
    border: 1px solid #2b3442; padding: {px(4)} {px(6)};
}}
QProgressDialog {{ min-width: {px(320)}; }}
"""


def apply_theme(app: Optional[QApplication] = None) -> bool:
    """应用风格/调色板/字号/样式表。幂等, 可重复调用。

    用 ``QApplication.instance()`` 而不是外部传入的对象, 这样在测试里
    被替换掉的假 QApplication 不会让主题逻辑出错。同一个 QApplication
    上重复调用直接返回 —— 每次 MainWindow 构造都重设样式表会触发全量
    样式重算, 拖慢测试与多窗口启动。
    """
    global _theme_applied, _theme_instance
    inst = app if app is not None else QApplication.instance()
    if inst is None:
        return False
    if _theme_applied and _theme_instance is inst:
        return True
    try:
        inst.setStyle("Fusion")
    except Exception:
        pass
    try:
        inst.setPalette(build_palette())
    except Exception:
        pass
    try:
        inst.setFont(build_font())
    except Exception:
        pass
    try:
        inst.setStyleSheet(build_stylesheet())
    except Exception:
        pass
    _theme_applied = True
    _theme_instance = inst
    return True


def describe_environment() -> str:
    """供"系统信息"对话框展示, 便于用户反馈分辨率问题时一眼看懂。"""
    scr = QApplication.primaryScreen()
    if scr is None:
        return "屏幕: 不可用"
    avail = scr.availableGeometry()
    return (f"屏幕: {scr.size().width()}×{scr.size().height()} "
            f"(可用 {avail.width()}×{avail.height()})\n"
            f"缩放: devicePixelRatio={scr.devicePixelRatio():.2f}, "
            f"逻辑 DPI={scr.logicalDotsPerInch():.0f}, "
            f"UI 倍数={ui_scale():.2f}")
