"""Add or edit an Odoo account.

The form is also the app's login screen: it is what the user sees on a first
launch, and it is the only place credentials are ever typed. Saving always
authenticates first, so an account can never be stored with credentials that do
not work.

Because it is the first screen of the product, it is laid out as one centred
card under the product mark rather than as a bare form: the two connection
fields and the two credential fields are grouped, each label sits above its
control so the inputs all share one width, and the build number is on screen
from the very first launch.

It uses the same :class:`~bytesraw_erp.ui.widgets.page.PageShell` as the account
list and the settings page, which puts Cancel and "Connect and save" in the
header band beside the title rather than below the last field. That is the one
visible change from the old layout, and it is deliberate: the band does not
scroll, so on a till screen too short for the whole form the submit button is
always in the same place instead of being somewhere below the fold. Enter in
the password field still submits, and the button is still this window's default.
"""

from __future__ import annotations

import logging

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from bytesraw_erp.constants import ROUTE_ACCOUNTS, ROUTE_ODOO
from bytesraw_erp.core.errors import BytesrawError
from bytesraw_erp.data.models import Account, normalize_base_url
from bytesraw_erp.services.odoo_client import OdooClient
from bytesraw_erp.services.session_service import open_session
from bytesraw_erp.services.tasks import run_async
from bytesraw_erp.ui.app_context import AppContext
from bytesraw_erp.ui.router import Router
from bytesraw_erp.ui.widgets.icons import icon
from bytesraw_erp.ui.widgets.page import WIDTH_FORM, PageShell
from bytesraw_erp.ui.widgets.sections import Card, Field, SectionHeader, rule

_log = logging.getLogger(__name__)


def _probe_databases(url: str, verify_tls: bool) -> list[str]:
    with OdooClient(url, verify_tls=verify_tls) as client:
        return client.list_databases()


def _row(*widgets: QWidget, stretch: int = 0) -> QWidget:
    """Wrap widgets in one horizontal control, so a Field can hold them."""
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    for index, widget in enumerate(widgets):
        layout.addWidget(widget, 1 if index == stretch else 0)
    return holder


class AccountFormPage(QWidget):
    """Create a new account, or edit an existing one in place."""

    def __init__(self, context: AppContext, router: Router, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._router = router
        self._editing: Account | None = None
        self._busy = False
        #: Section badges hold rendered bitmaps; the theme can change under
        #: them while this page is open.
        self._sections: list[SectionHeader] = []

        self._shell = PageShell(
            "Add account",
            "Connect to an Odoo 19 server. The password is kept in Windows "
            "Credential Manager, never in a file.",
            width=WIDTH_FORM,
        )
        self._banner = self._shell.banner
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._shell)

        self._build_actions()
        self._shell.body.addWidget(self._build_card())
        self._shell.body.addStretch(1)

        context.theme.theme_changed.connect(self._on_theme_changed)
        self._on_theme_changed(context.theme.palette)

    # -- construction ------------------------------------------------------

    def _section(self, icon_name: str, title: str, description: str = "") -> SectionHeader:
        header = SectionHeader(icon_name, title, description)
        self._sections.append(header)
        return header

    def _build_card(self) -> Card:
        card = Card()

        card.body.addWidget(
            self._section(
                "globe",
                "Server",
                "Where this account connects, and which database on it.",
            )
        )

        self._url = QLineEdit()
        self._url.setPlaceholderText("https://mycompany.odoo.com")
        self._url.editingFinished.connect(self._on_url_committed)
        card.body.addWidget(Field("Server URL", self._url))

        self._database = QComboBox()
        self._database.setEditable(True)
        self._database.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._database.lineEdit().setPlaceholderText("Database name")
        self._load_db_button = QPushButton("Load")
        self._load_db_button.setToolTip("Ask the server which databases it exposes")
        self._load_db_button.clicked.connect(self._load_databases)
        card.body.addWidget(
            Field("Database", _row(self._database, self._load_db_button))
        )

        card.body.addWidget(rule())
        card.body.addWidget(
            self._section("key", "Credentials", "Checked against the server before saving.")
        )

        self._login = QLineEdit()
        self._login.setPlaceholderText("user@example.com")
        card.body.addWidget(Field("Login", self._login))

        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password.returnPressed.connect(self._submit)
        self._reveal = QAction(self._password)
        self._reveal.setToolTip("Show the password")
        self._reveal.triggered.connect(self._toggle_password)
        self._password.addAction(self._reveal, QLineEdit.ActionPosition.TrailingPosition)
        card.body.addWidget(Field("Password", self._password))

        card.body.addWidget(rule())
        card.body.addWidget(
            self._section("sliders", "This connection", "Optional, and specific to this machine.")
        )

        self._name = QLineEdit()
        self._name.setPlaceholderText("Optional - defaults to the server host")
        card.body.addWidget(Field("Display name", self._name))

        self._allow_untrusted = QCheckBox("Allow a self-signed certificate for this server")
        self._allow_untrusted.setToolTip(
            "Only enable this for an on-premise server whose certificate you "
            "control. It disables TLS verification for this account."
        )
        card.body.addWidget(
            Field(
                "",
                self._allow_untrusted,
                "Leave this off unless the server uses a certificate your "
                "computer does not already trust.",
            )
        )
        return card

    def _build_actions(self) -> None:
        """Cancel and submit, in the header band rather than under the form.

        The build number that used to sit in a footer here is gone with it: the
        shell's own header already carries a version badge, and two statements
        of the same number on one screen is one more than is useful.
        """
        self._cancel = QPushButton("Cancel")
        self._cancel.clicked.connect(self._on_cancel)
        self._shell.add_action(self._cancel)

        self._submit_button = QPushButton("Connect and save")
        self._submit_button.setProperty("variant", "primary")
        self._submit_button.setDefault(True)
        self._submit_button.clicked.connect(self._submit)
        self._shell.add_action(self._submit_button)

    # -- theme -------------------------------------------------------------

    def _on_theme_changed(self, palette: object) -> None:
        self._shell.apply_theme(palette)  # type: ignore[arg-type]
        for section in self._sections:
            section.apply_theme(palette)  # type: ignore[arg-type]
        self._sync_reveal_icon()

    # -- router hooks ------------------------------------------------------

    def on_enter(self, params: dict[str, str]) -> None:
        self._banner.clear_message()
        account_id = params.get("account_id")
        self._editing = self._context.store.get(account_id) if account_id else None

        prefill = None
        if self._editing is None:
            self._shell.set_title("Add account")
            self._reset_fields()
            # An account the server rejected was just deleted; its connection
            # details are still worth keeping so only the password is retyped.
            prefill = self._context.take_prefill()
            if prefill is not None:
                self._fill_from(prefill, with_password=False)
        else:
            self._shell.set_title("Edit account")
            self._fill_from(self._editing)

        notice = self._context.take_notice()
        if notice:
            self._banner.show_error(notice)

        # The list is only worth returning to when something is saved there.
        self._cancel.setVisible(not self._context.store.is_empty)
        # Everything else is already filled in when carrying an account over,
        # so the caret belongs on the one field the user has to supply.
        (self._password if prefill is not None else self._url).setFocus()

    # -- field helpers -----------------------------------------------------

    def _reset_fields(self) -> None:
        for field in (self._url, self._login, self._password, self._name):
            field.clear()
        self._database.clear()
        self._allow_untrusted.setChecked(False)

    def _toggle_password(self) -> None:
        hidden = self._password.echoMode() == QLineEdit.EchoMode.Password
        self._password.setEchoMode(
            QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password
        )
        self._sync_reveal_icon()

    def _sync_reveal_icon(self) -> None:
        hidden = self._password.echoMode() == QLineEdit.EchoMode.Password
        self._reveal.setIcon(icon("eye" if hidden else "eye-off"))
        self._reveal.setToolTip("Show the password" if hidden else "Hide the password")

    def _fill_from(self, account: Account, *, with_password: bool = True) -> None:
        """Load ``account`` into the fields.

        ``with_password=False`` is for an account that no longer exists - its
        vault entry has been deleted along with it, so asking for the password
        would either fail or, worse, return a stale one from a namesake id.
        """
        self._url.setText(account.url)
        self._database.clear()
        self._database.addItem(account.database)
        self._database.setCurrentText(account.database)
        self._login.setText(account.login)
        self._name.setText(account.name)
        self._allow_untrusted.setChecked(account.allow_untrusted_certificate)
        if not with_password:
            self._password.clear()
            return
        try:
            self._password.setText(self._context.store.get_password(account.id) or "")
        except BytesrawError as exc:
            self._banner.show_error(str(exc))

    def _collect(self) -> Account:
        """Build the account under edit, preserving its id when editing."""
        base = self._editing or Account()
        return base.evolve(
            url=normalize_base_url(self._url.text()),
            database=self._database.currentText().strip(),
            login=self._login.text().strip(),
            name=self._name.text().strip(),
            allow_untrusted_certificate=self._allow_untrusted.isChecked(),
        )

    def _validate(self, account: Account) -> str | None:
        if not account.url:
            return "Enter the Odoo server URL."
        if not account.database:
            return "Enter the database name, or press Load to list them."
        if not account.login:
            return "Enter your Odoo login."
        if not self._password.text():
            return "Enter your Odoo password."
        duplicate = self._context.store.find_duplicate(account)
        if duplicate is not None:
            return f"'{duplicate.name}' already connects this login to that database."
        return None

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self._submit_button.setEnabled(not busy)
        self._submit_button.setText(message or "Connect and save")
        self._load_db_button.setEnabled(not busy)
        for field in (self._url, self._database, self._login, self._password, self._name):
            field.setEnabled(not busy)

    # -- database discovery ------------------------------------------------

    def _on_url_committed(self) -> None:
        """Normalise what the user typed, then offer the database list."""
        normalized = normalize_base_url(self._url.text())
        if normalized and normalized != self._url.text():
            self._url.setText(normalized)
        if normalized and self._database.count() == 0 and not self._database.currentText():
            self._load_databases()

    def _load_databases(self) -> None:
        url = normalize_base_url(self._url.text())
        if not url:
            self._banner.show_error("Enter the Odoo server URL first.")
            return
        self._url.setText(url)
        self._banner.clear_message()
        self._load_db_button.setEnabled(False)
        self._load_db_button.setText("Loading")

        def done(names: list[str]) -> None:
            self._load_db_button.setEnabled(True)
            self._load_db_button.setText("Load")
            if not names:
                self._banner.show_info(
                    "This server does not publish its database list. "
                    "Type the database name manually."
                )
                return
            current = self._database.currentText()
            self._database.clear()
            self._database.addItems(names)
            if current in names:
                self._database.setCurrentText(current)
            elif len(names) == 1:
                self._database.setCurrentIndex(0)

        def failed(exc: Exception) -> None:
            self._load_db_button.setEnabled(True)
            self._load_db_button.setText("Load")
            self._banner.show_error(str(exc))

        run_async(
            _probe_databases,
            url,
            not self._allow_untrusted.isChecked(),
            on_success=done,
            on_error=failed,
        )

    # -- submit ------------------------------------------------------------

    def _submit(self) -> None:
        if self._busy:
            return
        account = self._collect()
        problem = self._validate(account)
        if problem:
            self._banner.show_error(problem)
            return

        password = self._password.text()
        self._banner.clear_message()
        self._set_busy(True, "Connecting")

        def done(result: object) -> None:
            client, session = result  # type: ignore[misc]
            self._set_busy(False)
            try:
                saved = self._context.store.upsert(account, password=password)
            except BytesrawError as exc:
                client.close()
                self._banner.show_error(str(exc))
                return
            self._context.adopt_session(saved, client, session)
            self._context.notify_accounts_changed()
            _log.info("Account %s connected as %s", saved.name, session.login)
            self._router.reset_to(ROUTE_ODOO)

        def failed(exc: Exception) -> None:
            self._set_busy(False)
            self._banner.show_error(str(exc))

        run_async(open_session, account, password, on_success=done, on_error=failed)

    def _on_cancel(self) -> None:
        if not self._router.back():
            self._router.go(ROUTE_ACCOUNTS)
