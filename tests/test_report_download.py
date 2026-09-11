"""End-to-end interception of an Odoo report download.

A local HTTP server stands in for Odoo and serves a real PDF from
``/report/download`` with the ``Content-Disposition: attachment`` header Odoo
uses (``addons/web/controllers/report.py:138``). That is the whole point of
using a server rather than a stub: the behaviour under test is QtWebEngine
deciding "this is a download", which only a real response triggers.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PySide6.QtGui import QPainter
from PySide6.QtPrintSupport import QPrinter

from bytesraw_erp.data.models import Account, PrintMode
from bytesraw_erp.services import print_service
from bytesraw_erp.services import profile_manager as pm
from bytesraw_erp.services.print_service import PrintService
from bytesraw_erp.services.profile_manager import ProfileManager
from bytesraw_erp.ui.widgets.web_view import OdooWebView

_PDF_PATH = "/report/download"
_ATTACHMENT_PATH = "/web/content/7"


def _one_page_pdf() -> bytes:
    """A genuine PDF, built with Qt so no fixture file is needed."""
    import tempfile

    target = Path(tempfile.mkdtemp()) / "src.pdf"
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(target))
    painter = QPainter()
    assert painter.begin(printer)
    try:
        painter.drawText(200, 200, "Invoice INV/2026/0001")
    finally:
        painter.end()
    return target.read_bytes()


class _Handler(BaseHTTPRequestHandler):
    pdf = b""

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path in (_PDF_PATH, _ATTACHMENT_PATH):
            name = "invoice.pdf" if path == _PDF_PATH else "attachment.pdf"
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(self.pdf)))
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.end_headers()
            self.wfile.write(self.pdf)
            return
        body = b"<html><body>odoo stand-in</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        """Silence the default stderr access log."""


@pytest.fixture(scope="module")
def server() -> Iterator[str]:
    _Handler.pdf = _one_page_pdf()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Keep downloads out of the real cache and Downloads folders."""
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
    return view


def test_report_download_is_routed_for_printing(
    server: str, sandbox: dict[str, Path], qtbot
) -> None:
    """A QWeb report lands in the reports directory and announces itself."""
    profiles = ProfileManager()
    view = _view(server, profiles, qtbot)

    with qtbot.waitSignal(profiles.report_downloaded, timeout=20000) as blocker:
        view.open_path(_PDF_PATH)

    landed = Path(blocker.args[0])
    assert landed.parent == sandbox["reports"], "report should not go to Downloads"
    assert landed.exists()
    assert landed.read_bytes()[:4] == b"%PDF"


def test_ordinary_attachment_goes_to_downloads(
    server: str, sandbox: dict[str, Path], qtbot
) -> None:
    """An attachment is a file the user wants kept, not a job to print."""
    profiles = ProfileManager()
    view = _view(server, profiles, qtbot)

    with qtbot.waitSignal(profiles.file_downloaded, timeout=20000) as blocker:
        view.open_path(_ATTACHMENT_PATH)

    landed = Path(blocker.args[0])
    assert landed.parent == sandbox["downloads"]
    assert landed.exists()


def test_a_report_does_not_raise_the_plain_download_signal(
    server: str, sandbox: dict[str, Path], qtbot
) -> None:
    """The two paths are exclusive, so the page cannot double-handle a file."""
    profiles = ProfileManager()
    view = _view(server, profiles, qtbot)
    seen: list[object] = []
    profiles.file_downloaded.connect(seen.append)

    with qtbot.waitSignal(profiles.report_downloaded, timeout=20000):
        view.open_path(_PDF_PATH)

    assert seen == []


def test_the_same_report_can_be_printed_twice_in_one_session(
    server: str, sandbox: dict[str, Path], tmp_path: Path, qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Printing a report must not spoil the next download of the same report.

    Printing used to leave the PDF open for the life of the process, which on
    Windows makes that path unwritable - and the second download aims at
    exactly that path. When it lands on a held file the download is interrupted
    ("The file cannot be written locally, due to access restrictions"),
    ``report_downloaded`` never fires, and nothing reaches the printer in any
    mode.
    """
    monkeypatch.setattr(
        print_service, "_build_printer", lambda name="": _pdf_printer(tmp_path)
    )
    profiles = ProfileManager()
    printing = PrintService()
    view = _view(server, profiles, qtbot)

    with qtbot.waitSignal(profiles.report_downloaded, timeout=20000) as first:
        view.open_path(_PDF_PATH)
    first_report = Path(first.args[0])
    printing.print_pdf(first_report, PrintMode.DIRECT)

    with qtbot.waitSignal(profiles.report_downloaded, timeout=20000) as second:
        view.open_path(_PDF_PATH)
    second_report = Path(second.args[0])
    printing.print_pdf(second_report, PrintMode.DIRECT)

    print("FIRST :", first_report)
    print("SECOND:", second_report)
    print("DIR   :", sorted(x.name for x in sandbox["reports"].iterdir()))
    assert first_report != second_report, "each print needs its own file"
    assert first_report.exists() and second_report.exists()
    assert second_report.read_bytes()[:4] == b"%PDF"


def _pdf_printer(tmp_path: Path) -> QPrinter:
    """A printer that writes a file, so the suite needs no physical device."""
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(tmp_path / "job.pdf"))
    return printer
