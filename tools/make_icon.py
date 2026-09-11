"""Generate ``resources/icon.ico`` from ``resources/logo.png``.

Kept as a script rather than done by hand so the icon can be regenerated when
the brand mark changes. Frames up to 128px are written as uncompressed DIBs and
the 256px frame as PNG - the layout Windows itself ships, and the one every
toolchain (the shell, QtWin, PyInstaller) reads without argument.
"""

from __future__ import annotations

import struct
import sys

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter

SRC = "src/bytesraw_erp/resources/logo.png"
OUT = "src/bytesraw_erp/resources/icon.ico"
DIB_SIZES = [16, 20, 24, 32, 40, 48, 64, 128]
PNG_SIZES = [256]


def render(source: QImage, size: int) -> QImage:
    """The mark centred in a square, with a little breathing room."""
    inset = max(1, round(size * 0.06))
    target = QImage(size, size, QImage.Format.Format_ARGB32)
    target.fill(Qt.GlobalColor.transparent)
    scaled = source.scaled(
        size - inset * 2,
        size - inset * 2,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    painter = QPainter(target)
    painter.drawImage((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    painter.end()
    return target


def as_png(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def as_dib(image: QImage) -> bytes:
    """A BITMAPINFOHEADER, bottom-up BGRA rows, then the 1-bit AND mask."""
    width, height = image.width(), image.height()
    header = struct.pack(
        "<IiiHHIIiiII", 40, width, height * 2, 1, 32, 0, width * height * 4, 0, 0, 0, 0
    )
    rows = []
    mask_rows = []
    mask_stride = ((width + 31) // 32) * 4
    for y in range(height - 1, -1, -1):
        row = bytearray()
        mask = bytearray(mask_stride)
        for x in range(width):
            r, g, b, a = image.pixelColor(x, y).getRgb()
            row += bytes((b, g, r, a))
            if a == 0:
                mask[x // 8] |= 0x80 >> (x % 8)
        rows.append(bytes(row))
        mask_rows.append(bytes(mask))
    return header + b"".join(rows) + b"".join(mask_rows)


def main() -> int:
    QGuiApplication(sys.argv)
    source = QImage(SRC)
    if source.isNull():
        print(f"cannot read {SRC}", file=sys.stderr)
        return 1

    frames: list[tuple[int, bytes]] = []
    for size in DIB_SIZES:
        frames.append((size, as_dib(render(source, size))))
    for size in PNG_SIZES:
        frames.append((size, as_png(render(source, size))))

    offset = 6 + 16 * len(frames)
    directory = bytearray(struct.pack("<HHH", 0, 1, len(frames)))
    for size, payload in frames:
        directory += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,
            0 if size >= 256 else size,
            0,
            0,
            1,
            32,
            len(payload),
            offset,
        )
        offset += len(payload)

    with open(OUT, "wb") as handle:
        handle.write(bytes(directory))
        for _size, payload in frames:
            handle.write(payload)
    print(f"wrote {OUT} ({offset} bytes, {len(frames)} frames)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
