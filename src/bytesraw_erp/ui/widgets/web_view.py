"""The embedded Odoo client.

A thin wrapper around :class:`QWebEngineView` that adds the behaviour a desktop
shell needs and a bare browser view does not:

* links that leave the Odoo host open in the user's real browser, so a click on
  an external URL never strands the user inside a chromeless window;
* certificate errors are rejected unless the account explicitly opted in, which
  is the only supported way to reach an on-premise server with a self-signed
  certificate;
* the current Odoo path is published as a signal so it can be remembered and
  restored on the next launch;
* an Odoo session that dies underneath the running web client is reported to
  the shell straight away, rather than being left in the modal dialog Odoo
  puts it in - see :data:`_SESSION_EXPIRY_SCRIPT`;
* every report Odoo's Print button asks for carries its name out where the
  shell can read it, so a report can have a printer of its own - see
  :data:`_REPORT_NAME_SCRIPT`.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebEngineCore import (
    QWebEngineCertificateError,
    QWebEngineNewWindowRequest,
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineSettings,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QWidget

from bytesraw_erp.constants import REPORT_NAME_HEADER, SESSION_EXPIRED_NAME
from bytesraw_erp.data.models import Account
from bytesraw_erp.services.profile_manager import is_attachment_download_url

_log = logging.getLogger(__name__)

#: What the injected script writes to the console when it sees the fault, and
#: what :meth:`OdooWebPage.javaScriptConsoleMessage` watches for. The console
#: is the channel because it is the one QtWebEngine already hands to the page
#: object; a ``QWebChannel`` would mean injecting Qt's own transport script
#: into Odoo's page for a single one-way notification.
SESSION_EXPIRED_MARKER = "__bytesraw_session_expired__"

#: Ceiling on the response body the script will search. A JSON-RPC fault is a
#: few hundred bytes even with a traceback in it; an ordinary Odoo response is
#: routinely megabytes and can never contain the marker, so scanning one is
#: pure cost.
_MAX_SCANNED_RESPONSE = 1 << 20


#: Tell the shell when Odoo's *own* client loses the session.
#:
#: This is the detector that matters for a session revoked on the server while
#: someone is working, and none of the shell's other three see it. Odoo's web
#: client catches ``odoo.http.SessionExpiredException`` from its own RPC calls
#: and hands it to ``SessionExpiredDialog``
#: (``addons/web/static/src/core/errors/error_dialogs.js:218``), which shows
#: "Your Odoo session expired. The current page is about to be refreshed." and
#: then *waits for a click* before reloading. So:
#:
#: * the web view never navigates to ``/web/login``, because the failure is an
#:   XHR inside the single-page client rather than a navigation;
#: * the shell's own RPC probe is up to ``SESSION_PROBE_SECONDS`` away.
#:
#: The user is therefore looking at a dead client and a modal, with the shell
#: entirely unaware, for up to five minutes. Reading the fault out of the
#: response as it arrives closes that gap to nothing, and the reload that
#: recovery performs takes Odoo's dialog with it.
#:
#: Odoo's RPC goes through ``browser.XMLHttpRequest``
#: (``addons/web/static/src/core/network/rpc.js``), so patching the prototype
#: at document creation - before a line of Odoo's own JS has run - catches
#: every call, including the ones POS makes. ``data.name`` is the only
#: untranslated part of the fault, which is why it is what the search is for.
_SESSION_EXPIRY_SCRIPT = f"""
(function () {{
    var send = XMLHttpRequest.prototype.send;
    if (!send || send.__bytesraw) {{ return; }}
    function inspect(xhr) {{
        try {{
            if (xhr.status !== 200) {{ return; }}
            if (xhr.responseType !== "" && xhr.responseType !== "text") {{ return; }}
            var body = xhr.responseText;
            if (!body || body.length > {_MAX_SCANNED_RESPONSE}) {{ return; }}
            if (body.indexOf("{SESSION_EXPIRED_NAME}") === -1) {{ return; }}
            console.info("{SESSION_EXPIRED_MARKER}");
        }} catch (ignored) {{
            /* a response this frame is not allowed to read is not Odoo's */
        }}
    }}
    function patched() {{
        try {{
            this.addEventListener("load", function () {{ inspect(this); }});
        }} catch (ignored) {{ /* nothing to lose; the call still goes out */ }}
        return send.apply(this, arguments);
    }}
    patched.__bytesraw = true;
    XMLHttpRequest.prototype.send = patched;
}})();
"""


#: Copy the report's name from the body of Odoo's report request to a header.
#:
#: Odoo 19's Print button never navigates. ``downloadReport``
#: (``addons/web/static/src/webclient/actions/reports/utils.js``) hands
#: ``download()`` a form whose ``data`` field is
#: ``JSON.stringify(["/report/pdf/<report_name>/<ids>", "qweb-pdf"])``, and
#: ``download.js`` sends it as a ``FormData`` XHR to ``/report/download``. The
#: report's technical name is therefore only in the request *body*, which the
#: profile's ``QWebEngineUrlRequestInterceptor`` cannot read. It can read
#: headers - measured, a header set here arrives in ``httpHeaders()`` on the
#: same request - so the name is moved to one, and
#: :class:`~bytesraw_erp.services.report_watcher.ReportRequestWatcher` receives
#: it together with the request it belongs to.
#:
#: The technical name rather than the title, because the title is translated
#: and the name is not: a rule written on an English till still matches on an
#: Arabic one. Anything unexpected is left alone - the request still goes out,
#: and an unnamed report prints on the report printer as it always has.
_REPORT_NAME_SCRIPT = f"""
(function () {{
    var send = XMLHttpRequest.prototype.send;
    if (!send || send.__bytesrawReportName) {{ return; }}
    var route = new RegExp("^/report/(?:pdf|text)/([^/?#]+)");
    function patched(body) {{
        try {{
            if (typeof FormData !== "undefined" && body instanceof FormData
                    && body.has("data")) {{
                var spec = JSON.parse(body.get("data"));
                var match = Array.isArray(spec) && typeof spec[0] === "string"
                    ? route.exec(spec[0]) : null;
                if (match) {{
                    this.setRequestHeader("{REPORT_NAME_HEADER}", match[1]);
                }}
            }}
        }} catch (ignored) {{ /* not a report request; send it untouched */ }}
        return send.apply(this, arguments);
    }}
    patched.__bytesrawReportName = true;
    XMLHttpRequest.prototype.send = patched;
}})();
"""


def _report_name_script() -> QWebEngineScript:
    script = QWebEngineScript()
    script.setName("bytesraw-report-name")
    script.setSourceCode(_REPORT_NAME_SCRIPT)
    # Same placement as the session script, for the same reasons: before Odoo's
    # modules capture the prototype, and in the world Odoo's XHR lives in.
    script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    script.setRunsOnSubFrames(False)
    return script


def _session_expiry_script() -> QWebEngineScript:
    script = QWebEngineScript()
    script.setName("bytesraw-session-expiry")
    script.setSourceCode(_SESSION_EXPIRY_SCRIPT)
    # Before the document exists, so Odoo's modules find the patched prototype
    # rather than racing it.
    script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    # The main world, necessarily: an isolated world has its own
    # ``XMLHttpRequest`` and patching that one would tell us nothing about
    # Odoo's.
    script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    script.setRunsOnSubFrames(False)
    return script


class OdooWebPage(QWebEnginePage):
    """Page policy: keep Odoo inside, push everything else outside."""

    external_link_requested = Signal(QUrl)
    #: Odoo's own client answered one of its RPC calls with an expired session.
    session_expired = Signal()

    def __init__(self, profile: QWebEngineProfile, account: Account, parent: QWidget) -> None:
        super().__init__(profile, parent)
        self._account = account
        self._host = QUrl(account.url).host()
        self.newWindowRequested.connect(self._on_new_window_requested)
        self.scripts().insert(_session_expiry_script())
        self.scripts().insert(_report_name_script())

    def set_account(self, account: Account) -> None:
        self._account = account
        self._host = QUrl(account.url).host()

    # -- policy hooks ------------------------------------------------------

    def certificateError(self, error: QWebEngineCertificateError) -> bool:
        if self._account.allow_untrusted_certificate and error.url().host() == self._host:
            _log.warning(
                "Accepting untrusted certificate for %s (enabled on this account)",
                error.url().host(),
            )
            error.acceptCertificate()
            return True
        _log.error("Rejected certificate for %s: %s", error.url().host(), error.description())
        return False

    def acceptNavigationRequest(
        self,
        url: QUrl,
        nav_type: QWebEnginePage.NavigationType,
        is_main_frame: bool,
    ) -> bool:
        is_link_click = nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked
        if is_main_frame and is_link_click and url.host() and url.host() != self._host:
            _log.info("Opening external link in the system browser: %s", url.toString())
            QDesktopServices.openUrl(url)
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)

    def _on_new_window_requested(self, request: QWebEngineNewWindowRequest) -> None:
        """Decide where a ``target=_blank`` navigation goes.

        Odoo opens reports and attachments this way, and folding them into this
        page is what stops an unmanaged popup window appearing - but a URL Odoo
        answers with ``Content-Disposition: attachment`` must never be folded
        in. Such a navigation can only turn into a download; it never commits a
        document, so it tears down whatever was on screen and leaves nothing in
        its place. POS's invoice button hits exactly that: an
        ``ir.actions.act_url`` with ``target: "download"``, which the web
        client's action service runs as ``browser.open(url, "_blank")``, so
        validating an order with an invoice blanked the whole POS session.

        ``download()`` fetches the same file through the same profile - and so
        through the same report routing and auto-printing - without navigating
        anywhere. Leaving the request unanswered is what keeps the page put:
        a request nobody calls ``openIn()`` on opens no window.

        This is the hook rather than :meth:`acceptNavigationRequest` because
        that one is never called for a navigation Chromium starts on behalf of
        ``window.open``; the decision is already made by the time a page is
        asked for. It replaces ``createWindow()`` for the same reason - that
        one is handed a window type and no URL, so it cannot tell the two
        cases apart.
        """
        url = request.requestedUrl()
        if is_attachment_download_url(url.toString()):
            _log.info("Downloading %s instead of navigating to it", url.path())
            self.download(url)
            return
        request.openIn(self)

    def javaScriptConsoleMessage(
        self,
        level: QWebEnginePage.JavaScriptConsoleMessageLevel,
        message: str,
        line: int,
        source: str,
    ) -> None:
        if SESSION_EXPIRED_MARKER in message:
            # Not a log line: this is the injected script reporting, and the
            # console is how it reports. See `_SESSION_EXPIRY_SCRIPT`.
            _log.info("Odoo's web client reported an expired session")
            self.session_expired.emit()
            return
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            _log.warning("JS error %s:%s - %s", source, line, message)


class OdooWebView(QWebEngineView):
    """The Odoo browser surface, one per account profile."""

    #: Emitted with the Odoo-relative path, e.g. ``/odoo/sales/12``.
    path_changed = Signal(str)
    #: ``True`` while a navigation is in flight.
    loading_changed = Signal(bool)
    #: The page asked to print - ``window.print()`` from a POS receipt, or the
    #: print button inside Chromium's PDF viewer showing an Odoo report.
    print_requested = Signal()
    #: Forwarded from the page: Odoo's client lost the session mid-flight.
    session_expired = Signal()
    #: A navigation *finished*, successfully, on this account's host, with the
    #: path it landed on. Deliberately separate from ``path_changed``, which
    #: fires the moment a navigation is asked for and says nothing about
    #: whether the server answered it.
    page_loaded = Signal(str)

    def __init__(
        self,
        account: Account,
        profile: QWebEngineProfile,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._account = account
        self._page = OdooWebPage(profile, account, self)
        self.setPage(self._page)

        self._configure_settings()

        self.urlChanged.connect(self._on_url_changed)
        self.loadStarted.connect(lambda: self.loading_changed.emit(True))
        self.loadFinished.connect(self._on_load_finished)
        # Fires for window.print() and for the PDF viewer's print button.
        self._page.printRequested.connect(self.print_requested.emit)
        self._page.session_expired.connect(self.session_expired.emit)

    def _configure_settings(self) -> None:
        settings = self._page.settings()
        # Open Odoo report PDFs in Chromium's viewer instead of downloading
        # them, so the user can read a report and print it without a detour
        # through the Downloads folder.
        settings.setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, True)
        # Odoo reports and POS receipts rely on background colours and shading;
        # Chromium drops those when printing unless asked not to.
        settings.setAttribute(QWebEngineSettings.WebAttribute.PrintElementBackgrounds, True)

    # -- appearance --------------------------------------------------------

    def set_dark_mode(self, dark: bool, *, force: bool) -> None:
        """Apply the dark appearance to the embedded client.

        ``force`` turns on Chromium's own auto-darkening. It is the fallback for
        servers that ignore the ``color_scheme`` cookie - Odoo Community, whose
        ``ir_http.color_scheme()`` is hardcoded to "light" - and must stay off
        for a server that ships a real dark bundle, or the page is darkened
        twice and the result is muddy.
        """
        self._page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.ForceDarkMode,
            dark and force,
        )

    # -- navigation --------------------------------------------------------

    def open_path(self, path: str) -> None:
        self.setUrl(QUrl(self._account.url_for(path)))

    def open_home(self) -> None:
        """Open the remembered path, falling back to ``/odoo``."""
        self.setUrl(QUrl(self._account.home_url))

    def current_path(self) -> str:
        url = self.url()
        path = url.path() or "/"
        if url.hasQuery():
            path = f"{path}?{url.query()}"
        return path

    def _on_url_changed(self, url: QUrl) -> None:
        if url.host() == QUrl(self._account.url).host():
            self.path_changed.emit(self.current_path())

    def _on_load_finished(self, ok: bool) -> None:
        self.loading_changed.emit(False)
        if ok and self.url().host() == QUrl(self._account.url).host():
            self.page_loaded.emit(self.current_path())
