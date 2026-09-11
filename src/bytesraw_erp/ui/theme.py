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

Two accents, on purpose
-----------------------
``primary`` is Odoo's plum. It is the colour of *action* - primary buttons,
focus rings, selection - so the shell agrees with the web client it frames.
``accent`` is the Bytesraw teal taken from the product mark, and it marks what
belongs to the *shell* rather than to Odoo: the brand block, the version badge.
Letting the two mix would make the app bar argue with the page below it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPalette

_log = logging.getLogger(__name__)

APP_BAR_HEIGHT = 56
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
    #: Primary at low opacity, pre-flattened to an opaque colour. Backs
    #: avatars, checked menu rows and anything that should read as "primary"
    #: without shouting.
    primary_soft: str
    #: What Qt fills a *natively drawn* control with - the check box tick
    #: plate, radio buttons. It is a shade darker than ``primary`` in the dark
    #: palette because the native tick is painted white either way, and white
    #: on the dark palette's pale plum measures 2.2:1. Left unset, Qt uses the
    #: Windows system accent, which arrives as whatever colour the user picked
    #: - salmon on this machine, next to Odoo's plum.
    control_accent: str

    #: The teal of the Bytesraw mark. Shell identity only, never an Odoo action.
    accent: str
    accent_soft: str

    surface: str
    surface_alt: str
    surface_raised: str
    #: The app bar's own ground, a shade apart from the page, so the native
    #: chrome reads as chrome rather than as the top of the web page below it.
    app_bar: str
    app_bar_edge: str
    #: Segmented-control and pill backgrounds.
    chip: str
    hover: str
    border: str
    border_strong: str

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
    primary_soft="#f3edf1",
    control_accent="#714B67",
    accent="#2b7079",
    accent_soft="#e6f2f3",
    surface="#ffffff",
    surface_alt="#f4f5f7",
    surface_raised="#ffffff",
    app_bar="#ffffff",
    app_bar_edge="#f2f2f5",
    chip="#eeeef1",
    hover="#f0f0f3",
    border="#dcdce0",
    border_strong="#c6c6cd",
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
    primary_soft="#332a31",
    control_accent="#8a5c7e",
    accent="#6fc6cf",
    accent_soft="#1e2b30",
    surface="#1f2029",
    surface_alt="#17181f",
    surface_raised="#272934",
    app_bar="#232531",
    app_bar_edge="#1d1f28",
    chip="#252734",
    hover="#2c2f3c",
    border="#3a3c4a",
    border_strong="#4b4e5f",
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
    font-family: "Segoe UI Variable Text", "Segoe UI", "Inter", system-ui, sans-serif;
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
    border-radius: 10px;
    padding: 6px;
}}
QMenu::item {{
    background: transparent;
    color: {p.text};
    padding: 8px 30px 8px 14px;
    border-radius: 7px;
}}
QMenu::item:selected {{ background: {p.menu_hover}; color: {p.text}; }}
QMenu::item:checked {{ background: {p.primary_soft}; color: {p.text}; }}
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
    border-radius: 6px;
    padding: 6px 8px;
}}

QMessageBox {{ background: {p.surface}; }}
QMessageBox QLabel {{ color: {p.text}; }}

/* --- App bar -----------------------------------------------------------
   A ground of its own plus a hard bottom edge, so the native chrome never
   dissolves into the white Odoo navbar sitting directly beneath it. */
#AppBar {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 {p.app_bar}, stop:1 {p.app_bar_edge});
    border-bottom: 1px solid {p.border};
}}
#AppBar QToolButton {{
    border: none;
    border-radius: 8px;
    padding: 7px;
    color: {p.text};
}}
#AppBar QToolButton:hover {{ background: {p.hover}; }}
#AppBar QToolButton:pressed {{ background: {p.chip}; }}
#AppBar QToolButton:disabled {{ color: {p.text_disabled}; background: transparent; }}
#AppBar QToolButton::menu-indicator {{ width: 0; image: none; }}
/* A split button's arrow half is painted by the native style, which uses the
   system highlight - a Windows blue that belongs to no palette here. */
#AppBar QToolButton::menu-button {{
    background: transparent;
    border: none;
    border-top-right-radius: 8px;
    border-bottom-right-radius: 8px;
    width: 14px;
}}
#AppBar QToolButton::menu-button:hover {{ background: {p.chip}; }}
/* The language combo keeps the ordinary input look. Giving it a chip
   background instead leaves the natively drawn drop-down sitting on its own
   pale rectangle inside the pill - and the drop-down cannot be restyled, see
   the QComboBox section below. */

/* The navigation cluster is one segmented pill, not four loose buttons. */
#NavGroup {{
    background: {p.chip};
    border: 1px solid transparent;
    border-radius: 10px;
}}
#NavGroup QToolButton:hover {{ background: {p.hover}; }}
#NavGroup QToolButton:pressed {{ background: {p.border}; }}

/* Brand block: the shell's own identity, in the shell's own accent. */
#BrandName {{ font-size: 14px; font-weight: 700; color: {p.text}; }}
#VersionBadge {{
    background: {p.accent_soft};
    color: {p.accent};
    border-radius: 7px;
    padding: 2px 7px;
    font-size: 11px;
    font-weight: 600;
}}

/* Company chip: display only - Odoo's own navbar is where it is switched. */
#CompanyChip {{
    background: {p.chip};
    border: 1px solid transparent;
    border-radius: 10px;
}}
#CompanyName {{ font-weight: 600; font-size: 13px; color: {p.text}; }}

#UserChip {{ background: transparent; border-radius: 10px; }}
#UserChip:hover {{ background: {p.hover}; }}
#Avatar {{
    background: {p.primary};
    color: {p.primary_text};
    border-radius: 15px;
    font-size: 12px;
    font-weight: 700;
}}
#UserName {{ font-weight: 600; color: {p.text}; }}
#UserMeta {{ color: {p.text_muted}; font-size: 11px; }}
#MutedLabel, #SubtleLabel {{ color: {p.text_muted}; }}
#FieldLabel {{ color: {p.text_muted}; font-size: 12px; font-weight: 600; }}
#FieldHint {{ color: {p.text_muted}; font-size: 12px; }}

/* --- Inputs ------------------------------------------------------------ */
QComboBox {{
    background: {p.surface};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 7px 12px;
    min-height: 20px;
}}
QComboBox:hover {{ border-color: {p.border_strong}; }}
QComboBox:disabled {{ background: {p.surface_alt}; color: {p.text_disabled}; }}
/* No ``QComboBox::drop-down`` rule on purpose. Styling that subcontrol makes
   Qt stop drawing the native arrow and draw the stylesheet's instead - and the
   stylesheet has no arrow to give it, because QSS ``image:`` takes a file or
   resource path and cannot take an inline SVG or a data: URI. Measured: with
   the rule the box renders with a bare right edge and no affordance at all. */
QComboBox QAbstractItemView {{
    background: {p.menu_bg};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 4px;
    selection-background-color: {p.menu_hover};
    selection-color: {p.text};
    outline: none;
}}
QComboBox:focus, QLineEdit:focus {{ border-color: {p.primary}; }}

QLineEdit {{
    background: {p.surface};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 9px 12px;
    selection-background-color: {p.selection};
    selection-color: {p.primary_text};
}}
QLineEdit:hover {{ border-color: {p.border_strong}; }}
QLineEdit:disabled {{ background: {p.surface_alt}; color: {p.text_disabled}; }}

QCheckBox {{ color: {p.text}; spacing: 8px; }}

QPushButton {{
    background: {p.surface};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 9px 18px;
    font-weight: 500;
}}
QPushButton:hover {{ background: {p.hover}; border-color: {p.border_strong}; }}
QPushButton:pressed {{ background: {p.chip}; }}
QPushButton:disabled {{ color: {p.text_disabled}; background: {p.surface_alt}; }}
QPushButton[variant="primary"] {{
    background: {p.primary};
    border-color: {p.primary};
    color: {p.primary_text};
    font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{
    background: {p.primary_hover};
    border-color: {p.primary_hover};
}}
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
QPushButton[variant="link"]:hover {{
    background: transparent;
    border: none;
    text-decoration: underline;
}}

/* --- Cards ------------------------------------------------------------- */
#Card, #AccountCard {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 14px;
}}
#AccountCard:hover {{ border-color: {p.primary}; background: {p.surface_raised}; }}
#AccountName {{ font-weight: 600; font-size: 14px; color: {p.text}; }}
#PageTitle {{ font-size: 24px; font-weight: 700; color: {p.text}; }}
#PageSubtitle {{ font-size: 13px; color: {p.text_muted}; }}
#SectionTitle {{ font-size: 15px; font-weight: 600; color: {p.text}; }}
#Rule {{ background: {p.border}; border: none; }}

/* Tinted plates behind a section's badge and behind the product mark. */
#SectionIcon {{ background: {p.primary_soft}; border-radius: 9px; }}
#BrandTile {{ background: {p.accent_soft}; border-radius: 14px; }}

#ErrorBanner {{
    background: {p.danger_bg};
    border: 1px solid {p.danger_border};
    border-radius: 8px;
    color: {p.danger};
    padding: 11px 13px;
}}
#InfoBanner {{
    background: {p.success_bg};
    border: 1px solid {p.success_border};
    border-radius: 8px;
    color: {p.success};
    padding: 11px 13px;
}}

#Toast {{
    background: {p.surface_raised};
    border: 1px solid {p.border};
    border-radius: 10px;
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

        self._apply_control_accent(palette)
        self._app.setStyleSheet(build_qss(palette))
        _log.info("Applied theme %s (dark=%s)", self._theme.value, palette.is_dark)
        self.theme_changed.emit(palette)

    def _apply_control_accent(self, palette: Palette) -> None:
        """Point Qt's own painting at our accent instead of Windows'.

        A check box tick plate is drawn by the native style, not by the
        stylesheet, and there is no way to restyle it from QSS without an image
        - setting ``QCheckBox::indicator`` replaces the tick with nothing.
        The style takes that fill from ``QPalette.Accent``, which defaults to
        the user's Windows accent colour: measured salmon ``#f38064`` on this
        machine, sitting next to Odoo's plum. Must run **after**
        ``setColorScheme``, which rebuilds the palette this reads from.
        """
        app_palette = self._app.palette()
        app_palette.setColor(QPalette.ColorRole.Accent, QColor(palette.control_accent))
        self._app.setPalette(app_palette)

    def _resolve_dark(self) -> bool:
        return self._theme is Theme.DARK


def accent_color(palette: Palette) -> QColor:
    return QColor(palette.primary)
