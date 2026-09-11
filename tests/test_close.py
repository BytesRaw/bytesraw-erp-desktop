"""Closing the window, and closing it *now*.

The close button used to leave the window on screen while the teardown ran
behind it, so the app looked hung for as long as an RPC call in flight, a page
still holding a web view and Chromium's flush of its cookie jar took between
them - and the queued full-screen correction could put the window back up while
it was on its way down, so one click was not always enough.

Every one of those is an ordering rule with no visible symptom of its own,
which is what these pin.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QWidget

from bytesraw_erp.data import account_store as store_module
from bytesraw_erp.data.account_store import AccountStore
from bytesraw_erp.services import tasks
from bytesraw_erp.ui.app_context import AppContext
from bytesraw_erp.ui.main_window import MainWindow
from bytesraw_erp.ui.theme import ThemeController


@pytest.fixture(autouse=True)
def _restore_task_gate() -> None:
    """``tasks.shutdown`` latches a module global; put it back for the next test."""
    yield
    tasks._shutting_down = False
    tasks._in_flight.clear()


@pytest.fixture(autouse=True)
def fake_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeKeyring:
        @staticmethod
        def get_password(service: str, key: str) -> str | None:
            return None

        @staticmethod
        def set_password(service: str, key: str, password: str) -> None:
            pass

        @staticmethod
        def delete_password(service: str, key: str) -> None:
            pass

    monkeypatch.setattr(store_module, "keyring", _FakeKeyring)


@pytest.fixture
def context(tmp_path: Path, qtbot) -> AppContext:
    instance = AppContext(ThemeController(QApplication.instance()))
    instance.store = AccountStore(tmp_path / "accounts.json")
    instance.store.load()
    return instance


@pytest.fixture
def window(context: AppContext, monkeypatch: pytest.MonkeyPatch, qtbot) -> MainWindow:
    # The real `quit()` would end the whole test session's application.
    monkeypatch.setattr(QApplication, "quit", lambda _self: None)
    widget = MainWindow(context)
    qtbot.addWidget(widget)
    return widget


class _FakePage(QWidget):
    """A page with the release hook, standing in for the Odoo page."""

    def __init__(self, log: list[str]) -> None:
        super().__init__()
        self._log = log

    def release(self) -> None:
        self._log.append("page")


# -- the teardown order ------------------------------------------------------


def test_the_window_is_gone_before_the_teardown_runs(
    window: MainWindow, context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The frame the user waits for is the window disappearing, not the cleanup."""
    visible_during_shutdown: list[bool] = []
    original = context.shutdown

    def record() -> None:
        visible_during_shutdown.append(window.isVisible())
        original()

    monkeypatch.setattr(context, "shutdown", record)
    window.show()

    window.close()

    assert visible_during_shutdown == [False], "the teardown ran with the window up"


def test_pages_release_before_the_profiles_are_freed(
    window: MainWindow, context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A profile freed while a page still holds it takes the process with it."""
    order: list[str] = []
    window._stack.addWidget(_FakePage(order))
    monkeypatch.setattr(context.profiles, "shutdown", lambda: order.append("profiles"))

    window.close()

    assert order == ["page", "profiles"]


def test_a_page_that_fails_to_release_does_not_trap_the_user(
    window: MainWindow,
) -> None:
    """Nothing a page does on the way out is worth keeping the app open for."""

    class _Broken(_FakePage):
        def release(self) -> None:
            raise RuntimeError("something let go badly")

    window._stack.addWidget(_Broken([]))
    window.show()

    window.close()

    assert not window.isVisible()


# -- the full-screen guard ---------------------------------------------------


def test_the_full_screen_guard_does_not_resurrect_a_closing_window(
    window: MainWindow,
) -> None:
    """The correction is queued, so it can outlive the click that closed the window.

    Without the guard ``showFullScreen`` puts the window back up, and the close
    has to be asked for twice.
    """
    window.showFullScreen()
    window.close()

    window._enforce_full_screen()  # what the queued timer would have run

    assert not window.isVisible()


def test_a_windowed_launch_is_never_forced_back_to_full_screen(
    context: AppContext, monkeypatch: pytest.MonkeyPatch, qtbot
) -> None:
    """``--windowed`` hands the size back to the user; enforcement would take it."""
    monkeypatch.setattr(QApplication, "quit", lambda _self: None)
    context.windowed = True
    widget = MainWindow(context)
    qtbot.addWidget(widget)
    widget.show()

    widget.changeEvent(QEvent(QEvent.Type.WindowStateChange))
    widget._enforce_full_screen()

    assert not widget.isFullScreen()


# -- background work ---------------------------------------------------------


def test_closing_stops_accepting_background_work(window: MainWindow) -> None:
    """Qt waits on its global thread pool at process exit.

    A job accepted after the window has gone is time the user spends looking at
    nothing, so the gate closes with the window rather than with the process.
    """
    window.close()

    received: list[object] = []
    tasks.run_async(lambda: "late", on_success=received.append)
    time.sleep(0.2)
    QApplication.processEvents()

    assert received == [], "a callback was wired up after the app was told to close"


def test_a_cancelled_task_delivers_nothing(qtbot) -> None:
    """Work already on a worker thread cannot be stopped - but nobody has to listen."""
    received: list[object] = []
    task = tasks.Task(lambda: "value")
    task.succeeded.connect(received.append)

    task.cancel()
    task.run()
    QApplication.processEvents()

    assert received == []
