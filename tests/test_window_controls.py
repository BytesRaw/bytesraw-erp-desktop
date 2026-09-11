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
    """The window has exactly one size; a restore control would lie."""
    controls: WindowControls = host.controls  # type: ignore[attr-defined]
    assert len(controls.findChildren(QToolButton)) == 2
