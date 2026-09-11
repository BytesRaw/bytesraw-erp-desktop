"""Bundled assets.

Files live inside the package rather than beside it, so an installed wheel and
a PyInstaller bundle find them the same way a source checkout does. Lookup goes
through :mod:`importlib.resources`, which works when the package is zipped.

Why there is an ``.ico`` as well as the ``.png``
------------------------------------------------
``QIcon(QPixmap)`` holds exactly one bitmap. Windows asks for the icon at many
sizes - 16px in the window corner, 32px in alt-tab, 256px in the shell's
extra-large view - and scaling one 128px bitmap to 16px turns a stroked
monogram to mush. ``icon.ico`` carries nine pre-rendered frames, so each
surface gets one drawn at its own size; it is also the file a Windows
executable needs at build time (``pyinstaller --icon``), which a PNG cannot be.

It is checked in rather than built, and its layout is the one Windows itself
ships: frames up to 128px as uncompressed DIBs, the 256px frame as PNG. Keep
that shape if the mark is ever redrawn - it is what the shell, Qt and
PyInstaller all read without argument.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from importlib import resources

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QIcon, QImageReader, QPixmap

_log = logging.getLogger(__name__)

_PACKAGE = "bytesraw_erp.resources"
_LOGO = "logo.png"
_ICON = "icon.ico"

#: Sizes synthesised from the PNG when ``icon.ico`` cannot be read, so the
#: fallback icon is still multi-resolution rather than one stretched bitmap.
_FALLBACK_SIZES = (16, 24, 32, 48, 64, 128)


def _read(name: str) -> bytes | None:
    """Bytes of a bundled file, or ``None``. Never raises.

    A missing asset should cost the app an image, not a startup.
    """
    try:
        return (resources.files(_PACKAGE) / name).read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        _log.warning("Could not read %s: %s", name, exc)
        return None


@lru_cache(maxsize=1)
def logo_pixmap() -> QPixmap:
    """The product logo, or a null pixmap when it cannot be read."""
    data = _read(_LOGO)
    if data is None:
        return QPixmap()

    pixmap = QPixmap()
    if not pixmap.loadFromData(data, "PNG"):
        _log.warning("%s is not a readable PNG", _LOGO)
        return QPixmap()
    return pixmap


def logo_scaled(height: int) -> QPixmap:
    """The mark at ``height`` pixels, smoothly scaled. Null stays null."""
    pixmap = logo_pixmap()
    if pixmap.isNull():
        return pixmap
    return pixmap.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    """Window, taskbar and alt-tab icon, at every size Windows asks for.

    Read from the multi-frame ``icon.ico`` through :class:`QImageReader`, which
    walks all of its images - ``QPixmap.loadFromData`` would take only the
    first. Falls back to scaling the PNG, so a build without the ``.ico`` still
    gets sharp small sizes rather than one stretched bitmap.
    """
    icon = QIcon()
    for pixmap in _icon_frames():
        icon.addPixmap(pixmap)
    if icon.availableSizes():
        return icon

    source = logo_pixmap()
    if source.isNull():
        return QIcon()
    for size in _FALLBACK_SIZES:
        icon.addPixmap(source.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))
    return icon


def _icon_frames() -> list[QPixmap]:
    data = _read(_ICON)
    if data is None:
        return []

    # setData, not QBuffer(QByteArray(...)): that constructor keeps a *pointer*
    # to the array, so a temporary is collected out from under the reader and
    # the next read is an access violation - measured, and the same trap as
    # PrintService._open_pdf. setData copies into the buffer's own storage.
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer, b"ico")
    frames: list[QPixmap] = []
    for index in range(max(reader.imageCount(), 1)):
        if index and not reader.jumpToImage(index):
            break
        image = reader.read()
        if image.isNull():
            break
        frames.append(QPixmap.fromImage(image))
    if not frames:
        _log.warning("%s holds no readable frames", _ICON)
    return frames
