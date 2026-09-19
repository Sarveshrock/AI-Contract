"""Background execution on QThreadPool. Callbacks are always delivered on the UI thread."""
from __future__ import annotations

import inspect
import itertools
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal, pyqtSlot

from app.core.errors import ContractLensError
from app.core.logging import get_logger

log = get_logger(__name__)
_ids = itertools.count(1)


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def __call__(self) -> bool:
        return self.cancelled


@dataclass
class TaskContext:
    """Passed to task functions that accept an argument."""

    task_id: int
    cancel: CancelToken
    _progress: Callable[[str, float, str], None]

    def progress(self, stage: str, fraction: float, message: str = "") -> None:
        self._progress(stage, fraction, message)

    def cancelled(self) -> bool:
        return self.cancel.cancelled


class _Signals(QObject):
    result = pyqtSignal(int, object)
    error = pyqtSignal(int, object)
    progress = pyqtSignal(int, str, float, str)
    event = pyqtSignal(int, str, object)
    finished = pyqtSignal(int)


class _Worker(QRunnable):
    def __init__(self, task_id: int, fn: Callable[..., Any], signals: _Signals, cancel: CancelToken, wants_ctx: bool) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._id, self._fn, self._sig, self._cancel, self._wants = task_id, fn, signals, cancel, wants_ctx

    def run(self) -> None:
        try:
            ctx = TaskContext(self._id, self._cancel, lambda stage, frac, msg: self._sig.progress.emit(self._id, stage, frac, msg))
            value = self._fn(ctx) if self._wants else self._fn()
            self._sig.result.emit(self._id, value)
        except BaseException as exc:  # noqa: BLE001 - delivered to the UI thread
            if not isinstance(exc, ContractLensError):
                log.error("background task failed: %s\n%s", type(exc).__name__, traceback.format_exc())
            self._sig.error.emit(self._id, exc)
        finally:
            self._sig.finished.emit(self._id)


@dataclass
class _Task:
    id: int
    name: str
    on_result: Callable[[Any], None] | None
    on_error: Callable[[BaseException], None] | None
    on_progress: Callable[[str, float, str], None] | None
    cancel: CancelToken
    signals: _Signals
    meta: dict[str, Any] = field(default_factory=dict)


class TaskRunner(QObject):
    """Submit blocking functions; results/errors/progress are delivered to callbacks on the UI thread."""

    busyChanged = pyqtSignal(int)  # number of active tasks

    def __init__(self, parent: QObject | None = None, max_threads: int = 4) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max_threads)
        self._tasks: dict[int, _Task] = {}

    @property
    def active(self) -> int:
        return len(self._tasks)

    def active_names(self) -> list[str]:
        return [t.name for t in self._tasks.values()]

    def submit(self, fn: Callable[..., Any], *, on_result: Callable[[Any], None] | None = None, on_error: Callable[[BaseException], None] | None = None,
               on_progress: Callable[[str, float, str], None] | None = None, name: str = "task") -> CancelToken:
        wants_ctx = len(inspect.signature(fn).parameters) >= 1
        tid = next(_ids)
        signals = _Signals()
        cancel = CancelToken()
        task = _Task(tid, name, on_result, on_error, on_progress, cancel, signals)
        self._tasks[tid] = task
        # bound-method receivers living in the UI thread => queued delivery
        signals.result.connect(self._on_result)
        signals.error.connect(self._on_error)
        signals.progress.connect(self._on_progress)
        signals.finished.connect(self._on_finished)
        self.busyChanged.emit(len(self._tasks))
        self._pool.start(_Worker(tid, fn, signals, cancel, wants_ctx))
        return cancel

    @pyqtSlot(int, object)
    def _on_result(self, tid: int, value: Any) -> None:
        t = self._tasks.get(tid)
        if t and t.on_result:
            self._safe(t.on_result, value)

    @pyqtSlot(int, object)
    def _on_error(self, tid: int, exc: object) -> None:
        t = self._tasks.get(tid)
        if t and t.on_error:
            self._safe(t.on_error, exc)
        elif t:
            log.warning("task %s failed without an error handler: %s", t.name, type(exc).__name__)

    @pyqtSlot(int, str, float, str)
    def _on_progress(self, tid: int, stage: str, frac: float, msg: str) -> None:
        t = self._tasks.get(tid)
        if t and t.on_progress:
            self._safe(t.on_progress, stage, frac, msg)

    @pyqtSlot(int)
    def _on_finished(self, tid: int) -> None:
        self._tasks.pop(tid, None)
        self.busyChanged.emit(len(self._tasks))

    @staticmethod
    def _safe(cb: Callable[..., Any], *args: Any) -> None:
        try:
            cb(*args)
        except Exception:  # noqa: BLE001 - a UI callback bug must not kill the event loop
            log.exception("UI callback raised")

    def wait_idle(self, timeout_ms: int = 30000) -> bool:
        return self._pool.waitForDone(timeout_ms)
