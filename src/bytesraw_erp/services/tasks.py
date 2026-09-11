"""Run blocking work off the GUI thread.

Every Odoo call is synchronous HTTP. Doing it on the GUI thread freezes the
window, so pages submit a callable to :func:`run_async` and get the result back
as a Qt signal, delivered on the GUI thread.
"""

from __future__ import annotations

import contextlib
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
        self._cancelled = False
        #: Exactly the signals :meth:`connect_callbacks` wired up. Recorded
        #: because PySide has no reliable way to ask a signal whether anything
        #: is listening, and disconnecting one that nobody connected warns.
        self._connected: list[Signal] = []

    def connect_callbacks(
        self,
        on_success: Callable[[Any], None] | None,
        on_error: Callable[[Exception], None] | None,
    ) -> None:
        """Wire up the caller's callbacks, remembering which were wired."""
        for signal, callback in (
            (self._signals.succeeded, on_success),
            (self._signals.failed, on_error),
        ):
            if callback is None:
                continue
            signal.connect(callback)
            self._connected.append(signal)

    def cancel(self) -> None:
        """Drop this task's callbacks without waiting for its worker.

        The work itself cannot be interrupted - it is blocking HTTP on a pool
        thread - but nothing has to listen to the answer. Letting go of the
        callbacks is what makes closing the window immediate rather than
        something that waits on the network, and it is also what stops a result
        arriving late from re-entering a page that is being torn down.
        """
        self._cancelled = True
        while self._connected:
            self._connected.pop().disconnect()

    @property
    def succeeded(self) -> Signal:
        return self._signals.succeeded

    @property
    def failed(self) -> Signal:
        return self._signals.failed

    @property
    def finished(self) -> Signal:
        return self._signals.finished

    def _emit(self, signal: Signal, *args: Any) -> None:
        """Emit unless the receiving side has already been torn down.

        A worker can still be inside :meth:`run` when the process starts to
        leave - an RPC call has up to ``RPC_TIMEOUT`` to answer, and the close
        path deliberately does not wait for it. By then the interpreter may
        have collected the ``_TaskSignals`` this task owns, and ``emit`` raises
        "Signal source has been deleted". Measured on a real exit; there is
        nothing listening at that point by definition, so it is not news.
        """
        with contextlib.suppress(RuntimeError):
            signal.emit(*args)

    def run(self) -> None:
        if self._cancelled:
            # Queued while the app was running, reached after it stopped.
            # `finished` still fires, so run_async lets go of this task rather
            # than holding it for a call that will never be made.
            self._emit(self._signals.finished)
            return
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:
            _log.exception("Background task %s failed", getattr(self._fn, "__name__", self._fn))
            self._emit(self._signals.failed, exc)
        else:
            self._emit(self._signals.succeeded, result)
        finally:
            self._emit(self._signals.finished)


#: Strong references to in-flight tasks. Without this the only reference to a
#: submitted Task is Qt's, which drops as soon as the worker returns - taking
#: the not-yet-delivered queued signals with it.
_in_flight: set[Task] = set()
_in_flight_lock = Lock()
#: Set once the window is closing. Qt's global thread pool is waited on at
#: process exit, so a job queued after that point is a job the user waits for
#: while looking at a window that has already gone.
_shutting_down = False


def _release(task: Task) -> None:
    with _in_flight_lock:
        _in_flight.discard(task)


def shutdown() -> None:
    """Stop accepting work, and let go of what is already running.

    Called from the close path. ``clear()`` discards runnables that are still
    queued; a job already on a worker thread cannot be cancelled, but every
    call this app makes carries ``RPC_TIMEOUT``, so it is bounded. What
    :meth:`Task.cancel` achieves is that nobody waits for it: the callbacks go,
    so a result arriving late cannot re-enter a page that is being torn down.

    The references in ``_in_flight`` are deliberately **not** cleared. Doing so
    drops the last Python reference to a task whose worker is still inside
    ``run()``, Python collects its ``_TaskSignals``, and the worker's next
    ``emit()`` raises "Signal source has been deleted" - measured, on the way
    out of a real session. They cost nothing to keep: the process is leaving,
    and each one releases itself through ``finished`` if its worker gets there
    first.
    """
    global _shutting_down
    _shutting_down = True
    QThreadPool.globalInstance().clear()
    with _in_flight_lock:
        for task in list(_in_flight):
            task.cancel()


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
    if _shutting_down:
        _log.debug("Refusing %s: the app is closing", getattr(fn, "__name__", fn))
        return task
    task.connect_callbacks(on_success, on_error)
    # Connected last so it is delivered after succeeded/failed: queued signals
    # arrive in emission order, so the task cannot be released early.
    task.finished.connect(lambda: _release(task))

    with _in_flight_lock:
        _in_flight.add(task)
    QThreadPool.globalInstance().start(task)
    return task
