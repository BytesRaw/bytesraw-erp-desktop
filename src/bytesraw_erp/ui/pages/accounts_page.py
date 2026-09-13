"""Account list: pick an Odoo connection, or manage the saved ones.

This page is only reached deliberately - from the app bar menu, or when the
user signs out. On a normal launch with at least one saved account the app goes
straight to :mod:`bytesraw_erp.ui.pages.odoo_page`; the list is not a login
gate.

Laid out on the shared :class:`~bytesraw_erp.ui.widgets.page.PageShell`, like
the account form and the settings page. It used to carry a bare text title and
no product mark, which made the two screens a user moves between - this one and
the form it routes to - look like parts of different applications.
"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import (
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from bytesraw_erp.constants import ROUTE_ACCOUNT_NEW, ROUTE_ODOO
from bytesraw_erp.core.errors import BytesrawError
from bytesraw_erp.ui.app_context import AppContext
from bytesraw_erp.ui.router import Router
from bytesraw_erp.ui.widgets.account_card import AccountCard
from bytesraw_erp.ui.widgets.icons import icon
from bytesraw_erp.ui.widgets.page import WIDTH_LIST, PageShell


class AccountsPage(QWidget):
    """Renders the saved accounts and routes to add / edit / open."""

    def __init__(self, context: AppContext, router: Router, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._router = router

        self._shell = PageShell(
            "Accounts",
            "Open a saved Odoo connection, or change the ones on this computer.",
            width=WIDTH_LIST,
            windowed=context.windowed,
        )
        self._banner = self._shell.banner
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._shell)

        add_button = QPushButton("Add account")
        # Explicitly the button's own foreground, not the theme's text colour:
        # icons are stroked bitmaps, and a plum button with a near-black glyph
        # on it is the same invisibility bug as dark text on a dark menu.
        add_button.setIcon(icon("plus", context.theme.palette.primary_text))
        add_button.setIconSize(QSize(16, 16))
        add_button.setProperty("variant", "primary")
        add_button.clicked.connect(lambda: self._router.go(ROUTE_ACCOUNT_NEW))
        self._add_button = add_button
        self._shell.add_action(add_button)

        self._empty_hint = QLabel(
            "No accounts yet. Add your first Odoo server to get started."
        )
        self._empty_hint.setObjectName("MutedLabel")
        self._shell.body.addWidget(self._empty_hint)

        self._list_layout = QVBoxLayout()
        self._list_layout.setSpacing(10)
        self._shell.body.addLayout(self._list_layout)
        self._shell.body.addStretch(1)

        context.accounts_changed.connect(self.refresh)
        context.theme.theme_changed.connect(self._on_theme_changed)

    # -- router hooks ------------------------------------------------------

    def on_enter(self, _params: dict[str, str]) -> None:
        notice = self._context.take_notice()
        if notice:
            self._banner.show_error(notice)
        else:
            self._banner.clear_message()
        self.refresh()

    # -- theme -------------------------------------------------------------

    def _on_theme_changed(self, palette: object) -> None:
        """Re-render every icon on the page.

        This page is kept alive across navigation, so its icons outlive the
        theme they were stroked in. Rebuilding the cards is the cheapest way to
        re-render theirs - the list is short, and it is already the path taken
        whenever an account changes.
        """
        self._shell.apply_theme(palette)  # type: ignore[arg-type]
        self._add_button.setIcon(icon("plus", palette.primary_text))  # type: ignore[attr-defined]
        self.refresh()

    # -- rendering ---------------------------------------------------------

    def refresh(self) -> None:
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        try:
            accounts = self._context.store.accounts
        except BytesrawError as exc:
            self._banner.show_error(str(exc))
            accounts = []

        self._empty_hint.setVisible(not accounts)
        for account in accounts:
            card = AccountCard(account)
            card.open_requested.connect(self._open_account)
            card.edit_requested.connect(self._edit_account)
            card.delete_requested.connect(self._delete_account)
            self._list_layout.addWidget(card)

    # -- actions -----------------------------------------------------------

    def _open_account(self, account_id: str) -> None:
        self._context.store.set_active(account_id)
        self._router.go(ROUTE_ODOO)

    def _edit_account(self, account_id: str) -> None:
        self._router.go(f"/accounts/{account_id}/edit")

    def _delete_account(self, account_id: str) -> None:
        account = self._context.store.get(account_id)
        if account is None:
            return
        confirm = QMessageBox.question(
            self,
            "Remove account",
            f"Remove '{account.name}'?\n\n"
            "The saved password and this account's browsing data are deleted "
            "from this computer. Nothing changes on the Odoo server.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            self._context.remove_account(account_id)
        except BytesrawError as exc:
            self._banner.show_error(str(exc))
