"""Minimise, maximise/restore, full screen and close, for a window that has no
title bar.

The shell normally runs full screen, so Windows draws it no caption and leaves
no taskbar button to right-click. The controls a user still needs therefore
have to live inside the app's own chrome, and every screen that can be the
first one on launch has to be able to reach a set - otherwise a machine with no
saved account opens on the account form with no way out of the application at
all.

Why all four are built, always
------------------------------
They were not, and the reasoning was sound while it held: a window pinned full
screen for its whole life has exactly one size, so a maximise button would have
promised a second one it could not deliver, and the full-screen toggle was only
built for a ``--windowed`` launch. The window now genuinely has three sizes in
every launch mode - full screen, maximised, and the size the user dragged it
to - so all three controls deliver, and a till that never touches them still
starts full screen and stays there. :class:`~bytesraw_erp.ui.main_window.
MainWindow` keeps that last part true: its correction back to full screen is
armed by ``showFullScreen`` and disarmed by a deliberate ``showNormal`` or
``showMaximized``, which is exactly what these buttons call.

Maximise and full screen are two controls because they are two promises.
Maximise leaves the taskbar and the window's own frame on screen; full screen
covers the whole display and is what a till wants. Collapsing them into one
button would make one of the two unreachable.

Close is the only destructive button in the bar, so it is the only one that
turns red under the pointer - the Windows convention, and the difference
between "I meant that" and "I hit the wrong glyph".
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QHBoxLayout, QToolButton, QWidget

from bytesraw_erp.ui.theme import Palette
from bytesraw_erp.ui.widgets.icons import icon, set_icon_color

_ICON = 16


class WindowControls(QWidget):
    """Caption buttons for whichever window this widget sits in.

    The target is resolved through :meth:`QWidget.window` at click time rather
    than being injected, so the same widget works inside the app bar and as a
    free-floating overlay without either caller wiring anything up.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("WindowControls")
        #: (button, icon name) pairs, re-rendered on every theme change.
        self._icons: list[tuple[QToolButton, str]] = []

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)

        minimize = self._button("minimize", "Minimise", "MinimizeButton")
        minimize.clicked.connect(self._on_minimize)
        row.addWidget(minimize)

        self._maximize = self._button("maximize", "Maximise", "MaximizeButton")
        self._maximize.clicked.connect(self._on_maximize)
        row.addWidget(self._maximize)

        self._full_screen = self._button(
            "full-screen", "Full screen (F11)", "FullScreenButton"
        )
        self._full_screen.clicked.connect(self._on_full_screen)
        row.addWidget(self._full_screen)

        close = self._button("close", "Close Bytesraw ERP", "CloseButton")
        close.clicked.connect(self._on_close)
        row.addWidget(close)

    def _button(self, icon_name: str, tooltip: str, object_name: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName(object_name)
        button.setIconSize(QSize(_ICON, _ICON))
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setIcon(icon(icon_name))
        self._icons.append((button, icon_name))
        return button

    # -- actions -----------------------------------------------------------

    def _on_minimize(self) -> None:
        self.window().showMinimized()

    def _on_close(self) -> None:
        self.window().close()

    def _on_maximize(self) -> None:
        """Maximise, or come back down from maximised or from full screen.

        Full screen counts as "bigger than maximised", so from there this
        button steps down to maximised rather than all the way to the window's
        loose size - the same step Windows takes when a full-screen video is
        left with the caption button rather than with Escape.
        """
        window = self.window()
        if window.isMaximized() and not window.isFullScreen():
            window.showNormal()
        else:
            window.showMaximized()
        self.sync_window_state()

    def _on_full_screen(self) -> None:
        """Toggle the window between full screen and its ordinary size."""
        window = self.window()
        if window.isFullScreen():
            window.showNormal()
        else:
            window.showFullScreen()
        self.sync_window_state()

    def sync_window_state(self) -> None:
        """Point both toggles at whichever state they will move the window *to*.

        Called after a click and from the window's own state handler, because
        the size can also change by F11, by a double-click on the app bar or
        by the shell, and a glyph left offering the state the window is already
        in is a lie.
        """
        window = self.window()
        full = window.isFullScreen()

        name = "exit-full-screen" if full else "full-screen"
        self._full_screen.setToolTip(
            "Leave full screen (F11)" if full else "Full screen (F11)"
        )
        self._rename_icon(self._full_screen, name)

        down = full or window.isMaximized()
        self._rename_icon(self._maximize, "restore" if down else "maximize")
        self._maximize.setToolTip("Restore down" if down else "Maximise")

    def _rename_icon(self, button: QToolButton, name: str) -> None:
        """Swap a button's glyph, in ``_icons`` as well as on the button.

        That list is what a theme change re-renders from, so a swap that only
        touched the button would revert to whichever state it was built in the
        next time the theme flipped.
        """
        for index, (candidate, _) in enumerate(self._icons):
            if candidate is button:
                self._icons[index] = (button, name)
                break
        button.setIcon(icon(name))

    # -- theme -------------------------------------------------------------

    def apply_theme(self, palette: Palette) -> None:
        """Re-stroke every glyph; a QIcon is a bitmap and will not follow.

        ``set_icon_color`` is a no-op when the colour is unchanged, so calling
        it here costs nothing even though the app bar and the window also call
        it on the same signal.
        """
        set_icon_color(palette.text)
        for button, icon_name in self._icons:
            button.setIcon(icon(icon_name))
