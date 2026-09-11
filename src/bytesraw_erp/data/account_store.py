"""The account registry: metadata in JSON, passwords in the OS vault.

Split rationale
---------------
``accounts.json`` holds only non-secret metadata (URL, database, login, last
visited path). Passwords go to :mod:`keyring`, which on Windows resolves to
Windows Credential Manager - so a stolen config file yields no credentials, and
the OS handles encryption at rest.

Writes are atomic (temp file + ``os.replace``) so an interrupted save can never
leave a half-written registry behind.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import keyring
from keyring.errors import KeyringError

from bytesraw_erp.constants import CONFIG_VERSION, KEYRING_SERVICE
from bytesraw_erp.core.errors import ConfigError, CredentialError
from bytesraw_erp.core.paths import config_file
from bytesraw_erp.data.models import Account

_log = logging.getLogger(__name__)


class AccountStore:
    """Loads, mutates and persists the set of configured Odoo accounts.

    The store is the single source of truth for "do we have any account?",
    which is what decides whether the app opens on the login page or goes
    straight to Odoo.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or config_file()
        self._accounts: list[Account] = []
        self._active_id: str | None = None
        self._loaded = False

    # -- lifecycle ---------------------------------------------------------

    def load(self) -> None:
        """Read the registry from disk. A missing file is an empty registry."""
        self._accounts = []
        self._active_id = None
        self._loaded = True

        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"Could not read {self._path.name}: {exc}") from exc

        version = int(raw.get("version", 0))
        if version > CONFIG_VERSION:
            raise ConfigError(
                f"{self._path.name} was written by a newer version of "
                f"Bytesraw ERP (format {version}). Please update the app."
            )

        self._accounts = [Account.from_dict(item) for item in raw.get("accounts", [])]
        active = raw.get("active_id")
        self._active_id = active if any(a.id == active for a in self._accounts) else None

    def save(self) -> None:
        payload = {
            "version": CONFIG_VERSION,
            "active_id": self._active_id,
            "accounts": [account.to_dict() for account in self._accounts],
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

    # -- queries -----------------------------------------------------------

    @property
    def accounts(self) -> list[Account]:
        self._require_loaded()
        return list(self._accounts)

    @property
    def is_empty(self) -> bool:
        self._require_loaded()
        return not self._accounts

    def get(self, account_id: str) -> Account | None:
        self._require_loaded()
        return next((a for a in self._accounts if a.id == account_id), None)

    @property
    def active(self) -> Account | None:
        """The account to open on launch: the last used one, else the first."""
        self._require_loaded()
        if self._active_id:
            found = self.get(self._active_id)
            if found:
                return found
        return self._accounts[0] if self._accounts else None

    def find_duplicate(self, account: Account) -> Account | None:
        """An account is a duplicate when host + database + login all match."""
        self._require_loaded()
        return next(
            (
                other
                for other in self._accounts
                if other.id != account.id
                and other.url == account.url
                and other.database == account.database
                and other.login.lower() == account.login.lower()
            ),
            None,
        )

    # -- mutations ---------------------------------------------------------

    def upsert(self, account: Account, password: str | None = None) -> Account:
        """Insert or replace ``account``, optionally (re)storing its password."""
        self._require_loaded()
        index = next((i for i, a in enumerate(self._accounts) if a.id == account.id), None)
        if index is None:
            self._accounts.append(account)
        else:
            self._accounts[index] = account
        if password is not None:
            self.set_password(account.id, password)
        self.save()
        return account

    def remove(self, account_id: str) -> None:
        self._require_loaded()
        self._accounts = [a for a in self._accounts if a.id != account_id]
        if self._active_id == account_id:
            self._active_id = None
        self.delete_password(account_id)
        self.save()

    def set_active(self, account_id: str) -> None:
        self._require_loaded()
        if self.get(account_id) is None:
            return
        self._active_id = account_id
        self.save()

    def remember_path(self, account_id: str, path: str) -> None:
        """Persist the last Odoo path so the next launch resumes where we left.

        Called on every in-app navigation, so it is a no-op when unchanged to
        avoid rewriting the registry on each click.
        """
        self._require_loaded()
        account = self.get(account_id)
        if account is None or account.last_path == path:
            return
        self.upsert(account.evolve(last_path=path))

    # -- credentials -------------------------------------------------------

    def get_password(self, account_id: str) -> str | None:
        try:
            return keyring.get_password(KEYRING_SERVICE, account_id)
        except KeyringError as exc:
            raise CredentialError(f"Could not read the saved password: {exc}") from exc

    def set_password(self, account_id: str, password: str) -> None:
        try:
            keyring.set_password(KEYRING_SERVICE, account_id, password)
        except KeyringError as exc:
            raise CredentialError(f"Could not save the password securely: {exc}") from exc

    def delete_password(self, account_id: str) -> None:
        try:
            keyring.delete_password(KEYRING_SERVICE, account_id)
        except KeyringError:
            # Nothing stored for this id, or the vault is unavailable. Removing
            # the account must succeed either way.
            _log.debug("No stored password to delete for account %s", account_id)
