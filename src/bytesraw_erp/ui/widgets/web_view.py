"""The embedded Odoo client.

A thin wrapper around :class:`QWebEngineView` that adds the behaviour a desktop
shell needs and a bare browser view does not:

* links that leave the Odoo host open in the user's real browser, so a click on
  an external URL never strands the user inside a chromeless window;
* certificate errors are rejected unless the account explicitly opted in, which
  is the only supported way to reach an on-premise server with a self-signed
  certificate;
* the current Odoo path is published as a signal so it can be remembered and
  restored on the next launch.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebEngineCore import (
    QWebEngineCertificateError,
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QWidget

from bytesraw_erp.data.models import Account

_log = logging.getLogger(__name__)


class OdooWebPage(QWebEnginePage):
    """Page policy: keep Odoo inside, push everything else outside."""

    external_link_requested = Signal(QUrl)

    def __init__(self, profile: QWebEngineProfile, account: Account, parent: QWidget) -> None:
        super().__init__(profile, parent)
        self._account = account
        self._host = QUrl(account.url).host()

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

    def createWindow(self, _window_type: QWebEnginePage.WebWindowType) -> QWebEnginePage:
        """Fold ``target=_blank`` navigations back into this view.

        Odoo opens reports and attachments this way. Returning ``self`` makes
        them load in place instead of spawning an unmanaged popup window.
        """
        return self

    def javaScriptConsoleMessage(
        self,
        level: QWebEnginePage.JavaScriptConsoleMessageLevel,
        message: str,
        line: int,
        source: str,
    ) -> None:
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
        self.loadFinished.connect(lambda _ok: self.loading_changed.emit(False))
        # Fires for window.print() and for the PDF viewer's print button.
        self._page.printRequested.connect(self.print_requested.emit)

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
