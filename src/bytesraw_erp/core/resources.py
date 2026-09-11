"""Bundled assets.

Files live inside the package rather than beside it, so an installed wheel and
a PyInstaller bundle find them the same way a source checkout does. Lookup goes
through :mod:`importlib.resources`, which works when the package is zipped.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from importlib import resources

from PySide6.QtGui import QIcon, QPixmap

_log = logging.getLogger(__name__)

_PACKAGE = "bytesraw_erp.resources"
_LOGO = "logo.png"


@lru_cache(maxsize=1)
def logo_pixmap() -> QPixmap:
    """The product logo, or a null pixmap when it cannot be read.

    Never raises: a missing logo should cost the app an icon, not a startup.
    """
    try:
        data = (resources.files(_PACKAGE) / _LOGO).read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        _log.warning("Could not read %s: %s", _LOGO, exc)
        return QPixmap()

    pixmap = QPixmap()
    if not pixmap.loadFromData(data, "PNG"):
        _log.warning("%s is not a readable PNG", _LOGO)
        return QPixmap()
    return pixmap


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    """Window, taskbar and alt-tab icon."""
    pixmap = logo_pixmap()
    return QIcon(pixmap) if not pixmap.isNull() else QIcon()
