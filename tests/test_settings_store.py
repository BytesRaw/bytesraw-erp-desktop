"""Settings store tests.

The theme of this file is that a damaged or foreign settings file must never
stop the app from starting. Unlike the account registry - where a corrupt file
means credentials are at stake and the user needs to know - settings have safe
defaults, so the store falls back rather than raising.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bytesraw_erp.constants import SETTINGS_VERSION
from bytesraw_erp.data.models import PrintMode, PrintSettings
from bytesraw_erp.data.settings_store import SettingsStore


@pytest.fixture
def store(tmp_path: Path) -> SettingsStore:
    instance = SettingsStore(tmp_path / "settings.json")
    instance.load()
    return instance


def test_defaults_when_no_file_exists(store: SettingsStore) -> None:
    printing = store.printing
    assert printing.mode is PrintMode.DIALOG
    assert printing.uses_system_default is True
    assert printing.auto_print_reports is True
    assert printing.keep_report_copy is False


def test_settings_survive_a_reload(tmp_path: Path, store: SettingsStore) -> None:
    store.set_printing(
        PrintSettings(
            mode=PrintMode.DIRECT,
            printer_name="EPSON TM-m30 Receipt",
            auto_print_reports=True,
            keep_report_copy=True,
        )
    )

    reloaded = SettingsStore(tmp_path / "settings.json")
    reloaded.load()
    assert reloaded.printing.mode is PrintMode.DIRECT
    assert reloaded.printing.printer_name == "EPSON TM-m30 Receipt"
    assert reloaded.printing.keep_report_copy is True
    assert reloaded.printing.uses_system_default is False


def test_writing_identical_settings_does_not_touch_the_file(
    tmp_path: Path, store: SettingsStore
) -> None:
    """Immediate-apply means this is called on every keystroke-ish event."""
    store.set_printing(PrintSettings(mode=PrintMode.DIRECT))
    path = tmp_path / "settings.json"
    before = path.stat().st_mtime_ns
    store.set_printing(PrintSettings(mode=PrintMode.DIRECT))
    assert path.stat().st_mtime_ns == before


def test_file_layout_is_versioned(tmp_path: Path, store: SettingsStore) -> None:
    store.set_printing(PrintSettings(mode=PrintMode.DIRECT))
    raw = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert raw["version"] == SETTINGS_VERSION
    assert raw["printing"]["mode"] == "direct"


def test_corrupt_file_falls_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{ not json", encoding="utf-8")
    store = SettingsStore(path)
    store.load()
    assert store.printing == PrintSettings()


def test_newer_format_falls_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"version": 99, "printing": {"mode": "direct"}}), encoding="utf-8"
    )
    store = SettingsStore(path)
    store.load()
    assert store.printing.mode is PrintMode.DIALOG


def test_unknown_print_mode_falls_back(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"version": SETTINGS_VERSION, "printing": {"mode": "telepathy"}}),
        encoding="utf-8",
    )
    store = SettingsStore(path)
    store.load()
    assert store.printing.mode is PrintMode.DIALOG


def test_partial_section_keeps_the_other_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"version": SETTINGS_VERSION, "printing": {"printer_name": "HP"}}),
        encoding="utf-8",
    )
    store = SettingsStore(path)
    store.load()
    assert store.printing.printer_name == "HP"
    assert store.printing.mode is PrintMode.DIALOG
    assert store.printing.auto_print_reports is True


def test_print_settings_round_trip() -> None:
    settings = PrintSettings(
        mode=PrintMode.DIRECT,
        printer_name="Microsoft Print to PDF",
        auto_print_reports=False,
        keep_report_copy=True,
    )
    assert PrintSettings.from_dict(settings.to_dict()) == settings


def test_evolve_leaves_the_original_alone() -> None:
    base = PrintSettings()
    changed = base.evolve(mode=PrintMode.DIRECT)
    assert changed.mode is PrintMode.DIRECT
    assert base.mode is PrintMode.DIALOG
