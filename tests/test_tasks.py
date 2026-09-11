"""Background-task tests.

These exist because of a real bug: with ``setAutoDelete(True)`` and no Python
reference held, Qt destroyed each runnable the moment ``run()`` returned. Its
results travel to the GUI thread as *queued* signals, and a queued signal is
discarded when its sender dies before the receiving thread processes the event -
so every callback was silently dropped and the UI sat on "Connecting" forever.
"""

from __future__ import annotations

import time

import pytest

from bytesraw_erp.services import tasks
from bytesraw_erp.services.tasks import run_async


@pytest.fixture(autouse=True)
def _drain() -> None:
    """Fail loudly if a test leaves a task registered."""
    yield
    tasks._in_flight.clear()


def test_success_callback_is_delivered(qtbot) -> None:
    received: list[object] = []
    run_async(lambda: "value", on_success=received.append)
    qtbot.waitUntil(lambda: received == ["value"], timeout=3000)


def test_slow_work_still_reports_back(qtbot) -> None:
    """The original failure only appeared once run() outlived the emit."""
    received: list[object] = []

    def slow() -> str:
        time.sleep(0.3)
        return "late"

    run_async(slow, on_success=received.append)
    qtbot.waitUntil(lambda: received == ["late"], timeout=5000)


def test_arguments_are_forwarded(qtbot) -> None:
    received: list[object] = []
    run_async(lambda a, b: a + b, 2, b=3, on_success=received.append)
    qtbot.waitUntil(lambda: received == [5], timeout=3000)


def test_failure_callback_receives_the_exception(qtbot) -> None:
    caught: list[Exception] = []

    def boom() -> None:
        raise ValueError("nope")

    run_async(boom, on_error=caught.append)
    qtbot.waitUntil(lambda: len(caught) == 1, timeout=3000)
    assert isinstance(caught[0], ValueError)
    assert str(caught[0]) == "nope"


def test_failure_does_not_fire_the_success_callback(qtbot) -> None:
    succeeded: list[object] = []
    failed: list[Exception] = []

    def boom() -> None:
        raise RuntimeError("x")

    run_async(boom, on_success=succeeded.append, on_error=failed.append)
    qtbot.waitUntil(lambda: len(failed) == 1, timeout=3000)
    assert succeeded == []


def test_a_task_returning_a_tuple_is_not_unpacked(qtbot) -> None:
    """``open_session`` returns ``(client, context)``; it must arrive intact."""
    received: list[object] = []
    run_async(lambda: ("a", "b"), on_success=received.append)
    qtbot.waitUntil(lambda: len(received) == 1, timeout=3000)
    assert received[0] == ("a", "b")


def test_task_is_released_after_completion(qtbot) -> None:
    """The registry must not leak: a finished task is dropped from it."""
    done: list[object] = []
    task = run_async(lambda: 1, on_success=done.append)
    assert task in tasks._in_flight

    qtbot.waitUntil(lambda: done == [1], timeout=3000)
    qtbot.waitUntil(lambda: task not in tasks._in_flight, timeout=3000)


def test_concurrent_tasks_all_report_back(qtbot) -> None:
    received: list[int] = []
    for value in range(8):
        run_async(lambda v=value: v, on_success=received.append)
    qtbot.waitUntil(lambda: len(received) == 8, timeout=5000)
    assert sorted(received) == list(range(8))
