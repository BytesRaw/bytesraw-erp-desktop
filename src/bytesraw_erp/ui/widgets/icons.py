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
