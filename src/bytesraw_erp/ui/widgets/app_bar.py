"""The native app bar that sits above the embedded Odoo client.

Shows the active company (name + logo), the signed-in user and the interface
language, plus browser-style navigation actions.

The company is **display only**. Odoo's own navbar already has a company
switcher, and two switchers over one session can disagree; ours also had to
write ``res.users.company_id`` to make the change stick, which is a heavier
side effect than Odoo's own cookie-only switch. So the company slot shows where
you are, and Odoo remains the one place that changes it.

Read-only rule
--------------
The language slot is only interactive when there is something to choose. With a
single active language the combo box is replaced by a plain label - not a
disabled combo - because a disabled control reads as "you may not", while this
case is "there is no alternative".
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from bytesraw_erp.data.models import PrintMode, SessionContext
from bytesraw_erp.ui.theme import APP_BAR_HEIGHT, Palette, Theme
from bytesraw_erp.ui.widgets.icons import icon, set_icon_color

_LOGO_HEIGHT = 28


def _separator() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.VLine)
    line.setFrameShadow(QFrame.Shadow.Plain)
    line.setFixedWidth(1)
    return line


class AppBar(QWidget):
    """Chrome for the Odoo page. Emits intent; performs no navigation itself."""

    back_requested = Signal()
    forward_requested = Signal()
    reload_requested = Signal()
    home_requested = Signal()
    language_changed = Signal(str)
    switch_account_requested = Signal()
    manage_accounts_requested = Signal()
    sign_out_requested = Signal()
    #: A print was asked for, with the mode the user picked.
    print_requested = Signal(PrintMode)
    #: The user chose a different appearance.
    theme_requested = Signal(Theme)
    #: Open the settings page.
    settings_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AppBar")
        self.setFixedHeight(APP_BAR_HEIGHT)
        self._default_print_mode = PrintMode.DIALOG
        #: (button, icon name) pairs. A QIcon is a finished bitmap, so a theme
        #: change means re-rendering and re-assigning every one of them.
        self._icon_targets: list[tuple[QToolButton, str]] = []
        #: Guards combo signals while the bar repopulates them programmatically.
        self._syncing = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(10)

        self._build_company_section(layout)
        layout.addWidget(_separator())
        self._build_navigation_section(layout)
        layout.addStretch(1)
        self._build_tools_section(layout)
        layout.addWidget(_separator())
        self._build_identity_section(layout)

        self.set_session(None)

    # -- construction ------------------------------------------------------

    def _build_company_section(self, layout: QHBoxLayout) -> None:
        self._logo = QLabel()
        self._logo.setFixedHeight(_LOGO_HEIGHT)
        self._logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._logo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout.addWidget(self._logo)

        self._company_label = QLabel()
        self._company_label.setObjectName("CompanyName")
        self._company_label.setToolTip("Active company - change it in Odoo's own menu")
        layout.addWidget(self._company_label)

    def _build_navigation_section(self, layout: QHBoxLayout) -> None:
        self._back = self._tool_button("arrow-left", "Back", self.back_requested)
        self._forward = self._tool_button("arrow-right", "Forward", self.forward_requested)
        self._reload = self._tool_button("refresh", "Reload", self.reload_requested)
        self._home = self._tool_button("home", "Odoo home", self.home_requested)
        for button in (self._back, self._forward, self._reload, self._home):
            layout.addWidget(button)

    def _build_tools_section(self, layout: QHBoxLayout) -> None:
        """Print and appearance controls.

        Both are split buttons: the icon runs the common action, the menu
        offers the explicit choices. Printing needs two, because a POS till
        wants paper immediately while an office user wants to pick a tray.
        """
        self._print_button = QToolButton()
        self._print_button.setIconSize(QSize(18, 18))
        self._print_button.setToolTip(
            "Print the page you are looking at (Ctrl+P).\n"
            "Odoo reports print by themselves - see Settings."
        )
        self._print_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self._print_button.clicked.connect(self._emit_default_print)
        self._register_icon(self._print_button, "printer")

        print_menu = QMenu(self)
        self._print_direct_action = QAction("Print this page", self)
        self._print_direct_action.setShortcut("Ctrl+P")
        self._print_direct_action.triggered.connect(
            lambda: self.print_requested.emit(PrintMode.DIRECT)
        )
        self._print_dialog_action = QAction("Print this page with options...", self)
        self._print_dialog_action.setShortcut("Ctrl+Shift+P")
        self._print_dialog_action.triggered.connect(
            lambda: self.print_requested.emit(PrintMode.DIALOG)
        )
        print_menu.addAction(self._print_direct_action)
        print_menu.addAction(self._print_dialog_action)
        print_menu.addSeparator()
        printer_settings = QAction("Printer settings...", self)
        printer_settings.triggered.connect(self.settings_requested.emit)
        print_menu.addAction(printer_settings)
        self._print_button.setMenu(print_menu)
        layout.addWidget(self._print_button)

        self._theme_button = QToolButton()
        self._theme_button.setIconSize(QSize(18, 18))
        self._theme_button.setToolTip("Appearance")
        self._theme_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        theme_menu = QMenu(self)
        self._theme_actions: dict[Theme, QAction] = {}
        for option in (Theme.LIGHT, Theme.DARK):
            action = QAction(option.label, self)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked, o=option: self.theme_requested.emit(o))
            theme_menu.addAction(action)
            self._theme_actions[option] = action
        self._theme_button.setMenu(theme_menu)
        layout.addWidget(self._theme_button)

    def _emit_default_print(self) -> None:
        """The button itself runs whatever this account is configured to do."""
        self.print_requested.emit(self._default_print_mode)

    def _build_identity_section(self, layout: QHBoxLayout) -> None:
        self._language_label = QLabel()
        self._language_label.setObjectName("MutedLabel")
        layout.addWidget(self._language_label)

        self._language_combo = QComboBox()
        self._language_combo.setMinimumWidth(140)
        self._language_combo.setToolTip("Interface language")
        self._language_combo.currentIndexChanged.connect(self._on_language_index_changed)
        layout.addWidget(self._language_combo)

        layout.addWidget(_separator())

        self._user_label = QLabel()
        self._user_label.setObjectName("UserName")
        layout.addWidget(self._user_label)

        self._menu_button = QToolButton()
        self._menu_button.setIconSize(QSize(18, 18))
        self._register_icon(self._menu_button, "chevron-down")
        self._menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu_button.setMenu(self._build_menu())
        layout.addWidget(self._menu_button)

    def _tool_button(self, icon_name: str, tooltip: str, signal: Signal) -> QToolButton:
        button = QToolButton()
        button.setIconSize(QSize(18, 18))
        button.setToolTip(tooltip)
        self._register_icon(button, icon_name)
        button.clicked.connect(signal.emit)
        return button

    def _build_menu(self) -> QMenu:
        menu = QMenu(self)
        entries = (
            ("Switch account", self.switch_account_requested),
            ("Manage accounts", self.manage_accounts_requested),
            ("Settings", self.settings_requested),
        )
        for title, signal in entries:
            action = QAction(title, self)
            action.triggered.connect(signal.emit)
            menu.addAction(action)
        menu.addSeparator()
        sign_out = QAction("Sign out of Odoo", self)
        sign_out.triggered.connect(self.sign_out_requested.emit)
        menu.addAction(sign_out)
        return menu

    def _register_icon(self, button: QToolButton, icon_name: str) -> None:
        self._icon_targets.append((button, icon_name))
        button.setIcon(icon(icon_name))

    # -- theme -------------------------------------------------------------

    def apply_theme(self, palette: Palette, theme: Theme) -> None:
        """Repaint the bar's icons and tick the active appearance option.

        Icons are stroked bitmaps, not font glyphs, so they do not inherit the
        stylesheet's colour - each one has to be re-rendered when the theme
        flips or they stay near-black on a dark bar.
        """
        set_icon_color(palette.text)
        for button, icon_name in self._icon_targets:
            button.setIcon(icon(icon_name))

        self._theme_button.setIcon(icon(theme.icon_name))
        for option, action in self._theme_actions.items():
            action.setChecked(option is theme)

        self._separator_style(palette)

    def _separator_style(self, palette: Palette) -> None:
        for line in self.findChildren(QFrame):
            line.setStyleSheet(f"background: {palette.border};")

    # -- printing ----------------------------------------------------------

    def set_default_print_mode(self, mode: PrintMode) -> None:
        """Set what the print button itself does, from the saved settings."""
        self._default_print_mode = mode
        self._print_button.setToolTip(
            f"Print the page you are looking at - {mode.label.lower()}.\n"
            "Odoo reports print by themselves - see Settings."
        )

    def set_print_enabled(self, enabled: bool) -> None:
        self._print_button.setEnabled(enabled)

    # -- state -------------------------------------------------------------

    def set_session(self, context: SessionContext | None) -> None:
        """Render ``context``; ``None`` blanks the bar while a session loads."""
        self._syncing = True
        try:
            if context is None:
                self._clear()
                return

            self._apply_logo(context.company_logo)
            self._company_label.setText(context.current_company_name)
            self._apply_languages(context)
            self._user_label.setText(context.user_name or context.login)
            self._user_label.setToolTip(
                f"{context.login} - {context.database} - Odoo {context.server_version}"
            )
        finally:
            self._syncing = False

    def set_navigation_state(self, *, can_go_back: bool, can_go_forward: bool) -> None:
        self._back.setEnabled(can_go_back)
        self._forward.setEnabled(can_go_forward)

    # -- rendering helpers -------------------------------------------------

    def _clear(self) -> None:
        self._apply_logo(None)
        self._company_label.setText("")
        self._language_combo.hide()
        self._language_label.setText("")
        self._language_label.show()
        self._user_label.setText("")
        self._user_label.setToolTip("")

    def _apply_logo(self, data: bytes | None) -> None:
        pixmap = QPixmap()
        if not data or not pixmap.loadFromData(data):
            self._logo.clear()
            self._logo.setFixedWidth(0)
            return
        scaled = pixmap.scaledToHeight(_LOGO_HEIGHT, Qt.TransformationMode.SmoothTransformation)
        self._logo.setPixmap(scaled)
        self._logo.setFixedWidth(scaled.width())

    def _apply_languages(self, context: SessionContext) -> None:
        if context.has_multiple_languages:
            self._language_label.hide()
            self._language_combo.clear()
            for language in context.languages:
                self._language_combo.addItem(language.name, language.code)
            index = self._language_combo.findData(context.language)
            self._language_combo.setCurrentIndex(max(index, 0))
            self._language_combo.show()
        else:
            self._language_combo.hide()
            self._language_label.setText(context.current_language_name)
            self._language_label.show()

    # -- signals -----------------------------------------------------------

    def _on_language_index_changed(self, index: int) -> None:
        if self._syncing or index < 0:
            return
        code = self._language_combo.itemData(index)
        if code:
            self.language_changed.emit(str(code))
