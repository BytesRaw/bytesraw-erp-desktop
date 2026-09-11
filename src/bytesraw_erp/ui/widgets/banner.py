"""Inline status banner used by the account pages.

Errors are shown in the page rather than in a modal dialog: a dialog forces an
acknowledgement click and then hides the message just as the user goes back to
fix the field it refers to.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QWidget


class Banner(QLabel):
    """A one-line message that hides itself when empty."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWordWrap(True)
        self.setVisible(False)

    def show_error(self, message: str) -> None:
        self.setObjectName("ErrorBanner")
        self._show(message)

    def show_info(self, message: str) -> None:
        self.setObjectName("InfoBanner")
        self._show(message)

    def clear_message(self) -> None:
        self.setText("")
        self.setVisible(False)

    def _show(self, message: str) -> None:
        self.setText(message)
        self.setVisible(bool(message))
        # Re-polish so the object-name-scoped stylesheet rule takes effect after
        # the name changes between error and info.
        self.style().unpolish(self)
        self.style().polish(self)
