"""The app bar's chrome: the brand block, the chips, and icon repainting.

These are the two failure modes this widget has: a bitmap icon left stroked in
the previous theme's colour, and a stylesheet written onto the wrong QFrame.
Neither raises - both just look wrong - so they are pinned here.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtWidgets import QFrame, QLabel

from bytesraw_erp.constants import APP_VERSION
from bytesraw_erp.data.models import Company, Language, SessionContext
from bytesraw_erp.ui.theme import DARK, LIGHT, Theme
from bytesraw_erp.ui.widgets.app_bar import AppBar, _initials


@pytest.fixture
def bar(qtbot) -> AppBar:
    widget = AppBar()
    qtbot.addWidget(widget)
    widget.apply_theme(LIGHT, Theme.LIGHT)
    return widget


@pytest.fixture
def session() -> SessionContext:
    return SessionContext(
        uid=2,
        user_name="Mitchell Admin",
        login="demo",
        database="demo",
        language="en_US",
        server_version="19.0",
        companies=(Company(id=1, name="Fatoora Demo Company"),),
        current_company_id=1,
        languages=(Language(code="en_US", name="English (US)"),),
    )


def _label(bar: AppBar, object_name: str) -> QLabel:
    match = bar.findChild(QLabel, object_name)
    assert match is not None, f"no QLabel named {object_name}"
    return match


# -- the version -------------------------------------------------------------


def test_the_build_number_is_on_screen(bar: AppBar) -> None:
    """Support's first question, answerable without opening anything."""
    assert _label(bar, "VersionBadge").text() == f"v{APP_VERSION}"


def test_the_version_survives_a_signed_out_bar(bar: AppBar, session: SessionContext) -> None:
    """It belongs to the shell, not to the session, so it never blanks."""
    bar.set_session(session)
    bar.set_session(None)
    assert _label(bar, "VersionBadge").text() == f"v{APP_VERSION}"


# -- identity ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Mitchell Admin", "MA"),
        ("demo", "DE"),
        ("mary jane watson", "MW"),
        ("", "?"),
    ],
)
def test_initials(name: str, expected: str) -> None:
    assert _initials(name) == expected


def test_the_user_chip_shows_who_and_where(bar: AppBar, session: SessionContext) -> None:
    bar.set_session(session)
    assert _label(bar, "UserName").text() == "Mitchell Admin"
    assert _label(bar, "UserMeta").text() == "demo"
    assert _label(bar, "Avatar").text() == "MA"


def test_a_company_with_no_name_hides_its_chip(bar: AppBar, session: SessionContext) -> None:
    """An empty chip is a floating grey pill with nothing in it."""
    bar.set_session(session)
    chip = bar.findChild(QFrame, "CompanyChip")
    assert chip is not None and chip.isVisibleTo(bar)

    bar.set_session(replace(session, current_company_id=99))
    assert not chip.isVisibleTo(bar)


# -- the regressions ---------------------------------------------------------


def test_theming_the_separators_leaves_the_chips_alone(bar: AppBar) -> None:
    """The chips are QFrames too.

    ``findChildren(QFrame)`` would sweep them up and paint them the separator
    colour, wiping the background their object-name rule gives them.
    """
    bar.apply_theme(DARK, Theme.DARK)
    for name in ("CompanyChip", "UserChip", "NavGroup"):
        frame = bar.findChild(QFrame, name)
        assert frame is not None
        assert frame.styleSheet() == "", f"{name} was painted over"


def test_every_icon_is_re_rendered_on_a_theme_change(bar: AppBar) -> None:
    """Menu actions included - a menu is as dark as the bar it hangs from."""
    before = {target.icon().cacheKey() for target, _ in bar._icon_targets}
    bar.apply_theme(DARK, Theme.DARK)
    after = {target.icon().cacheKey() for target, _ in bar._icon_targets}
    assert before != after
    assert all(not target.icon().isNull() for target, _ in bar._icon_targets)


def test_menu_actions_are_among_the_repainted_targets(bar: AppBar) -> None:
    from PySide6.QtGui import QAction

    assert any(isinstance(target, QAction) for target, _ in bar._icon_targets)
