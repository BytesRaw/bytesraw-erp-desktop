"""What the Odoo page does with a report it is not going to print.

Three settings can stop a report at the Downloads folder - the mode saves
instead of printing, no A4 printer is assigned, or automatic printing is off -
and the thing worth pinning is that none of them is allowed to be *silent*.
A report lands in a scratch directory that is pruned within a day, so a user
who pressed Print in Odoo and was told nothing has simply lost their invoice.
That is a failure this application has shipped once already, from a different
direction, and it leaves no trace anywhere for anyone to debug from.

The toast also has to say *which* of the three it was, because the fix differs:
one is a mode, one is a missing printer, one is a checkbox.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from bytesraw_erp.data.models import NO_PRINTER, PrintMode, PrintSettings
from bytesraw_erp.data.settings_store import SettingsStore
from bytesraw_erp.ui.app_context import AppContext
from bytesraw_erp.ui.pages import odoo_page as odoo_page_module
from bytesraw_erp.ui.pages.odoo_page import OdooPage
from bytesraw_erp.ui.theme import ThemeController


class _FakeRouter:
    def go(self, path: str, **_kwargs: object) -> bool:
        return True

    def reset_to(self, path: str) -> bool:
        return True

    def back(self) -> bool:
        return False


@pytest.fixture
def downloads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Downloads, patched where the page imported it.

    ``odoo_page`` does ``from ...paths import downloads_dir``, so patching
    ``core.paths`` would leave the page writing into the developer's real
    Downloads folder - which a test in this suite once did.
    """
    target = tmp_path / "Downloads"
    target.mkdir()
    monkeypatch.setattr(odoo_page_module, "downloads_dir", lambda: target)
    return target


@pytest.fixture
def context(tmp_path: Path, qtbot) -> AppContext:
    settings = SettingsStore(tmp_path / "settings.json")
    settings.load()
    return AppContext(ThemeController(QApplication.instance()), settings=settings)


@pytest.fixture
def page(context: AppContext, qtbot) -> OdooPage:
    widget = OdooPage(context, _FakeRouter())  # type: ignore[arg-type]
    qtbot.addWidget(widget)
    return widget


def _messages(page: OdooPage) -> list[str]:
    return [
        toast.findChild(QLabel).text()
        for toast in page._toasts._toasts
        if not toast.isHidden()
    ]


def _a_report(tmp_path: Path) -> Path:
    report = tmp_path / "INV_2026_0001.pdf"
    report.write_bytes(b"%PDF-1.4\nnot really, and nothing here reads it\n")
    return report


@pytest.mark.parametrize(
    ("settings", "reason"),
    [
        (PrintSettings(mode=PrintMode.SAVE), "reports are set to be saved"),
        (
            PrintSettings(report_printer_name=NO_PRINTER),
            "no report printer is assigned",
        ),
        (
            PrintSettings(auto_print_reports=False),
            "automatic report printing is off",
        ),
    ],
    ids=["saving mode", "no printer assigned", "auto-print off"],
)
def test_a_report_that_is_not_printed_is_kept_and_named(
    page: OdooPage,
    context: AppContext,
    downloads: Path,
    tmp_path: Path,
    settings: PrintSettings,
    reason: str,
) -> None:
    context.settings.set_printing(settings)

    page._on_report_downloaded(_a_report(tmp_path))

    assert (downloads / "INV_2026_0001.pdf").exists(), "the report was left to be pruned"
    assert len(_messages(page)) == 1
    message = _messages(page)[0]
    assert "INV_2026_0001.pdf" in message
    assert reason in message


def test_a_second_copy_does_not_replace_the_first(
    page: OdooPage, context: AppContext, downloads: Path, tmp_path: Path
) -> None:
    """Two invoices printed in a row are two files, not one overwritten twice.

    Chromium numbers its own duplicate downloads; a file this app writes has to
    do its own or the morning's invoices are all the same PDF.
    """
    context.settings.set_printing(PrintSettings(mode=PrintMode.SAVE))

    page._on_report_downloaded(_a_report(tmp_path))
    page._on_report_downloaded(_a_report(tmp_path))

    assert sorted(p.name for p in downloads.iterdir()) == [
        "INV_2026_0001 (1).pdf",
        "INV_2026_0001.pdf",
    ]


def test_an_unassigned_receipt_printer_says_so_instead_of_printing(
    page: OdooPage, context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A POS receipt is a page, not a file, so there is nothing to keep.

    Saving one per order would bury Downloads in tickets nobody asked for, so
    this route only reports. Reporting is not optional: ``window.print()`` that
    produces nothing anywhere, with no word of why, is the same silent loss.
    """
    context.settings.set_printing(
        PrintSettings(mode=PrintMode.DIRECT, pos_printer_name=NO_PRINTER)
    )
    printed: list[object] = []
    monkeypatch.setattr(
        page._context.printing, "print_view", lambda *a, **k: printed.append(a)
    )
    monkeypatch.setattr(page, "_is_pos", lambda: True)

    page._on_page_print_requested()

    assert printed == []
    assert _messages(page) == ["No receipt printer is assigned, so nothing was printed."]


def test_an_assigned_receipt_printer_still_prints(
    page: OdooPage, context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half: unassigning one slot must not stop the other printing."""
    context.settings.set_printing(
        PrintSettings(
            mode=PrintMode.DIRECT,
            report_printer_name=NO_PRINTER,
            pos_printer_name="EPSON TM-m30 Receipt",
        )
    )
    asked: list[object] = []
    monkeypatch.setattr(
        page._context.printing, "print_view", lambda *a, **k: asked.append(a[3])
    )
    monkeypatch.setattr(page, "_is_pos", lambda: True)
    # _print refuses on a page with no web view, which is the state a page that
    # has never signed in is in - stand one in so the route is reached. It is
    # asked where it is, because a report open in the PDF viewer has a printer
    # of its own; this one is on the POS screen, as `_is_pos` says.
    monkeypatch.setattr(page, "_web", SimpleNamespace(current_path=lambda: "/pos/ui"))
    monkeypatch.setattr(page._stack, "currentWidget", lambda: page._web)

    page._on_page_print_requested()

    assert asked == ["EPSON TM-m30 Receipt"]


# -- the settings page's half ------------------------------------------------


@pytest.fixture
def settings_page(context: AppContext, qtbot):
    from bytesraw_erp.ui.pages.settings_page import SettingsPage

    widget = SettingsPage(context, _FakeRouter())  # type: ignore[arg-type]
    qtbot.addWidget(widget)
    # The page fills itself from storage in on_enter, not in __init__ - it is
    # rebuilt on every visit, so the router hook is the load.
    widget.on_enter({})
    return widget


def test_not_assigned_is_offered_for_both_printers(settings_page) -> None:
    """And it carries NO_PRINTER itself, not a string standing in for it.

    Every string is a name somebody could give a printer, so a sentinel that
    is one would be indistinguishable from a device with that name.
    """
    for combo in (settings_page._report_printer, settings_page._pos_printer):
        index = combo.findData(NO_PRINTER)
        assert index >= 0, "no way to unassign this printer"
        assert combo.itemData(index) is NO_PRINTER
        # And the Windows default is still a separate, different choice.
        assert combo.findData("") != index


def test_choosing_not_assigned_stores_it_rather_than_the_default(
    settings_page, context: AppContext
) -> None:
    combo = settings_page._report_printer
    combo.setCurrentIndex(combo.findData(NO_PRINTER))

    stored = context.settings.printing
    assert stored.report_printer_name is NO_PRINTER
    assert stored.report_printer_name != ""
    assert stored.prints_reports() is False


def test_the_saving_mode_does_not_eat_the_checkboxes_it_overrides(
    settings_page, context: AppContext
) -> None:
    """The mode shows its consequences without adopting them as the user's.

    In the saving mode nothing prints and the PDF is always kept, so both
    checkboxes are answers rather than questions and are shown as such. What
    must not happen is those forced values being written back: a trip through
    the mode and out again would then have silently changed two settings the
    user never touched.
    """
    context.settings.set_printing(
        PrintSettings(auto_print_reports=True, keep_report_copy=False)
    )
    settings_page.on_enter({})

    mode = settings_page._mode
    mode.setCurrentIndex(mode.findData(PrintMode.SAVE.value))

    assert settings_page._auto_print.isChecked() is False
    assert settings_page._keep_copy.isChecked() is True
    assert settings_page._auto_print.isEnabled() is False
    assert settings_page._keep_copy.isEnabled() is False

    mode.setCurrentIndex(mode.findData(PrintMode.DIRECT.value))

    stored = context.settings.printing
    assert stored.auto_print_reports is True
    assert stored.keep_report_copy is False
    assert settings_page._auto_print.isChecked() is True
    assert settings_page._keep_copy.isChecked() is False
    assert settings_page._auto_print.isEnabled() is True


def test_the_hint_says_what_an_unassigned_printer_means(
    settings_page, context: AppContext
) -> None:
    combo = settings_page._pos_printer
    combo.setCurrentIndex(combo.findData(NO_PRINTER))
    assert "Point of Sale receipts" in settings_page._printer_hint.text()

    combo = settings_page._report_printer
    combo.setCurrentIndex(combo.findData(NO_PRINTER))
    text = settings_page._printer_hint.text()
    assert "reports" in text
    assert "saved in Downloads" in text
