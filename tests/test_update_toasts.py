"""The Odoo page's half of the update flow.

What is worth testing here is not the wording but the wiring: these handlers are
connected to signals carrying particular argument types, and a signature that
does not match fails at emit time rather than at import time - so the suite
would stay green while the toast never appeared on a real machine.

The other thing pinned here is that a download reports progress by *rewriting*
one toast. Showing a new one per progress signal would bury the screen and push
the user's other notifications off it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from bytesraw_erp.constants import APP_VERSION
from bytesraw_erp.data.models import UpdateChannel
from bytesraw_erp.data.settings_store import SettingsStore
from bytesraw_erp.services.update_service import Update, UpdateArtifact
from bytesraw_erp.ui.app_context import AppContext
from bytesraw_erp.ui.pages.odoo_page import OdooPage
from bytesraw_erp.ui.theme import ThemeController


class _FakeRouter:
    def go(self, path: str, **_kwargs: object) -> bool:
        return True

    def reset_to(self, path: str) -> bool:
        return True

    def back(self) -> bool:
        return False


def _update(version: str = "0.2.0", **overrides: object) -> Update:
    fields: dict[str, object] = {
        "version": version,
        "channel": UpdateChannel.STABLE,
        "artifact": UpdateArtifact(
            url="https://example.invalid/setup.exe",
            filename="BytesrawERP-setup.exe",
            sha256="0" * 64,
            size=146_036_024,
        ),
    }
    fields.update(overrides)
    return Update(**fields)  # type: ignore[arg-type]


@pytest.fixture
def context(tmp_path: Path, qtbot) -> AppContext:
    # The settings store is passed in rather than swapped afterwards: the
    # AppContext hands it to the UpdateService as it is built, so replacing the
    # attribute later would leave the service writing the real settings.json.
    settings = SettingsStore(tmp_path / "settings.json")
    settings.load()
    return AppContext(ThemeController(QApplication.instance()), settings=settings)


@pytest.fixture
def page(context: AppContext, qtbot) -> OdooPage:
    widget = OdooPage(context, _FakeRouter())  # type: ignore[arg-type]
    qtbot.addWidget(widget)
    return widget


def _messages(page: OdooPage) -> list[str]:
    from PySide6.QtWidgets import QLabel

    return [
        toast.findChild(QLabel).text()
        for toast in page._toasts._toasts
        if not toast.isHidden()
    ]


def test_an_available_update_is_offered_with_its_size(page: OdooPage, context: AppContext) -> None:
    context.updates.update_available.emit(_update())

    shown = _messages(page)
    assert len(shown) == 1
    assert "0.2.0" in shown[0]
    assert "139 MB" in shown[0]


def test_a_required_update_says_so(page: OdooPage, context: AppContext) -> None:
    """It is still the user's click - a till mid-sale is not interrupted."""
    context.updates.update_available.emit(_update(minimum_supported="0.2.0"))
    assert "required" in _messages(page)[0]


def test_an_optional_update_does_not_claim_to_be_required(
    page: OdooPage, context: AppContext
) -> None:
    context.updates.update_available.emit(_update(minimum_supported=APP_VERSION))
    assert "required" not in _messages(page)[0]


def test_the_offer_carries_an_action(page: OdooPage, context: AppContext) -> None:
    from PySide6.QtWidgets import QPushButton

    context.updates.update_available.emit(_update())
    toast = page._toasts._toasts[0]
    assert toast.findChild(QPushButton) is not None


def test_progress_rewrites_one_toast_rather_than_stacking(
    page: OdooPage, context: AppContext
) -> None:
    context.updates.download_started.emit(_update())
    for received in (10_000_000, 70_000_000, 140_000_000):
        context.updates.download_progress.emit(received, 140_000_000)

    shown = _messages(page)
    assert len(shown) == 1
    assert "100%" in shown[0]


def test_the_install_step_says_the_app_will_reopen(page: OdooPage, context: AppContext) -> None:
    """A till that closes itself with no warning reads as a crash."""
    context.updates.download_started.emit(_update())
    context.updates.download_finished.emit(Path("C:/temp/setup.exe"))

    shown = _messages(page)
    assert len(shown) == 1
    assert "close and reopen" in shown[0]


def test_a_failure_replaces_the_download_toast(page: OdooPage, context: AppContext) -> None:
    """The stale "Downloading..." must not sit next to the reason it stopped."""
    context.updates.download_started.emit(_update())
    context.updates.download_failed.emit("The update did not match its checksum.")

    shown = _messages(page)
    assert shown == ["The update did not match its checksum."]


def test_a_failed_check_is_never_toasted(page: OdooPage, context: AppContext) -> None:
    """A till between access points fails one every four hours."""
    context.updates.check_failed.emit("Could not reach the update service.")
    assert _messages(page) == []


def test_being_up_to_date_is_silent(page: OdooPage, context: AppContext) -> None:
    context.updates.up_to_date.emit()
    assert _messages(page) == []
