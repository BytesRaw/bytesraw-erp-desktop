"""Picking the window up by the strip its caption buttons sit in.

The shell draws its own caption, so it owes the other half of one: a band you
can drag the window by and double-click to maximise. Both are silent when they
break - a band that never armed the drag simply does nothing under the pointer,
and nothing raises - so both are pinned here.

The move itself is deliberately *not* asserted against the real path. On
Windows ``startSystemMove()`` hands the window to the window manager, which
runs a modal move loop the process cannot observe or drive; what these exercise
is the fallback, reached the same way the real platforms reach it - a window
with no handle yet - plus the arming and the threshold, which are the parts
that decide whether the native call is ever made.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget

from bytesraw_erp.ui.widgets.window_drag import _WindowDragFilter, enable_window_drag


def _send(band: QWidget, kind: QEvent.Type, where: QPoint, *, held: bool) -> QMouseEvent:
    """Deliver a mouse event to ``band`` exactly as Qt would."""
    buttons = Qt.MouseButton.LeftButton if held else Qt.MouseButton.NoButton
    event = QMouseEvent(
        kind,
        QPointF(band.mapFromGlobal(where)),
        QPointF(where),
        Qt.MouseButton.LeftButton,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(band, event)
    return event


@pytest.fixture
def band(qtbot) -> QWidget:
    """A top-level band that has never been shown, so it has no window handle.

    That is what routes the drag through the manual fallback: with a handle,
    ``startSystemMove`` takes over and there is nothing left to measure.
    """
    widget = QWidget()
    widget.resize(600, 40)
    widget.move(100, 100)
    enable_window_drag(widget)
    qtbot.addWidget(widget)
    return widget


def test_a_drag_on_blank_chrome_moves_the_window(band: QWidget) -> None:
    start = band.pos()
    _send(band, QEvent.Type.MouseButtonPress, QPoint(300, 20), held=True)
    _send(band, QEvent.Type.MouseMove, QPoint(380, 65), held=True)

    assert band.pos() == start + QPoint(80, 45)


def test_a_click_is_not_a_drag(band: QWidget) -> None:
    """The move waits for ``startDragDistance``, and the reason is the release.

    ``startSystemMove`` swallows it, so beginning on the press would cost the
    band every click and every double-click it contains - including the ones
    belonging to controls that let a press travel up to it.
    """
    start = band.pos()
    _send(band, QEvent.Type.MouseButtonPress, QPoint(300, 20), held=True)
    _send(band, QEvent.Type.MouseMove, QPoint(301, 21), held=True)

    assert band.pos() == start


def test_the_press_is_taken_so_the_moves_arrive(band: QWidget) -> None:
    """Qt sends the moves after a press only to whoever accepted the press."""
    event = _send(band, QEvent.Type.MouseButtonPress, QPoint(300, 20), held=True)
    assert event.isAccepted()


def test_the_release_disarms_it(band: QWidget) -> None:
    """A move with no button down afterwards must not still be dragging."""
    start = band.pos()
    _send(band, QEvent.Type.MouseButtonPress, QPoint(300, 20), held=True)
    _send(band, QEvent.Type.MouseButtonRelease, QPoint(300, 20), held=False)
    _send(band, QEvent.Type.MouseMove, QPoint(400, 120), held=True)

    assert band.pos() == start


# -- double-click ------------------------------------------------------------


@pytest.fixture
def host(qtbot) -> QMainWindow:
    """A real window, for the gesture that changes its state rather than its place."""
    window = QMainWindow()
    central = QWidget()
    layout = QVBoxLayout(central)
    strip = QWidget()
    strip.setFixedHeight(40)
    enable_window_drag(strip)
    layout.addWidget(strip)
    window.setCentralWidget(central)
    window.strip = strip  # type: ignore[attr-defined]
    qtbot.addWidget(window)
    window.show()
    return window


def test_a_double_click_maximises_and_restores(host: QMainWindow) -> None:
    strip: QWidget = host.strip  # type: ignore[attr-defined]
    where = strip.mapToGlobal(QPoint(20, 20))

    _send(strip, QEvent.Type.MouseButtonDblClick, where, held=True)
    assert host.isMaximized()

    _send(strip, QEvent.Type.MouseButtonDblClick, where, held=True)
    assert not host.isMaximized()


def test_full_screen_arms_no_drag(host: QMainWindow) -> None:
    """There is nowhere to move a window that already covers the screen.

    The press is still taken, so the double-click that leaves full screen
    arrives - but no move is armed, because the window manager would drop the
    window out of full screen to honour one.
    """
    strip: QWidget = host.strip  # type: ignore[attr-defined]
    host.showFullScreen()
    where = strip.mapToGlobal(QPoint(20, 20))

    event = _send(strip, QEvent.Type.MouseButtonPress, where, held=True)

    assert event.isAccepted(), "the double-click still has to reach the band"
    drag = strip.findChildren(_WindowDragFilter)[0]
    assert drag._pressed_at is None
