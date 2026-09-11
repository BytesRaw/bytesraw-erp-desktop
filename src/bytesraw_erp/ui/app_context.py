"""Shared application state handed to every page.

Pages receive this object instead of reaching for globals, which keeps them
constructible in tests with a stub context and makes the dependency graph
explicit: a page can only touch what the context exposes.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal

from bytesraw_erp.constants import ROUTE_ACCOUNT_NEW, ROUTE_ODOO
from bytesraw_erp.core.errors import BytesrawError
from bytesraw_erp.data.account_store import AccountStore
from bytesraw_erp.data.models import Account, SessionContext
from bytesraw_erp.data.settings_store import SettingsStore
from bytesraw_erp.services.odoo_client import OdooClient
from bytesraw_erp.services.print_service import PrintService
from bytesraw_erp.services.profile_manager import ProfileManager
from bytesraw_erp.ui.theme import ThemeController

_log = logging.getLogger(__name__)


class AppContext(QObject):
    """Owns the account registry, the web profiles and the live Odoo session."""

    #: Emitted whenever the account list changes, so open pages can refresh.
    accounts_changed = Signal()
    #: Emitted with the new :class:`SessionContext` after a successful sign-in.
    session_changed = Signal(object)

    def __init__(self, theme: ThemeController, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.store = AccountStore()
        self.profiles = ProfileManager(self)
        self.printing = PrintService(self)
        self.settings = SettingsStore()
        self.theme = theme
        self._account: Account | None = None
        self._session: SessionContext | None = None
        self._client: OdooClient | None = None
        #: Handover to whichever page is opened next, consumed once by its
        #: ``on_enter``. A page that routes away because something went wrong
        #: cannot show the explanation itself - it is no longer on screen - and
        #: the destination has no other way to learn why it was opened.
        self._notice: str | None = None
        self._prefill: Account | None = None

    # -- active session ----------------------------------------------------

    @property
    def account(self) -> Account | None:
        return self._account

    @property
    def session(self) -> SessionContext | None:
        return self._session

    @property
    def client(self) -> OdooClient | None:
        return self._client

    def adopt_session(
        self,
        account: Account,
        client: OdooClient,
        session: SessionContext,
    ) -> None:
        """Install a freshly authenticated session as the active one.

        Any previous client is closed first - leaving it open would leak a
        connection pool and a live Odoo session for every account switch.
        """
        self.release_session()
        self._account = account
        self._client = client
        self._session = session
        self.store.set_active(account.id)
        if client.session_id:
            self.profiles.inject_session(account, client.cookies)
        self.session_changed.emit(session)

    def update_session(self, session: SessionContext) -> None:
        """Replace the session view model after a company/language change."""
        self._session = session
        self.session_changed.emit(session)

    def release_session(self) -> None:
        if self._client is not None:
            self._client.close()
        self._client = None
        self._session = None
        self._account = None

    def shutdown(self) -> None:
        self.release_session()

    def landing_route(self) -> str:
        """Where "done" and "back to the app" should go.

        With no account saved there is nothing to return to but the form.
        """
        try:
            return ROUTE_ODOO if not self.store.is_empty else ROUTE_ACCOUNT_NEW
        except BytesrawError:
            return ROUTE_ACCOUNT_NEW

    # -- handover to the next page -----------------------------------------

    def post_notice(self, message: str, prefill: Account | None = None) -> None:
        """Leave a message - and optionally a form to pre-fill - for the next page."""
        self._notice = message
        self._prefill = prefill

    def take_notice(self) -> str | None:
        """Read and clear the pending message, so it is shown exactly once."""
        message, self._notice = self._notice, None
        return message

    def take_prefill(self) -> Account | None:
        """Read and clear the account whose details the add form should carry over."""
        account, self._prefill = self._prefill, None
        return account

    # -- account registry --------------------------------------------------

    def notify_accounts_changed(self) -> None:
        self.accounts_changed.emit()

    def remove_account(self, account_id: str) -> None:
        """Delete an account, its stored password and its browsing data."""
        if self._account is not None and self._account.id == account_id:
            self.release_session()
        self.store.remove(account_id)
        self.profiles.forget(account_id)
        _log.info("Removed account %s", account_id)
        self.notify_accounts_changed()
