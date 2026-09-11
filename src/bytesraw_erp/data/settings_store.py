"""Application settings, stored locally as JSON.

Separate from :mod:`bytesraw_erp.data.account_store` because the two answer
different questions. An account is "which Odoo, as whom"; settings are "how
this machine behaves" - which printer is attached, whether reports should reach
paper on their own. Settings therefore survive deleting every account.

Written atomically, like the account registry, so an interrupted save cannot
leave a truncated file behind.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from bytesraw_erp.constants import SETTINGS_VERSION
from bytesraw_erp.core.errors import ConfigError
from bytesraw_erp.core.paths import settings_file
from bytesraw_erp.data.models import PrintSettings

_log = logging.getLogger(__name__)


class SettingsStore:
    """Loads and saves :class:`PrintSettings` (and future sections) as JSON.

    A missing or unreadable file yields defaults rather than an error: the app
    must still start when its settings file is damaged, and the user can then
    fix the values on the settings page.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or settings_file()
        self._printing = PrintSettings()
        self._loaded = False

    # -- lifecycle ---------------------------------------------------------

    def load(self) -> None:
        self._printing = PrintSettings()
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

    def save(self) -> None:
        payload = {
            "version": SETTINGS_VERSION,
            "printing": self._printing.to_dict(),
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

    @property
    def path(self) -> Path:
        return self._path
