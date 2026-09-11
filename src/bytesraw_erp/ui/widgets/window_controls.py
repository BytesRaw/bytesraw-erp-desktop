"""Minimise, full screen and close, for a window that has no title bar.

The shell normally runs full screen, so Windows draws it no caption and leaves
no taskbar button to right-click. The controls a user still needs therefore
have to live inside the app's own chrome, and every screen that can be the
first one on launch has to be able to reach a set - otherwise a machine with no
saved account opens on the account form with no way out of the application at
all.

Why there is still no maximise button
-------------------------------------
There is a full-screen toggle, and it is not the same control. It appears only
when the app was launched windowed (``--windowed``), where the window genuinely
has two sizes and the button delivers both. In the normal full-screen launch
the window has exactly one size, and a control promising a second one it will
not deliver is worse than no control - so it is not built at all rather than
built and disabled.

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

    ``allow_full_screen`` adds the toggle between minimise and close. It is a
    constructor argument rather than a runtime property because the answer is
    fixed at launch and a hidden button would still be found by
    ``findChildren`` - and by anyone reading the widget tree.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        allow_full_screen: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("WindowControls")
        #: (button, icon name) pairs, re-rendered on every theme change.
        self._icons: list[tuple[QToolButton, str]] = []
        self._full_screen: QToolButton | None = None

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)

        minimize = self._button("minimize", "Minimise", "MinimizeButton")
        minimize.clicked.connect(self._on_minimize)
        row.addWidget(minimize)

        if allow_full_screen:
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

    def _on_full_screen(self) -> None:
        """Toggle the window between full screen and its ordinary size."""
        window = self.window()
        if window.isFullScreen():
            window.showNormal()
        else:
            window.showFullScreen()
        self.sync_full_screen()

    def sync_full_screen(self) -> None:
        """Point the toggle at whichever state it will move the window *to*.

        Called after a click and from the window's own state handler, because
        full screen can also be left with Escape or by the shell, and a glyph
        left offering the state the window is already in is a lie.
        """
        if self._full_screen is None:
            return
        full = self.window().isFullScreen()
        name = "exit-full-screen" if full else "full-screen"
        self._full_screen.setToolTip(
            "Leave full screen (F11)" if full else "Full screen (F11)"
        )
        # The pair in `_icons` is what a theme change re-renders from, so the
        # new name has to replace the old one there too - otherwise the glyph
        # reverts to whichever state it was built in the next time the theme
        # flips.
        for index, (button, _) in enumerate(self._icons):
            if button is self._full_screen:
                self._icons[index] = (button, name)
                break
        self._full_screen.setIcon(icon(name))

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
