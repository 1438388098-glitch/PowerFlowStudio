"""
undocmds.py — 撤销/重做命令

采用整网快照粒度: 每次增删元件/建立连线前后各存一份拓扑 JSON,
undo/redo 直接恢复快照并重建画布。相比逐对象 QUndoCommand:
- 正确性容易保证(重建路径 _apply_network 已被测试覆盖)
- 实现量小, 不侵入 canvas.py 的交互代码
代价是拖动位置不属于快照(变压器/阻抗本就不存坐标), v1 可接受。
"""
from __future__ import annotations

from PyQt5.QtWidgets import QUndoCommand


class SnapshotCommand(QUndoCommand):
    """一次增/删/连线操作的前后快照"""

    def __init__(self, mainwindow, before: dict, after: dict, label: str):
        super().__init__(label)
        self._mw = mainwindow
        self._before = before
        self._after = after
        self._first_redo = True   # push() 会立刻调一次 redo, 跳过

    def undo(self):
        self._mw._restore_snapshot(self._before)

    def redo(self):
        if self._first_redo:
            self._first_redo = False
            return
        self._mw._restore_snapshot(self._after)
