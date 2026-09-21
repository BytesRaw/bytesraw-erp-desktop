"""One window, however many times the desktop icon is clicked.

Clicking the icon again used to start a second process: a second Chromium, a
second set of web profiles over the same on-disk storage, and two windows
showing the same till. The guard turns the second launch into a request that
the *existing* window come forward.

The window half is tested here too, because the obvious way to un-minimise a
window is the one way that must not be used: ``showNormal()`` is one of the
three calls that record what size the window is supposed to be, so restoring a
till through it would quietly take it out of full screen for the rest of the
session.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from PySide6.QtCore import Qt

from bytesraw_erp.services.single_instance import SingleInstanceGuard


@pytest.fixture
def name() -> str:
    """A pipe of this test's own.

    The real name is derived from the login, so a suite that used it would
    find the developer's running copy of the app and fail depending on whether
    they happened to have it open.
    """
    return f"BytesrawERP-test-{uuid.uuid4().hex}"


@pytest.fixture
def guards() -> Iterator[list[SingleInstanceGuard]]:
    built: list[SingleInstanceGuard] = []
    yield built
    for guard in built:
        guard.close()


def _guard(name: str, guards: list[SingleInstanceGuard]) -> SingleInstanceGuard:
    guard = SingleInstanceGuard(name=name)
    guards.append(guard)
    return guard


def test_the_first_launch_claims_the_name(
    name: str, guards: list[SingleInstanceGuard], qapp
) -> None:
    assert _guard(name, guards).claim() is True


def test_a_second_launch_is_told_to_stand_down(
    name: str, guards: list[SingleInstanceGuard], qapp
) -> None:
    """``False`` is what makes ``app.run`` return before building anything."""
    assert _guard(name, guards).claim() is True

    assert _guard(name, guards).claim() is False


def test_the_second_launch_asks_the_first_to_come_forward(
    name: str, guards: list[SingleInstanceGuard], qtbot
) -> None:
    first = _guard(name, guards)
    assert first.claim() is True

    with qtbot.waitSignal(first.activation_requested, timeout=5000):
        _guard(name, guards).claim()


def test_the_name_is_free_again_once_the_app_closes(
    name: str, guards: list[SingleInstanceGuard], qapp
) -> None:
    """Otherwise the launch after a quit would raise a window that is gone."""
    first = _guard(name, guards)
    assert first.claim() is True
    first.close()

    assert _guard(name, guards).claim() is True


def test_presenting_the_window_does_not_cost_a_till_its_full_screen(qtbot) -> None:
    """The trap: ``showNormal()`` would lower the intent flag on the way past.

    A till is full screen on purpose and the correction in ``changeEvent``
    reads that flag, so un-minimising through ``showNormal`` would leave the
    window loose for the rest of the session - and no longer corrected back.
    """
    from bytesraw_erp.ui.main_window import MainWindow

    window = MainWindow.__new__(MainWindow)  # no context, no pages, no web view
    window._closing = False
    window._full_screen_intended = True
    shown: list[str] = []
    window.showFullScreen = lambda: shown.append("full screen")  # type: ignore[method-assign]
    window.showNormal = lambda: shown.append("normal")  # type: ignore[method-assign]
    window.show = lambda: shown.append("show")  # type: ignore[method-assign]
    window.raise_ = lambda: None  # type: ignore[method-assign]
    window.activateWindow = lambda: None  # type: ignore[method-assign]

    MainWindow.present(window)

    assert shown == ["full screen"]
    assert window._full_screen_intended is True


def test_presenting_a_minimised_window_leaves_its_other_state_alone(qtbot) -> None:
    """A maximised window comes back maximised, not merely visible."""
    from bytesraw_erp.ui.main_window import MainWindow

    window = MainWindow.__new__(MainWindow)
    window._closing = False
    window._full_screen_intended = False
    states: list[Qt.WindowState] = []
    window.windowState = lambda: (  # type: ignore[method-assign]
        Qt.WindowState.WindowMinimized | Qt.WindowState.WindowMaximized
    )
    window.setWindowState = states.append  # type: ignore[method-assign]
    window.show = lambda: None  # type: ignore[method-assign]
    window.raise_ = lambda: None  # type: ignore[method-assign]
    window.activateWindow = lambda: None  # type: ignore[method-assign]

    MainWindow.present(window)

    assert states == [Qt.WindowState.WindowMaximized]


def test_a_window_on_its_way_out_is_not_brought_back(qtbot) -> None:
    """A launch racing a close must not resurrect the window being torn down."""
    from bytesraw_erp.ui.main_window import MainWindow

    window = MainWindow.__new__(MainWindow)
    window._closing = True
    window._full_screen_intended = True
    window.showFullScreen = lambda: pytest.fail("raised a closing window")  # type: ignore[method-assign]

    MainWindow.present(window)
