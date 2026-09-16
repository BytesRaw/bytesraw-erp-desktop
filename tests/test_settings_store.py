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
from bytesraw_erp.data.models import NO_PRINTER, PrintMode, PrintSettings
from bytesraw_erp.data.settings_store import SettingsStore


@pytest.fixture
def store(tmp_path: Path) -> SettingsStore:
    instance = SettingsStore(tmp_path / "settings.json")
    instance.load()
    return instance


def test_defaults_when_no_file_exists(store: SettingsStore) -> None:
    printing = store.printing
    assert printing.mode is PrintMode.DIALOG
    assert printing.report_printer_name == ""
    assert printing.pos_printer_name == ""
    assert printing.auto_print_reports is True
    assert printing.keep_report_copy is False


def test_settings_survive_a_reload(tmp_path: Path, store: SettingsStore) -> None:
    store.set_printing(
        PrintSettings(
            mode=PrintMode.DIRECT,
            report_printer_name="HP LaserJet",
            pos_printer_name="EPSON TM-m30 Receipt",
            auto_print_reports=True,
            keep_report_copy=True,
        )
    )

    reloaded = SettingsStore(tmp_path / "settings.json")
    reloaded.load()
    assert reloaded.printing.mode is PrintMode.DIRECT
    assert reloaded.printing.report_printer_name == "HP LaserJet"
    assert reloaded.printing.pos_printer_name == "EPSON TM-m30 Receipt"
    assert reloaded.printing.keep_report_copy is True


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
        json.dumps({"version": SETTINGS_VERSION, "printing": {"pos_printer_name": "HP"}}),
        encoding="utf-8",
    )
    store = SettingsStore(path)
    store.load()
    assert store.printing.pos_printer_name == "HP"
    assert store.printing.mode is PrintMode.DIALOG
    assert store.printing.auto_print_reports is True


def test_the_one_printer_a_settings_file_used_to_hold_becomes_the_pos_one(
    tmp_path: Path,
) -> None:
    """An upgrade must not start aiming A4 at the receipt roll.

    Before the split there was a single ``printer_name``, and on a till it was
    the thermal printer - reports were not printed at all then, they were
    saved. Reading it as the POS printer keeps the receipt behaviour the user
    already had, and leaves reports on the Windows default.
    """
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "version": SETTINGS_VERSION,
                "printing": {"mode": "direct", "printer_name": "EPSON TM-m30 Receipt"},
            }
        ),
        encoding="utf-8",
    )
    store = SettingsStore(path)
    store.load()
    assert store.printing.pos_printer_name == "EPSON TM-m30 Receipt"
    assert store.printing.report_printer_name == ""
    assert store.printing.mode is PrintMode.DIRECT


def test_print_settings_round_trip() -> None:
    settings = PrintSettings(
        mode=PrintMode.DIRECT,
        report_printer_name="Microsoft Print to PDF",
        pos_printer_name="EPSON TM-m30 Receipt",
        auto_print_reports=False,
        keep_report_copy=True,
    )
    assert PrintSettings.from_dict(settings.to_dict()) == settings


def test_each_kind_of_job_gets_its_own_device() -> None:
    settings = PrintSettings(
        report_printer_name="HP LaserJet",
        pos_printer_name="EPSON TM-m30 Receipt",
    )
    assert settings.printer_for(pos=True) == "EPSON TM-m30 Receipt"
    assert settings.printer_for(pos=False) == "HP LaserJet"


def test_evolve_leaves_the_original_alone() -> None:
    base = PrintSettings()
    changed = base.evolve(mode=PrintMode.DIRECT)
    assert changed.mode is PrintMode.DIRECT
    assert base.mode is PrintMode.DIALOG


# -- a printer slot left unassigned ------------------------------------------


def test_an_unassigned_printer_is_not_the_windows_default(
    tmp_path: Path, store: SettingsStore
) -> None:
    """The two mean opposite things and must not collapse into each other.

    An empty name asks Windows which device to use; ``NO_PRINTER`` says not to
    print at all. Reading the stored ``null`` with ``or`` would turn the second
    into the first - and on a till the Windows default is the receipt roll, the
    one device an unassigned A4 slot exists to keep reports away from.
    """
    store.set_printing(
        PrintSettings(report_printer_name=NO_PRINTER, pos_printer_name="EPSON TM-m30")
    )

    raw = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert raw["printing"]["report_printer_name"] is None

    reloaded = SettingsStore(tmp_path / "settings.json")
    reloaded.load()
    assert reloaded.printing.report_printer_name is NO_PRINTER
    assert reloaded.printing.report_printer_name != ""
    assert reloaded.printing.printer_for(pos=False) is NO_PRINTER
    assert reloaded.printing.printer_for(pos=True) == "EPSON TM-m30"


def test_a_missing_printer_key_is_still_the_windows_default(tmp_path: Path) -> None:
    """Only an explicit null unassigns; an older file has no opinion at all."""
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"version": SETTINGS_VERSION, "printing": {"mode": "direct"}}),
        encoding="utf-8",
    )
    store = SettingsStore(path)
    store.load()
    assert store.printing.report_printer_name == ""
    assert store.printing.pos_printer_name == ""


def test_the_saving_mode_round_trips(tmp_path: Path, store: SettingsStore) -> None:
    """A new mode value an older build cannot read still falls back cleanly."""
    store.set_printing(PrintSettings(mode=PrintMode.SAVE))
    raw = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert raw["printing"]["mode"] == "save"

    reloaded = SettingsStore(tmp_path / "settings.json")
    reloaded.load()
    assert reloaded.printing.mode is PrintMode.SAVE
    assert reloaded.printing.mode.prints is False
    assert reloaded.printing.prints_reports() is False
