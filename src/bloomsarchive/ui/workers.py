"""Run blocking API calls on QThreadPool and get results back on the UI thread."""
from __future__ import annotations

import logging
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

log = logging.getLogger(__name__)

_live: set["_Signals"] = set()   # keep signal objects alive until delivered


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(object)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any], signals: _Signals):
        super().__init__()
        self.fn = fn
        self.signals = signals
        self.cancelled = False

    def run(self) -> None:
        try:
            result = self.fn()
        except Exception as e:  # delivered to on_error
            log.debug("worker failed: %s", traceback.format_exc())
            if not self.cancelled:
                self.signals.failed.emit(e)
            return
        if not self.cancelled:
            self.signals.done.emit(result)


class Handle:
    def __init__(self, task: _Task):
        self._task = task

    def cancel(self) -> None:
        """Results of a cancelled task are dropped (the call itself still finishes)."""
        self._task.cancelled = True
        _live.discard(self._task.signals)


def run_async(fn: Callable[[], Any], on_done: Callable[[Any], None] | None = None,
              on_error: Callable[[Exception], None] | None = None) -> Handle:
    # The QObject is created on the calling (UI) thread, so emits from the
    # worker are queued and the callbacks run on the UI thread.
    signals = _Signals()
    _live.add(signals)

    def finish(cb, value):
        _live.discard(signals)
        if cb:
            cb(value)

    signals.done.connect(lambda v: finish(on_done, v))
    signals.failed.connect(lambda e: finish(on_error, e))
    task = _Task(fn, signals)
    QThreadPool.globalInstance().start(task)
    return Handle(task)
