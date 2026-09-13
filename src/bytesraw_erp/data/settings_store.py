"""Application settings, stored locally as JSON.

Separate from :mod:`bytesraw_erp.data.account_store` because the two answer
different questions. An account is "which Odoo, as whom"; settings are "how
this machine behaves" - which printer is attached, whether reports should reach
paper on their own. Settings therefore survive deleting every account.

Written atomically, like the account registry, so an interrupted save cannot
leave a truncated file behind.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bytesraw_erp.constants import SETTINGS_VERSION
from bytesraw_erp.core.errors import ConfigError
from bytesraw_erp.core.paths import settings_file
from bytesraw_erp.data.models import (
    DisplaySettings,
    PrintSettings,
    UpdateSettings,
    new_install_id,
)

_log = logging.getLogger(__name__)


class SettingsStore:
    """Loads and saves :class:`PrintSettings` and :class:`DisplaySettings` as JSON.

    A missing or unreadable file yields defaults rather than an error: the app
    must still start when its settings file is damaged, and the user can then
    fix the values on the settings page.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or settings_file()
        self._printing = PrintSettings()
        self._display = DisplaySettings()
        self._updates = UpdateSettings()
        self._loaded = False

    # -- lifecycle ---------------------------------------------------------

    def load(self) -> None:
        self._printing = PrintSettings()
        self._display = DisplaySettings()
        self._updates = UpdateSettings()
        self._loaded = True

        if not self._path.exists():
            return
        try:
            raw: dict[str, Any] = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # Deliberately not fatal - see the class docstring.
            _log.warning("Ignoring unreadable %s: %s", self._path.name, exc)
            return

        version = int(raw.get("version", 0))
        if version > SETTINGS_VERSION:
            _log.warning(
                "%s was written by a newer version (format %d); using defaults",
                self._path.name,
                version,
            )
            return

        self._printing = PrintSettings.from_dict(raw.get("printing") or {})
        self._display = DisplaySettings.from_dict(raw.get("display") or {})
        self._updates = UpdateSettings.from_dict(raw.get("updates") or {})

    def save(self) -> None:
        payload = {
            "version": SETTINGS_VERSION,
            "printing": self._printing.to_dict(),
            "display": self._display.to_dict(),
            "updates": self._updates.to_dict(),
        }
        tmp = self._path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError as exc:
            raise ConfigError(f"Could not save {self._path.name}: {exc}") from exc
        finally:
            tmp.unlink(missing_ok=True)

    def _require_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # -- printing ----------------------------------------------------------

    @property
    def printing(self) -> PrintSettings:
        self._require_loaded()
        return self._printing

    def set_printing(self, settings: PrintSettings) -> None:
        self._require_loaded()
        if settings == self._printing:
            return
        self._printing = settings
        self.save()
        _log.info(
            "Print settings: mode=%s printer=%s auto_print=%s",
            settings.mode.value,
            settings.printer_name or "<system default>",
            settings.auto_print_reports,
        )

    # -- display -----------------------------------------------------------

    @property
    def display(self) -> DisplaySettings:
        self._require_loaded()
        return self._display

    def set_display(self, settings: DisplaySettings) -> None:
        self._require_loaded()
        if settings == self._display:
            return
        self._display = settings
        self.save()
        _log.info("Display settings: render_mode=%s", settings.render_mode.value)

    # -- updates -----------------------------------------------------------

    @property
    def updates(self) -> UpdateSettings:
        self._require_loaded()
        return self._updates

    def set_updates(self, settings: UpdateSettings) -> None:
        self._require_loaded()
        if settings == self._updates:
            return
        self._updates = settings
        self.save()
        _log.info(
            "Update settings: automatic=%s channel=%s",
            settings.check_automatically,
            settings.channel.value,
        )

    def ensure_install_id(self) -> str:
        """This installation's rollout id, minting one on first use.

        Written here rather than in :meth:`load` because loading a settings
        file should not have the side effect of writing one, and the id is
        wanted only by the update service. A failure to persist it is not
        fatal: the rollout bucket is then re-rolled on the next launch, which
        is a worse staged rollout but not a broken app, and every other setting
        on the page would be lost if this raised.
        """
        self._require_loaded()
        if not self._updates.install_id:
            self._updates = self._updates.evolve(install_id=new_install_id())
            with contextlib.suppress(ConfigError):
                self.save()
        return self._updates.install_id

    def record_update_check(self) -> None:
        """Stamp "checked just now", so the settings page can say so.

        Separate from :meth:`set_updates` because this fires on a timer every
        few hours and must not log a line each time as though the user had
        changed something.
        """
        self._require_loaded()
        self._updates = self._updates.evolve(
            last_check=datetime.now(UTC).isoformat(timespec="seconds")
        )
        with contextlib.suppress(ConfigError):
            self.save()

    @property
    def path(self) -> Path:
        return self._path
