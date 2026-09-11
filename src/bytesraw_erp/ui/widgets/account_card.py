"""One row in the account list."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from bytesraw_erp.data.models import Account
from bytesraw_erp.ui.widgets.icons import icon


class AccountCard(QWidget):
    """Shows one saved connection with open / edit / delete actions."""

    open_requested = Signal(str)
    edit_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, account: Account, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AccountCard")
        self._account_id = account.id
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 12, 12)
        layout.setSpacing(12)

        badge = QLabel()
        badge.setPixmap(icon("server").pixmap(QSize(22, 22)))
        badge.setFixedWidth(26)
        layout.addWidget(badge, alignment=Qt.AlignmentFlag.AlignTop)

        text_column = QVBoxLayout()
        text_column.setSpacing(2)
        name = QLabel(account.name)
        name.setObjectName("AccountName")
        subtitle = QLabel(f"{account.login} - {account.database} - {account.display_host}")
        subtitle.setObjectName("SubtleLabel")
        text_column.addWidget(name)
        text_column.addWidget(subtitle)
        layout.addLayout(text_column, 1)

        layout.addWidget(self._action("edit", "Edit this account", self._emit_edit))
        layout.addWidget(self._action("trash", "Remove this account", self._emit_delete))

    def _action(self, icon_name: str, tooltip: str, slot) -> QToolButton:
        button = QToolButton()
        button.setIcon(icon(icon_name))
        button.setIconSize(QSize(16, 16))
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(slot)
        return button

    def mouseReleaseEvent(self, event) -> None:
        """Clicking anywhere but the action buttons opens the account."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_requested.emit(self._account_id)
        super().mouseReleaseEvent(event)

    def _emit_edit(self) -> None:
        self.edit_requested.emit(self._account_id)

    def _emit_delete(self) -> None:
        self.delete_requested.emit(self._account_id)
