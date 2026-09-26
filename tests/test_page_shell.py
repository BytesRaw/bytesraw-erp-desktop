"""The shared layout the three non-Odoo screens are built on.

Three things here are load-bearing and all three fail silently:

* the header must stay out of the scroll area, or "Done" and "Cancel" scroll
  away on the one page long enough to scroll - which is the state the settings
  page shipped in;
* the shell has to provide its own ``WindowControls``, because the window hides
  its floating pair the moment a page provides one, and a page that provided
  none *and* was not covered by the overlay would be a full-screen screen with
  no way to quit the application;
* the two-column reflow has to move the same widgets rather than rebuild them,
  or crossing the breakpoint would reset every control on the settings page.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from bytesraw_erp.ui.theme import DARK, LIGHT
from bytesraw_erp.ui.widgets.page import CardColumns, PageShell
from bytesraw_erp.ui.widgets.window_controls import WindowControls


@pytest.fixture
def shell(qtbot) -> PageShell:
    widget = PageShell("Settings", "What this page is for.")
    qtbot.addWidget(widget)
    return widget


def test_the_header_is_outside_the_scroll_area(shell: PageShell) -> None:
    """The whole point of the band: it does not move when the body does."""
    scroll = shell.findChild(QScrollArea)
    assert scroll is not None
    assert shell.header not in scroll.findChildren(type(shell.header))
    assert shell.banner not in scroll.findChildren(type(shell.banner))


def test_actions_land_in_the_header_not_the_body(shell: PageShell) -> None:
    button = QPushButton("Done")
    shell.add_action(button)
    scroll = shell.findChild(QScrollArea)
    assert scroll is not None
    assert button not in scroll.findChildren(QPushButton)


def test_the_shell_carries_its_own_caption_buttons(shell: PageShell) -> None:
    """``MainWindow`` hides its floating pair for any page that has these.

    So a shell without them would leave a full-screen window with no way out -
    and nothing would raise, because the overlay hides itself on a sweep that
    simply found nothing.
    """
    assert shell.findChild(WindowControls) is not None


def test_the_band_carries_the_same_four_controls_as_the_app_bar(qtbot) -> None:
    """A screen outside Odoo is not a screen with fewer ways to size the window.

    These used to depend on the launch mode, which meant the account form -
    the first screen a fresh machine ever shows - could offer two buttons
    while the app bar offered three.
    """
    shell = PageShell("Accounts")
    qtbot.addWidget(shell)

    names = {
        button.objectName() for button in shell.window_controls.findChildren(QToolButton)
    }
    assert names == {
        "MinimizeButton",
        "MaximizeButton",
        "FullScreenButton",
        "CloseButton",
    }


def test_the_band_moves_the_window_like_a_title_bar(qtbot) -> None:
    """The band carries the caption buttons, so it carries the caption's grip.

    Pinned by the filter being installed rather than by synthesising a drag:
    the move itself is ``startSystemMove``, which hands the window to the
    window manager and cannot be observed from inside the process.
    """
    from bytesraw_erp.ui.widgets.window_drag import _WindowDragFilter

    shell = PageShell("Accounts")
    qtbot.addWidget(shell)
    band = shell.findChild(QFrame, "PageHeader")
    assert band is not None
    assert band.findChildren(_WindowDragFilter), "the header band is not draggable"


def test_a_theme_change_re_strokes_the_caption_glyphs(shell: PageShell) -> None:
    """A QIcon is a finished bitmap; the band's are as fixed as the app bar's."""
    shell.apply_theme(LIGHT)
    before = {button.icon().cacheKey() for button, _ in shell.window_controls._icons}
    shell.apply_theme(DARK)
    after = {button.icon().cacheKey() for button, _ in shell.window_controls._icons}
    assert before != after


def test_the_title_can_be_changed_after_construction(shell: PageShell) -> None:
    """The account form is one page serving Add and Edit."""
    shell.set_title("Edit account")
    titles = [
        label.text() for label in shell.findChildren(QLabel) if label.objectName() == "PageTitle"
    ]
    assert titles == ["Edit account"]


# --- the two-column reflow -------------------------------------------------


def _cards(count: int) -> list[QWidget]:
    return [QLabel(f"card {index}") for index in range(count)]


@pytest.fixture
def columns(qtbot) -> CardColumns:
    """Shown, because a hidden widget is never told it was resized.

    Measured: ``resize()`` on a widget that has never been shown updates
    ``width()`` and delivers no ``QResizeEvent`` until the widget appears, so a
    test that only resized would assert against the layout it started in.
    """
    widget = CardColumns(breakpoint=800)
    qtbot.addWidget(widget)
    widget.show()
    return widget


def test_a_wide_page_uses_both_columns(columns: CardColumns) -> None:
    left, right = _cards(1), _cards(1)
    columns.add_card(left[0], column=0)
    columns.add_card(right[0], column=1)

    columns.resize(1000, 400)

    assert columns._left.indexOf(left[0]) >= 0
    assert columns._right.indexOf(right[0]) >= 0


def test_a_narrow_page_collapses_to_one_column(columns: CardColumns) -> None:
    """And in declaration order - a card does not jump the queue on a resize."""
    cards = _cards(3)
    columns.add_card(cards[0], column=0)
    columns.add_card(cards[1], column=1)
    columns.add_card(cards[2], column=0)

    columns.resize(500, 400)

    assert [columns._left.itemAt(i).widget() for i in range(3)] == cards
    assert columns._right.count() == 1  # the trailing stretch only


def test_crossing_the_breakpoint_moves_cards_rather_than_rebuilding(
    columns: CardColumns,
) -> None:
    """Rebuilding would reset every control the settings page is made of."""
    cards = _cards(2)
    columns.add_card(cards[0], column=0)
    columns.add_card(cards[1], column=1)

    columns.resize(1000, 400)
    columns.resize(500, 400)
    columns.resize(1000, 400)

    assert all(card.parent() is columns for card in cards)
    assert columns._left.indexOf(cards[0]) >= 0
    assert columns._right.indexOf(cards[1]) >= 0


def test_one_column_does_not_leave_half_the_page_empty(columns: CardColumns) -> None:
    """An empty right column would still claim its half of the width."""
    columns.add_card(_cards(1)[0], column=0)

    columns.resize(500, 400)
    assert columns._row.stretch(1) == 0

    columns.resize(1000, 400)
    assert columns._row.stretch(1) == 1


def test_a_card_that_grows_after_the_page_is_shown_gets_the_room(qtbot) -> None:
    """The regression behind a crushed Report printers card.

    ``_drain`` used to leave each ``QWidgetItem`` it took alive, and Qt clears
    only the first item ever made for a widget when that widget's size changes.
    Every reflow re-adds the cards through new items, so a card that grew after
    the page was up - rows added in ``on_enter``, which the router calls after
    showing the page - kept the height its column measured on the first pass.
    Wrapping labels matter: they make the cards height-for-width, which is the
    cache that went stale.
    """
    shell = PageShell("Settings", "What this page is for.")
    qtbot.addWidget(shell)
    columns = CardColumns()
    growing = QFrame()
    body = QVBoxLayout(growing)
    body.addWidget(_wrapping("A card whose rows arrive once the page is on screen. " * 3))
    columns.add_card(_wrapping("Left. " * 40), column=0)
    columns.add_card(growing, column=1)
    columns.add_card(_wrapping("Below it. " * 30), column=1)
    shell.body.addWidget(columns)
    shell.resize(1200, 700)
    shell.show()
    qtbot.waitExposed(shell)

    for index in range(8):
        body.addWidget(QPushButton(f"Row {index}"))
    qtbot.wait(50)

    assert growing.height() >= growing.minimumSizeHint().height()


def _wrapping(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    return label
