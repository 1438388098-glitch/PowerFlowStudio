"""
tools/render_multires.py — 多分辨率 / 多界面倍数渲染核对

用途: 改过 theme.py 或面板布局后, 一次性把主窗口在几种典型分辨率下截图,
肉眼看有没有截断、重叠、横向滚动条、面板被挤成一条的问题。

要点:
1. 高分屏属性必须在 QApplication **实例化之前**设置, 所以先 import theme
   并调 enable_high_dpi(), 再创建 QApplication。
2. ``POWERFLOW_UI_SCALE`` 可模拟"系统未做缩放但屏幕是 4K"的场景;
   系统已缩放(devicePixelRatio>1)时 ui_scale() 会返回 1.0, 交给 Qt 缩放。
3. 不要用 offscreen 平台 —— Windows 上 offscreen 没有字体数据库, 文字会
   静默丢失, 截出来全是空白。用默认平台且不调 show() 即可, 隐藏窗口下
   grab() 同样成像, 不会弹窗打扰桌面。

用法:
    python tools/render_multires.py                 # 当前倍数
    POWERFLOW_UI_SCALE=1.5 python tools/render_multires.py
产出: .ui_check/s100_default.png 等 (s100 表示界面倍数 1.00)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import theme  # noqa: E402

theme.enable_high_dpi()          # 必须在 QApplication 之前

from PyQt5.QtWidgets import QApplication  # noqa: E402

from app import MainWindow  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "..", ".ui_check")

CASES = [("default", None),
         ("hd_1366x768", (1366, 768)),
         ("fhd_1920x1080", (1920, 1080)),
         ("qhd_2560x1440", (2560, 1440)),
         ("uhd_3840x2160", (3840, 2160))]


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    theme.apply_theme(app)

    scale = theme.ui_scale()
    tag = f"s{int(round(scale * 100)):03d}"

    w = MainWindow()
    w._load_two_end_demo()
    w.results_dock.show()
    # 选中一台发电机, 让右侧属性面板展开字段最多的那张表单
    gen_uid = next(iter(w.network.gens))
    w.scene.clearSelection()
    w.scene._comp_by_uid[gen_uid].setSelected(True)

    for label, size in CASES:
        if size:
            w.resize(*size)
        app.processEvents()
        path = os.path.join(OUT, f"{tag}_{label}.png")
        w.grab().save(path)
        print(f"{tag} {label:16s} win={w.width()}x{w.height()} "
              f"splitter={w.centralWidget().sizes()}")

    print(f"ui_scale={scale}  min={theme.minimum_window_size()}  "
          f"default={theme.default_window_size()}  "
          f"font={app.font().pointSizeF():.1f}pt/{app.font().family()}")
    print(theme.describe_environment())
    print("输出目录:", os.path.abspath(OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
