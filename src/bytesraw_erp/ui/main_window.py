"""The single application window and its route table."""

from __future__ import annotations

import logging

from PySide6.QtCore import QByteArray, QSettings
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMainWindow, QMessageBox, QStackedWidget, QWidget

from bytesraw_erp.constants import (
    APP_NAME,
    DEFAULT_WINDOW_SIZE,
    MIN_WINDOW_SIZE,
    ROUTE_ACCOUNT_EDIT,
    ROUTE_ACCOUNT_NEW,
    ROUTE_ACCOUNTS,
    ROUTE_ODOO,
    ROUTE_SETTINGS,
)
from bytesraw_erp.core.errors import BytesrawError
from bytesraw_erp.ui.app_context import AppContext
from bytesraw_erp.ui.pages.account_form_page import AccountFormPage
from bytesraw_erp.ui.pages.accounts_page import AccountsPage
from bytesraw_erp.ui.pages.odoo_page import OdooPage
from bytesraw_erp.ui.pages.settings_page import SettingsPage
from bytesraw_erp.ui.router import Router

_log = logging.getLogger(__name__)

_GEOMETRY_KEY = "window/geometry"


class MainWindow(QMainWindow):
    """Hosts the router. All navigation happens inside one window."""

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self._context = context
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(*MIN_WINDOW_SIZE)

        stack = QStackedWidget(self)
        self.setCentralWidget(stack)
        self.router = Router(stack, self)

        # Forms are rebuilt on each visit so they never show stale input; the
        # Odoo page is kept alive so the embedded browser survives navigation.
        self.router.register(
            ROUTE_ACCOUNTS, lambda: AccountsPage(context, self.router), keep_alive=True
        )
        self.router.register(
            ROUTE_ACCOUNT_NEW, lambda: AccountFormPage(context, self.router), keep_alive=False
        )
        self.router.register(
            ROUTE_ACCOUNT_EDIT, lambda: AccountFormPage(context, self.router), keep_alive=False
        )
        self.router.register(ROUTE_ODOO, lambda: OdooPage(context, self.router), keep_alive=True)
        # Rebuilt per visit so it always shows the stored values and the
        # current list of printers, which can change while the app runs.
        self.router.register(
            ROUTE_SETTINGS, lambda: SettingsPage(context, self.router), keep_alive=False
        )

        self._restore_geometry()
        # A stylesheet change does not re-polish children that were built
        # before it; force it so an open page picks the new palette up.
        context.theme.theme_changed.connect(self._repolish)

    # -- startup -----------------------------------------------------------

    def start(self) -> None:
        """Pick the landing route.

        With at least one saved account the app goes straight to Odoo - the
        account form is a setup step, not a login gate shown on every launch.
        """
        try:
            has_accounts = not self._context.store.is_empty
        except BytesrawError as exc:
            _log.error("Could not read the account registry: %s", exc)
            QMessageBox.critical(self, APP_NAME, str(exc))
            has_accounts = False
        self.router.reset_to(ROUTE_ODOO if has_accounts else ROUTE_ACCOUNT_NEW)

    def _repolish(self, _palette: object) -> None:
        for widget in self.findChildren(QWidget):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        self.update()

    # -- geometry ----------------------------------------------------------

    def _settings(self) -> QSettings:
        return QSettings()

    def _restore_geometry(self) -> None:
        saved = self._settings().value(_GEOMETRY_KEY)
        if isinstance(saved, QByteArray) and not saved.isEmpty():
            self.restoreGeometry(saved)
        else:
            self.resize(*DEFAULT_WINDOW_SIZE)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._settings().setValue(_GEOMETRY_KEY, self.saveGeometry())
        self._context.shutdown()
        super().closeEvent(event)
