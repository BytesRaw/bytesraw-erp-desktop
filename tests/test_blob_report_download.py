"""Odoo 19's real report download: an XHR POST, then a blob.

This is the regression test for a bug that made report auto-printing do nothing.
Odoo 19 never navigates to ``/report/download``; its ``download()`` helper XHRs
it with ``responseType = "blob"`` and clicks a hidden ``<a download>`` on the
resulting object URL. QtWebEngine reports that download with a ``blob:`` URL, so
matching on the download URL alone can never identify it - measured::

    url: blob:http://127.0.0.1:28911/83137414-...  mime: application/pdf

The page below performs exactly that sequence, so the test fails if the
blob-claiming path is ever removed or "simplified" back to URL matching.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from bytesraw_erp.data.models import Account
from bytesraw_erp.services import profile_manager as pm
from bytesraw_erp.services.profile_manager import ProfileManager
from bytesraw_erp.ui.widgets.web_view import OdooWebView

_PDF = b"%PDF-1.4\n%minimal\ntrailer\n%%EOF\n"

#: Mirrors addons/web/static/src/core/network/download.js: XHR POST with a blob
#: response type, then an object URL on a hidden anchor.
_PAGE = """<html><body><div id="ready">page</div><script>
window.odooStyleReportDownload = function (path) {
  const xhr = new XMLHttpRequest();
  xhr.open('POST', path);
  xhr.responseType = 'blob';
  xhr.onload = function () {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(xhr.response);
    a.setAttribute('download', 'invoice.pdf');
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };
  const fd = new FormData();
  fd.append('data', JSON.stringify(['/report/pdf/account.report_invoice/1', 'qweb-pdf']));
  xhr.send(fd);
};
window.plainBlobDownload = function () {
  const blob = new Blob([new Uint8Array([37, 80, 68, 70])], {type: 'application/pdf'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.setAttribute('download', 'notes.pdf');
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
};
</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path.split("?")[0] == "/report/download":
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(_PDF)))
            self.send_header("Content-Disposition", 'attachment; filename="invoice.pdf"')
            self.end_headers()
            self.wfile.write(_PDF)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        body = _PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        """Silence the access log."""


@pytest.fixture(scope="module")
def server() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    reports = tmp_path / "reports"
    downloads = tmp_path / "downloads"
    reports.mkdir()
    downloads.mkdir()
    monkeypatch.setattr(pm, "reports_dir", lambda: reports)
    monkeypatch.setattr(pm, "downloads_dir", lambda: downloads)
    return {"reports": reports, "downloads": downloads}


def _view(base_url: str, profiles: ProfileManager, qtbot) -> OdooWebView:
    account = Account(url=base_url, database="demo", login="demo")
    view = OdooWebView(account, profiles.profile_for(account))
    qtbot.addWidget(view)
    view.resize(600, 400)
    view.show()
    qtbot.waitExposed(view)
    with qtbot.waitSignal(view.loadFinished, timeout=20000):
        view.open_path("/")
    return view


def test_blob_report_is_recognised_and_routed(
    server: str, sandbox: dict[str, Path], qtbot
) -> None:
    """The whole point: no user action, and it is identified as a report."""
    profiles = ProfileManager()
    view = _view(server, profiles, qtbot)

    with qtbot.waitSignal(profiles.report_downloaded, timeout=25000) as blocker:
        view.page().runJavaScript("window.odooStyleReportDownload('/report/download')")

    landed = Path(blocker.args[0])
    assert landed.parent == sandbox["reports"]
    assert landed.read_bytes().startswith(b"%PDF")


def test_an_unrelated_blob_pdf_is_not_claimed_as_a_report(
    server: str, sandbox: dict[str, Path], qtbot
) -> None:
    """A blob PDF with no preceding report request must not be auto-printed.

    This is what stops the claiming heuristic from hijacking, say, a PDF the
    user assembled in the browser and saved deliberately.
    """
    profiles = ProfileManager()
    view = _view(server, profiles, qtbot)

    with qtbot.waitSignal(profiles.file_downloaded, timeout=25000) as blocker:
        view.page().runJavaScript("window.plainBlobDownload()")

    assert Path(blocker.args[0]).parent == sandbox["downloads"]


def test_one_report_request_is_claimed_only_once(
    server: str, sandbox: dict[str, Path], qtbot
) -> None:
    """A single report must not license every later blob as a report."""
    profiles = ProfileManager()
    view = _view(server, profiles, qtbot)

    with qtbot.waitSignal(profiles.report_downloaded, timeout=25000):
        view.page().runJavaScript("window.odooStyleReportDownload('/report/download')")

    with qtbot.waitSignal(profiles.file_downloaded, timeout=25000) as blocker:
        view.page().runJavaScript("window.plainBlobDownload()")

    assert Path(blocker.args[0]).parent == sandbox["downloads"]
