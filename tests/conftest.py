"""
tests/conftest.py — 测试全局配置

PyQt5 + pyqtgraph 在 Windows 上于解释器关闭阶段偶发 access violation
(纯析构顺序问题, 用例本身全过)。在 pytest 完全收尾(unconfigure, 摘要
已打印)后硬退出避开析构; 退出码沿用 pytest 的判定, 失败时为 1。
"""
import os
import sys

import pytest

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
