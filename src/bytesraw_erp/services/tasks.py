"""Run blocking work off the GUI thread.

Every Odoo call is synchronous HTTP. Doing it on the GUI thread freezes the
window, so pages submit a callable to :func:`run_async` and get the result back
as a Qt signal, delivered on the GUI thread.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from threading import Lock
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

_log = logging.getLogger(__name__)


class _TaskSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(Exception)
    finished = Signal()


class Task(QRunnable):
    """A one-shot background job.

    Connect to :attr:`succeeded` / :attr:`failed` before handing it to
    :func:`run_async`. Exactly one of the two fires, then :attr:`finished`.
    """

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._signals = _TaskSignals()
        # Qt must NOT delete this runnable when run() returns. Its results reach
        # the GUI thread through queued signal connections, and a queued signal
        # is discarded if its sender dies before the receiving thread gets round
        # to the event. With auto-delete on, the C++ runnable - and the
        # _TaskSignals it owns - is destroyed the instant run() returns, so
        # every callback was silently dropped. run_async() owns the lifetime
        # instead, releasing the task only once `finished` has been delivered.
        self.setAutoDelete(False)

    @property
    def succeeded(self) -> Signal:
        return self._signals.succeeded

    @property
    def failed(self) -> Signal:
        return self._signals.failed

    @property
    def finished(self) -> Signal:
        return self._signals.finished

    def run(self) -> None:
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:
            _log.exception("Background task %s failed", getattr(self._fn, "__name__", self._fn))
            self._signals.failed.emit(exc)
        else:
            self._signals.succeeded.emit(result)
        finally:
            self._signals.finished.emit()


#: Strong references to in-flight tasks. Without this the only reference to a
#: submitted Task is Qt's, which drops as soon as the worker returns - taking
#: the not-yet-delivered queued signals with it.
_in_flight: set[Task] = set()
_in_flight_lock = Lock()


def _release(task: Task) -> None:
    with _in_flight_lock:
        _in_flight.discard(task)


def run_async(
    fn: Callable[..., Any],
    *args: Any,
    on_success: Callable[[Any], None] | None = None,
    on_error: Callable[[Exception], None] | None = None,
    **kwargs: Any,
) -> Task:
    """Submit ``fn`` to the global thread pool and wire up its callbacks.

    The returned task may be ignored; its lifetime is managed here until its
    callbacks have been delivered on the GUI thread.
    """
    task = Task(fn, *args, **kwargs)
    if on_success is not None:
        task.succeeded.connect(on_success)
    if on_error is not None:
        task.failed.connect(on_error)
    # Connected last so it is delivered after succeeded/failed: queued signals
    # arrive in emission order, so the task cannot be released early.
    task.finished.connect(lambda: _release(task))

    with _in_flight_lock:
        _in_flight.add(task)
    QThreadPool.globalInstance().start(task)
    return task
