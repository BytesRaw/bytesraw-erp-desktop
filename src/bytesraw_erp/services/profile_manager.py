"""Per-account QtWebEngine profiles.

Each Odoo account gets its own :class:`QWebEngineProfile` backed by its own
on-disk storage directory. That is what makes "multiple accounts" real: two
profiles never share a cookie jar, so the same user can keep sessions open on
two databases - or two logins on one database - at the same time.

Profiles are cached for the lifetime of the process. QtWebEngine crashes if a
profile is garbage collected while a page still uses it, so the manager holds
the only reference and pages borrow it.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import (
    QWebEngineCookieStore,
    QWebEngineDownloadRequest,
    QWebEngineProfile,
)

from bytesraw_erp.constants import (
    APP_VERSION,
    ATTACHMENT_DOWNLOAD_URL_PREFIXES,
    COLOR_SCHEME_COOKIE,
    REPORT_URL_PREFIXES,
    SESSION_COOKIE,
)
from bytesraw_erp.core.paths import (
    downloads_dir,
    profile_cache_dir,
    profile_storage_dir,
    reports_dir,
)
from bytesraw_erp.data.models import Account
from bytesraw_erp.services.report_watcher import (
    ClaimedReport,
    ReportRequestWatcher,
    report_name_from_path,
)

_log = logging.getLogger(__name__)

#: Appended to Chromium's UA so server-side logs and Odoo modules can tell the
#: desktop shell apart from a plain browser.
_UA_SUFFIX = f"BytesrawERP/{APP_VERSION}"


def is_report_url(url: str) -> bool:
    """Does this URL carry a rendered QWeb report?

    Matched on the path only, so a query string (``/report/download?data=...``,
    which is how the web client's Print button calls it) still counts.
    """
    path = QUrl(url).path()
    return any(path.startswith(prefix) for prefix in REPORT_URL_PREFIXES)


def is_attachment_download_url(url: str) -> bool:
    """Would navigating to this URL only ever produce a download, never a page?

    Used to stop :class:`~bytesraw_erp.ui.widgets.web_view.OdooWebPage` from
    navigating its visible view to one of these - see
    ``OdooWebPage._on_new_window_requested``.
    """
    path = QUrl(url).path()
    return any(path.startswith(prefix) for prefix in ATTACHMENT_DOWNLOAD_URL_PREFIXES)


#: How long a printed report PDF is kept in the scratch directory. Long enough
#: to re-open one that was just printed, short enough that the cache does not
#: grow by a file per printed invoice forever.
_REPORT_RETENTION_SECONDS = 24 * 60 * 60


def _unique_report_name(suggested: str) -> str:
    """A file name that does not exist in the reports directory yet.

    Two prints of the same invoice arrive with the same suggested name, so the
    second aims at the file the first produced. Chromium's own handling of that
    is not something to rely on: measured on this machine it renamed to
    "invoice (1).pdf" in most runs, but overwrote in place when the name had
    been set explicitly - and an overwrite of a file something else still holds
    open fails the download outright, with only "The file cannot be written
    locally, due to access restrictions" to show for it.

    Picking a free name here makes the outcome ours rather than Chromium's.
    """
    target = Path(suggested)
    stem, suffix = target.stem or "report", target.suffix or ".pdf"
    candidate = f"{stem}{suffix}"
    counter = 1
    while (reports_dir() / candidate).exists():
        candidate = f"{stem} ({counter}){suffix}"
        counter += 1
    return candidate


def _prune_reports(now: float | None = None) -> None:
    """Delete report PDFs left over from earlier prints.

    Reports are a means to an end - the copy the user asked to keep went to
    Downloads - so nothing here is a document of record. Failures are ignored:
    a file still open in a preview cannot be removed, and will be next time.
    """
    cutoff = (time.time() if now is None else now) - _REPORT_RETENTION_SECONDS
    for stale in reports_dir().glob("*.pdf"):
        try:
            if stale.stat().st_mtime < cutoff:
                stale.unlink()
        except OSError as exc:
            _log.debug("Could not prune %s: %s", stale.name, exc)


class ProfileManager(QObject):
    """Creates and owns one persistent web profile per account id."""

    #: A QWeb report PDF finished downloading, with the path it landed at and
    #: Odoo's technical name for the report - ``""`` when nothing named it.
    #: Printing is not done here - this layer has no business knowing the
    #: user's print settings - so the page decides what happens next.
    report_downloaded = Signal(Path, str)
    #: Any other download finished, saved in the Downloads folder.
    file_downloaded = Signal(Path)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._profiles: dict[str, QWebEngineProfile] = {}
        #: One watcher per profile. Must be kept referenced for as long as the
        #: profile it is installed on, or QtWebEngine calls into a dead object.
        self._watchers: dict[str, ReportRequestWatcher] = {}

    def profile_for(self, account: Account) -> QWebEngineProfile:
        existing = self._profiles.get(account.id)
        if existing is not None:
            return existing

        profile = QWebEngineProfile(f"bytesraw-{account.id}", self)
        profile.setPersistentStoragePath(str(profile_storage_dir(account.id)))
        profile.setCachePath(str(profile_cache_dir(account.id)))
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        profile.setHttpUserAgent(f"{profile.httpUserAgent()} {_UA_SUFFIX}")
        profile.setDownloadPath(str(downloads_dir()))
        profile.downloadRequested.connect(self._on_download_requested)

        watcher = ReportRequestWatcher()
        profile.setUrlRequestInterceptor(watcher)
        self._watchers[account.id] = watcher

        self._profiles[account.id] = profile
        _log.info("Created web profile for account %s", account.id)
        return profile

    def shutdown(self) -> None:
        """Release every profile, on the way out of the application.

        Must run **after** the pages that borrow these profiles are gone - a
        profile freed while a page still holds it takes the process with it.
        :meth:`bytesraw_erp.ui.main_window.MainWindow.closeEvent` guarantees
        that order.

        The interceptor is detached first: it is a Python object QtWebEngine
        calls into from the IO thread, and a request still in flight during
        teardown would otherwise reach an object that is being collected.
        Releasing here rather than leaving it to interpreter shutdown also puts
        Chromium's flush of its cookie jar and disk cache on the close path,
        where the window has already been hidden, instead of after it.
        """
        for account_id, profile in self._profiles.items():
            profile.setUrlRequestInterceptor(None)
            with contextlib.suppress(RuntimeError):  # already disconnected
                profile.downloadRequested.disconnect(self._on_download_requested)
            _log.debug("Released web profile for account %s", account_id)
            profile.deleteLater()
        self._profiles.clear()
        self._watchers.clear()

    def forget(self, account_id: str) -> None:
        """Drop a profile when its account is deleted."""
        profile = self._profiles.pop(account_id, None)
        if profile is not None:
            profile.setUrlRequestInterceptor(None)
            profile.deleteLater()
        self._watchers.pop(account_id, None)

    # -- session handoff ---------------------------------------------------

    def inject_session(self, account: Account, cookies: Mapping[str, str]) -> None:
        """Plant the RPC client's cookies into this account's cookie jar.

        This is what lets the app authenticate once over JSON-RPC and have the
        embedded browser land straight on ``/odoo`` already logged in, instead
        of replaying credentials into the HTML login form.

        The whole jar is transplanted, not just ``session_id``: a deployment
        behind a load balancer also sets a sticky routing cookie, and dropping
        it can send the browser to a backend that has never seen the session.

        **Host-only, exactly as the server writes them.** See
        :meth:`_write_cookie` - writing a *domain* cookie instead does not
        replace the server's, it shadows it, and the stale one wins.
        """
        origin = QUrl(account.url)
        host = urlparse(account.url).hostname or ""
        store = self.profile_for(account).cookieStore()

        for name, value in cookies.items():
            self._forget_domain_cookie(store, origin, host, name)
            # Only Odoo's own session cookie is HttpOnly; marking a routing
            # cookie HttpOnly would not match what the server set.
            self._write_cookie(
                store, origin, name, value, http_only=name == SESSION_COOKIE
            )

        _log.info(
            "Injected %d session cookie(s) for account %s: %s",
            len(cookies),
            account.id,
            ", ".join(sorted(cookies)),
        )

    @staticmethod
    def _write_cookie(
        store: QWebEngineCookieStore,
        origin: QUrl,
        name: str,
        value: str,
        *,
        http_only: bool = False,
    ) -> None:
        """Write one cookie the way the server would write it: **host-only**.

        The absent ``setDomain`` call is the whole point of this method, and it
        is not a tidy-up. A ``QNetworkCookie`` with a domain set is stored by
        Chromium as ``.example.com`` - a *domain* cookie - while Odoo's own
        ``Set-Cookie`` carries no ``Domain`` attribute and is stored as
        ``example.com``, host-only. Those are two different cookies under RFC
        6265, so they **coexist**: measured, setting one never removes the
        other, and the jar ends up holding two ``session_id`` entries for one
        site.

        Chromium then sends both, ordered by path length and then by creation
        time, and werkzeug's ``request.cookies.get('session_id')`` - which is
        what ``odoo/http.py:1486`` calls - takes the first. So whichever cookie
        was created *earlier* decides the session, whatever the other says.

        That is a bug with a fuse in it. A fresh profile holds no server
        cookie, so injection works and the first sign-in succeeds. But Odoo
        plants a host-only ``session_id`` of its own the first time a session
        expires - ``handle_error`` rotates the session and sets the cookie - and
        from that moment the server's dead cookie is the *older* of the two, so
        every re-authentication afterwards injected a perfectly good session
        that was never sent. Measured in a real profile::

            created=2026-09-21 06:34:00  host=demo.fatoora.cloud   session_id
            created=2026-09-21 07:25:09  host=.demo.fatoora.cloud  session_id

        with the app logging a successful sign-in and then a redirect to
        ``/web/login`` 280ms later, over and over. Writing host-only makes an
        injection *replace* the server's entry, because it is the same cookie.

        Do not "fix" this by naming the domain explicitly, and do not test it
        against ``127.0.0.1``: a ``Domain`` attribute on an IP literal cannot
        create a domain cookie, so the two forms collapse into one there and
        the bug disappears.
        """
        cookie = QNetworkCookie(name.encode(), value.encode())
        cookie.setPath("/")
        cookie.setHttpOnly(http_only)
        cookie.setSecure(origin.scheme() == "https")
        cookie.setSameSitePolicy(QNetworkCookie.SameSite.Lax)
        store.setCookie(cookie, origin)

    @staticmethod
    def _forget_domain_cookie(
        store: QWebEngineCookieStore, origin: QUrl, host: str, name: str
    ) -> None:
        """Retire the domain-scoped twin an earlier build of this app planted.

        Without this an upgrade fixes nothing: the shadowing pair is already in
        the user's profile, and writing the host-only half leaves the other one
        sitting beside it. Deliberately deletes only the *domain* form - the
        host-only entry is the one the write above replaces, and a delete
        racing that write could take the new session with it.

        It would also remove a domain cookie a load balancer set on purpose,
        for sharing across subdomains. That costs nothing here: the shell talks
        to exactly one host, so a host-only cookie reaches every request it
        makes, and the alternative is the shadowing this exists to end.
        """
        stale = QNetworkCookie(name.encode(), b"")
        stale.setDomain(host)
        stale.setPath("/")
        store.deleteCookie(stale, origin)

    def set_color_scheme(self, account: Account, dark: bool) -> None:
        """Tell Odoo which colour scheme to render.

        Odoo reads this cookie both server-side (asset bundle selection) and
        client-side (chart palettes, the code editor theme, the PDF viewer).
        Writing it is the supported way to theme the web client; how much of
        it actually changes depends on the edition - see
        ``SessionContext.native_dark_mode``.
        """
        self._set_cookie(account, COLOR_SCHEME_COOKIE, "dark" if dark else "light")

    def _set_cookie(self, account: Account, name: str, value: str) -> None:
        origin = QUrl(account.url)
        host = urlparse(account.url).hostname or ""
        store = self.profile_for(account).cookieStore()
        # Host-only, and the domain twin dropped, for the same reason
        # `inject_session` does it: Odoo reads `color_scheme` client-side too,
        # and a shadowed one leaves the web client rendering the other theme.
        self._forget_domain_cookie(store, origin, host, name)
        self._write_cookie(store, origin, name, value)

    def clear_session(self, account: Account) -> None:
        """Delete every cookie for the account, e.g. on explicit sign-out."""
        self.profile_for(account).cookieStore().deleteAllCookies()

    # -- downloads ---------------------------------------------------------

    def _claim_report(self, download: QWebEngineDownloadRequest) -> ClaimedReport | None:
        """Was this blob download preceded by an Odoo report request?

        Only blob downloads are considered: a report that arrives as a real
        navigation is already matched by its URL, and claiming a pending
        request for it would consume the evidence twice.
        """
        if download.url().scheme() != "blob":
            return None
        for watcher in self._watchers.values():
            # At most one pending request is consumed.
            if (claimed := watcher.claim()) is not None:
                return claimed
        return None

    def _on_download_requested(self, download: QWebEngineDownloadRequest) -> None:
        """Accept a download, routing report PDFs aside so they can be printed.

        Odoo triggers real downloads for PDF reports, XLSX exports and backups;
        without an explicit ``accept()`` QtWebEngine silently cancels them.

        A QWeb report is diverted to a scratch directory rather than Downloads:
        the user pressed Print in Odoo, so the PDF is a means to an end, and
        littering Downloads with one file per printed invoice is not what they
        asked for. Whether a copy is kept is a setting the page applies.
        """
        url = download.url().toString()
        is_pdf = download.mimeType() == "application/pdf"
        # Two ways a report can arrive. A direct navigation still carries the
        # report URL, and names the report when it is a `/report/pdf/` one;
        # Odoo 19's own Print button does not - it XHRs the report and saves a
        # blob, so the only evidence is the request the watcher saw going out
        # moments earlier, which carries the name the page stamped on it.
        report_name = ""
        if is_pdf and is_report_url(url):
            is_report = True
            report_name = report_name_from_path(download.url().path())
        elif is_pdf and (claimed := self._claim_report(download)) is not None:
            is_report = True
            report_name = claimed.name
        else:
            is_report = False

        if is_report:
            _prune_reports()
            download.setDownloadDirectory(str(reports_dir()))
            download.setDownloadFileName(_unique_report_name(download.downloadFileName()))
        else:
            download.setDownloadDirectory(str(downloads_dir()))

        target = Path(download.downloadDirectory()) / download.downloadFileName()

        def on_finished() -> None:
            if download.state() != QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
                _log.warning(
                    "Download of %s ended in state %s: %s",
                    target.name,
                    download.state(),
                    download.interruptReasonString(),
                )
                return
            if is_report:
                _log.info("QWeb report ready: %s (%s)", target, report_name or "unnamed")
                self.report_downloaded.emit(target, report_name)
            else:
                _log.info("Download finished: %s", target)
                self.file_downloaded.emit(target)

        download.isFinishedChanged.connect(on_finished)
        _log.info(
            "Accepting %s: %s -> %s",
            "report" if is_report else "download",
            download.downloadFileName(),
            download.downloadDirectory(),
        )
        download.accept()
