"""Building blocks the full-page forms share.

The account form and the settings page are the two screens a user meets
outside the embedded web client, so they have to look like one product: the
same card, the same section header, the same field. Keeping the pieces here
rather than in each page is what stops them drifting apart.

Icons are the reason these are classes and not functions. A ``QIcon`` is a
finished bitmap, so a widget that draws one has to be told when the theme
changes - every piece here that holds an icon exposes ``apply_theme`` for its
page to call, exactly as the app bar does for its own buttons.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from bytesraw_erp.constants import APP_NAME, APP_VERSION
from bytesraw_erp.core.resources import logo_scaled
from bytesraw_erp.ui.theme import Palette
from bytesraw_erp.ui.widgets.icons import icon

_TILE = 52
_BADGE = 34


def hint(text: str) -> QLabel:
    """Secondary prose: an explanation, never an instruction to act on."""
    label = QLabel(text)
    label.setObjectName("FieldHint")
    label.setWordWrap(True)
    return label


def rule() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    line.setObjectName("Rule")
    return line


class Card(QFrame):
    """A rounded panel. Add to ``body``; never to the card itself."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(22, 20, 22, 20)
        self.body.setSpacing(16)


class BrandHeader(QWidget):
    """The product mark, the screen's title, and the build.

    The version rides with the mark rather than hiding in an About dialog: on a
    machine that someone else installed, "which build am I on" is the first
    question support asks, and this is the screen a user is already on when
    something has gone wrong enough to bring them here.
    """

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(16)

        tile = QFrame()
        tile.setObjectName("BrandTile")
        tile.setFixedSize(_TILE, _TILE)
        tile_layout = QVBoxLayout(tile)
        tile_layout.setContentsMargins(0, 0, 0, 0)
        mark = QLabel()
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo = logo_scaled(30)
        if not logo.isNull():
            mark.setPixmap(logo)
        tile_layout.addWidget(mark)
        row.addWidget(tile, 0, Qt.AlignmentFlag.AlignTop)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        self._title = QLabel(title)
        self._title.setObjectName("PageTitle")
        title_row.addWidget(self._title)
        version = QLabel(f"v{APP_VERSION}")
        version.setObjectName("VersionBadge")
        version.setToolTip(f"{APP_NAME} {APP_VERSION}")
        title_row.addWidget(version, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addStretch(1)
        column.addLayout(title_row)

        self._subtitle = QLabel(subtitle)
        self._subtitle.setObjectName("PageSubtitle")
        self._subtitle.setWordWrap(True)
        self._subtitle.setVisible(bool(subtitle))
        column.addWidget(self._subtitle)
        row.addLayout(column, 1)

    def set_title(self, title: str) -> None:
        self._title.setText(title)

    def set_subtitle(self, subtitle: str) -> None:
        self._subtitle.setText(subtitle)
        self._subtitle.setVisible(bool(subtitle))


class SectionHeader(QWidget):
    """A badged title with one line of explanation, opening a card."""

    def __init__(
        self,
        icon_name: str,
        title: str,
        description: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._icon_name = icon_name

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        self._badge = QLabel()
        self._badge.setObjectName("SectionIcon")
        self._badge.setFixedSize(_BADGE, _BADGE)
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self._badge, 0, Qt.AlignmentFlag.AlignTop)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("SectionTitle")
        column.addWidget(title_label)
        if description:
            column.addWidget(hint(description))
        row.addLayout(column, 1)

    def apply_theme(self, palette: Palette) -> None:
        """Re-stroke the badge in the theme's primary, on its tinted plate."""
        self._badge.setPixmap(
            icon(self._icon_name, palette.primary).pixmap(QSize(18, 18))
        )


class Field(QWidget):
    """A labelled control, label above, optional hint below.

    Labels sit above their control rather than beside it so the column stays
    one width: a right-aligned label column makes the longest label decide how
    much room the inputs get, which is how a form ends up with a 90px URL box.
    """

    def __init__(
        self,
        label: str,
        control: QWidget,
        description: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)

        if label:
            caption = QLabel(label)
            caption.setObjectName("FieldLabel")
            caption.setBuddy(control)
            column.addWidget(caption)
        column.addWidget(control)
        if description:
            column.addWidget(hint(description))
