r"""Filesystem locations, resolved through Qt's platform-correct standard paths.

On Windows this puts everything under
``%APPDATA%\BytesRaw\Bytesraw ERP`` (config) and
``%LOCALAPPDATA%\BytesRaw\Bytesraw ERP\cache`` (web cache), which is what
users expect and what roaming profiles handle correctly.

Every function creates the directory it returns, so callers never have to.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QStandardPaths

_Location = QStandardPaths.StandardLocation


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    """Roaming application data: config, per-account web profiles, logs."""
    return _ensure(Path(QStandardPaths.writableLocation(_Location.AppDataLocation)))


def cache_dir() -> Path:
    """Machine-local cache. Safe to delete; never holds anything of record."""
    return _ensure(Path(QStandardPaths.writableLocation(_Location.CacheLocation)))


def downloads_dir() -> Path:
    """Where files downloaded from inside the embedded Odoo client land."""
    return _ensure(Path(QStandardPaths.writableLocation(_Location.DownloadLocation)))


def config_file() -> Path:
    """Account metadata. Never contains a password - those live in the vault."""
    return data_dir() / "accounts.json"


def settings_file() -> Path:
    """Application settings: printing, and anything else machine-scoped."""
    return data_dir() / "settings.json"


def reports_dir() -> Path:
    """Scratch space for QWeb report PDFs intercepted on their way to a printer.

    Lives under the cache directory because a report that has been printed is
    not a document of record - the copy the user keeps, if they asked for one,
    goes to Downloads instead.
    """
    return _ensure(cache_dir() / "reports")


def logs_dir() -> Path:
    return _ensure(data_dir() / "logs")


def profile_storage_dir(account_id: str) -> Path:
    """Persistent QtWebEngine storage (cookies, localStorage) for one account.

    Each account gets its own directory so two Odoo sessions - even on the same
    host - never share a cookie jar.
    """
    return _ensure(data_dir() / "profiles" / account_id)


def profile_cache_dir(account_id: str) -> Path:
    return _ensure(cache_dir() / "profiles" / account_id)
