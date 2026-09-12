"""Rendering mode: the flags, and the launch that chooses them.

Why any of this is tested rather than trusted: the whole mechanism is read
once, before the ``QApplication`` exists, and a mistake in it is invisible.
Chromium takes its switches from an environment variable and never reports
back, so a flag spelled wrongly, dropped, or applied after the fact produces
exactly the same thing as no flag at all - a web view that is still painted by
the broken driver.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from bytesraw_erp.app import parse_arguments
from bytesraw_erp.constants import SETTINGS_VERSION
from bytesraw_erp.data.models import DisplaySettings, RenderMode
from bytesraw_erp.data.settings_store import SettingsStore
from bytesraw_erp.services import graphics

# -- the flags ---------------------------------------------------------------


def test_auto_asks_chromium_for_nothing() -> None:
    """The default must not touch Chromium's command line at all."""
    assert graphics.flags_for(RenderMode.AUTO) == ""


def test_software_disables_the_gpu() -> None:
    flags = graphics.flags_for(RenderMode.SOFTWARE).split()
    assert "--disable-gpu" in flags


def test_software_keeps_the_cpu_fallback() -> None:
    """``--disable-software-rasterizer`` would remove the very thing this mode
    falls back to, leaving nothing to paint with."""
    assert "--disable-software-rasterizer" not in graphics.flags_for(RenderMode.SOFTWARE)


def test_existing_flags_survive() -> None:
    """A deployment may already point the variable at something of its own."""
    merged = graphics.flags_for(RenderMode.SOFTWARE, "--proxy-server=http://proxy:3128")
    assert "--proxy-server=http://proxy:3128" in merged
    assert "--disable-gpu" in merged


def test_a_flag_is_never_added_twice() -> None:
    merged = graphics.flags_for(RenderMode.SOFTWARE, "--disable-gpu").split()
    assert merged.count("--disable-gpu") == 1


def test_auto_leaves_a_preset_command_line_alone() -> None:
    assert graphics.flags_for(RenderMode.AUTO, "--no-sandbox") == "--no-sandbox"


# -- applying it -------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_active_mode() -> Iterator[None]:
    """``configure_rendering`` records the mode in module state, which would
    otherwise outlive the test and be read by the next one."""
    yield
    graphics._active = RenderMode.AUTO


def test_configuring_software_mode_sets_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(graphics.CHROMIUM_FLAGS_VAR, raising=False)
    graphics.configure_rendering(RenderMode.SOFTWARE)
    assert "--disable-gpu" in os.environ[graphics.CHROMIUM_FLAGS_VAR]


def test_configuring_auto_mode_invents_no_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty command line must not be exported as an empty variable - a
    reader of the environment, here or in a support call, would be misled."""
    monkeypatch.delenv(graphics.CHROMIUM_FLAGS_VAR, raising=False)
    graphics.configure_rendering(RenderMode.AUTO)
    assert graphics.CHROMIUM_FLAGS_VAR not in os.environ


def test_the_active_mode_is_reported_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """The settings page compares the stored mode against this to decide
    whether it has to ask for a restart."""
    monkeypatch.delenv(graphics.CHROMIUM_FLAGS_VAR, raising=False)
    graphics.configure_rendering(RenderMode.SOFTWARE)
    assert graphics.active_render_mode() is RenderMode.SOFTWARE


# -- the launch arguments ----------------------------------------------------


def test_no_flags_defers_to_the_settings_file() -> None:
    options = parse_arguments(["app.exe"])
    assert options.render_mode is None
    assert options.windowed is False


def test_the_software_flag_overrides_the_setting() -> None:
    options = parse_arguments(["app.exe", "--software-render"])
    assert options.render_mode is RenderMode.SOFTWARE


def test_the_gpu_flag_undoes_a_stored_software_mode() -> None:
    """The escape hatch from the other direction: a machine where the stored
    value turned out to be the wrong one still has to be startable."""
    options = parse_arguments(["app.exe", "--gpu-render"])
    assert options.render_mode is RenderMode.AUTO


def test_the_apps_own_flags_never_reach_qt() -> None:
    """Qt warns about switches it does not recognise, so they are removed."""
    options = parse_arguments(["app.exe", "--windowed", "--software-render", "-style", "fusion"])
    assert options.argv == ["app.exe", "-style", "fusion"]
    assert options.windowed is True
    assert options.render_mode is RenderMode.SOFTWARE


# -- the stored setting ------------------------------------------------------


def test_the_default_is_the_graphics_card(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    store.load()
    assert store.display.render_mode is RenderMode.AUTO
    assert store.display.uses_gpu is True


def test_the_mode_survives_a_reload(tmp_path: Path) -> None:
    """This is the whole point of storing it: the till that needs software
    rendering needs it on every launch, not just the one where it was chosen."""
    store = SettingsStore(tmp_path / "settings.json")
    store.load()
    store.set_display(DisplaySettings(render_mode=RenderMode.SOFTWARE))

    reloaded = SettingsStore(tmp_path / "settings.json")
    reloaded.load()
    assert reloaded.display.render_mode is RenderMode.SOFTWARE


def test_an_unknown_mode_falls_back_to_auto(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"version": SETTINGS_VERSION, "display": {"render_mode": "crayons"}}),
        encoding="utf-8",
    )
    store = SettingsStore(path)
    store.load()
    assert store.display.render_mode is RenderMode.AUTO


def test_a_settings_file_from_before_this_setting_existed(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"version": SETTINGS_VERSION, "printing": {"mode": "direct"}}),
        encoding="utf-8",
    )
    store = SettingsStore(path)
    store.load()
    assert store.display == DisplaySettings()


def test_display_settings_round_trip() -> None:
    settings = DisplaySettings(render_mode=RenderMode.SOFTWARE)
    assert DisplaySettings.from_dict(settings.to_dict()) == settings


# -- the settings page -------------------------------------------------------
#
# The card is worth a test of its own because it is the only way in on the
# machine that needs it: the web view there is unreadable, and this page is
# reachable only because it is Qt widgets all the way down.


def test_the_settings_page_stores_and_explains_the_choice(tmp_path: Path, qtbot) -> None:
    from PySide6.QtWidgets import QApplication

    from bytesraw_erp.ui.app_context import AppContext
    from bytesraw_erp.ui.pages.settings_page import SettingsPage
    from bytesraw_erp.ui.router import Router
    from bytesraw_erp.ui.theme import ThemeController

    graphics.configure_rendering(RenderMode.AUTO)

    context = AppContext(ThemeController(QApplication.instance()))
    context.settings = SettingsStore(tmp_path / "settings.json")
    context.settings.load()

    page = SettingsPage(context, Router(None))
    qtbot.addWidget(page)
    page.on_enter({})

    combo = page._render_mode
    combo.setCurrentIndex(combo.findData(RenderMode.SOFTWARE.value))

    assert context.settings.display.render_mode is RenderMode.SOFTWARE
    # The mode in force is still the one this process started with, so the
    # page has to ask for a restart rather than implying it has taken effect.
    assert "reopen" in page._render_hint.text().lower()
