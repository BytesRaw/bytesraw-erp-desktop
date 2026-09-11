"""Add or edit an Odoo account.

The form is also the app's login screen: it is what the user sees on a first
launch, and it is the only place credentials are ever typed. Saving always
authenticates first, so an account can never be stored with credentials that do
not work.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bytesraw_erp.constants import ROUTE_ACCOUNTS, ROUTE_ODOO
from bytesraw_erp.core.errors import BytesrawError
from bytesraw_erp.core.resources import logo_pixmap
from bytesraw_erp.data.models import Account, normalize_base_url
from bytesraw_erp.services.odoo_client import OdooClient
from bytesraw_erp.services.session_service import open_session
from bytesraw_erp.services.tasks import run_async
from bytesraw_erp.ui.app_context import AppContext
from bytesraw_erp.ui.router import Router
from bytesraw_erp.ui.widgets.banner import Banner

_log = logging.getLogger(__name__)


def _probe_databases(url: str, verify_tls: bool) -> list[str]:
    with OdooClient(url, verify_tls=verify_tls) as client:
        return client.list_databases()


class AccountFormPage(QWidget):
    """Create a new account, or edit an existing one in place."""

    def __init__(self, context: AppContext, router: Router, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._router = router
        self._editing: Account | None = None
        self._busy = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        container = QWidget()
        container.setMaximumWidth(560)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 32, 24, 32)
        layout.setSpacing(16)

        heading = QHBoxLayout()
        heading.setSpacing(12)
        mark = QLabel()
        logo = logo_pixmap()
        if not logo.isNull():
            mark.setPixmap(
                logo.scaledToHeight(32, Qt.TransformationMode.SmoothTransformation)
            )
            heading.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
        self._title = QLabel("Add account")
        self._title.setObjectName("PageTitle")
        heading.addWidget(self._title, 1)
        layout.addLayout(heading)

        subtitle = QLabel(
            "Connect Bytesraw ERP to an Odoo 19 server. "
            "The password is stored in Windows Credential Manager, never in a file."
        )
        subtitle.setObjectName("MutedLabel")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self._banner = Banner()
        layout.addWidget(self._banner)

        layout.addLayout(self._build_form())
        layout.addLayout(self._build_actions())
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(container)
        outer.addWidget(scroll)

    # -- construction ------------------------------------------------------

    def _build_form(self) -> QFormLayout:
        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self._url = QLineEdit()
        self._url.setPlaceholderText("https://mycompany.odoo.com")
        self._url.editingFinished.connect(self._on_url_committed)
        form.addRow("Server URL", self._url)

        database_row = QHBoxLayout()
        self._database = QComboBox()
        self._database.setEditable(True)
        self._database.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._database.lineEdit().setPlaceholderText("Database name")
        database_row.addWidget(self._database, 1)
        self._load_db_button = QPushButton("Load")
        self._load_db_button.setToolTip("Ask the server which databases it exposes")
        self._load_db_button.clicked.connect(self._load_databases)
        database_row.addWidget(self._load_db_button)
        form.addRow("Database", database_row)

        self._login = QLineEdit()
        self._login.setPlaceholderText("user@example.com")
        form.addRow("Login", self._login)

        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password.returnPressed.connect(self._submit)
        form.addRow("Password", self._password)

        self._name = QLineEdit()
        self._name.setPlaceholderText("Optional - defaults to the server host")
        form.addRow("Display name", self._name)

        self._allow_untrusted = QCheckBox(
            "Allow a self-signed certificate for this server"
        )
        self._allow_untrusted.setToolTip(
            "Only enable this for an on-premise server whose certificate you "
            "control. It disables TLS verification for this account."
        )
        form.addRow("", self._allow_untrusted)
        return form

    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._cancel = QPushButton("Cancel")
        self._cancel.clicked.connect(self._on_cancel)
        row.addWidget(self._cancel)
        row.addStretch(1)
        self._submit_button = QPushButton("Connect and save")
        self._submit_button.setProperty("variant", "primary")
        self._submit_button.setDefault(True)
        self._submit_button.clicked.connect(self._submit)
        row.addWidget(self._submit_button)
        return row

    # -- router hooks ------------------------------------------------------

    def on_enter(self, params: dict[str, str]) -> None:
        self._banner.clear_message()
        account_id = params.get("account_id")
        self._editing = self._context.store.get(account_id) if account_id else None

        prefill = None
        if self._editing is None:
            self._title.setText("Add account")
            self._reset_fields()
            # An account the server rejected was just deleted; its connection
            # details are still worth keeping so only the password is retyped.
            prefill = self._context.take_prefill()
            if prefill is not None:
                self._fill_from(prefill, with_password=False)
        else:
            self._title.setText("Edit account")
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
