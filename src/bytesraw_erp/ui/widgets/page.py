"""The one layout every full-page screen in the shell uses.

Three screens live outside the embedded web client - the account list, the
account form and the settings page - and they used to be three different
shapes: a bare text title on one and a branded header on the next, actions at
the top of one and at the bottom of another, and on the only page long enough
to scroll, a header that scrolled away with the content.

:class:`PageShell` is that shape, written once. A header band that stays put,
carrying the product mark, the page's title, its build badge, its message
banner and its actions, over a body that scrolls underneath. The header is
deliberately *outside* the scroll area: on a till screen the way out of a page
("Done", "Cancel", and the close button itself) must never be something the
user has to scroll up to find.

**The caption buttons live in the band.** The window has no title bar, so every
screen without an app bar needs its own set, and
:class:`~bytesraw_erp.ui.main_window.MainWindow` floats one over any page that
provides none. A page built on this shell provides one - the window's sweep for
``WindowControls`` finds it and the floating pair stays hidden - which is what
puts the caption buttons in the same place on every screen in the product
rather than in the app bar on one and over the top of the page on the others.

The band is the rest of a title bar too: its blank space drags the window and a
double-click on it maximises or restores, exactly as the app bar's does. A
screen that carries the caption buttons and cannot be picked up by the strip
they sit in is half a title bar.

Centring is done with a mirror of the caption buttons' width on the left edge
rather than with a plain stretch. Without it the band's centre is pushed left
by exactly the width of those buttons, and the header's title no longer lines
up with the column of cards beneath it - which on a page whose whole point is
that the header does not move is the one misalignment that would be noticed.

:class:`CardColumns` is the settings page's second column. It lives here rather
than in that page because the rule it encodes - two columns when the window is
wide enough, one when it is not - is a fact about this layout, not about
settings.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bytesraw_erp.ui.theme import Palette
from bytesraw_erp.ui.widgets.banner import Banner
from bytesraw_erp.ui.widgets.sections import BrandHeader
from bytesraw_erp.ui.widgets.window_controls import WindowControls
from bytesraw_erp.ui.widgets.window_drag import enable_window_drag

#: Content widths. A page picks the one that suits what it holds - a credential
#: form has no business being 1000px wide - while the chrome around it stays
#: identical, which is what makes the three screens read as one product.
WIDTH_FORM = 560
WIDTH_LIST = 760
WIDTH_WIDE = 1060

#: How far the caption buttons sit from the window's edge.
_BAND_GUTTER = 12
#: Horizontal inset of the content inside its centred column. The header and
#: the body use the same value, which is what puts the page's title directly
#: above the left edge of the first card rather than a few pixels off it.
_INSET = 24

#: See the comment where it is used: large enough that the two spacers either
#: side of the header column only ever get the space the column cannot take.
_COLUMN_STRETCH = 1000


class PageShell(QWidget):
    """A fixed header over a scrolling body, with the caption buttons in it.

    Add content to :attr:`body`, actions to the header with :meth:`add_action`,
    and call :meth:`apply_theme` from the page's own ``theme_changed`` handler -
    the mark, the section badges and the caption glyphs are all rendered
    bitmaps and none of them follow a stylesheet.
    """

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        *,
        width: int = WIDTH_LIST,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        band = QFrame()
        band.setObjectName("PageHeader")
        band_row = QHBoxLayout(band)
        band_row.setContentsMargins(_BAND_GUTTER, 10, _BAND_GUTTER, 0)
        band_row.setSpacing(0)

        self.window_controls = WindowControls()
        enable_window_drag(band)

        # The left mirror: an empty widget as wide as the caption buttons, so
        # the two stretches either side of the column stay equal and the column
        # is centred on the *window*, not on what is left of it.
        mirror = QWidget()
        mirror.setFixedWidth(self.window_controls.sizeHint().width())
        band_row.addWidget(mirror, 0, Qt.AlignmentFlag.AlignTop)
        band_row.addStretch(1)
        # The column's stretch has to dwarf the two spacers', not merely equal
        # them: a QHBoxLayout splits the row by stretch factor, so 1/1/1 gives
        # the header a third of a wide screen and leaves the title floating in
        # the middle of it, nowhere near the cards below. With a large factor
        # the column takes everything up to its maximum width and the spacers
        # share only what is left over, which is what centres it.

        column = QWidget()
        column.setMaximumWidth(width)
        header = QVBoxLayout(column)
        header.setContentsMargins(_INSET, 8, _INSET, 16)
        header.setSpacing(12)

        row = QHBoxLayout()
        row.setSpacing(16)
        self.header = BrandHeader(title, subtitle)
        row.addWidget(self.header, 1)

        # The actions are a widget rather than a bare layout only so they can
        # be pinned to the top of the row; a QHBoxLayout cannot align a
        # nested layout, and a two-line subtitle would otherwise centre them
        # against it.
        actions_host = QWidget()
        self.actions = QHBoxLayout(actions_host)
        self.actions.setContentsMargins(0, 0, 0, 0)
        self.actions.setSpacing(8)
        row.addWidget(actions_host, 0, Qt.AlignmentFlag.AlignTop)
        header.addLayout(row)

        self.banner = Banner()
        header.addWidget(self.banner)

        band_row.addWidget(column, _COLUMN_STRETCH)
        band_row.addStretch(1)
        band_row.addWidget(self.window_controls, 0, Qt.AlignmentFlag.AlignTop)
        outer.addWidget(band)

        content = QWidget()
        content.setMaximumWidth(width)
        self.body = QVBoxLayout(content)
        self.body.setContentsMargins(_INSET, 20, _INSET, 32)
        self.body.setSpacing(18)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(content)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll, 1)
        self.scroll = scroll

    # -- header ------------------------------------------------------------

    def add_action(self, button: QPushButton) -> None:
        """Put a button in the header, to the right of the title."""
        self.actions.addWidget(button)

    def set_title(self, title: str) -> None:
        self.header.set_title(title)

    def set_subtitle(self, subtitle: str) -> None:
        self.header.set_subtitle(subtitle)

    # -- theme -------------------------------------------------------------

    def apply_theme(self, palette: Palette) -> None:
        """Re-stroke everything in the band that is a bitmap."""
        self.window_controls.apply_theme(palette)


class CardColumns(QWidget):
    """Cards in two columns, falling back to one when there is no room.

    Which column a card goes in is declared, not computed. Balancing by
    measured height reads better on paper and is wrong in practice here: the
    Updates card grows two buttons the moment a release is found and the
    printing card grows a banner when a printer disappears, so a page laid out
    by height would rearrange itself under the user while they were reading it.
    A declared column is the same every time the page opens.
    """

    def __init__(
        self,
        *,
        breakpoint: int = 820,
        spacing: int = 18,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._breakpoint = breakpoint
        #: (widget, preferred column) in declaration order. The order is what
        #: the single-column fallback reads back, so it has to be kept.
        self._cards: list[tuple[QWidget, int]] = []
        #: 0 until the first reflow, so the first resize always lays out.
        self._columns = 0

        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(spacing)
        self._left = QVBoxLayout()
        self._right = QVBoxLayout()
        for column in (self._left, self._right):
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(spacing)
            self._row.addLayout(column, 1)

    def add_card(self, card: QWidget, *, column: int = 0) -> None:
        """Add ``card``, preferring the left (0) or right (1) column."""
        card.setParent(self)
        self._cards.append((card, 1 if column else 0))
        self._reflow(self._columns or 1, force=True)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._reflow(2 if self.width() >= self._breakpoint else 1)

    # -- layout ------------------------------------------------------------

    def _reflow(self, columns: int, *, force: bool = False) -> None:
        if columns == self._columns and not force:
            return
        self._columns = columns
        for layout in (self._left, self._right):
            _drain(layout)
        for card, preferred in self._cards:
            target = self._right if columns == 2 and preferred else self._left
            target.addWidget(card)
        # The trailing stretch is what keeps a short column's cards at the top
        # of the page instead of spread down the full height of the tall one.
        self._left.addStretch(1)
        self._right.addStretch(1)
        # An empty right column would still claim half the width, so a
        # single-column page would sit in the left half of the screen.
        self._row.setStretch(1, 0 if columns == 1 else 1)


def _drain(layout: QLayout) -> None:
    """Empty a layout without destroying the widgets it held.

    ``takeAt`` un-manages an item but leaves a widget parented where it was, so
    every card survives to be added to a column again a moment later. Deleting
    them instead - the usual way to clear a layout - would mean rebuilding the
    whole settings page every time the window crossed the breakpoint, and
    losing the state of every control on it.
    """
    while layout.count():
        layout.takeAt(0)
