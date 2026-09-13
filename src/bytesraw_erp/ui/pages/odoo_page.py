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
* nothing at all happens, because the till has been idle. A probe every
  ``SESSION_PROBE_SECONDS`` covers that case and, since Odoo's
  ``get_session_info`` calls ``session.touch()``, doubles as the keepalive that
  stops the session expiring in the first place.

All three funnel into :meth:`OdooPage._recover_session`, which signs in again
with the password already in the vault and puts the user back on the page they
were on. Nothing is asked of them: the credentials have not changed, only the
server's memory of the session has. The one thing that must not happen is a
loop - a server that keeps refusing gets one attempt, after which the failure
is shown like any other.
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
from bytesraw_erp.data.models import Account, PrintMode, SessionContext
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


class OdooPage(QWidget):
    """Hosts the app bar plus one :class:`OdooWebView` per active account."""

    def __init__(self, context: AppContext, router: Router, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._router = router
        self._web: OdooWebView | None = None
        self._web_account_id: str | None = None
        #: True while a silent re-authentication is running, so the three
        #: detectors cannot each start one for the same expiry.
        self._recovering = False
        #: The last Odoo path the user was actually on. Tracked here rather
        #: than read back from the account registry: the store hands out fresh
        #: copies, so the adopted ``Account`` goes stale the moment a path is
        #: remembered.
        self._last_path: str | None = None
        #: Where to return once the session is back.
        self._resume_path: str | None = None
        #: One silent retry per expiry. A server that answers the re-login with
        #: another expired session is not going to be fixed by a third attempt,
        #: and a page that keeps signing itself in is a page in a loop.
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

        self._app_bar = AppBar(allow_full_screen=context.windowed)
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

        def failed(exc: Exception) -> None:
            if isinstance(exc, OdooSessionExpired):
                self._recover_session("Odoo signed this session out.")
            else:
                _log.debug("Session probe could not reach the server: %s", exc)

        run_async(probe_session, client, on_error=failed)

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
            _log.warning("Session expired again straight after a recovery; giving up")
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
            self._on_auth_failed(exc)

        run_async(open_session, account, password, on_success=done, on_error=failed)

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

    def _print(self, mode: PrintMode) -> None:
        """Print the page with an explicit mode, on the configured printer."""
        if self._web is None or self._stack.currentWidget() is not self._web:
            return
        try:
            self._context.printing.print_view(
                self._web, mode, self, self._context.settings.printing.printer_name
            )
        except PrintError as exc:
            QMessageBox.warning(self, "Print", str(exc))

    def _on_page_print_requested(self) -> None:
        """Odoo called ``window.print()`` - a POS receipt, or the PDF viewer.

        Honours the configured mode, so a till set to print directly puts a
        receipt on paper without anyone touching a dialog.
        """
        self._print(self._context.settings.printing.mode)

    def _on_report_downloaded(self, path: Path) -> None:
        """A QWeb report PDF arrived from Odoo. Print it per local settings.

        This is what makes Odoo's own Print button reach paper: the web client
        answers a print action with a PDF download, which a plain browser would
        simply drop in the Downloads folder.
        """
        settings = self._context.settings.printing
        if settings.keep_report_copy:
            self._keep_copy(path)

        if not settings.auto_print_reports:
            _log.info("Automatic report printing is off; kept %s", path)
            return

        try:
            self._context.printing.print_pdf_with(path, settings, self)
        except PrintError as exc:
            QMessageBox.warning(self, "Print", str(exc))

    def _keep_copy(self, path: Path) -> None:
        """Copy a printed report into Downloads, without clobbering a namesake."""
        target = downloads_dir() / path.name
        counter = 1
        while target.exists():
            target = downloads_dir() / f"{path.stem} ({counter}){path.suffix}"
            counter += 1
        try:
            shutil.copy2(path, target)
            _log.info("Kept a copy of %s at %s", path.name, target)
        except OSError as exc:
            _log.warning("Could not keep a copy of %s: %s", path.name, exc)

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
