"""The native app bar that sits above the embedded Odoo client.

Left to right: the shell's own identity (mark, name, version), a segmented
navigation cluster, the active company, then the tools and the signed-in user.

Why the shell brands itself at all
----------------------------------
The bar frames a full-screen web client that has its own navbar two pixels
below it. Without a brand block and a ground of its own the two read as one
confused strip of chrome, and the user cannot tell which row belongs to the
app and which to Odoo. So the shell claims the left edge in its own accent -
the Bytesraw teal - while every *action* colour stays Odoo's plum.

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
from PySide6.QtGui import QAction, QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from bytesraw_erp.constants import APP_NAME, APP_VERSION
from bytesraw_erp.core.resources import logo_scaled
from bytesraw_erp.data.models import PrintMode, SessionContext
from bytesraw_erp.ui.theme import APP_BAR_HEIGHT, Palette, Theme
from bytesraw_erp.ui.widgets.icons import icon, set_icon_color

_LOGO_HEIGHT = 24
_BRAND_HEIGHT = 22
_AVATAR = 30


def _initials(name: str) -> str:
    """Up to two initials for the avatar, or a fallback glyph."""
    parts = [part for part in name.replace(".", " ").split() if part]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


class _ChipFrame(QFrame):
    """A rounded slab that forwards a click to a menu button it holds."""

    def __init__(self, object_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self._trigger: QToolButton | None = None

    def set_trigger(self, button: QToolButton) -> None:
        self._trigger = button
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._trigger is not None and event.button() == Qt.MouseButton.LeftButton:
            self._trigger.showMenu()
        super().mouseReleaseEvent(event)


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
        #: (target, icon name) pairs. A QIcon is a finished bitmap, so a theme
        #: change means re-rendering and re-assigning every one of them - menu
        #: actions included, since a menu is as dark as the bar is.
        self._icon_targets: list[tuple[QToolButton | QAction, str]] = []
        #: The vertical hairlines, kept apart from every other QFrame in the
        #: bar: the chips are QFrames too, and painting them with the
        #: separator colour would wipe out their own background.
        self._separators: list[QFrame] = []
        #: Guards combo signals while the bar repopulates them programmatically.
        self._syncing = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 7, 12, 7)
        layout.setSpacing(10)

        self._build_brand_section(layout)
        layout.addWidget(self._separator())
        self._build_navigation_section(layout)
        self._build_company_section(layout)
        layout.addStretch(1)
        self._build_tools_section(layout)
        layout.addWidget(self._separator())
        self._build_identity_section(layout)

        self.set_session(None)

    # -- construction ------------------------------------------------------

    def _separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Plain)
        line.setFixedWidth(1)
        self._separators.append(line)
        return line

    def _build_brand_section(self, layout: QHBoxLayout) -> None:
        """The shell's own mark, name and version.

        The version lives here rather than buried in an About box because this
        is a desktop shell that will be rolled out to tills and back offices:
        "which build is this machine on" has to be answerable at a glance,
        without navigating anywhere.
        """
        mark = QLabel()
        mark.setFixedHeight(_BRAND_HEIGHT)
        logo = logo_scaled(_BRAND_HEIGHT)
        if not logo.isNull():
            mark.setPixmap(logo)
            mark.setFixedWidth(logo.width())
        layout.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)

        name = QLabel(APP_NAME)
        name.setObjectName("BrandName")
        layout.addWidget(name, 0, Qt.AlignmentFlag.AlignVCenter)

        self._version = QLabel(f"v{APP_VERSION}")
        self._version.setObjectName("VersionBadge")
        layout.addWidget(self._version, 0, Qt.AlignmentFlag.AlignVCenter)

        for widget in (mark, name, self._version):
            widget.setToolTip(f"{APP_NAME} {APP_VERSION}")

    def _build_navigation_section(self, layout: QHBoxLayout) -> None:
        """Back / forward / reload / home, as one segmented pill."""
        group = QFrame()
        group.setObjectName("NavGroup")
        row = QHBoxLayout(group)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(1)

        self._back = self._tool_button("arrow-left", "Back", self.back_requested)
        self._forward = self._tool_button("arrow-right", "Forward", self.forward_requested)
        self._reload = self._tool_button("refresh", "Reload", self.reload_requested)
        self._home = self._tool_button("home", "Odoo home", self.home_requested)
        for button in (self._back, self._forward, self._reload, self._home):
            row.addWidget(button)
        layout.addWidget(group)

    def _build_company_section(self, layout: QHBoxLayout) -> None:
        self._company_chip = _ChipFrame("CompanyChip")
        row = QHBoxLayout(self._company_chip)
        row.setContentsMargins(10, 5, 12, 5)
        row.setSpacing(8)

        self._logo = QLabel()
        self._logo.setFixedHeight(_LOGO_HEIGHT)
        self._logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._logo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        row.addWidget(self._logo)

        self._company_label = QLabel()
        self._company_label.setObjectName("CompanyName")
        row.addWidget(self._company_label)

        self._company_chip.setToolTip("Active company - change it in Odoo's own menu")
        layout.addWidget(self._company_chip)

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
        self._register_icon(self._print_direct_action, "printer")
        self._print_dialog_action = QAction("Print this page with options...", self)
        self._print_dialog_action.setShortcut("Ctrl+Shift+P")
        self._print_dialog_action.triggered.connect(
            lambda: self.print_requested.emit(PrintMode.DIALOG)
        )
        self._register_icon(self._print_dialog_action, "printer-cog")
        print_menu.addAction(self._print_direct_action)
        print_menu.addAction(self._print_dialog_action)
        print_menu.addSeparator()
        printer_settings = QAction("Printer settings...", self)
        printer_settings.triggered.connect(self.settings_requested.emit)
        self._register_icon(printer_settings, "sliders")
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
            self._register_icon(action, option.icon_name)
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
        self._language_combo.setMinimumWidth(130)
        self._language_combo.setToolTip("Interface language")
        self._language_combo.currentIndexChanged.connect(self._on_language_index_changed)
        layout.addWidget(self._language_combo)

        layout.addWidget(self._separator())

        chip = _ChipFrame("UserChip")
        row = QHBoxLayout(chip)
        row.setContentsMargins(6, 3, 6, 3)
        row.setSpacing(9)

        self._avatar = QLabel()
        self._avatar.setObjectName("Avatar")
        self._avatar.setFixedSize(_AVATAR, _AVATAR)
        self._avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self._avatar)

        names = QVBoxLayout()
        names.setContentsMargins(0, 0, 0, 0)
        names.setSpacing(0)
        self._user_label = QLabel()
        self._user_label.setObjectName("UserName")
        self._user_meta = QLabel()
        self._user_meta.setObjectName("UserMeta")
        names.addWidget(self._user_label)
        names.addWidget(self._user_meta)
        row.addLayout(names)

        self._menu_button = QToolButton()
        self._menu_button.setIconSize(QSize(16, 16))
        self._register_icon(self._menu_button, "chevron-down")
        self._menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu_button.setMenu(self._build_menu())
        row.addWidget(self._menu_button)

        chip.set_trigger(self._menu_button)
        layout.addWidget(chip)

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
            ("Switch account", "switch", self.switch_account_requested),
            ("Manage accounts", "users", self.manage_accounts_requested),
            ("Settings", "sliders", self.settings_requested),
        )
        for title, icon_name, signal in entries:
            action = QAction(title, self)
            action.triggered.connect(signal.emit)
            self._register_icon(action, icon_name)
            menu.addAction(action)
        menu.addSeparator()
        sign_out = QAction("Sign out of Odoo", self)
        sign_out.triggered.connect(self.sign_out_requested.emit)
        self._register_icon(sign_out, "log-out")
        menu.addAction(sign_out)
        return menu

    def _register_icon(self, target: QToolButton | QAction, icon_name: str) -> None:
        self._icon_targets.append((target, icon_name))
        target.setIcon(icon(icon_name))

    # -- theme -------------------------------------------------------------

    def apply_theme(self, palette: Palette, theme: Theme) -> None:
        """Repaint the bar's icons and tick the active appearance option.

        Icons are stroked bitmaps, not font glyphs, so they do not inherit the
        stylesheet's colour - each one has to be re-rendered when the theme
        flips or they stay near-black on a dark bar.
        """
        set_icon_color(palette.text)
        for target, icon_name in self._icon_targets:
            target.setIcon(icon(icon_name))

        self._theme_button.setIcon(icon(theme.icon_name))
        for option, action in self._theme_actions.items():
            action.setChecked(option is theme)

        self._separator_style(palette)

    def _separator_style(self, palette: Palette) -> None:
        for line in self._separators:
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
            self._company_chip.setVisible(bool(context.current_company_name))
            self._apply_languages(context)

            name = context.user_name or context.login
            self._user_label.setText(name)
            self._user_meta.setText(context.database)
            self._avatar.setText(_initials(name))
            tooltip = f"{context.login} - {context.database} - Odoo {context.server_version}"
            for widget in (self._user_label, self._user_meta, self._avatar):
                widget.setToolTip(tooltip)
        finally:
            self._syncing = False

    def set_navigation_state(self, *, can_go_back: bool, can_go_forward: bool) -> None:
        self._back.setEnabled(can_go_back)
        self._forward.setEnabled(can_go_forward)

    # -- rendering helpers -------------------------------------------------

    def _clear(self) -> None:
        self._apply_logo(None)
        self._company_label.setText("")
        self._company_chip.hide()
        self._language_combo.hide()
        self._language_label.setText("")
        self._language_label.show()
        self._user_label.setText("")
        self._user_meta.setText("")
        self._avatar.setText("")
        for widget in (self._user_label, self._user_meta, self._avatar):
            widget.setToolTip("")

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
