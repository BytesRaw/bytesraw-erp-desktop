"""Vector icons, drawn from inline SVG.

Icons are embedded rather than shipped as files so the app has no runtime asset
lookup and PyInstaller has nothing extra to bundle. Each entry is the inner
markup of a 24x24 stroked SVG; :func:`icon` wraps it, recolours it and renders
it through Qt's SVG renderer.

Icons are stroked in the current theme's text colour, so :func:`set_icon_color`
must be called whenever the theme changes - a near-black glyph on a dark app bar
is as invisible as near-black menu text on a dark menu.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from bytesraw_erp.ui.theme import LIGHT

_SIZE = 40  # rendered at ~2x display size so it stays crisp on HiDPI

_PATHS: dict[str, str] = {
    "arrow-left": '<path d="M19 12H5M12 19l-7-7 7-7"/>',
    "arrow-right": '<path d="M5 12h14M12 5l7 7-7 7"/>',
    "refresh": '<path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/>',
    "home": '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "trash": '<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M6 6v14h12V6"/>',
    "edit": (
        '<path d="M12 20h9"/>'
        '<path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/>'
    ),
    "server": (
        '<rect x="3" y="4" width="18" height="7" rx="2"/>'
        '<rect x="3" y="13" width="18" height="7" rx="2"/>'
        '<path d="M7 7.5h.01M7 16.5h.01"/>'
    ),
    # --- identity and account menu --------------------------------------
    "user": (
        '<circle cx="12" cy="8" r="3.6"/>'
        '<path d="M4.5 20a7.5 7.5 0 0 1 15 0"/>'
    ),
    "users": (
        '<circle cx="9" cy="8" r="3.2"/>'
        '<path d="M3 19.5a6 6 0 0 1 12 0"/>'
        '<path d="M16 5.3a3.2 3.2 0 0 1 0 5.4"/>'
        '<path d="M17.5 14.4a6 6 0 0 1 3.5 5.1"/>'
    ),
    "switch": (
        '<path d="M4 8h13l-3-3"/>'
        '<path d="M20 16H7l3 3"/>'
    ),
    "log-out": (
        '<path d="M10 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4"/>'
        '<path d="M16 8l4 4-4 4"/><path d="M20 12H10"/>'
    ),
    # Sliders rather than a cog: a cog's teeth are notches between strokes,
    # and at the 18px these are drawn at a 1.8 stroke fills them in - measured,
    # it renders as a blob. Sliders stay legible all the way down.
    "sliders": (
        '<path d="M4 8h7"/><path d="M15 8h5"/><circle cx="13" cy="8" r="2.1"/>'
        '<path d="M4 16h5"/><path d="M13 16h7"/><circle cx="11" cy="16" r="2.1"/>'
    ),
    # --- settings sections ----------------------------------------------
    "palette": (
        '<path d="M12 3a9 9 0 1 0 0 18c1.2 0 1.8-.8 1.8-1.7 0-1.4-1.1-1.7'
        '-1.1-2.6 0-.7.6-1.2 1.4-1.2H16a5 5 0 0 0 5-5c0-4.1-4-7.5-9-7.5Z"/>'
        '<circle cx="7.8" cy="11.5" r="1"/><circle cx="10.5" cy="7.6" r="1"/>'
        '<circle cx="15" cy="8.4" r="1"/>'
    ),
    "folder": (
        '<path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2.4h9A1.5 1.5 0 0 1 21 9.9'
        'v7.6A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5Z"/>'
    ),
    "info": (
        '<circle cx="12" cy="12" r="9"/>'
        '<path d="M12 11v5.5"/><path d="M12 7.6h.01"/>'
    ),
    "globe": (
        '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/>'
        '<path d="M12 3a14 14 0 0 1 0 18a14 14 0 0 1 0-18Z"/>'
    ),
    "key": (
        '<circle cx="8" cy="12" r="3.6"/>'
        '<path d="M11.6 12H21"/><path d="M17.5 12v3.2"/><path d="M20.2 12v2.4"/>'
    ),
    "shield": (
        '<path d="M12 3l7 2.6v5.6c0 4.2-2.9 7.6-7 9.2-4.1-1.6-7-5-7-9.2V5.6Z"/>'
    ),
    "eye": (
        '<path d="M2.5 12S6 5.8 12 5.8 21.5 12 21.5 12 18 18.2 12 18.2 2.5 12 2.5 12Z"/>'
        '<circle cx="12" cy="12" r="3"/>'
    ),
    "eye-off": (
        '<path d="M4 4l16 16"/>'
        '<path d="M9.9 5.2A9.6 9.6 0 0 1 12 5c6 0 9.5 6.2 9.5 6.2a17 17 0 0 1-3.3 3.9"/>'
        '<path d="M6.6 7.6A16.7 16.7 0 0 0 2.5 11.2S6 17.4 12 17.4a9.4 9.4 0 0 0 3.6-.7"/>'
        '<path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>'
    ),
    # --- theme ---------------------------------------------------------
    "sun": (
        '<circle cx="12" cy="12" r="4.2"/>'
        '<path d="M12 2v2.2M12 19.8V22M4.2 12H2M22 12h-2.2"/>'
        '<path d="M5.6 5.6 4 4M20 20l-1.6-1.6M18.4 5.6 20 4M4 20l1.6-1.6"/>'
    ),
    "moon": '<path d="M20 13.6A8.3 8.3 0 1 1 10.4 4a6.6 6.6 0 0 0 9.6 9.6Z"/>',
    # --- printing ------------------------------------------------------
    "printer": (
        '<path d="M7 9V3h10v6"/>'
        '<path d="M7 19H5a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-2"/>'
        '<rect x="7" y="15" width="10" height="6"/>'
    ),
    "printer-cog": (
        '<path d="M7 9V3h10v4"/>'
        '<path d="M7 18H5a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h11"/>'
        '<rect x="7" y="14" width="7" height="7"/>'
        '<circle cx="18.5" cy="14.5" r="2.4"/>'
        '<path d="M18.5 10.6v1.1M18.5 17.3v1.1M22 14.5h-1.1M16.1 14.5H15"/>'
    ),
}

_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
    'fill="none" stroke="{color}" color="{color}" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
)

#: Stroke colour used when a caller does not name one. Tracks the active theme.
_default_color = LIGHT.text


def set_icon_color(color: str) -> None:
    """Point subsequent :func:`icon` calls at a new stroke colour.

    Clears the render cache, so widgets must re-request their icons afterwards;
    a ``QIcon`` already handed to a button is a finished bitmap and will not
    repaint itself.
    """
    global _default_color
    if color == _default_color:
        return
    _default_color = color
    _render.cache_clear()


def icon(name: str, color: str | None = None) -> QIcon:
    """Return the named icon, or an empty :class:`QIcon` if it is unknown."""
    return _render(name, color or _default_color)


@lru_cache(maxsize=128)
def _render(name: str, color: str) -> QIcon:
    body = _PATHS.get(name)
    if body is None:
        return QIcon()
    svg = _TEMPLATE.format(color=color, body=body).encode("utf-8")
    renderer = QSvgRenderer(QByteArray(svg))
    if not renderer.isValid():
        return QIcon()

    # Rasterise at the target size rather than scaling a 24px bitmap up, so
    # strokes stay sharp on high-DPI displays.
    pixmap = QPixmap(_SIZE, _SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    return QIcon(pixmap)
