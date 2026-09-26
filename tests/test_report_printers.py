"""Report printers: any Odoo report can be sent to a printer of its own.

The whole feature rests on knowing *which* report a download is, and Odoo 19
does not make that easy. Its Print button sends a ``FormData`` XHR to
``/report/download`` whose ``data`` field is
``["/report/pdf/<report_name>/<ids>", "qweb-pdf"]``, then saves the answer as a
``blob:`` - so neither the request URL nor the download URL names the report.
An injected script copies the name into a request header, which the profile's
request interceptor can read; measured against the live demo server, every
report Odoo printed arrived named, before its download, in order.

The rule is keyed on that technical name, never on the title, because titles
are translated (the demo database runs in Arabic) and are not unique.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QMarginsF, QSizeF
from PySide6.QtGui import QPageLayout, QPageSize, QPainter, QPdfWriter
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QApplication, QLabel

from bytesraw_erp.core.errors import OdooRpcError
from bytesraw_erp.data.models import (
    NO_PRINTER,
    PrintMode,
    PrintSettings,
    ReportPrinterRule,
)
from bytesraw_erp.data.settings_store import SettingsStore
from bytesraw_erp.services import print_service
from bytesraw_erp.services import profile_manager as pm
from bytesraw_erp.services.print_service import PrintError, PrintService
from bytesraw_erp.services.profile_manager import ProfileManager
from bytesraw_erp.services.report_catalog import (
    ReportInfo,
    ReportListUnavailable,
    fetch_reports,
)
from bytesraw_erp.ui.app_context import AppContext
from bytesraw_erp.ui.pages import odoo_page as odoo_page_module
from bytesraw_erp.ui.pages.odoo_page import OdooPage
from bytesraw_erp.ui.theme import ThemeController

_MM = 72 / 25.4
_DYMO = "product.report_producttemplatelabel_dymo"
_SHEET = "product.report_producttemplatelabel2x7"
_SLIP = "stock.report_deliveryslip"

#: Where every test's rules point, so a wrong route shows up as a wrong name.
_SETTINGS = PrintSettings(
    mode=PrintMode.DIRECT,
    report_printer_name="HP LaserJet",
    pos_printer_name="EPSON TM-m30 Receipt",
    report_printers=(
        ReportPrinterRule(_DYMO, "Product Label (PDF)", "ZDesigner GK420d"),
        ReportPrinterRule(_SLIP, "Delivery Slip", "Warehouse HP"),
    ),
)


def _pdf(path: Path, width_mm: float = 210, height_mm: float = 297) -> Path:
    """A one-page PDF of the given size, as wkhtmltopdf would lay one out."""
    writer = QPdfWriter(str(path))
    writer.setPageLayout(
        QPageLayout(
            QPageSize(QSizeF(width_mm, height_mm), QPageSize.Unit.Millimeter),
            QPageLayout.Orientation.Portrait,
            QMarginsF(0, 0, 0, 0),
        )
    )
    painter = QPainter()
    assert painter.begin(writer)
    try:
        painter.drawText(10, 40, "LBL-0001")
    finally:
        painter.end()
    return path


def _page_mm(path: Path) -> tuple[int, int]:
    document = QPdfDocument()
    document.load(str(path))
    try:
        size = document.pagePointSize(0)
        return round(size.width() / _MM), round(size.height() / _MM)
    finally:
        document.close()


# -- the rules ---------------------------------------------------------------


def test_a_report_with_a_rule_goes_to_its_printer() -> None:
    assert _SETTINGS.report_printer_for(_DYMO) == "ZDesigner GK420d"
    assert _SETTINGS.report_printer_for(_SLIP) == "Warehouse HP"


def test_everything_else_keeps_the_report_printer() -> None:
    """Including an A4 label sheet, and a report nothing could name."""
    assert _SETTINGS.report_printer_for(_SHEET) == "HP LaserJet"
    assert _SETTINGS.report_printer_for("") == "HP LaserJet"
    assert _SETTINGS.rule_for("") is None


def test_the_published_printers_are_untouched() -> None:
    """The POS receipt printer and the page-print route know nothing of rules."""
    assert _SETTINGS.printer_for(pos=True) == "EPSON TM-m30 Receipt"
    assert _SETTINGS.printer_for(pos=False) == "HP LaserJet"


def test_a_rule_prints_where_no_a4_printer_is_assigned() -> None:
    """A till with a label printer and nothing else still prints its labels."""
    settings = _SETTINGS.evolve(report_printer_name=NO_PRINTER)
    assert settings.prints_reports(_DYMO) is True
    assert settings.prints_reports(_SHEET) is False


def test_a_rule_can_keep_one_report_off_paper() -> None:
    settings = _SETTINGS.evolve(report_printers=(ReportPrinterRule(_SLIP, "", NO_PRINTER),))
    assert settings.prints_reports(_SLIP) is False
    assert settings.prints_reports(_SHEET) is True


@pytest.mark.parametrize("printer", ["ZDesigner GK420d", "", NO_PRINTER])
def test_rules_survive_a_round_trip(tmp_path: Path, printer: str | None) -> None:
    """The Windows default and "do not print" stay two different things."""
    rule = ReportPrinterRule(_DYMO, "Product Label (PDF)", printer)
    store = SettingsStore(tmp_path / "settings.json")
    store.load()
    store.set_printing(PrintSettings(report_printers=(rule,)))

    reloaded = SettingsStore(tmp_path / "settings.json")
    reloaded.load()
    [back] = reloaded.printing.report_printers
    assert back == rule
    assert (back.printer_name is NO_PRINTER) is (printer is NO_PRINTER)


def test_a_settings_file_from_before_rules_has_none(tmp_path: Path) -> None:
    """Additive, so no SETTINGS_VERSION bump - see CLAUDE.md on why that matters."""
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.load()
    store.set_printing(PrintSettings(report_printer_name="HP LaserJet"))
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["printing"]["report_printers"]
    path.write_text(json.dumps(raw), encoding="utf-8")

    reloaded = SettingsStore(path)
    reloaded.load()
    assert reloaded.printing.report_printers == ()
    assert reloaded.printing.report_printer_name == "HP LaserJet"


def test_unreadable_and_duplicate_rules_are_dropped() -> None:
    settings = PrintSettings.from_dict(
        {
            "report_printers": [
                {"report_name": _DYMO, "printer_name": "ZDesigner"},
                {"report_name": _DYMO, "printer_name": "Somewhere else"},
                {"report_name": "", "printer_name": "Nameless"},
                "not a rule",
                {"printer_name": "No report"},
            ]
        }
    )
    assert settings.report_printers == (ReportPrinterRule(_DYMO, "", "ZDesigner"),)


# -- naming the report, in a real web view -----------------------------------

_BLOB_PDF = b"%PDF-1.4\n%minimal\ntrailer\n%%EOF\n"

#: Mirrors Odoo 19's own sequence: ``downloadReport`` in
#: ``webclient/actions/reports/utils.js`` builds the form, and ``download.js``
#: posts it as FormData with ``responseType = "blob"`` and clicks an anchor on
#: the object URL. Nothing here names the report except the form body.
_PAGE = """<html><body><script>
window.odooPrint = function (reportUrl, withData) {
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/report/download');
  xhr.responseType = 'blob';
  xhr.onload = function () {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(xhr.response);
    a.setAttribute('download', 'report.pdf');
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };
  const form = new FormData();
  if (withData) {
    form.append('data', JSON.stringify([reportUrl, 'qweb-pdf']));
  }
  form.append('context', '{}');
  form.append('csrf_token', 'x');
  xhr.send(form);
};
</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(_BLOB_PDF)))
        self.send_header("Content-Disposition", 'attachment; filename="report.pdf"')
        self.end_headers()
        self.wfile.write(_BLOB_PDF)

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


#: Profiles and views outlive each test: qtbot destroys widgets after the test
#: body has dropped its locals, and a profile freed while a page still uses it
#: takes the process with it (see CLAUDE.md).
_KEEP_ALIVE: list[object] = []


@pytest.fixture
def view(server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qtbot):
    from bytesraw_erp.data.models import Account
    from bytesraw_erp.ui.widgets.web_view import OdooWebView

    monkeypatch.setattr(pm, "reports_dir", lambda: tmp_path)
    monkeypatch.setattr(pm, "downloads_dir", lambda: tmp_path)
    profiles = ProfileManager()
    account = Account(url=server, database="demo", login="demo")
    web = OdooWebView(account, profiles.profile_for(account))
    _KEEP_ALIVE.extend((profiles, web))
    qtbot.addWidget(web)
    web.resize(600, 400)
    web.show()
    qtbot.waitExposed(web)
    with qtbot.waitSignal(web.loadFinished, timeout=20000):
        web.open_path("/")
    return SimpleNamespace(web=web, profiles=profiles)


def _print(view, qtbot, report_url: str, *, with_data: bool = True) -> tuple[Path, str]:
    script = f"window.odooPrint({json.dumps(report_url)}, {json.dumps(with_data)})"
    with qtbot.waitSignal(view.profiles.report_downloaded, timeout=25000) as blocker:
        view.web.page().runJavaScript(script)
    return Path(blocker.args[0]), blocker.args[1]


def test_odoos_print_button_names_its_report(view, qtbot) -> None:
    _, name = _print(view, qtbot, f"/report/pdf/{_DYMO}/12,13")
    assert name == _DYMO


def test_a_wizard_report_is_named_too(view, qtbot) -> None:
    """Print Labels passes its options in the query, not as record ids."""
    url = f"/report/pdf/{_SHEET}?options=%7B%22quantity_by_product%22%3A%7B%7D%7D&context=%7B%7D"
    _, name = _print(view, qtbot, url)
    assert name == _SHEET


def test_several_reports_keep_their_own_names(view, qtbot) -> None:
    names = [_print(view, qtbot, f"/report/pdf/{n}/1")[1] for n in (_SLIP, _DYMO, _SHEET)]
    assert names == [_SLIP, _DYMO, _SHEET]


def test_a_report_the_script_cannot_name_is_still_a_report(view, qtbot) -> None:
    """A future Odoo changing its form costs the routing, never the print."""
    path, name = _print(view, qtbot, "", with_data=False)
    assert name == ""
    assert path.read_bytes().startswith(b"%PDF")


# -- printing ----------------------------------------------------------------


@pytest.fixture
def jobs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str | None, Path]]:
    """Every job goes to a PDF file, recording the device it asked for."""
    asked: list[tuple[str | None, Path]] = []

    def fake_printer(printer_name: str | None = "") -> QPrinter:
        if printer_name is NO_PRINTER:
            raise PrintError("No printer is chosen")
        target = tmp_path / f"job{len(asked)}.pdf"
        asked.append((printer_name, target))
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(target))
        return printer

    monkeypatch.setattr(print_service, "_build_printer", fake_printer)
    return asked


def test_the_print_goes_to_the_rules_printer(jobs, tmp_path: Path, qtbot) -> None:
    PrintService().print_pdf_with(_pdf(tmp_path / "l.pdf", 57, 32), _SETTINGS, None, _DYMO)
    PrintService().print_pdf_with(_pdf(tmp_path / "s.pdf"), _SETTINGS, None, _SHEET)
    assert [name for name, _ in jobs] == ["ZDesigner GK420d", "HP LaserJet"]


def test_a_rules_report_is_printed_on_its_own_paper(jobs, tmp_path: Path, qtbot) -> None:
    """Not stretched over whatever stock the label driver defaults to."""
    PrintService().print_pdf_with(_pdf(tmp_path / "l.pdf", 57, 32), _SETTINGS, None, _DYMO)
    assert _page_mm(jobs[0][1]) == (57, 32)


def test_other_reports_keep_the_printers_paper(jobs, tmp_path: Path, qtbot) -> None:
    """The shared A4 printer is never asked for custom paper.

    Compared with the printer's default rather than A4, because Qt picks that
    default from the locale and a Letter machine is not a failure.
    """
    reference = QPrinter(QPrinter.PrinterMode.HighResolution)
    reference.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    default = reference.pageLayout().fullRect(QPageLayout.Unit.Millimeter)
    PrintService().print_pdf_with(_pdf(tmp_path / "l.pdf", 57, 32), _SETTINGS, None, _SHEET)
    assert _page_mm(jobs[0][1]) == (round(default.width()), round(default.height()))


def test_a_rule_to_do_not_print_is_refused(jobs, tmp_path: Path, qtbot) -> None:
    settings = _SETTINGS.evolve(report_printers=(ReportPrinterRule(_SLIP, "", NO_PRINTER),))
    with pytest.raises(PrintError):
        PrintService().print_pdf_with(_pdf(tmp_path / "s.pdf"), settings, None, _SLIP)
    assert jobs == []


# -- the Odoo page -----------------------------------------------------------


class _FakeRouter:
    def go(self, path: str, **_kwargs: object) -> bool:
        return True

    def reset_to(self, path: str) -> bool:
        return True

    def back(self) -> bool:
        return False


@pytest.fixture
def context(tmp_path: Path, qtbot) -> AppContext:
    settings = SettingsStore(tmp_path / "settings.json")
    settings.load()
    return AppContext(ThemeController(QApplication.instance()), settings=settings)


@pytest.fixture
def page(context: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qtbot) -> OdooPage:
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    monkeypatch.setattr(odoo_page_module, "downloads_dir", lambda: downloads)
    widget = OdooPage(context, _FakeRouter())  # type: ignore[arg-type]
    qtbot.addWidget(widget)
    return widget


def _messages(page: OdooPage) -> list[str]:
    return [t.findChild(QLabel).text() for t in page._toasts._toasts if not t.isHidden()]


def test_the_page_hands_the_report_name_to_the_printer(
    page: OdooPage, context: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context.settings.set_printing(_SETTINGS.evolve(report_printer_name=NO_PRINTER))
    printed: list[str] = []
    monkeypatch.setattr(
        context.printing,
        "print_pdf_with",
        lambda path, settings, parent=None, report_name="": printed.append(report_name),
    )

    page._on_report_downloaded(_pdf(tmp_path / "label.pdf", 57, 32), _DYMO)

    assert printed == [_DYMO]
    assert _messages(page) == []


def test_a_report_set_not_to_print_says_so(
    page: OdooPage, context: AppContext, tmp_path: Path
) -> None:
    """The fix is under Report printers, not Report printer, so the toast differs."""
    context.settings.set_printing(
        _SETTINGS.evolve(report_printers=(ReportPrinterRule(_SLIP, "", NO_PRINTER),))
    )

    page._on_report_downloaded(_pdf(tmp_path / "slip.pdf"), _SLIP)

    [message] = _messages(page)
    assert "this report is set not to print" in message


def test_a_report_open_in_the_pdf_viewer_follows_its_rule(
    page: OdooPage, context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Printing from Chromium's viewer is the same report by another route."""
    context.settings.set_printing(_SETTINGS)
    asked: list[object] = []
    monkeypatch.setattr(context.printing, "print_view", lambda *a, **k: asked.append(a[3]))
    cases = {
        f"/report/pdf/{_SLIP}/4": "Warehouse HP",
        f"/report/pdf/{_SHEET}/4": "HP LaserJet",
        "/pos/ui/2": "EPSON TM-m30 Receipt",
    }
    for path in cases:
        monkeypatch.setattr(page, "_web", SimpleNamespace(current_path=lambda p=path: p))
        monkeypatch.setattr(page._stack, "currentWidget", lambda: page._web)
        page._on_page_print_requested()
    assert asked == list(cases.values())


# -- recently printed --------------------------------------------------------


def test_printed_reports_are_remembered_newest_first(context: AppContext, tmp_path: Path) -> None:
    context._note_report(tmp_path / "Product Label (PDF).pdf", _DYMO)
    context._note_report(tmp_path / "Delivery Slip.pdf", _SLIP)
    context._note_report(tmp_path / "Product Label (PDF) (1).pdf", _DYMO)
    context._note_report(tmp_path / "Mystery.pdf", "")

    assert context.recent_reports == {_DYMO: "Product Label (PDF)", _SLIP: "Delivery Slip"}


# -- the report list ---------------------------------------------------------


class _FakeClient:
    base_url = "https://erp.example.com"

    def __init__(self, answer: object) -> None:
        self._answer = answer
        self.calls: list[tuple] = []

    def call_kw(self, model, method, args=None, kwargs=None):
        self.calls.append((model, method, args, kwargs))
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


def test_the_report_list_is_read_in_the_users_language() -> None:
    client = _FakeClient(
        [
            {"name": "Delivery Slip", "report_name": _SLIP, "model": "stock.picking"},
            {"name": "Broken", "report_name": False, "model": "x"},
        ]
    )
    reports = fetch_reports(client, "ar_001")  # type: ignore[arg-type]
    assert reports == [ReportInfo(_SLIP, "Delivery Slip", "stock.picking")]
    _model, _method, args, kwargs = client.calls[0]
    assert args == [[["report_type", "=", "qweb-pdf"]]]
    assert kwargs["context"] == {"lang": "ar_001"}


def test_a_user_who_may_not_list_reports_is_told_how_else(qtbot) -> None:
    refused = OdooRpcError("You are not allowed...", name="odoo.exceptions.AccessError")
    with pytest.raises(ReportListUnavailable, match="Print a report once"):
        fetch_reports(_FakeClient(refused))  # type: ignore[arg-type]


def test_any_other_failure_is_not_disguised_as_a_refusal() -> None:
    broken = OdooRpcError("boom", name="odoo.exceptions.UserError")
    with pytest.raises(OdooRpcError):
        fetch_reports(_FakeClient(broken))  # type: ignore[arg-type]


# -- the settings page -------------------------------------------------------


@pytest.fixture
def settings_page(context: AppContext, qtbot):
    from bytesraw_erp.ui.pages.settings_page import SettingsPage

    widget = SettingsPage(context, _FakeRouter())  # type: ignore[arg-type]
    qtbot.addWidget(widget)
    widget.on_enter({})
    return widget


def _choices(settings_page) -> dict[str, str]:
    combo = settings_page._report_choice
    return {
        combo.itemData(i): combo.itemText(i) for i in range(combo.count()) if combo.itemData(i)
    }


def test_changing_the_print_mode_keeps_every_rule(settings_page, context: AppContext) -> None:
    """The printing card rebuilds PrintSettings from its own widgets, and the
    rules are not among them - left out, one click on the mode deletes them all.
    """
    context.settings.set_printing(_SETTINGS)
    settings_page.on_enter({})

    mode = settings_page._mode
    mode.setCurrentIndex(mode.findData(PrintMode.DIALOG.value))

    assert context.settings.printing.mode is PrintMode.DIALOG
    assert context.settings.printing.report_printers == _SETTINGS.report_printers


def test_a_recently_printed_report_can_be_given_a_printer(
    settings_page, context: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The path that works for any user: print it once, then pick it."""
    context._note_report(tmp_path / "Product Label (PDF).pdf", _DYMO)
    settings_page._refresh_report_choices()
    assert _choices(settings_page) == {_DYMO: "Product Label (PDF) - printed recently"}

    combo = settings_page._report_choice
    combo.setCurrentIndex(combo.findData(_DYMO))
    monkeypatch.setattr(settings_page._rule_printer_choice, "currentData", lambda: "ZDesigner")
    settings_page._add_rule.click()

    assert context.settings.printing.report_printers == (
        ReportPrinterRule(_DYMO, "Product Label (PDF)", "ZDesigner"),
    )
    # Offered once: a report with a rule leaves the list of reports to add.
    assert _DYMO not in _choices(settings_page)


def test_the_database_list_is_offered_and_same_titles_told_apart(settings_page) -> None:
    settings_page._on_catalog_loaded(
        [
            ReportInfo(_SLIP, "Delivery Slip"),
            ReportInfo("stock.report_package_barcode_small", "Package Barcode (PDF)"),
            ReportInfo("stock.report_package_history_barcode_small", "Package Barcode (PDF)"),
        ]
    )
    choices = _choices(settings_page)
    assert choices[_SLIP] == "Delivery Slip"
    assert choices["stock.report_package_barcode_small"] == (
        "Package Barcode (PDF) (stock.report_package_barcode_small)"
    )


def test_a_refused_list_explains_the_other_way_in(settings_page) -> None:
    settings_page._on_catalog_failed(ReportListUnavailable("Only an Odoo administrator..."))
    assert "Only an Odoo administrator" in settings_page._catalog_hint.text()


def test_a_rules_printer_can_be_changed_and_the_rule_removed(
    settings_page, context: AppContext, qtbot
) -> None:
    context.settings.set_printing(_SETTINGS)
    settings_page.on_enter({})

    rows = [settings_page._rules.itemAt(i).widget() for i in range(settings_page._rules.count())]
    assert len(rows) == 2
    from PySide6.QtWidgets import QComboBox, QPushButton

    combo = rows[1].findChild(QComboBox)
    combo.setCurrentIndex(combo.findData(NO_PRINTER))
    assert context.settings.printing.rule_for(_SLIP).printer_name is NO_PRINTER

    remove = next(b for b in rows[0].findChildren(QPushButton) if b.text() == "Remove")
    remove.click()
    assert [r.report_name for r in context.settings.printing.report_printers] == [_SLIP]
    # The row goes with it, and the slip's row still shows its new choice.
    assert settings_page._rules.count() == 1
    [left] = [settings_page._rules.itemAt(0).widget().findChild(QComboBox)]
    assert left.currentData() is NO_PRINTER
