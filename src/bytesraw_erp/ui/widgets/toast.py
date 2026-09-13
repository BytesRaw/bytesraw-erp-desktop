"""Transient notifications shown over the Odoo view.

A download finishing, or a report reaching the printer, is worth telling the
user about - but not worth a modal dialog they have to dismiss before carrying
on working. These slide in at the bottom-right of the page, offer at most one
action, and disappear on their own.

They are deliberately children of the page rather than top-level windows: a
real window would steal focus from the embedded browser and appear in the
taskbar.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

_log = logging.getLogger(__name__)

#: How long a toast stays before fading, in milliseconds.
_LINGER_MS = 6000
_FADE_MS = 350
_MARGIN = 16
_SPACING = 10
_MAX_VISIBLE = 3


class Toast(QWidget):
    """One notification: a line of text and an optional action button."""

    #: Emitted just before the widget is deleted. Used instead of ``destroyed``
    #: because that fires after the C++ object is gone, leaving a handler to
    #: poke at a dead Python wrapper.
    closed = Signal()

    def __init__(
        self,
        parent: QWidget,
        message: str,
        *,
        action_text: str = "",
        on_action: Callable[[], None] | None = None,
        linger_ms: int = _LINGER_MS,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMaximumWidth(420)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 11, 10, 11)
        layout.setSpacing(12)

        self._label = QLabel(message)
        self._label.setObjectName("ToastText")
        self._label.setWordWrap(True)
        layout.addWidget(self._label, 1)

        if action_text and on_action is not None:
            button = QPushButton(action_text)
            button.setObjectName("ToastAction")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(self._run_action(on_action))
            layout.addWidget(button)

        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._effect.setOpacity(1.0)

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.dismiss)
        self._dismiss_timer.start(linger_ms)

        self._fade_timer = QTimer(self)
        self._fade_timer.timeout.connect(self._step_fade)

    def set_message(self, message: str, linger_ms: int | None = None) -> None:
        """Replace the text in place, rather than stacking another toast.

        A download that takes two minutes needs to say so as it goes, and a new
        toast per percentage point would bury the screen. Go through
        :meth:`ToastArea.update_message` so the stack is re-laid out too - the
        new text can be a different height.

        ``linger_ms`` restarts the countdown, and also cancels a fade already in
        progress. That is what lets a long-lived toast be turned into a short
        one: a download that fails has to say why, and dismissing the download
        toast instead would leave it fading on screen for a third of a second
        beside the message explaining that it stopped.
        """
        self._label.setText(message)
        self.adjustSize()
        if linger_ms is None:
            return
        self._fade_timer.stop()
        self._effect.setOpacity(1.0)
        self._dismiss_timer.start(linger_ms)

    def _run_action(self, on_action: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            try:
                on_action()
            except Exception:
                _log.exception("Toast action failed")
            self.dismiss()

        return run

    def enterEvent(self, event: QEnterEvent) -> None:
        """Stop the countdown while the pointer is over the toast.

        Qt6 delivers a ``QEnterEvent`` here, not a bare ``QEvent``; passing the
        latter to the base implementation is a TypeError.
        """
        self._dismiss_timer.stop()
        self._fade_timer.stop()
        self._effect.setOpacity(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._dismiss_timer.start(_LINGER_MS)
        super().leaveEvent(event)

    def dismiss(self) -> None:
        self._dismiss_timer.stop()
        if not self._fade_timer.isActive():
            self._fade_timer.start(_FADE_MS // 10)

    def _step_fade(self) -> None:
        opacity = self._effect.opacity() - 0.1
        if opacity <= 0:
            self._fade_timer.stop()
            self.hide()
            self.closed.emit()
            self.deleteLater()
            return
        self._effect.setOpacity(opacity)


class ToastArea(QObject):
    """Stacks toasts at the bottom-right of a host widget.

    Not a widget itself: it watches the host for resizes and positions free-
    floating children, so it adds nothing to the host's layout and cannot
    disturb the web view filling it.
    """

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self._host = host
        self._toasts: list[Toast] = []
        host.installEventFilter(self)

    def show_message(
        self,
        message: str,
        *,
        action_text: str = "",
        on_action: Callable[[], None] | None = None,
        linger_ms: int = _LINGER_MS,
    ) -> Toast:
        """Float a notification over the host.

        ``linger_ms`` exists for the one message that is worth more than six
        seconds: an update being offered is a decision, not an acknowledgement,
        and a toast that has faded before the user finished reading it is a
        decision they were never actually given.
        """
        toast = Toast(
            self._host,
            message,
            action_text=action_text,
            on_action=on_action,
            linger_ms=linger_ms,
        )
        toast.closed.connect(lambda: self._forget(toast))
        self._toasts.append(toast)

        # Oldest first out, so a burst of downloads does not bury the screen.
        # Every excess toast is retired, not just the first.
        for stale in self._toasts[:-_MAX_VISIBLE]:
            stale.dismiss()

        toast.adjustSize()
        toast.show()
        toast.raise_()
        self._relayout()
        return toast

    def update_message(
        self, toast: Toast, message: str, linger_ms: int | None = None
    ) -> None:
        """Rewrite a toast that is already on screen, and re-stack the rest."""
        toast.set_message(message, linger_ms)
        self._relayout()

    def _forget(self, toast: Toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        self._relayout()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._host and event.type() == QEvent.Type.Resize:
            self._relayout()
        return super().eventFilter(watched, event)

    def _relayout(self) -> None:
        bottom = self._host.height() - _MARGIN
        for toast in reversed([t for t in self._toasts if not t.isHidden()]):
            toast.adjustSize()
            width = min(toast.sizeHint().width(), 420)
            height = toast.sizeHint().height()
            toast.setGeometry(
                self._host.width() - width - _MARGIN,
                bottom - height,
                width,
                height,
            )
            bottom -= height + _SPACING
