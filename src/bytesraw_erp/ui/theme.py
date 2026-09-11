"""Palettes, stylesheet and the theme controller.

Two complete palettes, one stylesheet template, and a controller that resolves
"system" against the OS setting and applies the result to the QApplication.

Why every control is styled explicitly
--------------------------------------
A stylesheet that sets ``QWidget { color: ... }`` but leaves a control's
*background* to the system palette produces invisible text the moment the OS
switches to dark mode. That is a real bug this file exists to prevent: menu
items were painted near-black on Qt's dark ``#1e1e1e`` menu background, a
contrast delta of zero. Any control that draws its own surface - menus,
tooltips, dialogs, scrollbars, headers - must have **both** its background and
its foreground defined here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication

_log = logging.getLogger(__name__)

APP_BAR_HEIGHT = 52
_SETTINGS_KEY = "appearance/theme"


class Theme(StrEnum):
    """The user's choice: an explicit Light or Dark, with Light as default.

    There is deliberately no "match system" option. It was implemented and
    removed: resolving it needs ``QStyleHints.setColorScheme(Unknown)``, which
    makes Qt resolve the OS value and fire ``colorSchemeChanged``, which
    re-entered ``apply()`` - so every switch to System emitted
    ``theme_changed`` **twice** (measured: 2 emissions against 1 for
    Light/Dark). Each emission drove an Odoo colour-scheme write and a web view
    reload, so two reloads raced and the embedded client never finished
    loading. Do not reintroduce it without breaking that re-entrancy.
    """

    LIGHT = "light"
    DARK = "dark"

    @property
    def label(self) -> str:
        return {"light": "Light", "dark": "Dark"}[self.value]

    @property
    def icon_name(self) -> str:
        return {"light": "sun", "dark": "moon"}[self.value]

    @property
    def odoo_color_scheme(self) -> str:
        """This theme in Odoo's vocabulary for ``res.users.settings``.

        Odoo's selection also offers "system"; the app never writes it, because
        it has no such state of its own to write.
        """
        return {"light": "light", "dark": "dark"}[self.value]


@dataclass(frozen=True, slots=True)
class Palette:
    """Every colour the app draws with. No widget may hardcode one."""

    is_dark: bool

    primary: str
    primary_hover: str
    primary_text: str

    surface: str
    surface_alt: str
    surface_raised: str
    border: str

    text: str
    text_muted: str
    text_disabled: str

    menu_bg: str
    menu_hover: str
    selection: str

    danger: str
    danger_bg: str
    danger_border: str
    success: str
    success_bg: str
    success_border: str

    scrollbar: str


LIGHT = Palette(
    is_dark=False,
    primary="#714B67",
    primary_hover="#8a5c7e",
    primary_text="#ffffff",
    surface="#ffffff",
    surface_alt="#f6f6f7",
    surface_raised="#ffffff",
    border="#dcdce0",
    text="#1f1f23",
    text_muted="#6b6b74",
    text_disabled="#a5a5ad",
    menu_bg="#ffffff",
    menu_hover="#f0ecef",
    selection="#714B67",
    danger="#c0392b",
    danger_bg="#fdecea",
    danger_border="#f5c6c2",
    success="#17703d",
    success_bg="#eaf6ef",
    success_border="#c3e4d1",
    scrollbar="#c8c8cf",
)

DARK = Palette(
    is_dark=True,
    primary="#b58aa6",
    primary_hover="#c9a3bb",
    primary_text="#1a1a1f",
    surface="#1f2029",
    surface_alt="#17181f",
    surface_raised="#272934",
    border="#3a3c4a",
    text="#e8e8ee",
    text_muted="#9a9aa8",
    text_disabled="#61616e",
    menu_bg="#272934",
    menu_hover="#353846",
    selection="#b58aa6",
    danger="#ff8a80",
    danger_bg="#3a2220",
    danger_border="#5c322e",
    success="#7ddba3",
    success_bg="#1d2f26",
    success_border="#2f4a3a",
    scrollbar="#4a4c5c",
)


def build_qss(p: Palette) -> str:
    """Render the stylesheet for ``p``."""
    return f"""
QWidget {{
    color: {p.text};
    font-family: "Segoe UI", "Inter", system-ui, sans-serif;
    font-size: 13px;
}}
QWidget:disabled {{ color: {p.text_disabled}; }}

QMainWindow, QStackedWidget, QDialog {{ background: {p.surface_alt}; }}

/* --- Menus, tooltips, dialogs -----------------------------------------
   These paint their own surface. Defining the text colour without the
   background here is what made menu items invisible in dark mode. */
QMenu {{
    background: {p.menu_bg};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 6px;
}}
QMenu::item {{
    background: transparent;
    color: {p.text};
    padding: 7px 28px 7px 14px;
    border-radius: 5px;
}}
QMenu::item:selected {{ background: {p.menu_hover}; color: {p.text}; }}
QMenu::item:disabled {{ color: {p.text_disabled}; }}
QMenu::separator {{
    height: 1px;
    background: {p.border};
    margin: 5px 8px;
}}
QMenu::icon {{ padding-left: 8px; }}

QToolTip {{
    background: {p.surface_raised};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: 5px;
    padding: 5px 7px;
}}

QMessageBox {{ background: {p.surface}; }}
QMessageBox QLabel {{ color: {p.text}; }}

/* --- App bar ----------------------------------------------------------- */
#AppBar {{
    background: {p.surface};
    border-bottom: 1px solid {p.border};
}}
#AppBar QToolButton {{
    border: none;
    border-radius: 6px;
    padding: 6px;
    color: {p.text};
}}
#AppBar QToolButton:hover {{ background: {p.surface_alt}; }}
#AppBar QToolButton:disabled {{ color: {p.text_disabled}; }}
#AppBar QToolButton::menu-indicator {{ width: 0; image: none; }}

#CompanyName {{ font-weight: 600; font-size: 14px; color: {p.text}; }}
#UserName {{ font-weight: 600; color: {p.text}; }}
#MutedLabel, #SubtleLabel {{ color: {p.text_muted}; }}

/* --- Inputs ------------------------------------------------------------ */
QComboBox {{
    background: {p.surface};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: 6px;
    padding: 5px 10px;
    min-height: 20px;
}}
QComboBox:disabled {{ background: {p.surface_alt}; color: {p.text_disabled}; }}
QComboBox QAbstractItemView {{
    background: {p.menu_bg};
    color: {p.text};
    border: 1px solid {p.border};
    selection-background-color: {p.menu_hover};
    selection-color: {p.text};
    outline: none;
}}
QComboBox:focus, QLineEdit:focus {{ border-color: {p.primary}; }}

QLineEdit {{
    background: {p.surface};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: 6px;
    padding: 8px 10px;
    selection-background-color: {p.selection};
    selection-color: {p.primary_text};
}}
QLineEdit:disabled {{ background: {p.surface_alt}; color: {p.text_disabled}; }}

QCheckBox {{ color: {p.text}; spacing: 8px; }}

QPushButton {{
    background: {p.surface};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: 6px;
    padding: 8px 16px;
}}
QPushButton:hover {{ background: {p.surface_alt}; }}
QPushButton:disabled {{ color: {p.text_disabled}; }}
QPushButton[variant="primary"] {{
    background: {p.primary};
    border-color: {p.primary};
    color: {p.primary_text};
    font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{ background: {p.primary_hover}; }}
QPushButton[variant="primary"]:disabled {{
    background: {p.border};
    border-color: {p.border};
    color: {p.text_disabled};
}}
QPushButton[variant="danger"] {{ color: {p.danger}; }}
QPushButton[variant="link"] {{
    background: transparent;
    border: none;
    color: {p.primary};
    padding: 4px 6px;
}}

/* --- Cards ------------------------------------------------------------- */
#Card, #AccountCard {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 10px;
}}
#AccountCard:hover {{ border-color: {p.primary}; }}
#AccountName {{ font-weight: 600; font-size: 14px; color: {p.text}; }}
#PageTitle {{ font-size: 22px; font-weight: 600; color: {p.text}; }}
#SectionTitle {{ font-size: 15px; font-weight: 600; color: {p.text}; }}
#Rule {{ background: {p.border}; border: none; }}

#ErrorBanner {{
    background: {p.danger_bg};
    border: 1px solid {p.danger_border};
    border-radius: 6px;
    color: {p.danger};
    padding: 10px 12px;
}}
#InfoBanner {{
    background: {p.success_bg};
    border: 1px solid {p.success_border};
    border-radius: 6px;
    color: {p.success};
    padding: 10px 12px;
}}

#Toast {{
    background: {p.surface_raised};
    border: 1px solid {p.border};
    border-radius: 8px;
}}
#ToastText {{ color: {p.text}; }}
#ToastAction {{
    background: transparent;
    border: none;
    color: {p.primary};
    font-weight: 600;
    padding: 4px 8px;
}}
#ToastAction:hover {{ text-decoration: underline; }}

QProgressBar {{ background: {p.surface_alt}; border: none; }}
QProgressBar::chunk {{ background: {p.primary}; }}

/* --- Scrollbars -------------------------------------------------------- */
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical, QScrollBar:horizontal {{
    background: transparent;
    width: 10px;
    height: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {p.scrollbar};
    border-radius: 5px;
    min-height: 28px;
    min-width: 28px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""


class ThemeController(QObject):
    """Owns the active theme and applies it to the whole application.

    Pushing the choice back onto ``QStyleHints`` is what makes Qt's own native
    drawing - window frame, native dialogs - agree with the stylesheet. The
    controller never follows the OS, so nothing outside it can trigger a
    re-apply and the ``theme_changed`` signal fires exactly once per switch.
    """

    #: Emitted with the resolved palette whenever the effective theme changes.
    theme_changed = Signal(object)

    def __init__(self, app: QGuiApplication, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._app = app
        self._theme = self._load()
        self._palette = LIGHT

    # -- state -------------------------------------------------------------

    @property
    def theme(self) -> Theme:
        return self._theme

    @property
    def palette(self) -> Palette:
        return self._palette

    @property
    def is_dark(self) -> bool:
        return self._palette.is_dark

    def _load(self) -> Theme:
        """Read the stored theme, defaulting to Light.

        A settings file written by an older build can still say "system"; that
        is no longer a theme, so it reads back as Light rather than failing.
        """
        raw = QSettings().value(_SETTINGS_KEY, Theme.LIGHT.value)
        try:
            return Theme(str(raw))
        except ValueError:
            return Theme.LIGHT

    # -- application -------------------------------------------------------

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        QSettings().setValue(_SETTINGS_KEY, theme.value)
        self.apply()

    def apply(self) -> None:
        """Resolve the theme and push it into the application."""
        palette = DARK if self._resolve_dark() else LIGHT
        self._palette = palette

        hints = self._app.styleHints()
        if hasattr(hints, "setColorScheme"):
            # Keep Qt's native painting in step with the stylesheet. Always an
            # explicit scheme, never Unknown - Unknown makes Qt resolve the OS
            # value and emit colorSchemeChanged, which is what made the old
            # System option re-enter this method.
            hints.setColorScheme(
                Qt.ColorScheme.Dark if palette.is_dark else Qt.ColorScheme.Light
            )

        self._app.setStyleSheet(build_qss(palette))
        _log.info("Applied theme %s (dark=%s)", self._theme.value, palette.is_dark)
        self.theme_changed.emit(palette)

    def _resolve_dark(self) -> bool:
        return self._theme is Theme.DARK


def accent_color(palette: Palette) -> QColor:
    return QColor(palette.primary)
