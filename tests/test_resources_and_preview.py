"""The bundled logo, and the preview print mode."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PySide6.QtPrintSupport import QPrinter

from bytesraw_erp.core.resources import app_icon, logo_pixmap
from bytesraw_erp.data.models import PrintMode
from bytesraw_erp.services import print_service
from bytesraw_erp.services.print_service import PrintService

# -- the logo ---------------------------------------------------------------


def test_the_logo_is_bundled_inside_the_package(qtbot) -> None:
    """It must travel with the package, not sit beside it in the repo."""
    pixmap = logo_pixmap()
    assert not pixmap.isNull()
    assert pixmap.width() > 0 and pixmap.height() > 0


def test_the_logo_ships_at_the_import_path_used_at_runtime() -> None:
    from importlib import resources

    path = resources.files("bytesraw_erp.resources") / "logo.png"
    assert path.is_file()


def test_the_app_icon_is_usable(qtbot) -> None:
    icon = app_icon()
    assert not icon.isNull()
    assert icon.availableSizes()


def test_the_windows_icon_ships_inside_the_package() -> None:
    """PyInstaller needs a real .ico; a PNG cannot be an executable's icon."""
    from importlib import resources

    path = resources.files("bytesraw_erp.resources") / "icon.ico"
    assert path.is_file()


def test_the_app_icon_carries_a_frame_for_every_shell_surface(qtbot) -> None:
    """One 128px bitmap scaled to 16px turns a stroked monogram to mush.

    ``QPixmap.loadFromData`` on an .ico reads only the first frame, so this
    also pins that the reader walks all of them.
    """
    sizes = {size.width() for size in app_icon().availableSizes()}
    assert {16, 32, 256} <= sizes
    assert len(sizes) >= 5


def test_a_missing_ico_falls_back_to_scaling_the_png(
    monkeypatch: pytest.MonkeyPatch, qtbot
) -> None:
    """A build without the .ico still gets sharp small sizes."""
    import bytesraw_erp.core.resources as res

    real_read = res._read

    def without_the_ico(name: str) -> bytes | None:
        return None if name.endswith(".ico") else real_read(name)

    monkeypatch.setattr(res, "_read", without_the_ico)
    res.app_icon.cache_clear()
    try:
        sizes = {size.width() for size in res.app_icon().availableSizes()}
        assert len(sizes) > 1
        assert 16 in sizes
    finally:
        res.app_icon.cache_clear()


def test_logo_lookup_is_cached(qtbot) -> None:
    """Called on every page build; re-decoding the PNG each time is waste."""
    assert logo_pixmap() is logo_pixmap()


def test_a_missing_logo_does_not_raise(monkeypatch: pytest.MonkeyPatch, qtbot) -> None:
    """A missing asset should cost an icon, not a startup."""
    logo_pixmap.cache_clear()

    def explode(_package: str) -> None:
        raise FileNotFoundError("gone")

    import bytesraw_erp.core.resources as res

    monkeypatch.setattr(res.resources, "files", explode)
    assert res.logo_pixmap().isNull()
    res.logo_pixmap.cache_clear()


# -- preview mode -----------------------------------------------------------


@pytest.fixture
def service(qtbot) -> PrintService:
    return PrintService()


def _make_pdf(path: Path, pages: int = 2) -> Path:
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


def test_preview_is_a_distinct_persisted_mode() -> None:
    assert PrintMode.PREVIEW.value == "preview"
    assert PrintMode("preview") is PrintMode.PREVIEW
    assert {m.value for m in PrintMode} == {"direct", "dialog", "preview"}


def test_every_mode_has_a_label() -> None:
    for mode in PrintMode:
        assert mode.label


def test_preview_mode_opens_the_preview_and_not_the_print_dialog(
    service: PrintService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows' print dialog has no preview pane, so this must be Qt's dialog."""
    source = _make_pdf(tmp_path / "report.pdf")
    monkeypatch.setattr(print_service, "_build_printer", lambda name="": QPrinter())

    calls: list[str] = []
    monkeypatch.setattr(
        PrintService,
        "_preview_document",
        lambda self, doc, printer, parent: calls.append("preview") or True,
    )
    monkeypatch.setattr(
        PrintService, "_confirm", staticmethod(lambda *_a: calls.append("dialog") or True)
    )

    service.print_pdf(source, PrintMode.PREVIEW)
    assert calls == ["preview"]


def test_dialog_mode_does_not_open_the_preview(
    service: PrintService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _make_pdf(tmp_path / "report.pdf")
    monkeypatch.setattr(print_service, "_build_printer", lambda name="": QPrinter())

    calls: list[str] = []
    monkeypatch.setattr(
        PrintService,
        "_preview_document",
        lambda self, doc, printer, parent: calls.append("preview") or True,
    )
    monkeypatch.setattr(
        PrintService, "_confirm", staticmethod(lambda *_a: calls.append("dialog") or False)
    )

    assert service.print_pdf(source, PrintMode.DIALOG) is False
    assert calls == ["dialog"]


def test_the_same_painter_serves_preview_and_paper(
    service: PrintService, tmp_path: Path
) -> None:
    """`_paint_document` is shared, so a preview shows the real output."""
    from PySide6.QtPdf import QPdfDocument

    source = _make_pdf(tmp_path / "report.pdf", pages=3)
    document = QPdfDocument()
    assert document.load(str(source)) == QPdfDocument.Error.None_

    out = tmp_path / "painted.pdf"
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(out))

    assert service._paint_document(document, printer) is True
    assert out.exists() and out.stat().st_size > 0


def test_printing_a_report_leaves_the_pdf_unlocked(
    service: PrintService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole reason report printing failed: a PDF held open cannot be replaced.

    ``QPdfDocument.load(str)`` keeps the file open for the life of the C++
    object, and the service used to park every document in a list forever.
    Chromium finishes each download by renaming its temporary file over the
    target, Windows refused that with ``ACCESS_DENIED``, and so a report that
    had been printed once could never be downloaded again - QtWebEngine only
    logged "The file cannot be written locally, due to access restrictions".

    ``os.replace`` is the same syscall, so this reproduces it without a browser.
    """
    source = _make_pdf(tmp_path / "report.pdf")
    monkeypatch.setattr(print_service, "_build_printer", lambda name="": _pdf_printer(tmp_path))

    service.print_pdf(source, PrintMode.DIRECT)

    incoming = _make_pdf(tmp_path / "incoming.pdf")
    os.replace(incoming, source)  # raises PermissionError if the PDF is still open
    assert source.exists()


def test_a_preview_document_is_released_when_the_dialog_closes(
    service: PrintService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The preview dialog blocks inside print_pdf, so nothing outlives the call.

    The dialog is parented to the caller and therefore owned by Qt, so a
    ``paintRequested`` connection left in place would keep the document - and
    the file behind it - alive for the rest of the session.
    """
    source = _make_pdf(tmp_path / "report.pdf")
    monkeypatch.setattr(print_service, "_build_printer", lambda name="": _pdf_printer(tmp_path))
    monkeypatch.setattr(
        PrintService, "_preview_document", lambda self, doc, printer, parent: True
    )

    service.print_pdf(source, PrintMode.PREVIEW)

    incoming = _make_pdf(tmp_path / "incoming.pdf")
    os.replace(incoming, source)
    assert not hasattr(service, "_documents")


def _pdf_printer(tmp_path: Path) -> QPrinter:
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(tmp_path / "job.pdf"))
    return printer
