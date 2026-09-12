"""Application settings: appearance, display, printing, and what is stored where.

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

Each group is a card with a badged header, so the page reads as a short list of
decisions rather than one long list of controls. The About card is the honest home for
the build number, the settings file and the folders the app writes to - the
things a user is asked for when they report a problem.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bytesraw_erp.constants import APP_NAME, APP_VERSION
from bytesraw_erp.core.errors import BytesrawError
from bytesraw_erp.core.paths import logs_dir, reports_dir
from bytesraw_erp.data.models import (
    DisplaySettings,
    PrintMode,
    PrintSettings,
    RenderMode,
)
from bytesraw_erp.services.graphics import active_render_mode
from bytesraw_erp.services.print_service import available_printers, default_printer_name
from bytesraw_erp.ui.app_context import AppContext
from bytesraw_erp.ui.router import Router
from bytesraw_erp.ui.theme import Palette, Theme
from bytesraw_erp.ui.widgets.banner import Banner
from bytesraw_erp.ui.widgets.sections import BrandHeader, Card, Field, SectionHeader, hint

_log = logging.getLogger(__name__)

_PAGE_WIDTH = 680
#: A combo holding two words should not run the width of the card.
_CONTROL_WIDTH = 240
_WIDE_CONTROL = 420

#: Stored as an empty printer name - resolved to whatever Windows says at print
#: time, so the setting keeps following the OS default when the user changes it.
_SYSTEM_DEFAULT = ""


class SettingsPage(QWidget):
    """Appearance, display and printing preferences, stored locally."""

    def __init__(self, context: AppContext, router: Router, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._router = router
        #: Guards the change handlers while fields are populated from storage.
        self._loading = False
        #: Section badges are rendered bitmaps, and this is the one page where
        #: the theme changes under the widget that is showing it.
        self._sections: list[SectionHeader] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        container = QWidget()
        container.setMaximumWidth(_PAGE_WIDTH)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 32, 24, 32)
        layout.setSpacing(18)

        header = QHBoxLayout()
        header.setSpacing(16)
        header.addWidget(
            BrandHeader("Settings", "Appearance, display and printing, for this computer."), 1
        )
        self._close = QPushButton("Done")
        self._close.setProperty("variant", "primary")
        self._close.clicked.connect(self._on_done)
        header.addWidget(self._close, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        self._banner = Banner()
        layout.addWidget(self._banner)

        layout.addWidget(self._build_appearance_card())
        layout.addWidget(self._build_display_card())
        layout.addWidget(self._build_printing_card())
        layout.addWidget(self._build_about_card())
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(container)
        outer.addWidget(scroll)

        context.theme.theme_changed.connect(self._apply_theme)
        self._apply_theme(context.theme.palette)

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
                "Applies to every print from Odoo, including a report's own "
                "Print button and a receipt calling window.print().",
            )
        )

        self._mode = QComboBox()
        self._mode.setMaximumWidth(_WIDE_CONTROL)
        for mode in (PrintMode.DIALOG, PrintMode.DIRECT, PrintMode.PREVIEW):
            self._mode.addItem(mode.label, mode.value)
        self._mode.currentIndexChanged.connect(self._on_printing_changed)
        card.body.addWidget(
            Field(
                "When Odoo prints",
                self._mode,
                "Windows' own print dialog has no preview pane, so choose "
                "'Show a print preview first' if you want to see the pages "
                "before they are printed.",
            )
        )

        self._printer = QComboBox()
        self._printer.setMaximumWidth(_WIDE_CONTROL)
        self._printer.currentIndexChanged.connect(self._on_printing_changed)
        card.body.addWidget(Field("Printer", self._printer))

        self._auto_print = QCheckBox("Print Odoo PDF reports as soon as they arrive")
        self._auto_print.setToolTip(
            "Odoo downloads a rendered PDF when you print a report. With this "
            "on, Bytesraw ERP sends it straight to the printer instead of only "
            "saving it."
        )
        self._auto_print.toggled.connect(self._on_printing_changed)
        card.body.addWidget(self._auto_print)

        self._keep_copy = QCheckBox("Also keep a copy in the Downloads folder")
        self._keep_copy.toggled.connect(self._on_printing_changed)
        card.body.addWidget(self._keep_copy)
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
            printer_index = self._printer.findData(settings.printer_name)
            if printer_index < 0:
                # The configured printer is gone. Show the system default and
                # say so, rather than silently pretending it is still there.
                printer_index = 0
                self._banner.show_info(
                    f"The printer '{settings.printer_name}' is no longer "
                    "available, so the Windows default will be used."
                )
            self._printer.setCurrentIndex(printer_index)
            self._auto_print.setChecked(settings.auto_print_reports)
            self._keep_copy.setChecked(settings.keep_report_copy)
            self._keep_copy.setEnabled(settings.auto_print_reports)
            self._storage_hint.setText(str(self._context.settings.path))
        finally:
            self._loading = False

    def _populate_printers(self) -> None:
        self._printer.clear()
        default = default_printer_name()
        self._printer.addItem(
            f"Windows default ({default})" if default else "Windows default (none set)",
            _SYSTEM_DEFAULT,
        )
        for name in available_printers():
            self._printer.addItem(name, name)

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
        settings = PrintSettings(
            mode=PrintMode(str(self._mode.currentData())),
            printer_name=str(self._printer.currentData() or _SYSTEM_DEFAULT),
            auto_print_reports=self._auto_print.isChecked(),
            keep_report_copy=self._keep_copy.isChecked(),
        )
        self._keep_copy.setEnabled(settings.auto_print_reports)
        try:
            self._context.settings.set_printing(settings)
        except BytesrawError as exc:
            self._banner.show_error(str(exc))
            return
        self._banner.show_info("Print settings saved.")

    # -- navigation --------------------------------------------------------

    def _on_done(self) -> None:
        if not self._router.back():
            self._router.go(self._context.landing_route())
