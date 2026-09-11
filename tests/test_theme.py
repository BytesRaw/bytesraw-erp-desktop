"""Theme tests.

The contrast assertions are the point of this file. Menu items were once
painted near-black on Qt's dark menu background - a contrast ratio of 1.0,
invisible - because the stylesheet set a foreground without a background. A
palette that reads fine in light mode can be unusable in dark, and only
measuring catches it.
"""

from __future__ import annotations

import re
from dataclasses import fields

import pytest

from bytesraw_erp.ui.theme import DARK, LIGHT, Palette, Theme, build_qss

#: WCAG AA for normal text. Anything below this is a real legibility defect.
_MIN_CONTRAST = 4.5

#: Surfaces that paint themselves and so must declare both colours.
_SELF_PAINTING = ["QMenu", "QMenu::item", "QToolTip", "QComboBox QAbstractItemView"]


def _relative_luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(fg: str, bg: str) -> float:
    a, b = _relative_luminance(fg), _relative_luminance(bg)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


def _block(qss: str, selector: str) -> str:
    """The declarations of the first rule for ``selector``."""
    match = re.search(rf"(?:^|\n)\s*{re.escape(selector)}\s*\{{([^}}]*)\}}", qss)
    assert match is not None, f"no rule found for {selector}"
    return match.group(1)


@pytest.fixture(params=[LIGHT, DARK], ids=["light", "dark"])
def palette(request: pytest.FixtureRequest) -> Palette:
    return request.param


# -- the regression this file exists for ------------------------------------


@pytest.mark.parametrize("selector", _SELF_PAINTING)
def test_self_painting_widgets_declare_both_colours(palette: Palette, selector: str) -> None:
    """A foreground without a background is how text becomes invisible."""
    block = _block(build_qss(palette), selector)
    assert "color:" in block, f"{selector} sets no text colour"
    assert "background:" in block, f"{selector} sets no background"


def test_menu_text_is_legible(palette: Palette) -> None:
    assert contrast_ratio(palette.text, palette.menu_bg) >= _MIN_CONTRAST


def test_menu_text_stays_legible_when_highlighted(palette: Palette) -> None:
    assert contrast_ratio(palette.text, palette.menu_hover) >= _MIN_CONTRAST


def test_body_text_is_legible(palette: Palette) -> None:
    assert contrast_ratio(palette.text, palette.surface) >= _MIN_CONTRAST
    assert contrast_ratio(palette.text, palette.surface_alt) >= _MIN_CONTRAST


def test_muted_text_is_still_readable(palette: Palette) -> None:
    """Muted is allowed to be quieter, but not decorative-only."""
    assert contrast_ratio(palette.text_muted, palette.surface) >= 3.0


def test_primary_button_label_is_legible(palette: Palette) -> None:
    assert contrast_ratio(palette.primary_text, palette.primary) >= _MIN_CONTRAST


def test_banner_text_is_legible(palette: Palette) -> None:
    assert contrast_ratio(palette.danger, palette.danger_bg) >= _MIN_CONTRAST
    assert contrast_ratio(palette.success, palette.success_bg) >= _MIN_CONTRAST


# -- palette / stylesheet shape ---------------------------------------------


def test_palettes_disagree_on_darkness() -> None:
    assert LIGHT.is_dark is False
    assert DARK.is_dark is True


def test_every_palette_slot_is_filled(palette: Palette) -> None:
    for field in fields(palette):
        if field.name == "is_dark":
            continue
        value = getattr(palette, field.name)
        assert isinstance(value, str) and value.startswith("#"), (
            f"{field.name} is not a colour"
        )


def test_stylesheet_has_no_unsubstituted_placeholders(palette: Palette) -> None:
    qss = build_qss(palette)
    assert "{" not in qss.replace("{{", "").replace("}}", "").split("QWidget")[0]
    assert "None" not in qss


def test_stylesheet_carries_the_palette_colours(palette: Palette) -> None:
    qss = build_qss(palette)
    assert palette.menu_bg in qss
    assert palette.text in qss
    assert palette.primary in qss


# -- Theme enum --------------------------------------------------------------


def test_theme_round_trips_through_its_value() -> None:
    for theme in Theme:
        assert Theme(theme.value) is theme


def test_every_theme_has_a_label_and_icon() -> None:
    for theme in Theme:
        assert theme.label
        assert theme.icon_name
