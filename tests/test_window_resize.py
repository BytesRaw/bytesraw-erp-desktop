"""The resize border a frameless window has to draw for itself.

The window carries ``FramelessWindowHint`` so the app bar can be the title bar,
and Windows then draws it no frame at all. Two things about the replacement are
easy to get wrong and silent when they are:

* a border laid *over* the page rather than in a margin eats the outer pixels of
  whatever is under it - on an Odoo screen, the scroll bar - and cannot be
  relied on to show through QtWebEngine's surface;
* a border left in place while the window is maximised or full screen is eight
  pixels of dead zone around a screen that has no resizing to offer.

The drag itself is ``startSystemResize``, which hands the window to the window
manager and cannot be observed from inside the process, so the geometry is
exercised through the fallback - reached the same way the real platforms reach
it, with a window that has no handle yet.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget

from bytesraw_erp.ui.widgets.window_resize import BORDER, ResizeFrame, _Grip


def _grip(frame: ResizeFrame, edges: Qt.Edge) -> _Grip:
    for grip in frame._grips:
        if grip._edges == edges:
            return grip
    raise AssertionError(f"no grip for {edges}")


def _send(grip: _Grip, kind: QEvent.Type, where: QPoint, *, held: bool) -> None:
    buttons = Qt.MouseButton.LeftButton if held else Qt.MouseButton.NoButton
    QApplication.sendEvent(
        grip,
        QMouseEvent(
            kind,
            QPointF(grip.mapFromGlobal(where)),
            QPointF(where),
            Qt.MouseButton.LeftButton,
            buttons,
            Qt.KeyboardModifier.NoModifier,
        ),
    )


@pytest.fixture
def loose(qtbot) -> tuple[QMainWindow, ResizeFrame]:
    """A window that has never been shown, so the fallback path is the one taken."""
    window = QMainWindow()
    window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
    window.setCentralWidget(QWidget())
    window.setGeometry(200, 150, 800, 500)
    frame = ResizeFrame(window)
    qtbot.addWidget(window)
    return window, frame


def test_the_border_sits_in_a_margin_not_over_the_page(
    loose: tuple[QMainWindow, ResizeFrame],
) -> None:
    """Otherwise its right-hand strip covers the outer half of Odoo's scroll bar."""
    window, _frame = loose
    window.show()

    assert window.contentsMargins().left() == BORDER
    central = window.centralWidget()
    assert central.geometry().topLeft() == QPoint(BORDER, BORDER)


def test_every_edge_and_corner_is_grabbable(
    loose: tuple[QMainWindow, ResizeFrame],
) -> None:
    window, frame = loose
    window.show()

    assert len(frame._grips) == 8
    assert all(grip.isVisible() for grip in frame._grips)
    assert {grip.cursor().shape() for grip in frame._grips} == {
        Qt.CursorShape.SizeHorCursor,
        Qt.CursorShape.SizeVerCursor,
        Qt.CursorShape.SizeFDiagCursor,
        Qt.CursorShape.SizeBDiagCursor,
    }


def test_a_maximised_window_has_no_border(
    loose: tuple[QMainWindow, ResizeFrame],
) -> None:
    """There is no size to change, so the margin and the dead zone both go."""
    window, frame = loose
    window.show()

    window.showMaximized()
    frame.sync()

    assert not frame.resizable
    assert window.contentsMargins().left() == 0
    assert not any(grip.isVisible() for grip in frame._grips)


def test_full_screen_has_no_border_either(
    loose: tuple[QMainWindow, ResizeFrame],
) -> None:
    """Which is the state the shipped till spends its whole life in."""
    window, frame = loose
    window.showFullScreen()
    frame.sync()

    assert not frame.resizable
    assert not any(grip.isVisible() for grip in frame._grips)


def test_the_border_comes_back_on_the_way_down(
    loose: tuple[QMainWindow, ResizeFrame],
) -> None:
    window, frame = loose
    window.showMaximized()
    frame.sync()

    window.showNormal()
    frame.sync()

    assert window.contentsMargins().left() == BORDER
    assert all(grip.isVisible() for grip in frame._grips)


# -- the drag itself ---------------------------------------------------------


def test_the_right_edge_widens_the_window(
    loose: tuple[QMainWindow, ResizeFrame],
) -> None:
    window, frame = loose
    grip = _grip(frame, Qt.Edge.RightEdge)
    before = window.geometry()

    _send(grip, QEvent.Type.MouseButtonPress, QPoint(995, 400), held=True)
    _send(grip, QEvent.Type.MouseMove, QPoint(1055, 400), held=True)

    assert window.geometry().width() == before.width() + 60
    assert window.geometry().left() == before.left(), "the far edge stays put"


def test_the_top_left_corner_moves_two_edges_at_once(
    loose: tuple[QMainWindow, ResizeFrame],
) -> None:
    window, frame = loose
    grip = _grip(frame, Qt.Edge.LeftEdge | Qt.Edge.TopEdge)
    before = window.geometry()

    _send(grip, QEvent.Type.MouseButtonPress, QPoint(205, 155), held=True)
    _send(grip, QEvent.Type.MouseMove, QPoint(225, 175), held=True)

    assert window.geometry().left() == before.left() + 20
    assert window.geometry().top() == before.top() + 20
    assert window.geometry().right() == before.right(), "the far edge stays put"


def test_the_release_disarms_it(loose: tuple[QMainWindow, ResizeFrame]) -> None:
    window, frame = loose
    grip = _grip(frame, Qt.Edge.RightEdge)

    _send(grip, QEvent.Type.MouseButtonPress, QPoint(995, 400), held=True)
    _send(grip, QEvent.Type.MouseButtonRelease, QPoint(995, 400), held=False)
    settled = window.geometry()
    _send(grip, QEvent.Type.MouseMove, QPoint(1200, 400), held=True)

    assert window.geometry() == settled
