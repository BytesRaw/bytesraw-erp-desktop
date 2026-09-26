"""Printing from the embedded Odoo client.

Two routes, because Odoo produces two kinds of printable output:

**Rendered pages** - POS receipts and portal documents call ``window.print()``.
QtWebEngine raises ``printRequested``; the current page is rendered and sent to
a ``QPrinter``.

**Report PDFs** - ``ir.actions.report`` serves a PDF from ``/report/pdf/...``.
With Chromium's PDF viewer enabled these open in the view and their own print
button funnels back into the same path. A PDF that lands on disk instead can be
printed page by page through :class:`QPdfDocument`.

Each route offers four modes: straight to the default Windows printer, via the
system print dialog, via a preview, or saved to the Downloads folder without
being printed at all. The last is for a machine with no printer attached, where
Odoo's Print button should still leave the user holding a document.

A printer slot can also be left **unassigned**, which is refused rather than
fallen back on: see :func:`_build_printer`.

A report can have a **printer of its own** (``PrintSettings.report_printers``).
One that does is printed at its own page size, so a 57x32mm label on a label
printer is not stretched over whatever stock that driver defaults to - see
:func:`_fit_page_to_document`.

Note on preview: Windows' own print dialog has **no preview pane** - the Win32
dialog simply does not offer one, and ``QPrintDialog`` exposes no such option -
so a preview cannot come from choosing "show the dialog". It has to be Qt's
:class:`QPrintPreviewDialog`, which renders the pages itself. Both routes reach
it through a PDF: the report route already has one, and the page route prints
itself to a temporary PDF first, so the preview shows exactly what will come out
of the printer.

Three Qt constraints shape this module:

* ``QWebEngineView.print()`` is asynchronous and completes through
  ``printFinished``. The ``QPrinter`` must stay alive until then, so every job
  is parked in ``_jobs`` rather than left to the garbage collector.
* Printing only completes for a **visible** view. A hidden one never renders
  and the finished signal never arrives.
* A ``QPdfDocument`` loaded from a *path* keeps that file open for as long as
  the C++ object lives, and Windows then refuses to replace the file. Reports
  are therefore loaded from **memory** - see :func:`_open_pdf`.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMarginsF, QObject, QSizeF, Signal
from PySide6.QtGui import QPageLayout, QPageSize, QPainter
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPrintSupport import (
    QPrintDialog,
    QPrinter,
    QPrinterInfo,
    QPrintPreviewDialog,
)
from PySide6.QtWidgets import QWidget

from bytesraw_erp.data.models import NO_PRINTER, PrintMode, PrintSettings

_log = logging.getLogger(__name__)

_DOC_NAME = "Bytesraw ERP"

__all__ = [
    "PrintError",
    "PrintMode",
    "PrintService",
    "available_printers",
    "default_printer_name",
]


class PrintError(Exception):
    """Raised when a job cannot be started. Message is end-user readable."""


def default_printer_name() -> str:
    """The Windows default printer, or an empty string when none is set."""
    return QPrinterInfo.defaultPrinterName()


def available_printers() -> list[str]:
    return [info.printerName() for info in QPrinterInfo.availablePrinters()]


_POINTS_PER_MM = 72 / 25.4


def _fit_page_to_document(printer: QPrinter, document: QPdfDocument) -> None:
    """Ask the printer for the document's own page size, with no margins.

    ``_paint_document`` stretches each page over the printer's paper, which is
    harmless for an A4 report on an A4 printer and ruinous for a label: a
    57x32mm label on a driver still set to 100x150mm comes out as a distorted
    smear. The PDF carries the size Odoo's paperformat laid it out for, which
    is the stock the user loaded to print it on.

    Only for a report with a printer of its own - that printer was chosen for
    this document, so its paper is this document's. Asking the shared A4
    printer for custom paper could leave it waiting for someone to load some.
    A driver that refuses the size keeps its own, with a warning.
    """
    size = document.pagePointSize(0)
    width, height = size.width(), size.height()
    if width <= 0 or height <= 0:
        return
    orientation = (
        QPageLayout.Orientation.Landscape if width > height else QPageLayout.Orientation.Portrait
    )
    page = QPageSize(
        QSizeF(min(width, height), max(width, height)),
        QPageSize.Unit.Point,
        "",
        QPageSize.SizeMatchPolicy.FuzzyMatch,
    )
    layout = QPageLayout(page, orientation, QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Point)
    if not printer.setPageLayout(layout):
        _log.warning(
            "%s would not take a %.0fx%.0fmm page; printing on its own paper size",
            printer.printerName(),
            width / _POINTS_PER_MM,
            height / _POINTS_PER_MM,
        )


def _build_printer(printer_name: str | None = "") -> QPrinter:
    """A high-resolution printer for ``printer_name``, or the system default.

    A configured printer that has since been unplugged or renamed falls back to
    the Windows default with a warning rather than failing: the user asked for
    a document, and the nearest working device beats an error dialog.

    :data:`NO_PRINTER` is the one name that gets no fallback. It means the user
    unassigned that slot, and falling back there would send the document to the
    Windows default - which on a till is the receipt roll, the exact device an
    unassigned A4 slot exists to keep reports away from. Every route to paper
    goes through here, so this is the single place that guarantee holds.

    Raises :class:`PrintError` when there is no usable printer, which is the
    one failure the user can actually act on.
    """
    if printer_name is NO_PRINTER:
        raise PrintError(
            "No printer is chosen for this kind of document. Pick one in "
            "Bytesraw ERP settings, under Printing."
        )

    if printer_name:
        info = QPrinterInfo.printerInfo(printer_name)
        if not info.isNull():
            printer = QPrinter(info, QPrinter.PrinterMode.HighResolution)
            printer.setDocName(_DOC_NAME)
            return printer
        _log.warning(
            "Configured printer %r is not available; falling back to the default",
            printer_name,
        )

    info = QPrinterInfo.defaultPrinter()
    if info.isNull():
        raise PrintError(
            "No default printer is set in Windows. Choose one in "
            "Settings > Bluetooth & devices > Printers & scanners, "
            "or pick a printer in Bytesraw ERP settings."
        )
    printer = QPrinter(info, QPrinter.PrinterMode.HighResolution)
    printer.setDocName(_DOC_NAME)
    return printer


@contextlib.contextmanager
def _open_pdf(path: Path) -> Iterator[QPdfDocument]:
    """Open ``path`` as a PDF **without holding an OS handle on it**.

    ``QPdfDocument.load(str)`` keeps the file open for the whole life of the
    C++ object, and ``close()`` is **not** enough to release it - measured, the
    handle survives until the object is destroyed. While it is held, Windows
    refuses to replace or delete that file: ``os.replace`` onto it raises
    ``PermissionError`` (``WinError 5``), and so does ``unlink``.

    That matters because a printed report is a file Chromium may write again -
    it is the target of the next download of the same report - and because
    nothing can prune the cache directory while the handles are open. The
    service used to park every document in a list for the life of the process,
    so each printed report leaked one handle permanently.

    Reading the bytes first sidesteps all of it: the file is closed before the
    document exists. ``data`` and ``buffer`` are named locals on purpose -
    ``QBuffer`` does not copy, and a temporary ``QByteArray`` is collected out
    from under it, leaving the document empty with no error beyond
    ``Status.Error``.
    """
    try:
        data = QByteArray(path.read_bytes())
    except OSError as exc:
        raise PrintError(f"{path.name} could not be read: {exc}") from exc

    buffer = QBuffer()
    buffer.setData(data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)

    document = QPdfDocument()
    document.load(buffer)
    if document.status() is not QPdfDocument.Status.Ready:
        raise PrintError(f"{path.name} could not be opened as a PDF.")
    try:
        yield document
    finally:
        document.close()


class PrintService(QObject):
    """Runs print jobs and keeps their printers alive until they finish."""

    #: ``(succeeded, message)`` - message is empty on success.
    finished = Signal(bool, str)
    #: A page was written to a PDF instead of printed, at this path. Separate
    #: from :attr:`finished` because the outcome is a *file*, and the caller
    #: has to be able to say where it went and offer to open it.
    saved = Signal(Path)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        #: Live jobs. A QPrinter collected mid-render aborts the job silently.
        self._jobs: list[QPrinter] = []

    # -- web page ----------------------------------------------------------

    def print_view(
        self,
        view: QWidget,
        mode: PrintMode,
        parent: QWidget | None = None,
        printer_name: str | None = "",
    ) -> bool:
        """Print the current page of ``view``.

        Returns ``False`` when the user cancelled the dialog. Completion is
        reported later through :attr:`finished`.
        """
        if not view.isVisible():
            # Guard rather than hang: an invisible view never renders, so the
            # finished signal would never arrive and the caller would wait
            # forever on a job that cannot complete.
            raise PrintError("The Odoo view must be visible before printing.")

        if mode is PrintMode.PREVIEW:
            # QWebEngineView.print() paints straight to a printer and cannot be
            # driven by the preview dialog's paintRequested. Rendering to a PDF
            # first gives the preview the real, paginated output.
            return self._preview_page(view, parent, printer_name)

        printer = _build_printer(printer_name)
        if mode is PrintMode.DIALOG and not self._confirm(printer, parent):
            return False

        self._jobs.append(printer)

        def done(ok: bool) -> None:
            with contextlib.suppress(RuntimeError, TypeError):
                view.printFinished.disconnect(done)
            self._release(printer)
            self._report(ok, printer.printerName())

        view.printFinished.connect(done)
        _log.info("Printing page to %s (%s)", printer.printerName(), mode.value)
        view.print(printer)
        return True

    def save_view(self, view: QWidget, target: Path) -> None:
        """Write the page in ``view`` to ``target`` as a PDF, printing nothing.

        What :attr:`PrintMode.SAVE` does with a page Odoo asked to print. The
        same ``printToPdf`` the preview route uses, pointed at a file the user
        keeps rather than at a scratch directory - so what lands in Downloads
        is the paginated document, not a screenshot of the page.

        Completion arrives through :attr:`saved`, or through :attr:`finished`
        with a failure. Nothing is raised: this runs from a page Odoo drove, so
        there is no call for the caller to have wrapped in a try.
        """

        def rendered(_path: str, ok: bool) -> None:
            with contextlib.suppress(RuntimeError, TypeError):
                view.pdfPrintingFinished.disconnect(rendered)
            if ok and target.exists():
                _log.info("Saved the page to %s", target)
                self.saved.emit(target)
            else:
                self.finished.emit(False, f"The page could not be saved as {target.name}.")

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.finished.emit(False, f"{target.parent} could not be written to: {exc}")
            return

        view.pdfPrintingFinished.connect(rendered)
        view.printToPdf(str(target))

    # -- PDF on disk -------------------------------------------------------

    def print_pdf(
        self,
        path: Path,
        mode: PrintMode,
        parent: QWidget | None = None,
        printer_name: str | None = "",
        *,
        fit_to_document: bool = False,
    ) -> bool:
        """Print a PDF file - an Odoo report that was downloaded rather than shown.

        ``fit_to_document`` prints at the PDF's own page size rather than on
        the printer's current paper; see :func:`_fit_page_to_document`.

        Every route below is synchronous - painting runs to completion, and the
        preview dialog owns the event loop until it is dismissed - so the
        document lives exactly as long as this call and is released on the way
        out. Retaining it would keep the PDF locked; see :func:`_open_pdf`.
        """
        with _open_pdf(path) as document:
            page_count = document.pageCount()
            if page_count <= 0:
                raise PrintError(f"{path.name} has no pages to print.")

            printer = _build_printer(printer_name)
            printer.setDocName(path.stem)
            if fit_to_document:
                # Before the dialog and the preview, so both show the paper
                # the document will actually come out on.
                _fit_page_to_document(printer, document)
            if mode is PrintMode.PREVIEW:
                return self._preview_document(document, printer, parent)
            if mode is PrintMode.DIALOG and not self._confirm(printer, parent):
                return False

            # Report PDFs already carry their own margins from the QWeb layout;
            # adding the printer's would shrink and re-crop every page.
            printer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Point)

            if not self._paint_document(document, printer):
                raise PrintError(f"Could not start a print job on {printer.printerName()}.")

            _log.info(
                "Printed %s (%d pages) to %s", path.name, page_count, printer.printerName()
            )
            self._report(True, printer.printerName())
            return True

    # -- preview -----------------------------------------------------------

    def _preview_page(
        self,
        view: QWidget,
        parent: QWidget | None,
        printer_name: str | None,
    ) -> bool:
        """Render the page to a temporary PDF, then preview that."""
        scratch = Path(tempfile.mkdtemp(prefix="bytesraw-preview-"))
        target = scratch / "page.pdf"

        def rendered(_path: str, ok: bool) -> None:
            with contextlib.suppress(RuntimeError, TypeError):
                view.pdfPrintingFinished.disconnect(rendered)
            try:
                if not ok or not target.exists():
                    self.finished.emit(False, "The page could not be prepared for preview.")
                    return
                self.print_pdf(target, PrintMode.PREVIEW, parent, printer_name)
            except PrintError as exc:
                self.finished.emit(False, str(exc))
            finally:
                # print_pdf holds no handle on the file, so this always works.
                shutil.rmtree(scratch, ignore_errors=True)

        view.pdfPrintingFinished.connect(rendered)
        view.printToPdf(str(target))
        return True

    def _preview_document(
        self,
        document: QPdfDocument,
        printer: QPrinter,
        parent: QWidget | None,
    ) -> bool:
        """Show Qt's preview dialog for an open PDF.

        The dialog owns the loop: it calls back through ``paintRequested``
        whenever it needs the pages drawn, including for the final print.
        """
        dialog = QPrintPreviewDialog(printer, parent)
        dialog.setWindowTitle("Print preview")
        dialog.resize(900, 800)
        dialog.paintRequested.connect(
            lambda target_printer: self._paint_document(document, target_printer)
        )
        try:
            accepted = dialog.exec() == QPrintPreviewDialog.DialogCode.Accepted
        finally:
            # The dialog is parented to `parent`, so Qt owns it and it outlives
            # this call. Left connected, its lambda would keep `document` -
            # and the PDF file behind it - alive for the rest of the session.
            with contextlib.suppress(RuntimeError, TypeError):
                dialog.paintRequested.disconnect()
            dialog.setParent(None)
        _log.info(
            "Print preview closed (%s) for %s",
            "printed" if accepted else "cancelled",
            printer.docName(),
        )
        if accepted:
            self._report(True, printer.printerName())
        return accepted

    # -- settings-driven entry points --------------------------------------

    def print_pdf_with(
        self,
        path: Path,
        settings: PrintSettings,
        parent: QWidget | None = None,
        report_name: str = "",
    ) -> bool:
        """Print a report PDF using the app's configured mode and printer.

        A report with a printer of its own goes there, at its own page size.
        Every other report is an A4 document wherever it was rendered from, so
        this route never consults the POS printer - not even for an invoice
        printed from inside a POS session, which is the case that made the
        split necessary.
        """
        rule = settings.rule_for(report_name)
        return self.print_pdf(
            path,
            settings.mode,
            parent,
            settings.report_printer_for(report_name),
            fit_to_document=rule is not None,
        )

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _paint_document(document: QPdfDocument, printer: QPrinter) -> bool:
        """Draw every page of ``document`` onto ``printer``.

        Shared by the direct print path and the preview dialog, so what the
        preview shows is produced by the same code that puts ink on paper.
        """
        painter = QPainter()
        if not painter.begin(printer):
            return False
        try:
            for index in range(document.pageCount()):
                if index:
                    printer.newPage()
                image = document.render(index, painter.viewport().size())
                if image.isNull():
                    continue
                painter.drawImage(painter.viewport(), image)
        finally:
            painter.end()
        return True

    @staticmethod
    def _confirm(printer: QPrinter, parent: QWidget | None) -> bool:
        dialog = QPrintDialog(printer, parent)
        dialog.setWindowTitle("Print")
        return dialog.exec() == QPrintDialog.DialogCode.Accepted

    def _release(self, printer: QPrinter) -> None:
        with contextlib.suppress(ValueError):
            self._jobs.remove(printer)

    def _report(self, ok: bool, printer_name: str) -> None:
        if ok:
            self.finished.emit(True, f"Sent to {printer_name}.")
        else:
            self.finished.emit(False, f"The print job sent to {printer_name} failed.")
