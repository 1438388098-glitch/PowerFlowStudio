"""
tools/render_screenshot.py — 渲染 README 用截图

注意: 不要用 QT_QPA_PLATFORM=offscreen —— Windows 上 offscreen 平台没有
字体数据库, 所有文字会静默丢失。用默认 windows 平台且不调用 show(),
widget.grab()/scene.render() 对隐藏窗口同样有效, 不会弹出窗口。
用法: python tools/render_screenshot.py
产出: docs/screenshot.png (两端供电示例画布)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt5.QtWidgets import QApplication

from app import MainWindow, render_scene_png


def main():
    qapp = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.resize(1280, 800)
    w._load_two_end_demo()
    # 不调用 show(): 隐藏状态下 grab/render 即可成像, 不会打扰桌面
    os.makedirs("docs", exist_ok=True)
    ok = render_scene_png(w.scene, "docs/screenshot.png", scale=2.0)
    print("canvas png:", ok)
    w.grab().save("docs/screenshot_window.png")
    print("window png:", os.path.exists("docs/screenshot_window.png"))


if __name__ == "__main__":
    main()
