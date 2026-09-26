"""Application settings: appearance, display, printing, updates, and storage.

Changes apply and save immediately. A settings page with a Save button invites
the user to close it with unsaved edits; applying on change means the printer
they pick is the printer that prints, with no second step.

Printing is configured here, once, rather than per account: which printer is
attached is a fact about the machine in front of the user, not about the Odoo
database they are signed in to.

The one setting here that cannot apply immediately is the rendering mode:
Chromium's command line is read once, as it starts. That card says so, and the
page is Qt widgets throughout - which is what makes it readable on the machine
whose web view is the problem.

The Updates card is the only place a check can be *asked for* rather than
waited for, and the only place that reports one. A failed automatic check is
deliberately silent - a till between access points fails one every four hours -
so this page is where "is this thing even looking?" gets an answer, which is
why it says when it last looked.

Each group is a card with a badged header, so the page reads as a short list of
decisions rather than one long list of controls. The About card is the honest home for
the build number, the settings file and the folders the app writes to - the
things a user is asked for when they report a problem.

The cards sit in **two columns** on a wide screen and one on a narrow one, and
the header does not scroll. Both come from :mod:`..widgets.page`. Together they
are what fits the whole page on a till screen without scrolling at all: five
cards stacked in one column ran to roughly twice the height of a 1080p display,
so "Done" - the way out - and the banner that reports what was just saved were
both off-screen for most of the page's length.

Which card goes in which column is declared, not measured. Appearance, Display
and Printing are the machine's own settings and stay together on the left;
Updates and About are about the build and stay together on the right. Report
printers heads the right-hand column: it belongs with Printing, but the left
column is already the longer one, and a card that grows a row per rule would
push Printing's own controls out of sight below it.

**Report printers** sends particular Odoo reports to a printer of their own -
labels to the label printer, delivery slips to the warehouse. A report is
chosen from two places: the reports this computer has printed since launch,
which anyone can use, and the database's full list, which Odoo only lets an
administrator read (see :mod:`bytesraw_erp.services.report_catalog`).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from bytesraw_erp.constants import APP_NAME, APP_VERSION
from bytesraw_erp.core.errors import BytesrawError
from bytesraw_erp.core.paths import logs_dir, reports_dir
from bytesraw_erp.data.models import (
    NO_PRINTER,
    SYSTEM_DEFAULT_PRINTER,
    DisplaySettings,
    PrintMode,
    PrintSettings,
    RenderMode,
    ReportPrinterRule,
    UpdateChannel,
)
from bytesraw_erp.services.graphics import active_render_mode
from bytesraw_erp.services.print_service import available_printers, default_printer_name
from bytesraw_erp.services.report_catalog import (
    ReportInfo,
    ReportListUnavailable,
    fetch_reports,
)
from bytesraw_erp.services.tasks import run_async
from bytesraw_erp.services.update_service import Update, is_installed_build
from bytesraw_erp.ui.app_context import AppContext
from bytesraw_erp.ui.router import Router
from bytesraw_erp.ui.theme import Palette, Theme
from bytesraw_erp.ui.widgets.page import WIDTH_WIDE, CardColumns, PageShell
from bytesraw_erp.ui.widgets.sections import Card, Field, SectionHeader, hint

_log = logging.getLogger(__name__)

#: A combo holding two words should not run the width of the card.
_CONTROL_WIDTH = 240
_WIDE_CONTROL = 420

#: Stored as an empty printer name - resolved to whatever Windows says at print
#: time, so the setting keeps following the OS default when the user changes it.
_SYSTEM_DEFAULT = ""


def _printer_choice(combo: QComboBox) -> str | None:
    """What a printer picker currently holds, keeping "not assigned" itself.

    ``None`` is :data:`NO_PRINTER` and must survive as ``None``: coercing it to
    a string would give ``"None"``, and ``or ""`` would give the Windows
    default - the device an unassigned report slot exists to avoid.
    """
    data = combo.currentData()
    return NO_PRINTER if data is NO_PRINTER else str(data)


def _describe_age(when: datetime) -> str:
    """When something happened, in the coarsest unit that is still true.

    Coarse on purpose: the exact second a background check ran is never what the
    reader wants to know, and a bare timestamp would leave them to do the
    subtraction themselves.
    """
    seconds = max(0, int((datetime.now(UTC) - when).total_seconds()))
    if seconds < 90:
        return "just now"
    for unit, length, ceiling in (("minute", 60, 60), ("hour", 3600, 24), ("day", 86400, 1000)):
        count = seconds // length
        if count < ceiling:
            return f"{count} {unit}s ago" if count != 1 else f"1 {unit} ago"
    return "a long time ago"


class SettingsPage(QWidget):
    """Appearance, display, printing and update preferences, stored locally."""

    def __init__(self, context: AppContext, router: Router, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._router = router
        #: Guards the change handlers while fields are populated from storage.
        self._loading = False
        #: Section badges are rendered bitmaps, and this is the one page where
        #: the theme changes under the widget that is showing it.
        self._sections: list[SectionHeader] = []
        #: The release currently on offer, so the buttons and the status line
        #: agree with each other and survive leaving and re-entering the page.
        self._pending: Update | None = None
        #: Printers as last read from Windows, so every picker on the page -
        #: including one row per report rule - offers the same list.
        self._printer_names: list[str] = []
        self._default_label = ""
        #: The database's reports by technical name, when they could be read.
        #: Recently printed reports take their titles from here when possible.
        self._catalog: dict[str, ReportInfo] = {}

        self._shell = PageShell(
            "Settings",
            "Appearance, display, printing and updates, for this computer.",
            width=WIDTH_WIDE,
        )
        self._banner = self._shell.banner
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._shell)

        self._close = QPushButton("Done")
        self._close.setProperty("variant", "primary")
        self._close.clicked.connect(self._on_done)
        self._shell.add_action(self._close)

        self._columns = CardColumns()
        self._columns.add_card(self._build_appearance_card(), column=0)
        self._columns.add_card(self._build_display_card(), column=0)
        self._columns.add_card(self._build_printing_card(), column=0)
        self._columns.add_card(self._build_report_printers_card(), column=1)
        self._columns.add_card(self._build_updates_card(), column=1)
        self._columns.add_card(self._build_about_card(), column=1)
        self._shell.body.addWidget(self._columns)
        self._shell.body.addStretch(1)

        context.theme.theme_changed.connect(self._apply_theme)
        self._apply_theme(context.theme.palette)

        # This page is rebuilt on every visit, so these connections die with
        # it: Qt drops a connection when the receiving QObject is destroyed,
        # and the router deletes the old page rather than parking it.
        updates = context.updates
        updates.checking.connect(self._on_update_checking)
        updates.update_available.connect(self._on_update_found)
        updates.up_to_date.connect(self._on_update_none)
        updates.check_failed.connect(self._on_update_check_failed)
        updates.download_started.connect(self._on_update_download_started)
        updates.download_progress.connect(self._on_update_download_progress)
        updates.download_finished.connect(self._on_update_download_finished)
        updates.download_failed.connect(self._on_update_download_failed)

    # -- construction ------------------------------------------------------

    def _section(self, icon_name: str, title: str, description: str = "") -> SectionHeader:
        header = SectionHeader(icon_name, title, description)
        self._sections.append(header)
        return header

    def _build_appearance_card(self) -> Card:
        card = Card()
        card.body.addWidget(
            self._section("palette", "Appearance", "How Bytesraw ERP and Odoo are painted.")
        )

        self._theme = QComboBox()
        self._theme.setMaximumWidth(_CONTROL_WIDTH)
        for option in (Theme.LIGHT, Theme.DARK):
            self._theme.addItem(option.label, option.value)
        self._theme.currentIndexChanged.connect(self._on_theme_changed)
        self._theme_hint = hint("")
        card.body.addWidget(Field("Theme", self._theme))
        card.body.addWidget(self._theme_hint)
        return card

    def _build_display_card(self) -> Card:
        card = Card()
        card.body.addWidget(
            self._section(
                "monitor",
                "Display",
                "How the Odoo view is drawn. Only change this if it looks wrong.",
            )
        )

        self._render_mode = QComboBox()
        self._render_mode.setMaximumWidth(_WIDE_CONTROL)
        for mode in (RenderMode.AUTO, RenderMode.SOFTWARE):
            self._render_mode.addItem(mode.label, mode.value)
        self._render_mode.currentIndexChanged.connect(self._on_display_changed)
        card.body.addWidget(
            Field(
                "Rendering",
                self._render_mode,
                "Some older graphics drivers paint the Odoo view as stripes, "
                "blank white or garbled tiles while this bar and this page "
                "stay correct. Compatibility mode draws it on the processor "
                "instead, which fixes that at the cost of some speed.",
            )
        )

        self._render_hint = hint("")
        card.body.addWidget(self._render_hint)
        return card

    def _build_printing_card(self) -> Card:
        card = Card()
        card.body.addWidget(
            self._section(
                "printer-cog",
                "Printing",
                "Reports print on A4 unless given a printer of their own. The "
                "Point of Sale screen prints on the receipt printer, and nothing "
                "else uses it.",
            )
        )

        self._mode = QComboBox()
        self._mode.setMaximumWidth(_WIDE_CONTROL)
        for mode in (PrintMode.DIALOG, PrintMode.DIRECT, PrintMode.PREVIEW, PrintMode.SAVE):
            self._mode.addItem(mode.label, mode.value)
        self._mode.setToolTip(
            "How a print that Odoo starts by itself reaches paper - a Point of "
            "Sale receipt, or a report you pressed Print on.\n"
            "Windows' own print dialog has no preview pane, so choose 'Show a "
            "print preview first' to see the pages before they are printed.\n"
            "'Save to the Downloads folder' prints nothing at all, for a "
            "machine with no printer attached."
        )
        self._mode.currentIndexChanged.connect(self._on_printing_changed)
        card.body.addWidget(
            Field(
                "When Odoo prints",
                self._mode,
                "Saving puts the PDF in Downloads and prints nothing.",
            )
        )

        self._report_printer = QComboBox()
        self._report_printer.setMaximumWidth(_WIDE_CONTROL)
        self._report_printer.currentIndexChanged.connect(self._on_printing_changed)
        card.body.addWidget(
            Field(
                "Report printer (A4)",
                self._report_printer,
                "Every report without a printer of its own prints here.",
            )
        )

        self._pos_printer = QComboBox()
        self._pos_printer.setMaximumWidth(_WIDE_CONTROL)
        self._pos_printer.currentIndexChanged.connect(self._on_printing_changed)
        card.body.addWidget(
            Field(
                "Receipt printer (Point of Sale)",
                self._pos_printer,
                "Used for the Point of Sale screen and nothing else.",
            )
        )

        # Directly under the two pickers, because it is about the pair of them.
        self._printer_hint = hint("")
        card.body.addWidget(self._printer_hint)

        self._auto_print = QCheckBox("Print Odoo PDF reports as soon as they arrive")
        self._auto_print.setToolTip(
            "Odoo downloads a rendered PDF when you print a report. With this "
            "on, Bytesraw ERP sends it straight to the report printer instead "
            "of only saving it."
        )
        self._auto_print.toggled.connect(self._on_printing_changed)
        card.body.addWidget(self._auto_print)

        self._keep_copy = QCheckBox("Save every report in the Downloads folder")
        self._keep_copy.setToolTip(
            "Keeps the PDF whether or not it is printed, so a report is still "
            "somewhere you can find it."
        )
        self._keep_copy.toggled.connect(self._on_printing_changed)
        card.body.addWidget(self._keep_copy)
        return card

    def _build_report_printers_card(self) -> Card:
        card = Card()
        card.body.addWidget(
            self._section(
                "printer",
                "Report printers",
                "Send particular Odoo reports to a printer of their own - labels "
                "to a label printer, for instance. Every other report uses the "
                "report printer.",
            )
        )

        rules = QWidget()
        self._rules = QVBoxLayout(rules)
        self._rules.setContentsMargins(0, 0, 0, 0)
        self._rules.setSpacing(12)
        card.body.addWidget(rules)
        self._rules_empty = hint("No report has a printer of its own yet.")
        card.body.addWidget(self._rules_empty)

        self._report_choice = QComboBox()
        self._report_choice.setMaximumWidth(_WIDE_CONTROL)
        self._report_choice.currentIndexChanged.connect(self._sync_add_rule)
        card.body.addWidget(Field("Add a report", self._report_choice))

        self._rule_printer_choice = QComboBox()
        self._add_rule = QPushButton("Add")
        self._add_rule.clicked.connect(self._on_add_rule)
        row = QWidget()
        row.setMaximumWidth(_WIDE_CONTROL)
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        line.addWidget(self._rule_printer_choice, 1)
        line.addWidget(self._add_rule)
        card.body.addWidget(Field("On printer", row))

        # Says where the list of reports came from, and what to do when it is
        # short - the one thing a user who cannot find their report needs.
        self._catalog_hint = hint("")
        card.body.addWidget(self._catalog_hint)
        return card

    def _build_updates_card(self) -> Card:
        card = Card()
        card.body.addWidget(
            self._section(
                "download",
                "Updates",
                f"How this computer gets a new version of {APP_NAME}.",
            )
        )

        self._auto_update = QCheckBox("Check for updates automatically")
        self._auto_update.setToolTip(
            "Checks on launch and every few hours. Nothing is ever downloaded "
            "or installed without you choosing to."
        )
        self._auto_update.toggled.connect(self._on_updates_changed)
        card.body.addWidget(self._auto_update)

        self._channel = QComboBox()
        self._channel.setMaximumWidth(_WIDE_CONTROL)
        for channel in (UpdateChannel.STABLE, UpdateChannel.BETA):
            self._channel.addItem(channel.label, channel.value)
        self._channel.currentIndexChanged.connect(self._on_updates_changed)
        card.body.addWidget(
            Field(
                "Channel",
                self._channel,
                "Beta releases are published for testing and have not been "
                "through the same checks. Leave this on Stable for a till.",
            )
        )

        self._update_status = hint("")
        card.body.addWidget(self._update_status)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._check_now = QPushButton("Check now")
        self._check_now.clicked.connect(self._on_check_now)
        actions.addWidget(self._check_now)

        # Built hidden rather than built on demand: the card's height then
        # never jumps as a check completes, and there is exactly one widget to
        # reason about whether an update is pending or not.
        self._install_update = QPushButton("Download and install")
        self._install_update.setProperty("variant", "primary")
        self._install_update.clicked.connect(self._on_install_update)
        self._install_update.hide()
        actions.addWidget(self._install_update)

        self._release_notes = QPushButton("Release notes")
        self._release_notes.clicked.connect(self._on_release_notes)
        self._release_notes.hide()
        actions.addWidget(self._release_notes)

        actions.addStretch(1)
        card.body.addLayout(actions)
        return card

    def _build_about_card(self) -> Card:
        card = Card()
        card.body.addWidget(
            self._section("info", "About", "What this build is, and where it keeps things.")
        )

        version = QLabel(f"{APP_NAME} {APP_VERSION}")
        version.setObjectName("AccountName")
        card.body.addWidget(
            Field("Version", version, "Quote this number when reporting a problem.")
        )

        self._storage_hint = hint("")
        self._storage_hint.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        card.body.addWidget(Field("Stored on this computer", self._storage_hint))

        folders = QHBoxLayout()
        folders.setSpacing(8)
        for title, path in (("Open logs", logs_dir()), ("Open reports", reports_dir())):
            button = QPushButton(title)
            button.clicked.connect(lambda _checked=False, p=path: self._open(p))
            folders.addWidget(button)
        folders.addStretch(1)
        card.body.addLayout(folders)
        return card

    def _open(self, path: Path) -> None:
        """Show a folder in Explorer. ``logs_dir`` and ``reports_dir`` both
        create the directory themselves, so there is always one to open."""
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self._banner.show_error(f"Could not open {path}.")

    # -- theme -------------------------------------------------------------

    def _apply_theme(self, palette: Palette) -> None:
        self._shell.apply_theme(palette)
        for section in self._sections:
            section.apply_theme(palette)

    # -- router hooks ------------------------------------------------------

    def on_enter(self, _params: dict[str, str]) -> None:
        self._banner.clear_message()
        self._load()

    # -- loading -----------------------------------------------------------

    def _load(self) -> None:
        self._loading = True
        try:
            theme = self._context.theme.theme
            index = self._theme.findData(theme.value)
            self._theme.setCurrentIndex(max(index, 0))
            self._refresh_theme_hint()

            display = self._context.settings.display
            self._render_mode.setCurrentIndex(
                max(self._render_mode.findData(display.render_mode.value), 0)
            )
            self._refresh_render_hint()

            self._populate_printers()
            settings = self._context.settings.printing
            self._mode.setCurrentIndex(max(self._mode.findData(settings.mode.value), 0))
            self._select_printer(self._report_printer, settings.report_printer_name, "report")
            self._select_printer(self._pos_printer, settings.pos_printer_name, "receipt")
            self._render_rules(settings)
            self._refresh_report_choices()
            self._load_catalog()
            # Not gated on auto-print: with printing off, keeping a copy is
            # the only thing that leaves a report anywhere the user can find
            # it. Only the saving mode, which keeps one unconditionally, takes
            # that choice away.
            self._sync_printing_controls(settings)
            self._refresh_printer_hint(settings)
            self._load_updates()
            self._storage_hint.setText(str(self._context.settings.path))
        finally:
            self._loading = False

    def _populate_printers(self) -> None:
        """Fill both pickers: the Windows default, "not assigned", then devices.

        "Not assigned" carries ``None`` as its item data, which is
        :data:`NO_PRINTER` itself rather than a stand-in for it - no string can
        do that job, because every string is a name somebody could give a
        printer. ``currentData()`` returns ``None`` for an empty combo too, but
        a combo is only empty inside this method, and ``_loading`` is up
        throughout the one reload that calls it.
        """
        default = default_printer_name()
        self._default_label = (
            f"Windows default ({default})" if default else "Windows default (none set)"
        )
        self._printer_names = available_printers()
        for combo in (self._report_printer, self._pos_printer, self._rule_printer_choice):
            self._fill_printer_combo(combo)

    def _fill_printer_combo(self, combo: QComboBox) -> None:
        combo.clear()
        combo.addItem(self._default_label, _SYSTEM_DEFAULT)
        combo.addItem("Not assigned - do not print", NO_PRINTER)
        for name in self._printer_names:
            combo.addItem(name, name)

    def _refresh_printer_hint(self, settings: PrintSettings) -> None:
        """Say what the two pickers add up to, when it is not obvious.

        Two things can need saying. An unassigned slot prints nothing of that
        kind, which is the point of choosing it but is worth confirming rather
        than leaving the user to discover on the next receipt. And both slots
        resolving to one device is the trap "Windows default" sets on a till,
        where the default printer is usually the receipt printer - the very
        thing that sent A4 invoices to an 80mm roll. That check is on the
        resolved device rather than on the printer's name: guessing "thermal"
        from a model name would be wrong on the machines that matter.
        """
        unassigned = [
            what
            for what, name in (
                ("reports", settings.report_printer_name),
                ("Point of Sale receipts", settings.pos_printer_name),
            )
            if name is NO_PRINTER
        ]
        if unassigned:
            # The two slots do not fail the same way, and the difference is
            # deliberate: a report PDF already exists and would be deleted
            # within a day, so it is kept; a receipt is a page that was never
            # rendered, and saving one per order would bury the user's
            # Downloads folder in tickets nobody asked for.
            text = f"Nothing will be printed for {' or '.join(unassigned)}."
            if settings.report_printer_name is NO_PRINTER:
                text += " A report Odoo prints is saved in Downloads instead."
            self._printer_hint.setText(text)
            return

        default = default_printer_name()
        report = settings.report_printer_name or default
        pos = settings.pos_printer_name or default
        if report and report == pos:
            self._printer_hint.setText(
                f"Both are currently '{report}'. If that is a receipt printer, "
                "A4 reports will not come out readable - choose a different "
                "printer for reports, or turn off printing them below."
            )
        else:
            self._printer_hint.setText("")

    def _select_printer(self, combo: QComboBox, name: str | None, what: str) -> None:
        """Show the stored printer, or say plainly that it has gone."""
        index = combo.findData(name)
        if index < 0:
            # The configured printer is gone. Show the system default and say
            # so, rather than silently pretending it is still there.
            index = 0
            self._banner.show_info(
                f"The {what} printer '{name}' is no longer available, so the "
                "Windows default will be used."
            )
        combo.setCurrentIndex(index)

    # -- report printers ---------------------------------------------------

    def _render_rules(self, settings: PrintSettings) -> None:
        """One row per rule: the report, its printer, and a way to remove it.

        Rebuilt rather than patched: a rule list is a handful of rows, and
        rebuilding is the one way the rows cannot drift from storage.
        """
        while self._rules.count():
            item = self._rules.takeAt(0)
            if (widget := item.widget()) is not None:
                widget.deleteLater()
        for rule in settings.report_printers:
            self._rules.addWidget(self._rule_row(rule))
        self._rules_empty.setVisible(not settings.report_printers)

    def _rule_row(self, rule: ReportPrinterRule) -> QWidget:
        combo = QComboBox()
        self._fill_printer_combo(combo)
        index = combo.findData(rule.printer_name)
        if index < 0:
            # Gone from Windows since the rule was written. Said, not hidden -
            # and printed on the Windows default meanwhile, which is what
            # `_build_printer` does with a printer it cannot find.
            index = 0
            self._banner.show_info(
                f"The printer '{rule.printer_name}' for {rule.label} is no longer "
                "available, so the Windows default will be used."
            )
        combo.setCurrentIndex(index)
        combo.currentIndexChanged.connect(
            lambda _i, name=rule.report_name, box=combo: self._on_rule_printer_changed(name, box)
        )

        remove = QPushButton("Remove")
        remove.setProperty("variant", "link")
        remove.setToolTip(f"{rule.label} goes back to the report printer.")
        remove.clicked.connect(lambda _c=False, name=rule.report_name: self._on_remove_rule(name))

        row = QWidget()
        row.setMaximumWidth(_WIDE_CONTROL)
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        line.addWidget(combo, 1)
        line.addWidget(remove)
        # The technical name underneath, because titles repeat - Odoo ships two
        # "Package Barcode (PDF)" - and it is what the rule actually matches.
        return Field(rule.label, row, rule.report_name)

    def _refresh_report_choices(self) -> None:
        """Offer every report that has no printer of its own yet.

        Printed-on-this-computer first: it is the short list, it is the one a
        cashier's session can use, and it is almost always what the user just
        went and printed in order to set it up.
        """
        taken = {r.report_name for r in self._context.settings.printing.report_printers}
        previous = self._report_choice.currentData()
        self._report_choice.blockSignals(True)
        try:
            self._report_choice.clear()
            self._report_choice.addItem("Choose a report...", None)
            recent = [
                (name, self._catalog[name].title if name in self._catalog else stem)
                for name, stem in self._context.recent_reports.items()
                if name not in taken
            ]
            for name, title in recent:
                self._report_choice.addItem(f"{title} - printed recently", name)
            listed = [info for info in self._catalog.values() if info.report_name not in taken]
            if recent and listed:
                self._report_choice.insertSeparator(self._report_choice.count())
            titles = [info.title for info in listed]
            for info in listed:
                text = info.title or info.report_name
                if titles.count(info.title) > 1:
                    text = f"{text} ({info.report_name})"
                self._report_choice.addItem(text, info.report_name)
            for index in range(self._report_choice.count()):
                if name := self._report_choice.itemData(index):
                    self._report_choice.setItemData(index, name, Qt.ItemDataRole.ToolTipRole)
            self._report_choice.setCurrentIndex(max(self._report_choice.findData(previous), 0))
        finally:
            self._report_choice.blockSignals(False)
        self._sync_add_rule()

    def _sync_add_rule(self, *_args: object) -> None:
        self._add_rule.setEnabled(bool(self._report_choice.currentData()))

    def _load_catalog(self) -> None:
        """Read the database's reports in the background, when there is one."""
        client, session = self._context.client, self._context.session
        if client is None or session is None:
            self._set_catalog_hint("Sign in to Odoo to choose from every report on the database.")
            return
        self._catalog_hint.setText("Reading the reports on this Odoo database...")
        run_async(
            fetch_reports,
            client,
            session.language,
            on_success=self._on_catalog_loaded,
            on_error=self._on_catalog_failed,
        )

    def _on_catalog_loaded(self, reports: object) -> None:
        self._catalog = {
            info.report_name: info
            for info in (reports if isinstance(reports, list) else [])
            if isinstance(info, ReportInfo)
        }
        self._catalog_hint.setText("")
        self._refresh_report_choices()

    def _on_catalog_failed(self, exc: Exception) -> None:
        if not isinstance(exc, ReportListUnavailable):
            _log.warning("Could not read the report list: %s", exc)
            self._set_catalog_hint(f"The reports could not be read from Odoo: {exc}")
            return
        self._set_catalog_hint(str(exc))

    def _set_catalog_hint(self, text: str) -> None:
        """Say where the short list comes from when the long one is missing."""
        if not self._context.recent_reports:
            text += " A report printed on this computer is listed here too."
        self._catalog_hint.setText(text)

    def _on_add_rule(self) -> None:
        name = self._report_choice.currentData()
        stored = self._context.settings.printing
        if not name or stored.rule_for(name) is not None:
            return
        info = self._catalog.get(name)
        title = info.title if info else self._context.recent_reports.get(name, "")
        rule = ReportPrinterRule(name, title, _printer_choice(self._rule_printer_choice))
        if not self._store_rules((*stored.report_printers, rule)):
            return
        self._rule_printer_choice.setCurrentIndex(
            max(self._rule_printer_choice.findData(SYSTEM_DEFAULT_PRINTER), 0)
        )
        self._banner.show_info(f"{rule.label} now prints on its own printer.")

    def _on_rule_printer_changed(self, report_name: str, combo: QComboBox) -> None:
        printer = _printer_choice(combo)
        rules = tuple(
            r.evolve(printer_name=printer) if r.report_name == report_name else r
            for r in self._context.settings.printing.report_printers
        )
        # The row stays: rebuilding it would take the combo out from under
        # the change it is still delivering.
        if self._store_rules(rules, rerender=False):
            self._banner.show_info("Print settings saved.")

    def _on_remove_rule(self, report_name: str) -> None:
        stored = self._context.settings.printing
        rule = stored.rule_for(report_name)
        rules = tuple(r for r in stored.report_printers if r.report_name != report_name)
        if self._store_rules(rules) and rule is not None:
            self._banner.show_info(f"{rule.label} prints on the report printer again.")

    def _store_rules(self, rules: tuple[ReportPrinterRule, ...], *, rerender: bool = True) -> bool:
        settings = self._context.settings.printing.evolve(report_printers=rules)
        try:
            self._context.settings.set_printing(settings)
        except BytesrawError as exc:
            self._banner.show_error(str(exc))
            return False
        if rerender:
            # Safe from inside a row's own Remove button: the old rows go by
            # deleteLater, after this click has finished being delivered.
            self._render_rules(settings)
            self._refresh_report_choices()
        return True

    def _refresh_render_hint(self) -> None:
        """Say whether the stored mode is the one actually in force.

        It cannot be, until the next launch: Chromium reads its command line
        as it starts, so a change here is a promise about the next process
        rather than about this one. Saying nothing would leave the user
        watching the same stripes and concluding the setting does nothing.
        """
        stored = RenderMode(str(self._render_mode.currentData()))
        active = active_render_mode()
        if stored is active:
            self._render_hint.setText(f"In force now: {active.label.lower()}.")
        else:
            self._render_hint.setText(
                f"Saved. Close and reopen {APP_NAME} to apply it - until then "
                f"this session is still using: {active.label.lower()}."
            )

    def _refresh_theme_hint(self) -> None:
        """Explain what the theme does to Odoo on *this* server.

        The honest answer depends on the server, so it is read from the live
        session rather than asserted.
        """
        session = self._context.session
        if session is None:
            self._theme_hint.setText("Applies to Bytesraw ERP. Sign in to also theme Odoo.")
        elif session.can_set_odoo_theme:
            self._theme_hint.setText(
                "Applies to Bytesraw ERP and to Odoo, which has a per-user "
                "colour scheme on this server."
            )
        elif session.native_dark_mode:
            self._theme_hint.setText(
                "Applies to Bytesraw ERP and to Odoo, through the colour-scheme cookie."
            )
        else:
            self._theme_hint.setText(
                "Applies to Bytesraw ERP. This Odoo server has no dark theme of "
                "its own, so the embedded client is darkened by the browser engine."
            )

    # -- change handlers ---------------------------------------------------

    def _on_theme_changed(self, index: int) -> None:
        if self._loading or index < 0:
            return
        value = self._theme.itemData(index)
        self._context.theme.set_theme(Theme(str(value)))
        self._refresh_theme_hint()
        self._banner.show_info("Appearance updated.")

    def _on_display_changed(self, index: int) -> None:
        if self._loading or index < 0:
            return
        settings = DisplaySettings(
            render_mode=RenderMode(str(self._render_mode.itemData(index)))
        )
        try:
            self._context.settings.set_display(settings)
        except BytesrawError as exc:
            self._banner.show_error(str(exc))
            return
        self._refresh_render_hint()
        if settings.render_mode is active_render_mode():
            self._banner.show_info("Display setting saved.")
        else:
            self._banner.show_info(
                f"Display setting saved. Close and reopen {APP_NAME} to apply it."
            )

    def _on_printing_changed(self, *_args: object) -> None:
        if self._loading:
            return
        mode = PrintMode(str(self._mode.currentData()))
        # In the saving mode both checkboxes are showing a decision the mode
        # already made, so what they display is not what the user chose. The
        # *outgoing* mode matters as much as the incoming one: on the way back
        # out they are still showing those forced values and have not been
        # rewritten yet, so reading them there is what would quietly adopt
        # them. Either end of the trip reads storage instead, and the user's
        # own choice survives the round trip untouched.
        stored = self._context.settings.printing
        forced = not mode.prints or not stored.mode.prints
        settings = PrintSettings(
            mode=mode,
            report_printer_name=_printer_choice(self._report_printer),
            pos_printer_name=_printer_choice(self._pos_printer),
            # Not on this card's controls; carried through untouched, or
            # changing the print mode would silently delete every rule.
            report_printers=stored.report_printers,
            auto_print_reports=(
                stored.auto_print_reports if forced else self._auto_print.isChecked()
            ),
            keep_report_copy=(
                stored.keep_report_copy if forced else self._keep_copy.isChecked()
            ),
        )
        try:
            self._context.settings.set_printing(settings)
        except BytesrawError as exc:
            self._banner.show_error(str(exc))
            return
        self._sync_printing_controls(settings)
        self._refresh_printer_hint(settings)
        self._banner.show_info("Print settings saved.")

    def _sync_printing_controls(self, settings: PrintSettings) -> None:
        """Show what the mode has already settled, instead of offering it twice.

        In the saving mode nothing is printed and the PDF is always kept, so
        both checkboxes are answers rather than questions: they are set to the
        truth and disabled. A checkbox reading "Print reports as soon as they
        arrive", ticked, on a machine that is saving them instead, would be a
        plain lie about what the app is doing.

        The stored settings are not touched, which is why ``_on_printing_changed``
        reads them rather than the widgets while this is in force.
        """
        saving = not settings.mode.prints
        previous, self._loading = self._loading, True
        try:
            self._auto_print.setChecked(False if saving else settings.auto_print_reports)
            self._keep_copy.setChecked(True if saving else settings.keep_report_copy)
        finally:
            self._loading = previous
        self._auto_print.setEnabled(not saving)
        self._keep_copy.setEnabled(not saving)

    # -- updates -----------------------------------------------------------

    def _load_updates(self) -> None:
        settings = self._context.settings.updates
        self._auto_update.setChecked(settings.check_automatically)
        self._channel.setCurrentIndex(max(self._channel.findData(settings.channel.value), 0))
        # An update found while the user was elsewhere is still on offer: the
        # service remembers it, so re-entering this page needs no fresh check.
        self._pending = self._context.updates.available
        self._refresh_update_status()

    def _refresh_update_status(self) -> None:
        """Say what is known, and only what is known.

        The states that matter are "nothing found", "something found", "a check
        is running" and "nothing has ever looked" - and it is the last one this
        card exists for, because an automatic check that fails says nothing.
        """
        updates = self._context.updates
        self._check_now.setEnabled(not updates.busy and not updates.installing)
        self._install_update.setVisible(self._pending is not None)
        self._install_update.setEnabled(not updates.busy and not updates.installing)
        self._release_notes.setVisible(bool(self._pending and self._pending.notes_url))

        if updates.installing:
            self._update_status.setText(
                f"Installing the update. {APP_NAME} will close and reopen."
            )
            return
        if updates.downloading:
            return  # the progress handler owns the text while a download runs
        if updates.checking_now:
            self._update_status.setText("Checking for updates...")
            return
        if self._pending is not None:
            required = " This update is required." if self._is_required() else ""
            self._update_status.setText(
                f"{APP_NAME} {self._pending.version} is available "
                f"({self._pending.artifact.size_mb}).{required}"
            )
            return
        self._update_status.setText(self._idle_status())

    def _is_required(self) -> bool:
        return self._pending is not None and self._pending.is_required_for(APP_VERSION)

    def _idle_status(self) -> str:
        """What to say when there is nothing on offer.

        A source checkout is called out explicitly. Its automatic checks are
        skipped - there is no installed copy to replace - and a card claiming to
        check automatically while nothing ever checked is a lie that costs a
        developer an afternoon.

        So is an empty channel. One manifest file per channel means a channel
        nobody has released to has no file at all, and saying "up to date" there
        would claim this build was compared against something when nothing was
        published to compare it with.
        """
        checked = self._context.settings.updates.last_check_at
        when = f"Last checked {_describe_age(checked)}." if checked else "Not checked yet."
        if self._context.updates.channel_empty:
            channel = self._context.settings.updates.channel.value
            return (
                f"{when} Nothing has been published to the {channel} channel "
                f"yet, so this computer stays on {APP_NAME} {APP_VERSION}."
            )
        if not is_installed_build():
            return (
                f"{when} This is a development build, so automatic checks are "
                "skipped - use Check now."
            )
        if not self._auto_update.isChecked():
            return f"{when} Automatic checks are off."
        return f"{when} {APP_NAME} {APP_VERSION} is up to date."

    def _on_updates_changed(self, *_args: object) -> None:
        if self._loading:
            return
        settings = self._context.settings.updates.evolve(
            check_automatically=self._auto_update.isChecked(),
            channel=UpdateChannel(str(self._channel.currentData())),
        )
        try:
            self._context.settings.set_updates(settings)
        except BytesrawError as exc:
            self._banner.show_error(str(exc))
            return
        self._context.updates.apply_settings()
        # A channel change makes whatever was on offer the wrong answer - it came
        # out of the other manifest. Dropping it is what stops a till moved back
        # to stable still being offered the beta it saw a moment ago.
        self._pending = None
        self._refresh_update_status()
        self._banner.show_info("Update settings saved.")

    def _on_check_now(self) -> None:
        self._banner.clear_message()
        # ``manual`` is what makes this ignore a staged rollout: a user who
        # asked the question is owed the true answer, not the one their bucket
        # allows.
        self._context.updates.check(manual=True)

    def _on_install_update(self) -> None:
        self._banner.clear_message()
        self._context.updates.download_and_install(self._pending)

    def _on_release_notes(self) -> None:
        if self._pending is None or not self._pending.notes_url:
            return
        if not QDesktopServices.openUrl(QUrl(self._pending.notes_url)):
            self._banner.show_error("Could not open the release notes in your browser.")

    def _on_update_checking(self) -> None:
        self._refresh_update_status()

    def _on_update_found(self, update: object) -> None:
        self._pending = update if isinstance(update, Update) else None
        self._refresh_update_status()

    def _on_update_none(self) -> None:
        self._pending = None
        self._refresh_update_status()

    def _on_update_check_failed(self, message: str) -> None:
        self._refresh_update_status()
        # Shown here and nowhere else. The automatic check stays silent on
        # purpose; this page is the one place a check was *asked for*, so this
        # is the one place a failure is news rather than noise.
        self._banner.show_error(message)

    def _on_update_download_started(self, _update: object) -> None:
        self._refresh_update_status()
        self._update_status.setText("Downloading the update...")

    def _on_update_download_progress(self, received: int, total: int) -> None:
        if total <= 0:
            return
        self._update_status.setText(
            f"Downloading the update... {received * 100 // total}% "
            f"({received / (1024 * 1024):.0f} MB of {total / (1024 * 1024):.0f} MB)"
        )

    def _on_update_download_finished(self, _path: object) -> None:
        self._refresh_update_status()

    def _on_update_download_failed(self, message: str) -> None:
        self._refresh_update_status()
        self._banner.show_error(message)

    # -- navigation --------------------------------------------------------

    def _on_done(self) -> None:
        if not self._router.back():
            self._router.go(self._context.landing_route())
