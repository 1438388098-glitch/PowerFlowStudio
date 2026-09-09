"""
tests/conftest.py — 测试全局配置

PyQt5 + pyqtgraph 在 Windows 上于解释器关闭阶段偶发 access violation
(纯析构顺序问题, 用例本身全过)。在 pytest 完全收尾(unconfigure, 摘要
已打印)后硬退出避开析构; 退出码沿用 pytest 的判定, 失败时为 1。
"""
import os
import sys

import pytest

# offscreen 平台下 pyqtgraph 实际绘屏会在 Windows 上偶发原生崩溃,
# 测试环境关掉结果 dock 的自动弹出(见 app._finish_power_flow)
os.environ.setdefault("POWERFLOW_NO_AUTOSHOW", "1")

_exit_status = {"code": None}


def pytest_sessionfinish(session, exitstatus):
    _exit_status["code"] = exitstatus


def pytest_unconfigure(config):
    code = _exit_status["code"]
    if code is None:
        return
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(0 if code == 0 else 1)
