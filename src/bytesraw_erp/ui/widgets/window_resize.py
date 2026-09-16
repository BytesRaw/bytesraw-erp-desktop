"""Give a frameless window the resize borders Windows stopped drawing for it.

The shell's app bar *is* its title bar, so the window carries
``FramelessWindowHint`` and Windows draws it no caption - and no frame either.
The caption's buttons and its grip are rebuilt elsewhere
(:mod:`.window_controls`, :mod:`.window_drag`); what is left is the eight
pixels of border a user grabs to resize, which is what this rebuilds.

Where the border lives, and why it is not over the page
------------------------------------------------------
Eight thin widgets, parented to the window and sitting in a margin the window
gives itself while it is loose. They do **not** overlay the page: the window's
own ``contentsMargins`` inset the central widget by the border's width, so the
strips cover nothing but the window's background.

Laying them over the content instead was the obvious first shape and is wrong
twice over. The right-hand edge of an Odoo screen is its scroll bar, and a strip
over it would eat the outer half of the one control a till user drags most; and
the surface underneath is QtWebEngine's, which a sibling widget that paints
nothing cannot be relied on to show through. The margin costs a few pixels of
window background and answers both - and on a frameless window with no drop
shadow it is also the only thing giving the app a visible edge against the
desktop behind it.

The border exists only while the window is loose. Maximised and full screen have
no resizing to offer, so the margins go to zero and the strips hide - which is
why the shipped full-screen till never sees any of this.

``startSystemResize()`` hands the drag to the window manager, for the same
reason :mod:`.window_drag` uses ``startSystemMove()``: it is what keeps the
native edge-snapping and the live geometry feedback. The manual fallback is for
a window with no handle yet, and is what makes the geometry testable at all.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QWidget

#: Thickness of the grab border, and of the margin that makes room for it.
#: Windows' own is between four and eight pixels depending on the theme; six is
#: comfortable with a mouse without being a visible slab on a till screen.
BORDER = 6
#: How far along each edge a corner reaches, so a diagonal resize is a target
#: someone can actually hit.
_CORNER = 16

_EDGES = Qt.Edge
_CURSORS = {
    _EDGES.LeftEdge: Qt.CursorShape.SizeHorCursor,
    _EDGES.RightEdge: Qt.CursorShape.SizeHorCursor,
    _EDGES.TopEdge: Qt.CursorShape.SizeVerCursor,
    _EDGES.BottomEdge: Qt.CursorShape.SizeVerCursor,
    _EDGES.LeftEdge | _EDGES.TopEdge: Qt.CursorShape.SizeFDiagCursor,
    _EDGES.RightEdge | _EDGES.BottomEdge: Qt.CursorShape.SizeFDiagCursor,
    _EDGES.RightEdge | _EDGES.TopEdge: Qt.CursorShape.SizeBDiagCursor,
    _EDGES.LeftEdge | _EDGES.BottomEdge: Qt.CursorShape.SizeBDiagCursor,
}


class _Grip(QWidget):
    """One edge or corner of the border."""

    def __init__(self, window: QWidget, edges: Qt.Edge) -> None:
        super().__init__(window)
        self.setObjectName("ResizeGrip")
        self._edges = edges
        self.setCursor(_CURSORS[edges])
        #: Fallback state, used only where ``startSystemResize`` is unavailable.
        self._pressed_at: QPoint | None = None
        self._origin: QRect | None = None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() is not Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        window = self.window()
        handle = window.windowHandle()
        if handle is not None and handle.startSystemResize(self._edges):
            # The window manager owns the pointer now and sends no release, so
            # nothing is armed - the same bargain ``startSystemMove`` strikes.
            event.accept()
            return
        self._pressed_at = event.globalPosition().toPoint()
        self._origin = window.geometry()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._pressed_at is None or self._origin is None:
            super().mouseMoveEvent(event)
            return
        delta = event.globalPosition().toPoint() - self._pressed_at
        geometry = QRect(self._origin)
        if self._edges & _EDGES.LeftEdge:
            geometry.setLeft(geometry.left() + delta.x())
        if self._edges & _EDGES.RightEdge:
            geometry.setRight(geometry.right() + delta.x())
        if self._edges & _EDGES.TopEdge:
            geometry.setTop(geometry.top() + delta.y())
        if self._edges & _EDGES.BottomEdge:
            geometry.setBottom(geometry.bottom() + delta.y())
        self.window().setGeometry(geometry)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._pressed_at = None
        self._origin = None
        super().mouseReleaseEvent(event)


class ResizeFrame(QObject):
    """The eight grips, kept positioned and kept out of the way.

    Not a widget: it owns free-floating children of the window and watches the
    window for the events that move them, so it adds nothing to any layout -
    the same arrangement :class:`~bytesraw_erp.ui.widgets.toast.ToastArea` uses
    over the web view, and for the same reason.
    """

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self._window = window
        #: Guards the re-entry that setting the margins would otherwise cause:
        #: changing them lays the window out again, which is a resize, which
        #: would land back here.
        self._syncing = False
        self._grips = [
            _Grip(window, edges)
            for edges in (
                _EDGES.TopEdge,
                _EDGES.BottomEdge,
                _EDGES.LeftEdge,
                _EDGES.RightEdge,
                _EDGES.LeftEdge | _EDGES.TopEdge,
                _EDGES.RightEdge | _EDGES.TopEdge,
                _EDGES.LeftEdge | _EDGES.BottomEdge,
                _EDGES.RightEdge | _EDGES.BottomEdge,
            )
        ]
        window.installEventFilter(self)
        self.sync()

    # -- watching the window ----------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._window and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.WindowStateChange,
            QEvent.Type.Show,
        ):
            self.sync()
        return False

    @property
    def resizable(self) -> bool:
        """Whether the window currently has a size the border could change."""
        return not (self._window.isMaximized() or self._window.isFullScreen())

    def sync(self) -> None:
        """Show, hide and place the border for the window's current state."""
        if self._syncing:
            return
        self._syncing = True
        try:
            loose = self.resizable
            margin = BORDER if loose else 0
            if self._window.contentsMargins().left() != margin:
                self._window.setContentsMargins(margin, margin, margin, margin)
            for grip in self._grips:
                grip.setVisible(loose)
            if loose:
                self._place()
        finally:
            self._syncing = False

    def _place(self) -> None:
        width = self._window.width()
        height = self._window.height()
        span_h = max(width - 2 * _CORNER, 0)
        span_v = max(height - 2 * _CORNER, 0)
        rects = (
            QRect(_CORNER, 0, span_h, BORDER),                       # top
            QRect(_CORNER, height - BORDER, span_h, BORDER),         # bottom
            QRect(0, _CORNER, BORDER, span_v),                       # left
            QRect(width - BORDER, _CORNER, BORDER, span_v),          # right
            QRect(0, 0, _CORNER, _CORNER),                           # top-left
            QRect(width - _CORNER, 0, _CORNER, _CORNER),             # top-right
            QRect(0, height - _CORNER, _CORNER, _CORNER),            # bottom-left
            QRect(width - _CORNER, height - _CORNER, _CORNER, _CORNER),
        )
        for grip, rect in zip(self._grips, rects, strict=True):
            grip.setGeometry(rect)
            grip.raise_()
