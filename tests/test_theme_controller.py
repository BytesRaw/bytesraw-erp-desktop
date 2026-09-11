"""ThemeController behaviour.

The emission-count tests are the point. A "match system" option used to exist
and was removed because switching to it applied the theme **twice**: resolving
it set `QStyleHints.setColorScheme(Unknown)`, Qt then resolved the OS value and
emitted `colorSchemeChanged`, which re-entered `apply()`. Each emission drove an
Odoo colour-scheme write and a web view reload, so two reloads raced and the
embedded client never finished loading.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings, Qt

from bytesraw_erp.ui.theme import DARK, LIGHT, Theme, ThemeController


@pytest.fixture
def controller(qtbot, monkeypatch: pytest.MonkeyPatch) -> ThemeController:
    """A controller on a scratch QSettings scope, so the real one is untouched."""
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    monkeypatch.setattr(QSettings, "setValue", lambda *_args, **_kwargs: None)
    from PySide6.QtWidgets import QApplication

    return ThemeController(QApplication.instance())


def test_only_light_and_dark_exist() -> None:
    assert [theme.value for theme in Theme] == ["light", "dark"]


def test_system_is_not_a_theme() -> None:
    """Guards against it being quietly reintroduced."""
    assert not hasattr(Theme, "SYSTEM")
    with pytest.raises(ValueError, match="system"):
        Theme("system")


def test_a_stored_system_value_reads_back_as_light(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Settings written by an older build must not break startup."""
    monkeypatch.setattr(QSettings, "value", lambda *_args, **_kwargs: "system")
    monkeypatch.setattr(QSettings, "setValue", lambda *_args, **_kwargs: None)
    from PySide6.QtWidgets import QApplication

    assert ThemeController(QApplication.instance()).theme is Theme.LIGHT


def test_an_unknown_stored_value_reads_back_as_light(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(QSettings, "value", lambda *_args, **_kwargs: "chartreuse")
    monkeypatch.setattr(QSettings, "setValue", lambda *_args, **_kwargs: None)
    from PySide6.QtWidgets import QApplication

    assert ThemeController(QApplication.instance()).theme is Theme.LIGHT


def test_light_resolves_to_the_light_palette(controller: ThemeController) -> None:
    controller.set_theme(Theme.LIGHT)
    assert controller.palette is LIGHT
    assert controller.is_dark is False


def test_dark_resolves_to_the_dark_palette(controller: ThemeController) -> None:
    controller.set_theme(Theme.DARK)
    assert controller.palette is DARK
    assert controller.is_dark is True


def test_each_switch_emits_exactly_once(controller: ThemeController) -> None:
    """The regression this module exists for.

    Two emissions per switch meant two Odoo writes and two overlapping web view
    reloads, which is what left the view stuck loading.
    """
    controller.set_theme(Theme.LIGHT)
    seen: list[bool] = []
    controller.theme_changed.connect(lambda palette: seen.append(palette.is_dark))

    controller.set_theme(Theme.DARK)
    assert seen == [True]

    controller.set_theme(Theme.LIGHT)
    assert seen == [True, False]


def test_qt_is_never_left_on_an_unknown_scheme(controller: ThemeController) -> None:
    """``Unknown`` is what made Qt resolve the OS value and re-enter apply()."""
    from PySide6.QtWidgets import QApplication

    hints = QApplication.instance().styleHints()
    for theme, expected in ((Theme.LIGHT, Qt.ColorScheme.Light), (Theme.DARK, Qt.ColorScheme.Dark)):
        controller.set_theme(theme)
        assert hints.colorScheme() == expected


def test_odoo_color_scheme_maps_to_odoo_vocabulary() -> None:
    assert Theme.LIGHT.odoo_color_scheme == "light"
    assert Theme.DARK.odoo_color_scheme == "dark"


def test_every_theme_has_a_label_and_an_icon() -> None:
    for theme in Theme:
        assert theme.label
        assert theme.icon_name in {"sun", "moon"}
