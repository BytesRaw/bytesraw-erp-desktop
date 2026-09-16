"""The caption buttons the app draws for itself.

The window has no title bar, so these four are the only way to minimise it,
resize it or leave it. Every failure mode here is silent - a button that
resolves the wrong window does nothing visible, a glyph left stroked in the
previous theme's colour is invisible rather than wrong, and a toggle left
showing the state the window is already in is simply a lie - so each is pinned.
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


def _controls(host: QMainWindow) -> WindowControls:
    return host.controls  # type: ignore[attr-defined,no-any-return]


def _button(controls: WindowControls, object_name: str) -> QToolButton:
    button = controls.findChild(QToolButton, object_name)
    assert button is not None, f"no {object_name}"
    return button


def test_minimise_acts_on_the_window_not_the_widget(host: QMainWindow) -> None:
    """``window()`` is resolved at click time, through whatever nests it."""
    host.showFullScreen()
    _button(_controls(host), "MinimizeButton").click()
    assert host.windowState() & Qt.WindowState.WindowMinimized


def test_close_closes_the_window(host: QMainWindow) -> None:
    host.show()
    _button(_controls(host), "CloseButton").click()
    assert not host.isVisible()


def test_every_glyph_is_re_stroked_on_a_theme_change(host: QMainWindow) -> None:
    """A QIcon is a finished bitmap and will not follow the stylesheet."""
    controls = _controls(host)
    controls.apply_theme(LIGHT)
    before = {button.icon().cacheKey() for button, _ in controls._icons}
    controls.apply_theme(DARK)
    after = {button.icon().cacheKey() for button, _ in controls._icons}
    assert before != after
    assert all(not button.icon().isNull() for button, _ in controls._icons)


def test_all_four_controls_are_built(host: QMainWindow) -> None:
    """The window has three sizes now, so all three size controls deliver.

    Maximise used to be left out because a window pinned full screen for its
    whole life had nothing to restore down to. It does now, and a set that
    offered full screen but not maximise would leave the ordinary Windows
    gesture - and the only size that keeps the taskbar reachable - unavailable.
    """
    controls = _controls(host)
    assert len(controls.findChildren(QToolButton)) == 4
    for name in ("MinimizeButton", "MaximizeButton", "FullScreenButton", "CloseButton"):
        assert controls.findChild(QToolButton, name) is not None, f"no {name}"


# -- the full-screen toggle ---------------------------------------------------


def test_the_toggle_takes_the_window_both_ways(host: QMainWindow) -> None:
    controls = _controls(host)
    host.show()
    button = _button(controls, "FullScreenButton")

    button.click()
    assert host.isFullScreen()

    button.click()
    assert not host.isFullScreen()


def test_the_glyph_offers_the_state_the_window_is_not_in(host: QMainWindow) -> None:
    """A bracket pointing outwards on a window already full screen is a lie."""
    controls = _controls(host)
    host.show()
    button = _button(controls, "FullScreenButton")
    before = button.icon().cacheKey()

    button.click()

    assert button.icon().cacheKey() != before
    assert "Leave full screen" in button.toolTip()


def test_the_toggles_glyph_survives_a_theme_change(host: QMainWindow) -> None:
    """It is re-rendered from a name kept in ``_icons``, which the swap updates."""
    controls = _controls(host)
    host.show()
    _button(controls, "FullScreenButton").click()  # now showing "exit"

    controls.apply_theme(DARK)

    names = {name for button, name in controls._icons}
    assert "exit-full-screen" in names
    assert "full-screen" not in names


# -- maximise and restore -----------------------------------------------------


def test_maximise_and_restore_are_the_same_button(host: QMainWindow) -> None:
    controls = _controls(host)
    host.show()
    button = _button(controls, "MaximizeButton")

    button.click()
    assert host.isMaximized()

    button.click()
    assert not host.isMaximized()


def test_maximise_steps_down_out_of_full_screen(host: QMainWindow) -> None:
    """Full screen is bigger than maximised, so this button comes *down* from it.

    Going the other way - ignoring the click, or jumping all the way to the
    loose size - would make the two controls disagree about what "bigger" is.
    """
    controls = _controls(host)
    host.showFullScreen()

    _button(controls, "MaximizeButton").click()

    assert not host.isFullScreen()
    assert host.isMaximized()


def test_the_maximise_glyph_follows_the_window(host: QMainWindow) -> None:
    """Restore brackets while the window is big, a single pane while it is not."""
    controls = _controls(host)
    host.show()
    button = _button(controls, "MaximizeButton")
    assert "Maximise" in button.toolTip()

    button.click()

    assert "Restore" in button.toolTip()
    assert ("restore" in {name for candidate, name in controls._icons
                          if candidate is button})


def test_syncing_reads_the_window_rather_than_the_last_click(host: QMainWindow) -> None:
    """F11, a double-click on the app bar and the shell all bypass the buttons."""
    controls = _controls(host)
    host.showFullScreen()

    controls.sync_window_state()

    assert "Leave full screen" in _button(controls, "FullScreenButton").toolTip()
    assert "Restore" in _button(controls, "MaximizeButton").toolTip()
