"""Print service tests.

``_build_printer`` is replaced with a PDF-output printer throughout, so the
whole path runs - dialog skipping, job lifetime, completion signal - without a
sheet of paper leaving a real device.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QLabel

from bytesraw_erp.constants import POS_PATH_PREFIX
from bytesraw_erp.data.models import PrintMode, PrintSettings
from bytesraw_erp.services import print_service
from bytesraw_erp.services.print_service import PrintError, PrintService


@pytest.fixture
def pdf_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect every job to a PDF file instead of a physical printer."""
    target = tmp_path / "job.pdf"

    def fake_printer(printer_name: str = "") -> QPrinter:
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(target))
        return printer

    monkeypatch.setattr(print_service, "_build_printer", fake_printer)
    return target


@pytest.fixture
def service(qtbot) -> PrintService:
    return PrintService()


def test_print_modes_are_stable_strings() -> None:
    """These values are persisted in accounts.json; renaming one breaks configs."""
    assert PrintMode.DIRECT.value == "direct"
    assert PrintMode.DIALOG.value == "dialog"
    assert PrintMode("direct") is PrintMode.DIRECT


def test_every_mode_has_a_label() -> None:
    for mode in PrintMode:
        assert mode.label


def test_a_report_is_never_sent_to_the_pos_printer(
    service: PrintService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of two printers: A4 output must not reach the roll.

    An invoice validated in POS arrives as a QWeb report, so the route that
    prints reports must read the A4 setting and never the POS one, however the
    report came to exist.
    """
    asked: list[str] = []

    def fake_printer(printer_name: str = "") -> QPrinter:
        asked.append(printer_name)
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(tmp_path / "job.pdf"))
        return printer

    monkeypatch.setattr(print_service, "_build_printer", fake_printer)
    report = tmp_path / "invoice.pdf"
    report.write_bytes(_one_page_pdf(tmp_path))

    settings = PrintSettings(
        mode=PrintMode.DIRECT,
        report_printer_name="HP LaserJet",
        pos_printer_name="EPSON TM-m30 Receipt",
    )
    service.print_pdf_with(report, settings)

    assert asked == ["HP LaserJet"]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        # Both spellings Odoo registers, plus the per-config form the user opens
        # (addons/point_of_sale/controllers/main.py:36).
        ("/pos/ui/2", "EPSON TM-m30 Receipt"),
        ("/pos/ui", "EPSON TM-m30 Receipt"),
        ("/pos/web", "EPSON TM-m30 Receipt"),
        # The back office is A4, including the accounting screens an invoice
        # is printed from.
        ("/odoo", "HP LaserJet"),
        ("/odoo/sales/12", "HP LaserJet"),
        ("/odoo/action-account.action_move_out_invoice_type/91", "HP LaserJet"),
        ("/", "HP LaserJet"),
    ],
)
def test_the_page_on_screen_picks_the_device(path: str, expected: str) -> None:
    """A page print follows what is being printed, which is where the user is.

    This is the rule ``OdooPage._is_pos`` applies; anything outside POS is a
    document on A4.
    """
    settings = PrintSettings(
        report_printer_name="HP LaserJet",
        pos_printer_name="EPSON TM-m30 Receipt",
    )
    assert settings.printer_for(pos=path.startswith(POS_PATH_PREFIX)) == expected


def _one_page_pdf(tmp_path: Path) -> bytes:
    """A genuine PDF, built with Qt so no fixture file is needed."""
    from PySide6.QtGui import QPainter

    source = tmp_path / "source.pdf"
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(source))
    painter = QPainter()
    assert painter.begin(printer)
    try:
        painter.drawText(200, 200, "INV/2026/0001")
    finally:
        painter.end()
    return source.read_bytes()


def test_hidden_view_is_refused_rather_than_hanging(
    service: PrintService, pdf_target: Path, qtbot
) -> None:
    """An invisible view never renders, so its finished signal never arrives.

    Failing fast turns a silent hang into a message the user can act on.
    """
    hidden = QLabel("not shown")
    qtbot.addWidget(hidden)
    with pytest.raises(PrintError, match="visible"):
        service.print_view(hidden, PrintMode.DIRECT)


def test_missing_default_printer_gives_actionable_advice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NullInfo:
        @staticmethod
        def isNull() -> bool:
            return True

    monkeypatch.setattr(
        print_service.QPrinterInfo, "defaultPrinter", staticmethod(lambda: NullInfo())
    )
    with pytest.raises(PrintError, match="No default printer"):
        print_service._build_printer()


def test_print_pdf_renders_every_page(
    service: PrintService, pdf_target: Path, tmp_path: Path, qtbot
) -> None:
    source = _make_pdf(tmp_path / "report.pdf", pages=3)
    with qtbot.waitSignal(service.finished, timeout=15000) as blocker:
        assert service.print_pdf(source, PrintMode.DIRECT) is True
    assert blocker.args[0] is True
    assert pdf_target.exists()
    assert pdf_target.stat().st_size > 0


def test_print_pdf_rejects_a_non_pdf(service: PrintService, tmp_path: Path) -> None:
    junk = tmp_path / "not.pdf"
    junk.write_text("this is not a pdf", encoding="utf-8")
    with pytest.raises(PrintError, match="could not be opened"):
        service.print_pdf(junk, PrintMode.DIRECT)


def test_print_pdf_reports_a_missing_file(service: PrintService, tmp_path: Path) -> None:
    with pytest.raises(PrintError):
        service.print_pdf(tmp_path / "absent.pdf", PrintMode.DIRECT)


def test_no_jobs_are_retained_after_completion(
    service: PrintService, pdf_target: Path, tmp_path: Path, qtbot
) -> None:
    """A retained QPrinter is a leak; a released one too early aborts the job."""
    source = _make_pdf(tmp_path / "r.pdf", pages=1)
    with qtbot.waitSignal(service.finished, timeout=15000):
        service.print_pdf(source, PrintMode.DIRECT)
    assert service._jobs == []


def _make_pdf(path: Path, pages: int) -> Path:
    """Produce a small multi-page PDF with Qt itself."""
    from PySide6.QtGui import QPainter

    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(path))
    painter = QPainter()
    assert painter.begin(printer)
    try:
        for index in range(pages):
            if index:
                printer.newPage()
            painter.drawText(200, 200, f"Page {index + 1}")
    finally:
        painter.end()
    return path
