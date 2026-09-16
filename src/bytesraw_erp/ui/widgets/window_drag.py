"""Make a band of chrome behave like the title bar the window does not have.

The shell draws its own caption buttons because the window has none, and the
other half of a title bar is that you can pick the window up by it. Without
this, a window restored down from full screen can be resized by its frame and
moved by nothing - there is no caption to grab, and the app bar looks exactly
like the strip a user would reach for.

Two behaviours, both the ones Windows gives a real caption:

* **drag to move.** Deferred until the pointer has actually travelled
  ``startDragDistance``, not begun on the press. ``startSystemMove()`` hands
  the window to the window manager, which runs a modal move loop and swallows
  the release - so starting it on the press would cost the band every click and
  every double-click it contains. Waiting for movement keeps a click a click.
* **double-click to maximise or restore**, which is the shortcut most people
  use far more than the button.

Going through ``startSystemMove()`` rather than moving the window ourselves is
what buys the native behaviour around the edges: Aero Snap at the screen edges,
shake-to-minimise, and dragging a maximised window back down to size. The
manual fallback exists for the platforms (and the offscreen test plugin) where
that call returns ``False``, and is a plain move with none of it.

A band installs this on itself and stops thinking about it. Presses on real
controls never arrive: a button or a combo box consumes its own, and anything
that must not be draggable - the user chip, which opens a menu on release -
sets ``WA_NoMousePropagation`` so its presses stay with it. What is left is
blank space and inert labels, which is exactly the surface a title bar is.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget


class _WindowDragFilter(QObject):
    """Turns presses that reach ``band`` into moves of the window holding it."""

    def __init__(self, band: QWidget) -> None:
        super().__init__(band)
        self._band = band
        #: Where the press landed, in screen coordinates, while a drag is
        #: still only a possibility. ``None`` whenever no button is down.
        self._pressed_at: QPoint | None = None
        #: The window's top-left when the press landed, for the fallback path.
        self._origin: QPoint | None = None

    # -- event filter ------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is not self._band:
            return False
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress:
            return self._on_press(event)  # type: ignore[arg-type]
        if kind == QEvent.Type.MouseMove:
            return self._on_move(event)  # type: ignore[arg-type]
        if kind == QEvent.Type.MouseButtonRelease:
            self._pressed_at = None
            self._origin = None
            return False
        if kind == QEvent.Type.MouseButtonDblClick:
            return self._on_double_click(event)  # type: ignore[arg-type]
        return False

    # -- the three gestures ------------------------------------------------

    def _on_press(self, event: QMouseEvent) -> bool:
        """Take the press, so the band is the one that hears the drag.

        Accepting is not optional: Qt delivers the moves and the release that
        follow a press only to the widget that accepted it, and the band's own
        handler ignores mouse events, so without this the filter would see a
        press and then nothing. Taking it costs nothing else - a band has no
        other use for a click, and the double-click still arrives.
        """
        if event.button() is not Qt.MouseButton.LeftButton:
            return False
        window = self._band.window()
        if not window.isFullScreen():
            # In full screen there is nowhere to move it to, so the press is
            # taken for the double-click alone and no drag is armed.
            self._pressed_at = event.globalPosition().toPoint()
            self._origin = window.frameGeometry().topLeft()
        event.accept()
        return True

    def _on_move(self, event: QMouseEvent) -> bool:
        if self._pressed_at is None:
            return False
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            self._pressed_at = None
            return False

        here = event.globalPosition().toPoint()
        if (here - self._pressed_at).manhattanLength() < QApplication.startDragDistance():
            return False

        window = self._band.window()
        handle = window.windowHandle()
        if handle is not None and handle.startSystemMove():
            # The window manager owns the pointer from here, and will not send
            # a release - so the state has to be dropped now rather than on one.
            self._pressed_at = None
            self._origin = None
            return True

        if self._origin is None or window.isMaximized():
            # Nothing sensible to do by hand: un-maximising and re-anchoring
            # under the cursor is the window manager's job, not ours.
            return False
        window.move(self._origin + (here - self._pressed_at))
        return True

    def _on_double_click(self, event: QMouseEvent) -> bool:
        """The caption convention: maximise, or come back down."""
        if event.button() is not Qt.MouseButton.LeftButton:
            return False
        window = self._band.window()
        if window.isMaximized() and not window.isFullScreen():
            window.showNormal()
        else:
            window.showMaximized()
        return True


def enable_window_drag(band: QWidget) -> None:
    """Let ``band`` move and maximise the window it belongs to."""
    band.installEventFilter(_WindowDragFilter(band))
