"""Minimise and close, for a window that has no title bar to carry them.

The shell runs full screen, so Windows draws it no caption and leaves no
taskbar button to right-click. The two controls a user still needs therefore
have to live inside the app's own chrome, and every screen that can be the
first one on launch has to be able to reach a set - otherwise a machine with no
saved account opens on the account form with no way out of the application at
all.

There is deliberately no restore/maximise button. The window has exactly one
size, and a control that promises a second one it will not deliver is worse
than no control.

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
    """Minimise and close for whichever window this widget sits in.

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

    # -- theme -------------------------------------------------------------

    def apply_theme(self, palette: Palette) -> None:
        """Re-stroke both glyphs; a QIcon is a bitmap and will not follow.

        ``set_icon_color`` is a no-op when the colour is unchanged, so calling
        it here costs nothing even though the app bar and the window also call
        it on the same signal.
        """
        set_icon_color(palette.text)
        for button, icon_name in self._icons:
            button.setIcon(icon(icon_name))
