"""The caption buttons the app draws for itself.

The window runs full screen with no title bar, so these two are the only way
out of the application. Both failure modes are silent - a button that resolves
the wrong window does nothing visible, and an icon left stroked in the previous
theme's colour is invisible rather than wrong - so both are pinned here.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow, QToolButton, QVBoxLayout, QWidget

from bytesraw_erp.ui.theme import DARK, LIGHT
from bytesraw_erp.ui.widgets.window_controls import WindowControls


@pytest.fixture
def host(qtbot) -> QMainWindow:
    """A window with the controls buried a layout deep, as in the app bar."""
    window = QMainWindow()
    central = QWidget()
    layout = QVBoxLayout(central)
    controls = WindowControls()
    layout.addWidget(controls)
    window.setCentralWidget(central)
    window.controls = controls  # type: ignore[attr-defined]
    qtbot.addWidget(window)
    return window


def _button(controls: WindowControls, object_name: str) -> QToolButton:
    button = controls.findChild(QToolButton, object_name)
    assert button is not None, f"no {object_name}"
    return button


def test_minimise_acts_on_the_window_not_the_widget(host: QMainWindow) -> None:
    """``window()`` is resolved at click time, through whatever nests it."""
    host.showFullScreen()
    _button(host.controls, "MinimizeButton").click()  # type: ignore[attr-defined]
    assert host.windowState() & Qt.WindowState.WindowMinimized


def test_close_closes_the_window(host: QMainWindow) -> None:
    host.show()
    _button(host.controls, "CloseButton").click()  # type: ignore[attr-defined]
    assert not host.isVisible()


def test_both_glyphs_are_re_stroked_on_a_theme_change(host: QMainWindow) -> None:
    """A QIcon is a finished bitmap and will not follow the stylesheet."""
    controls: WindowControls = host.controls  # type: ignore[attr-defined]
    controls.apply_theme(LIGHT)
    before = {button.icon().cacheKey() for button, _ in controls._icons}
    controls.apply_theme(DARK)
    after = {button.icon().cacheKey() for button, _ in controls._icons}
    assert before != after
    assert all(not button.icon().isNull() for button, _ in controls._icons)


def test_there_is_no_maximise_button(host: QMainWindow) -> None:
    """The full-screen default has exactly one size; a restore control would lie.

    The toggle below is a different control and a different promise, and it is
    not built at all unless the window genuinely has two sizes to offer.
    """
    controls: WindowControls = host.controls  # type: ignore[attr-defined]
    assert len(controls.findChildren(QToolButton)) == 2
    assert controls.findChild(QToolButton, "FullScreenButton") is None


# -- the full-screen toggle, for a windowed launch ---------------------------


@pytest.fixture
def windowed_host(qtbot) -> QMainWindow:
    """A window launched with ``--windowed``, so the toggle exists."""
    window = QMainWindow()
    central = QWidget()
    layout = QVBoxLayout(central)
    controls = WindowControls(allow_full_screen=True)
    layout.addWidget(controls)
    window.setCentralWidget(central)
    window.controls = controls  # type: ignore[attr-defined]
    qtbot.addWidget(window)
    return window


def test_the_toggle_takes_the_window_both_ways(windowed_host: QMainWindow) -> None:
    controls: WindowControls = windowed_host.controls  # type: ignore[attr-defined]
    windowed_host.show()
    button = _button(controls, "FullScreenButton")

    button.click()
    assert windowed_host.isFullScreen()

    button.click()
    assert not windowed_host.isFullScreen()


def test_the_glyph_offers_the_state_the_window_is_not_in(
    windowed_host: QMainWindow,
) -> None:
    """A bracket pointing outwards on a window already full screen is a lie."""
    controls: WindowControls = windowed_host.controls  # type: ignore[attr-defined]
    windowed_host.show()
    button = _button(controls, "FullScreenButton")
    before = button.icon().cacheKey()

    button.click()

    assert button.icon().cacheKey() != before
    assert "Leave full screen" in button.toolTip()


def test_the_toggles_glyph_survives_a_theme_change(windowed_host: QMainWindow) -> None:
    """It is re-rendered from a name kept in ``_icons``, which the swap updates."""
    controls: WindowControls = windowed_host.controls  # type: ignore[attr-defined]
    windowed_host.show()
    _button(controls, "FullScreenButton").click()  # now showing "exit"

    controls.apply_theme(DARK)

    names = {name for button, name in controls._icons}
    assert "exit-full-screen" in names
    assert "full-screen" not in names


def test_syncing_a_window_that_cannot_go_full_screen_is_a_no_op(
    host: QMainWindow,
) -> None:
    """Every window state change calls this; most windows have no toggle."""
    controls: WindowControls = host.controls  # type: ignore[attr-defined]
    controls.sync_full_screen()
