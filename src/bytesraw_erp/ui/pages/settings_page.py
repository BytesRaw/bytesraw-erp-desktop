"""Application settings: appearance and printing.

Changes apply and save immediately. A settings page with a Save button invites
the user to close it with unsaved edits; applying on change means the printer
they pick is the printer that prints, with no second step.

Printing is configured here, once, rather than per account: which printer is
attached is a fact about the machine in front of the user, not about the Odoo
database they are signed in to.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bytesraw_erp.core.errors import BytesrawError
from bytesraw_erp.data.models import PrintMode, PrintSettings
from bytesraw_erp.services.print_service import available_printers, default_printer_name
from bytesraw_erp.ui.app_context import AppContext
from bytesraw_erp.ui.router import Router
from bytesraw_erp.ui.theme import Theme
from bytesraw_erp.ui.widgets.banner import Banner

_log = logging.getLogger(__name__)

#: Stored as an empty printer name - resolved to whatever Windows says at print
#: time, so the setting keeps following the OS default when the user changes it.
_SYSTEM_DEFAULT = ""


def _section(title: str) -> QLabel:
    label = QLabel(title)
    label.setObjectName("SectionTitle")
    return label


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("MutedLabel")
    label.setWordWrap(True)
    return label


def _rule() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    line.setObjectName("Rule")
    return line


class SettingsPage(QWidget):
    """Appearance and printing preferences, stored locally."""

    def __init__(self, context: AppContext, router: Router, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._router = router
        #: Guards the change handlers while fields are populated from storage.
        self._loading = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        container = QWidget()
        container.setMaximumWidth(680)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 28, 24, 32)
        layout.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("Settings")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)
        self._close = QPushButton("Done")
        self._close.setProperty("variant", "primary")
        self._close.clicked.connect(self._on_done)
        header.addWidget(self._close)
        layout.addLayout(header)

        self._banner = Banner()
        layout.addWidget(self._banner)

        layout.addWidget(_section("Appearance"))
        layout.addLayout(self._build_appearance())
        layout.addWidget(_rule())

        layout.addWidget(_section("Printing"))
        layout.addLayout(self._build_printing())
        layout.addWidget(
            _hint(
                "These settings apply to every print from Odoo, including the "
                "Print button on a report and a receipt calling window.print()."
            )
        )
        layout.addWidget(_rule())
        layout.addWidget(_section("Storage"))
        self._storage_hint = _hint("")
        layout.addWidget(self._storage_hint)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(container)
        outer.addWidget(scroll)

    # -- construction ------------------------------------------------------

    def _build_appearance(self) -> QFormLayout:
        form = QFormLayout()
        form.setSpacing(10)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self._theme = QComboBox()
        for option in (Theme.LIGHT, Theme.DARK):
            self._theme.addItem(option.label, option.value)
        self._theme.currentIndexChanged.connect(self._on_theme_changed)
        form.addRow("Theme", self._theme)

        self._theme_hint = _hint("")
        form.addRow("", self._theme_hint)
        return form

    def _build_printing(self) -> QFormLayout:
        form = QFormLayout()
        form.setSpacing(10)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self._mode = QComboBox()
        for mode in (PrintMode.DIALOG, PrintMode.DIRECT, PrintMode.PREVIEW):
            self._mode.addItem(mode.label, mode.value)
        self._mode.currentIndexChanged.connect(self._on_printing_changed)
        form.addRow("When Odoo prints", self._mode)
        form.addRow(
            "",
            _hint(
                "Windows' own print dialog has no preview pane, so choose "
                "'Show a print preview first' if you want to see the pages "
                "before they are printed."
            ),
        )

        self._printer = QComboBox()
        self._printer.currentIndexChanged.connect(self._on_printing_changed)
        form.addRow("Printer", self._printer)

        self._auto_print = QCheckBox("Print Odoo PDF reports as soon as they arrive")
        self._auto_print.setToolTip(
            "Odoo downloads a rendered PDF when you print a report. With this "
            "on, Bytesraw ERP sends it straight to the printer instead of only "
            "saving it."
        )
        self._auto_print.toggled.connect(self._on_printing_changed)
        form.addRow("", self._auto_print)

        self._keep_copy = QCheckBox("Also keep a copy in the Downloads folder")
        self._keep_copy.toggled.connect(self._on_printing_changed)
        form.addRow("", self._keep_copy)
        return form

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
            self._storage_hint.setText(
                f"Settings are stored on this computer at\n{self._context.settings.path}"
            )
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
