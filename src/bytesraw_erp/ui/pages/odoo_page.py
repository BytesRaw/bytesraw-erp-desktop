"""The home page: the Odoo 19 web client embedded under a native app bar.

Sign-in happens over JSON-RPC, not by driving the HTML login form. The
resulting ``session_id`` cookie is planted into this account's QtWebEngine
profile, so the browser surface opens already authenticated and the RPC client
and the embedded client share one Odoo session.

Session expiry
--------------
Because the two clients share one session, they also lose it together, and
either one can be the first to notice:

* the **web view** is redirected to ``/web/login``. Odoo's HTTP dispatcher
  answers an expired session that way (``odoo/http.py:2516``), so this is what
  a user sitting in front of the app actually sees first;
* an **RPC call** comes back with ``odoo.http.SessionExpiredException``, which
  :class:`~bytesraw_erp.services.odoo_client.OdooClient` raises as
  :class:`OdooSessionExpired`;
* **Odoo's own client** reports the fault, which is what happens when a
  session is revoked while someone is working: the failure is an XHR inside the
  single-page client, so nothing navigates, and Odoo answers it with a modal
  that waits for a click. The script in
  :mod:`bytesraw_erp.ui.widgets.web_view` reads it out of the response and
  raises :meth:`OdooPage._on_web_session_expired`;
* nothing at all happens, because the till has been idle. A probe every
  ``SESSION_PROBE_SECONDS`` covers that case and, since Odoo's
  ``get_session_info`` calls ``session.touch()``, doubles as the keepalive that
  stops the session expiring in the first place.

All four funnel into :meth:`OdooPage._recover_session`, which signs in again
with the password already in the vault and puts the user back on the page they
were on. Nothing is asked of them: the credentials have not changed, only the
server's memory of the session has. The one thing that must not happen is a
loop - a server that keeps refusing gets one attempt, after which the failure
is shown like any other.

That one attempt is spent *per expiry*, not per run of the application, and the
difference is the whole point of the allowance. A session that expires, is
recovered, and then expires again hours later at the end of its ordinary life
is not a loop - it is the mechanism working twice - and a latch that stayed
down would have made every till stop recovering after its first expiry of the
day. Proof of life is what puts the allowance back - an Odoo page that finishes
loading somewhere other than the login screen, or the first probe to come back
healthy - because that is the server saying the replacement session is a real
one and not another redirect to the login page, which is the only case the latch
exists to stop.

What the renewal must never do is delete the account. An interactive sign-in the
server rejects does, and should: the user is at the screen and the form carries
their details back to them. A silent renewal is the opposite situation - nobody
asked, and the details that worked this morning were changed by somebody else -
so :meth:`OdooPage._on_recovery_failed` reports it and leaves the account and
its vault entry alone.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QLabel,
    QMessageBox,
    QProgressBar,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from bytesraw_erp.constants import (
    APP_NAME,
    APP_VERSION,
    ODOO_HOME_PATH,
    ODOO_LOGIN_PATH,
    POS_PATH_PREFIX,
    ROUTE_ACCOUNT_NEW,
    ROUTE_ACCOUNTS,
    ROUTE_SETTINGS,
    SESSION_PROBE_SECONDS,
)
from bytesraw_erp.core.errors import (
    BytesrawError,
    OdooCredentialsRejected,
    OdooSessionExpired,
)
from bytesraw_erp.core.paths import downloads_dir
from bytesraw_erp.data.models import (
    NO_PRINTER,
    Account,
    PrintMode,
    PrintSettings,
    SessionContext,
)
from bytesraw_erp.services.print_service import PrintError
from bytesraw_erp.services.session_service import (
    apply_odoo_color_scheme,
    build_session_context,
    open_session,
    probe_session,
    set_user_language,
)
from bytesraw_erp.services.tasks import run_async
from bytesraw_erp.services.update_service import Update
from bytesraw_erp.ui.app_context import AppContext
from bytesraw_erp.ui.router import Router
from bytesraw_erp.ui.theme import Palette
from bytesraw_erp.ui.widgets.app_bar import AppBar
from bytesraw_erp.ui.widgets.banner import Banner
from bytesraw_erp.ui.widgets.toast import Toast, ToastArea
from bytesraw_erp.ui.widgets.web_view import OdooWebView

_log = logging.getLogger(__name__)

#: An update toast is a decision, not an acknowledgement, so it stays long
#: enough to be read and acted on rather than fading like a download notice.
_UPDATE_TOAST_MS = 30_000
#: The download toast has to outlive a 140 MB transfer on a till's connection.
#: It is dismissed explicitly when the download ends, in either direction, so
#: this is only the backstop for a transfer that stalls without erroring.
_DOWNLOAD_TOAST_MS = 30 * 60 * 1000


def _free_download_path(name: str) -> Path:
    """A path in Downloads that nothing is using, from ``name``.

    Chromium numbers a duplicate download itself; a file this app writes has to
    do its own, or the second report of the day silently replaces the first.
    ``downloads_dir`` is looked up here, in this module, which is also the name
    the tests patch - importing it by value means patching ``core.paths`` does
    nothing and a stray test writes into a real Downloads folder.
    """
    stem, suffix = Path(name).stem, Path(name).suffix
    target = downloads_dir() / name
    counter = 1
    while target.exists():
        target = downloads_dir() / f"{stem} ({counter}){suffix}"
        counter += 1
    return target


class OdooPage(QWidget):
    """Hosts the app bar plus one :class:`OdooWebView` per active account."""

    def __init__(self, context: AppContext, router: Router, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._router = router
        self._web: OdooWebView | None = None
        self._web_account_id: str | None = None
        #: True while a silent re-authentication is running, so the four
        #: detectors cannot each start one for the same expiry.
        self._recovering = False
        #: The last Odoo path the user was actually on. Tracked here rather
        #: than read back from the account registry: the store hands out fresh
        #: copies, so the adopted ``Account`` goes stale the moment a path is
        #: remembered.
        self._last_path: str | None = None
        #: Where to return once the session is back.
        self._resume_path: str | None = None
        #: One silent retry per expiry, put back by the first proof that the
        #: recovered session works - a page that loads, or a probe that is
        #: answered. A server that bounces the replacement straight back is not
        #: going to be fixed by a third attempt; a session that lived for hours
        #: and then expired again is not that server.
        self._recovery_spent = False
        #: The download toast, kept so its text can be rewritten as the
        #: transfer proceeds instead of stacking one toast per percent.
        self._update_toast: Toast | None = None

        #: Liveness probe and keepalive in one. Started when a session is
        #: adopted, stopped whenever there is none - a timer firing RPC calls
        #: at a signed-out app would resurrect the status panel behind the
        #: account list.
        self._probe = QTimer(self)
        self._probe.setInterval(SESSION_PROBE_SECONDS * 1000)
        self._probe.timeout.connect(self._probe_session)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._app_bar = AppBar()
        layout.addWidget(self._app_bar)
        self._connect_app_bar()

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(2)
        self._progress.hide()
        layout.addWidget(self._progress)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)

        self._status = self._build_status_panel()
        self._stack.addWidget(self._status)

        #: Download and print notifications, floated over the web view.
        self._toasts = ToastArea(self._stack)

        # The app bar is a pure projection of the session, so it follows the
        # context rather than being poked from every action that changes it.
        context.session_changed.connect(self._app_bar.set_session)
        context.theme.theme_changed.connect(self._on_theme_changed)
        context.printing.finished.connect(self._on_print_finished)
        context.printing.saved.connect(self._on_page_saved)
        context.profiles.report_downloaded.connect(self._on_report_downloaded)
        context.profiles.file_downloaded.connect(self._on_file_downloaded)
        # A failed *check* is deliberately not connected: a till between access
        # points fails one every four hours, and a toast each time would teach
        # the user to ignore toasts. The settings page is where a check that
        # was asked for reports back.
        context.updates.update_available.connect(self._on_update_available)
        context.updates.download_started.connect(self._on_update_download_started)
        context.updates.download_progress.connect(self._on_update_progress)
        context.updates.download_finished.connect(self._on_update_installing)
        context.updates.download_failed.connect(self._on_update_failed)
        self._app_bar.apply_theme(context.theme.palette, context.theme.theme)

    def _build_status_panel(self) -> QWidget:
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(48, 48, 48, 48)
        panel_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label = QLabel("Connecting to Odoo...")
        self._status_label.setObjectName("MutedLabel")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_banner = Banner()
        self._status_banner.setMaximumWidth(520)
        panel_layout.addWidget(self._status_label)
        panel_layout.addWidget(self._status_banner)
        return panel

    def _connect_app_bar(self) -> None:
        bar = self._app_bar
        bar.back_requested.connect(lambda: self._web and self._web.back())
        bar.forward_requested.connect(lambda: self._web and self._web.forward())
        bar.reload_requested.connect(lambda: self._web and self._web.reload())
        bar.home_requested.connect(self._go_odoo_home)
        bar.language_changed.connect(self._change_language)
        bar.switch_account_requested.connect(lambda: self._router.go(ROUTE_ACCOUNTS))
        bar.manage_accounts_requested.connect(lambda: self._router.go(ROUTE_ACCOUNTS))
        bar.sign_out_requested.connect(self._sign_out)
        bar.print_requested.connect(self._print)
        bar.theme_requested.connect(self._context.theme.set_theme)
        bar.settings_requested.connect(lambda: self._router.go(ROUTE_SETTINGS))

    # -- router hooks ------------------------------------------------------

    def on_enter(self, _params: dict[str, str]) -> None:
        account = self._context.store.active
        if account is None:
            self._router.reset_to(ROUTE_ACCOUNT_NEW)
            return

        live = self._context.session is not None and self._context.account is not None
        if live and self._context.account.id == account.id:
            self._show_web(account)
            self._app_bar.set_session(self._context.session)
            return

        self._authenticate(account)

    # -- authentication ----------------------------------------------------

    def _authenticate(self, account: Account) -> None:
        try:
            password = self._context.store.get_password(account.id)
        except BytesrawError as exc:
            self._show_error(str(exc))
            return

        if not password:
            # The vault entry is gone (cleared profile, restored machine).
            # Send the user to the form rather than failing cryptically.
            _log.warning("No stored password for account %s; opening the form", account.id)
            self._router.go(f"/accounts/{account.id}/edit")
            return

        self._show_status(f"Signing in to {account.name}...")

        def done(result: object) -> None:
            client, session = result  # type: ignore[misc]
            self._context.adopt_session(account, client, session)
            self._recovery_spent = False
            self._probe.start()
            self._show_web(account, reload_home=True)

        run_async(open_session, account, password, on_success=done, on_error=self._on_auth_failed)

    def _on_auth_failed(self, exc: Exception) -> None:
        account = self._context.store.active
        if isinstance(exc, OdooCredentialsRejected) and account is not None:
            self._discard_rejected_account(account, exc)
            return
        self._show_error(
            f"{exc}\n\nChoose 'Manage accounts' from the menu to correct the "
            "connection details."
        )

    def _discard_rejected_account(self, account: Account, exc: Exception) -> None:
        """Delete a saved account the server will never accept, and say so.

        Only :class:`OdooCredentialsRejected` reaches here: the server answered,
        and answered that these details are wrong. A connection failure, a
        timeout or a two-factor prompt leaves the account alone - those are
        temporary, or need a different fix, and deleting on them would throw a
        working account away because the network was down.

        The details themselves are not secret and are tedious to retype, so
        they are carried into the add form. Only the password is gone, and that
        is the part that has to be typed again anyway.
        """
        _log.warning("Removing account %s - the server rejected it: %s", account.id, exc)
        try:
            self._context.remove_account(account.id)
        except BytesrawError as removal_failed:
            self._show_error(f"{exc}\n\n{removal_failed}")
            return

        notice = (
            f"{exc}\n\n'{account.name}' has been removed from this computer, "
            "along with its saved password. Nothing changed on the Odoo server."
        )
        if self._context.store.is_empty:
            self._context.post_notice(notice, prefill=account)
            self._router.reset_to(ROUTE_ACCOUNT_NEW)
        else:
            self._context.post_notice(notice)
            self._router.reset_to(ROUTE_ACCOUNTS)

    # -- session expiry ----------------------------------------------------

    def _probe_session(self) -> None:
        """Ask Odoo whether the session is still there, and touch it if it is.

        Deliberately silent in both directions: a probe that succeeds shows
        nothing, and one that fails on the network shows nothing either - the
        till may simply be between access points, and the next probe will say
        so. Only an answer that the *session* is gone is acted on.
        """
        client = self._context.client
        if client is None or self._recovering:
            return

        def answered(_result: object) -> None:
            # The session is demonstrably alive, so whatever it took to get
            # here worked and the next expiry deserves its own silent retry.
            # Only a *successful* probe can say that: a recovery is otherwise
            # indistinguishable from a server that will bounce the replacement
            # too, which is the case the allowance exists to stop.
            self._recovery_spent = False

        def failed(exc: Exception) -> None:
            if isinstance(exc, OdooSessionExpired):
                self._recover_session("Odoo signed this session out.")
            else:
                _log.debug("Session probe could not reach the server: %s", exc)

        run_async(probe_session, client, on_success=answered, on_error=failed)

    def _on_web_session_expired(self) -> None:
        """The embedded client's own RPC call came back with the fault.

        The fourth detector, and the only one that fires while the user is
        actually looking at the problem. Odoo's client does not navigate on an
        expired session - it shows a modal and waits for a click - so without
        this the shell learns nothing until the probe comes round, up to
        ``SESSION_PROBE_SECONDS`` later. The reload at the end of recovery
        disposes of Odoo's dialog on the way past.
        """
        self._recover_session("Odoo's web client reported the session as expired.")

    def _on_session_expired(self, exc: Exception) -> bool:
        """Route an expired-session fault from an RPC call into recovery.

        Returns whether it handled ``exc``, so a caller's own error branch can
        step aside rather than showing a message about a session that is
        already being replaced.
        """
        if not isinstance(exc, OdooSessionExpired):
            return False
        self._recover_session(str(exc))
        return True

    def _recover_session(self, reason: str) -> None:
        """Sign in again with the stored password, without asking the user.

        The details in the vault are still correct - the server has forgotten
        the session, not rejected the account - so there is nothing to ask.
        What the user gets is the page they were on, reloaded, and a toast
        saying it happened, because silently re-authenticating with no trace at
        all is indistinguishable from a page that simply refreshed itself.
        """
        account = self._context.account or self._context.store.active
        if account is None or self._recovering:
            return
        if self._recovery_spent:
            # Straight after a recovery, before a single probe has come back
            # healthy - so the replacement session was refused as fast as it
            # was issued, and a third sign-in would only be the start of a loop.
            _log.warning(
                "Session expired again straight after a recovery; giving up: %s", reason
            )
            self._show_error(
                "The Odoo session keeps expiring. Sign in again from "
                "'Manage accounts', or check the server's session settings."
            )
            return

        try:
            password = self._context.store.get_password(account.id)
        except BytesrawError as exc:
            self._show_error(str(exc))
            return
        if not password:
            # Nothing to sign in with. The form is the only honest destination.
            self._router.go(f"/accounts/{account.id}/edit")
            return

        _log.info("Re-authenticating %s silently: %s", account.name, reason)
        self._recovering = True
        self._recovery_spent = True
        self._probe.stop()
        # The redirect to /web/login is already on its way; `_last_path` is
        # the last place the user actually chose, because the login path is
        # never recorded there.
        self._resume_path = self._resume_path or self._last_path

        def done(result: object) -> None:
            client, session = result  # type: ignore[misc]
            self._recovering = False
            self._context.adopt_session(account, client, session)
            self._probe.start()
            self._resume()
            self._toasts.show_message("Signed back in to Odoo.")

        def failed(exc: Exception) -> None:
            self._recovering = False
            self._on_recovery_failed(exc)

        run_async(open_session, account, password, on_success=done, on_error=failed)

    def _on_recovery_failed(self, exc: Exception) -> None:
        """Report a silent re-authentication the server refused.

        Deliberately **not** :meth:`_on_auth_failed`. That branch deletes an
        account the server rejects, which is the right answer when the user
        has just typed details that cannot work - they are still on screen,
        they know what they entered, and the form is carrying it back to them.

        Here nobody asked for anything. Somebody was working, the session went,
        and details that were correct minutes ago are not any more: an
        administrator changed the password, archived the user, or revoked their
        access. Deleting the account and emptying its vault entry on that would
        destroy a working till configuration behind the user's back, over a
        change they may not even have been told about. So the account stays
        exactly as it is and the message says where to go and fix it.
        """
        _log.warning("Could not renew the Odoo session: %s", exc)
        if isinstance(exc, OdooCredentialsRejected):
            self._show_error(
                f"{exc}\n\nThe Odoo session expired and the saved password is no "
                "longer accepted. Choose 'Manage accounts' from the menu to sign "
                "in again or update the account."
            )
            return
        self._show_error(
            f"{exc}\n\nThe Odoo session expired and could not be renewed. "
            "Choose 'Manage accounts' from the menu to sign in again."
        )

    def _resume(self) -> None:
        """Put the web view back where the user was, on the new session."""
        path, self._resume_path = self._resume_path, None
        if self._web is None:
            account = self._context.account
            if account is not None:
                self._show_web(account, reload_home=True)
            return
        self._web.open_path(path or ODOO_HOME_PATH)
        self._stack.setCurrentWidget(self._web)

    # -- web surface -------------------------------------------------------

    def _show_web(self, account: Account, *, reload_home: bool = False) -> None:
        if self._web is None or self._web_account_id != account.id:
            self._replace_web_view(account)
            reload_home = True

        assert self._web is not None
        if reload_home:
            self._web.open_home()
        self._stack.setCurrentWidget(self._web)

    def _replace_web_view(self, account: Account) -> None:
        """Build the browser surface for ``account`` and retire the previous one."""
        if self._web is not None:
            self._stack.removeWidget(self._web)
            self._web.deleteLater()

        profile = self._context.profiles.profile_for(account)
        web = OdooWebView(account, profile, self)
        web.path_changed.connect(self._on_path_changed)
        web.loading_changed.connect(self._on_loading_changed)
        web.print_requested.connect(self._on_page_print_requested)
        web.session_expired.connect(self._on_web_session_expired)
        web.page_loaded.connect(self._on_page_loaded)
        self._stack.addWidget(web)
        self._web = web
        self._web_account_id = account.id

        # A fresh view starts with default settings, so re-apply the theme and
        # the account's print preference before it is shown.
        palette = self._context.theme.palette
        session = self._context.session
        self._context.profiles.set_color_scheme(account, palette.is_dark)
        web.set_dark_mode(palette.is_dark, force=not (session and session.native_dark_mode))
        self._app_bar.set_default_print_mode(self._context.settings.printing.mode)

    def _on_path_changed(self, path: str) -> None:
        """Remember where the user is, and notice when Odoo bounces them out.

        A redirect to ``/web/login`` is how an expired session reaches a
        browser: Odoo's dispatcher logs the session out and redirects there
        (``odoo/http.py:2516``). It is also the first thing the *user* sees, so
        it is the detector that matters most - the RPC probe may be four
        minutes away.

        That path is never remembered as somewhere to resume, either. Storing
        it would make the login screen the landing page on the next launch.
        """
        account = self._context.account or self._context.store.active
        if account is None or not path.startswith("/"):
            return

        if path.startswith(ODOO_LOGIN_PATH):
            self._recover_session("Odoo redirected the web view to its login page.")
            return

        self._last_path = path
        try:
            self._context.store.remember_path(account.id, path)
        except BytesrawError as exc:
            _log.warning("Could not remember the last path: %s", exc)

    def _on_page_loaded(self, path: str) -> None:
        """An Odoo page finished loading, so restore the one silent retry.

        The server answered a real navigation with a real page instead of
        bouncing it to the login screen, which is the same proof of life a
        successful probe carries - and it arrives seconds after a recovery
        rather than up to ``SESSION_PROBE_SECONDS`` later. Without it the
        allowance stays spent for the whole probe interval, so a second expiry
        inside that window is met with "the session keeps expiring" although
        the first recovery plainly worked.

        It has to be the *finished* load rather than ``path_changed``: that one
        fires the moment a navigation is requested, so it would clear the latch
        on the very navigation a dead session is about to have redirected.
        """
        if path.startswith(ODOO_LOGIN_PATH):
            return
        self._recovery_spent = False

    def _on_loading_changed(self, loading: bool) -> None:
        self._progress.setVisible(loading)
        if self._web is not None:
            history = self._web.history()
            self._app_bar.set_navigation_state(
                can_go_back=history.canGoBack(),
                can_go_forward=history.canGoForward(),
            )

    def _go_odoo_home(self) -> None:
        if self._web is not None:
            self._web.open_path(ODOO_HOME_PATH)

    # -- app bar actions ---------------------------------------------------

    def _change_language(self, code: str) -> None:
        """Write the user's language, then re-read the session and reload."""
        session = self._context.session
        client = self._context.client
        if session is None or client is None:
            return

        self._show_status("Applying language...")

        def apply() -> SessionContext:
            set_user_language(client, session.uid, code)
            return build_session_context(client, client.session_info())

        def done(new_session: SessionContext) -> None:
            self._context.update_session(new_session)
            account = self._context.account
            if account is not None:
                self._show_web(account)
            if self._web is not None:
                self._web.reload()

        def failed(exc: Exception) -> None:
            self._app_bar.set_session(session)  # roll the combo back
            if self._on_session_expired(exc):
                return
            self._show_error(str(exc))

        run_async(apply, on_success=done, on_error=failed)

    def _sign_out(self) -> None:
        account = self._context.account
        if account is not None:
            self._context.profiles.clear_session(account)
        self._probe.stop()
        self._recovering = False
        self._resume_path = None
        self._context.release_session()
        self._app_bar.set_session(None)
        self._release_web()
        self._router.reset_to(ROUTE_ACCOUNTS)

    def _release_web(self) -> None:
        if self._web is None:
            return
        self._web.stop()
        self._stack.removeWidget(self._web)
        self._web.deleteLater()
        self._web = None
        self._web_account_id = None

    def release(self) -> None:
        """Drop the web view, on the way out of the application.

        Called by the window before it frees the profiles, because a profile
        released while a page still holds it takes the process down. Stopping
        the probe here matters as much: a timer that fires during teardown
        submits an RPC call nobody will be alive to hear the answer to.
        """
        self._probe.stop()
        self._release_web()

    # -- theme -------------------------------------------------------------

    def _on_theme_changed(self, palette: Palette) -> None:
        self._app_bar.apply_theme(palette, self._context.theme.theme)
        self._apply_theme_to_odoo(palette)

    def _apply_theme_to_odoo(self, palette: Palette) -> None:
        """Carry the app's appearance into the embedded Odoo client.

        Three mechanisms, applied according to what the server actually
        supports - measured at sign-in, not guessed from the edition:

        1. ``res.users.settings.color_scheme``, when that field exists. It is
           added by an addon rather than by core, and it **outranks the
           cookie** unless it holds "system" - so on a server that has it,
           writing the cookie alone achieves nothing. This is the same record
           the theme-switcher addon's own JavaScript writes, so Odoo's user
           menu and this app agree instead of overriding each other.
        2. The ``color_scheme`` cookie, which decides when the setting is
           "system", and is also read client-side for chart palettes, the code
           editor theme and the PDF viewer.
        3. Chromium's ForceDarkMode, only for a server with no dark stylesheet
           at all. Forcing it where one exists darkens the page twice.
        """
        account = self._context.account
        if account is None or self._web is None:
            return

        session = self._context.session
        native = bool(session and session.native_dark_mode)

        # Always right for the "system" case, and cheap.
        self._context.profiles.set_color_scheme(account, palette.is_dark)
        self._web.set_dark_mode(palette.is_dark, force=not native)

        client = self._context.client
        if session is not None and client is not None and session.can_set_odoo_theme:
            run_async(
                apply_odoo_color_scheme,
                client,
                session,
                self._context.theme.theme.odoo_color_scheme,
                on_success=lambda _r: self._reload_web(),
                on_error=self._on_theme_write_failed,
            )
            return

        self._reload_web()

    def _reload_web(self) -> None:
        if self._web is not None:
            self._web.reload()

    def _on_theme_write_failed(self, exc: Exception) -> None:
        """A failed theme write is not worth interrupting the user over.

        The cookie and the browser-level fallback are already applied, so the
        page still changes appearance; only Odoo's stored preference did not
        stick.
        """
        _log.warning("Could not store the Odoo colour scheme: %s", exc)
        self._reload_web()

    # -- printing ----------------------------------------------------------

    def _is_pos(self) -> bool:
        """Is the view showing Odoo's Point of Sale client?

        The page on screen is what decides which printer a page print belongs
        on, because that is what is about to come out of it: under ``/pos/`` it
        is a receipt for the thermal roll, anywhere else it is an A4 page.
        """
        return self._web is not None and self._web.current_path().startswith(POS_PATH_PREFIX)

    def _print(self, mode: PrintMode) -> None:
        """Print the page with an explicit mode, on the printer for this screen.

        This is the bar's menu: the user picked the mode themselves, so an
        unassigned printer is reported as the error it is rather than passed
        over quietly.
        """
        if self._web is None or self._stack.currentWidget() is not self._web:
            return
        if not mode.prints:
            self._save_page()
            return
        settings = self._context.settings.printing
        try:
            self._context.printing.print_view(
                self._web, mode, self, settings.printer_for(pos=self._is_pos())
            )
        except PrintError as exc:
            QMessageBox.warning(self, "Print", str(exc))

    def _on_page_print_requested(self) -> None:
        """Odoo called ``window.print()`` - a POS receipt, or the PDF viewer.

        Honours the configured mode, so a till set to print directly puts a
        receipt on paper without anyone touching a dialog.

        Odoo drove this, not the user, so an unassigned printer is not an error
        here - it is the configuration doing what it was asked. It still gets a
        toast: a Print button that produces nothing anywhere, with no word of
        why, is the silent loss this file already learned about once.
        """
        settings = self._context.settings.printing
        if not settings.mode.prints:
            self._save_page()
            return
        if settings.printer_for(pos=self._is_pos()) is NO_PRINTER:
            which = "receipt" if self._is_pos() else "report"
            _log.info("No %s printer is assigned; the page was not printed", which)
            self._toasts.show_message(
                f"No {which} printer is assigned, so nothing was printed.",
                action_text="Settings",
                on_action=lambda: self._router.go(ROUTE_SETTINGS),
            )
            return
        self._print(settings.mode)

    def _save_page(self) -> None:
        """Write the page to Downloads instead of printing it.

        The whole of :attr:`PrintMode.SAVE`. The name is what the file will be
        called in the user's Downloads folder, so it says what the document is
        rather than repeating the page title, which in Odoo is the record's.
        """
        if self._web is None or self._stack.currentWidget() is not self._web:
            return
        name = "POS receipt.pdf" if self._is_pos() else "Odoo page.pdf"
        self._context.printing.save_view(self._web, _free_download_path(name))

    def _on_page_saved(self, path: Path) -> None:
        """A page was written to Downloads rather than printed."""
        self._toasts.show_message(
            f"Saved {path.name} to {path.parent.name}",
            action_text="Show in folder",
            on_action=lambda: self._reveal(path),
        )

    def _on_report_downloaded(self, path: Path) -> None:
        """A QWeb report PDF arrived from Odoo. Print it per local settings.

        This is what makes Odoo's own Print button reach paper: the web client
        answers a print action with a PDF download, which a plain browser would
        simply drop in the Downloads folder. It always prints on the A4
        printer - a report is an A4 document even when POS produced it.

        Three separate things can say the report is not to be printed, and they
        are told apart on purpose: the mode saves instead of printing, no A4
        printer is assigned, or automatic printing is simply off. All three end
        in a Downloads copy and a toast that says which one it was, because a
        report that is neither printed nor findable is gone - the scratch
        directory it arrived in is pruned within a day.
        """
        settings = self._context.settings.printing
        printing = settings.prints_reports()
        # Copy it when asked to, and whenever nothing is going to print it: in
        # that case Downloads is the only place it will still exist tomorrow.
        copied = self._keep_copy(path) if settings.keep_report_copy or not printing else None

        if not printing:
            landed = copied or path
            reason = self._why_not_printed(settings)
            _log.info("%s; kept %s", reason, landed)
            self._toasts.show_message(
                f"Saved {landed.name} - {reason.lower()}",
                action_text="Show in folder",
                on_action=lambda: self._reveal(landed),
            )
            return

        try:
            self._context.printing.print_pdf_with(path, settings, self)
        except PrintError as exc:
            QMessageBox.warning(self, "Print", str(exc))

    @staticmethod
    def _why_not_printed(settings: PrintSettings) -> str:
        """Which of the three reasons stopped this report at the Downloads folder."""
        if not settings.mode.prints:
            return "Reports are set to be saved"
        if settings.report_printer_name is NO_PRINTER:
            return "No report printer is assigned"
        return "Automatic report printing is off"

    def _keep_copy(self, path: Path) -> Path | None:
        """Copy a report into Downloads, without clobbering a namesake."""
        target = _free_download_path(path.name)
        try:
            shutil.copy2(path, target)
        except OSError as exc:
            _log.warning("Could not keep a copy of %s: %s", path.name, exc)
            return None
        _log.info("Kept a copy of %s at %s", path.name, target)
        return target

    def _on_file_downloaded(self, path: Path) -> None:
        """Tell the user where a download landed, and offer to reveal it.

        Odoo gives no feedback of its own once the browser takes over a
        download, so without this a file saved from the chatter simply appears
        to do nothing.
        """
        self._toasts.show_message(
            f"Saved {path.name} to {path.parent.name}",
            action_text="Show in folder",
            on_action=lambda: self._reveal(path),
        )

    @staticmethod
    def _reveal(path: Path) -> None:
        """Open the containing folder in Explorer, selecting the file if possible."""
        if not path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
            return
        # Explorer selects the file when given /select; fall back to opening
        # the folder if it is not available.
        if not QProcess.startDetached("explorer", ["/select,", str(path)]):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    # -- updates -----------------------------------------------------------

    def _on_update_available(self, update: object) -> None:
        """Offer a newer build. One toast, one action, no dialog.

        Not a modal: the user may be halfway through a sale, and an update is
        never more important than the transaction in front of them. A required
        update says so and is still their click to make - forcing a 140 MB
        download onto a till mid-sale would be the worse failure.
        """
        if not isinstance(update, Update):  # pragma: no cover - defensive
            return
        message = f"{APP_NAME} {update.version} is available ({update.artifact.size_mb})."
        if update.is_required_for(APP_VERSION):
            message += " This update is required."
        self._toasts.show_message(
            message,
            action_text="Update now",
            on_action=lambda: self._context.updates.download_and_install(update),
            linger_ms=_UPDATE_TOAST_MS,
        )

    def _on_update_download_started(self, update: object) -> None:
        version = update.version if isinstance(update, Update) else ""
        self._show_update_message(
            f"Downloading {APP_NAME} {version}...", linger_ms=_DOWNLOAD_TOAST_MS
        )

    def _on_update_progress(self, received: int, total: int) -> None:
        if self._update_toast is None:
            return
        share = f"{received * 100 // total}%" if total > 0 else ""
        self._toasts.update_message(
            self._update_toast, f"Downloading the update... {share}".rstrip()
        )

    def _on_update_installing(self, _path: object) -> None:
        """The installer verified and is about to run.

        The app is not quit here, or anywhere: Inno's Restart Manager closes it,
        replaces the files and starts it again - see
        :mod:`bytesraw_erp.services.update_service`. All this does is say so,
        because a till that closes itself with no warning reads as a crash.
        """
        # The reference is kept rather than cleared: a declined UAC prompt
        # arrives as a failure moments later, and that has to replace this
        # message instead of appearing underneath it.
        self._show_update_message(f"Installing the update. {APP_NAME} will close and reopen.")

    def _on_update_failed(self, message: str) -> None:
        self._show_update_message(message)
        # Terminal, so the toast is let go of: whatever comes next is a new
        # attempt and deserves its own notification rather than overwriting the
        # reason the last one stopped.
        self._update_toast = None

    def _show_update_message(self, message: str, *, linger_ms: int = _UPDATE_TOAST_MS) -> None:
        """Put ``message`` in the update toast, reusing the one already on screen.

        Dismissing one and showing another looks equivalent and is not: a
        dismissed toast *fades*, so for a third of a second the stale
        "Downloading..." would sit next to the message explaining that the
        download stopped.
        """
        if self._update_toast is not None:
            self._toasts.update_message(self._update_toast, message, linger_ms=linger_ms)
            return
        self._update_toast = self._toasts.show_message(message, linger_ms=linger_ms)
        self._update_toast.closed.connect(self._forget_update_toast)

    def _forget_update_toast(self) -> None:
        self._update_toast = None

    def _on_print_finished(self, ok: bool, message: str) -> None:
        if ok:
            self._toasts.show_message(message or "Printed.")
            return
        if message:
            QMessageBox.warning(self, "Print", message)

    # -- status panel ------------------------------------------------------

    def _show_status(self, message: str) -> None:
        self._status_label.setText(message)
        self._status_banner.clear_message()
        self._stack.setCurrentWidget(self._status)

    def _show_error(self, message: str) -> None:
        self._status_label.setText("")
        self._status_banner.show_error(message)
        self._stack.setCurrentWidget(self._status)
