"""The app bar's chrome: the brand block, the chips, and icon repainting.

These are the two failure modes this widget has: a bitmap icon left stroked in
the previous theme's colour, and a stylesheet written onto the wrong QFrame.
Neither raises - both just look wrong - so they are pinned here.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QFrame, QLabel, QToolButton

from bytesraw_erp.constants import APP_VERSION
from bytesraw_erp.data.models import Company, Language, SessionContext
from bytesraw_erp.ui.theme import DARK, LIGHT, Theme
from bytesraw_erp.ui.widgets.app_bar import AppBar, _initials
from bytesraw_erp.ui.widgets.window_controls import WindowControls


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


def test_the_bar_says_nothing_about_the_company(bar: AppBar, session: SessionContext) -> None:
    """Odoo's own navbar owns the company; the shell does not repeat it.

    The chip is gone rather than hidden, so a session that names a company
    must not bring any of it back.
    """
    bar.set_session(session)
    assert bar.findChild(QFrame, "CompanyChip") is None
    labels = {label.text() for label in bar.findChildren(QLabel)}
    assert session.current_company_name not in labels


# -- the window controls -----------------------------------------------------


def test_the_bar_carries_minimise_and_close(bar: AppBar) -> None:
    """There is no title bar to carry them - the app runs full screen."""
    controls = bar.findChild(WindowControls)
    assert controls is not None
    for name in ("MinimizeButton", "CloseButton"):
        button = controls.findChild(QToolButton, name)
        assert button is not None, f"no {name}"
        assert not button.icon().isNull()


def test_the_window_controls_follow_the_theme(bar: AppBar) -> None:
    """They hold their own icons, so the bar has to repaint them too."""
    controls = bar.findChild(WindowControls)
    assert controls is not None
    before = {button.icon().cacheKey() for button, _ in controls._icons}
    bar.apply_theme(DARK, Theme.DARK)
    after = {button.icon().cacheKey() for button, _ in controls._icons}
    assert before != after


# -- the regressions ---------------------------------------------------------


def test_theming_the_separators_leaves_the_chips_alone(bar: AppBar) -> None:
    """The chips are QFrames too.

    ``findChildren(QFrame)`` would sweep them up and paint them the separator
    colour, wiping the background their object-name rule gives them.
    """
    bar.apply_theme(DARK, Theme.DARK)
    for name in ("UserChip", "NavGroup"):
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
