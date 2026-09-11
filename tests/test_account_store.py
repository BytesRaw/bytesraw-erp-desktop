"""Account registry tests.

The OS credential vault is stubbed so the suite never writes to the real
Windows Credential Manager.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bytesraw_erp.core.errors import ConfigError
from bytesraw_erp.data import account_store as store_module
from bytesraw_erp.data.account_store import AccountStore
from bytesraw_erp.data.models import Account


@pytest.fixture(autouse=True)
def fake_vault(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Replace :mod:`keyring` with an in-memory dict."""
    vault: dict[str, str] = {}

    class _FakeKeyring:
        @staticmethod
        def get_password(service: str, key: str) -> str | None:
            return vault.get(f"{service}/{key}")

        @staticmethod
        def set_password(service: str, key: str, password: str) -> None:
            vault[f"{service}/{key}"] = password

        @staticmethod
        def delete_password(service: str, key: str) -> None:
            vault.pop(f"{service}/{key}", None)

    monkeypatch.setattr(store_module, "keyring", _FakeKeyring)
    return vault


@pytest.fixture
def store(tmp_path: Path) -> AccountStore:
    instance = AccountStore(tmp_path / "accounts.json")
    instance.load()
    return instance


def _account(**overrides: object) -> Account:
    defaults = {"url": "https://erp.example.com", "database": "prod", "login": "admin"}
    return Account(**{**defaults, **overrides})  # type: ignore[arg-type]


def test_missing_file_is_an_empty_registry(store: AccountStore) -> None:
    assert store.is_empty
    assert store.active is None


def test_upsert_persists_and_reloads(tmp_path: Path, store: AccountStore) -> None:
    account = store.upsert(_account(), password="secret")

    reloaded = AccountStore(tmp_path / "accounts.json")
    reloaded.load()
    assert [a.id for a in reloaded.accounts] == [account.id]
    assert reloaded.get(account.id).login == "admin"


def test_password_goes_to_the_vault_not_the_file(
    tmp_path: Path, store: AccountStore, fake_vault: dict[str, str]
) -> None:
    account = store.upsert(_account(), password="hunter2")

    on_disk = (tmp_path / "accounts.json").read_text(encoding="utf-8")
    assert "hunter2" not in on_disk
    assert store.get_password(account.id) == "hunter2"
    assert "hunter2" in fake_vault.values()


def test_upsert_replaces_rather_than_duplicates(store: AccountStore) -> None:
    account = store.upsert(_account(), password="x")
    store.upsert(account.evolve(login="other"), password="x")
    assert len(store.accounts) == 1
    assert store.get(account.id).login == "other"


def test_remove_clears_the_password(store: AccountStore, fake_vault: dict[str, str]) -> None:
    account = store.upsert(_account(), password="x")
    store.remove(account.id)
    assert store.is_empty
    assert not fake_vault


def test_active_falls_back_to_the_first_account(store: AccountStore) -> None:
    first = store.upsert(_account(), password="x")
    store.upsert(_account(login="second"), password="x")
    assert store.active.id == first.id


def test_set_active_is_honoured(store: AccountStore) -> None:
    store.upsert(_account(), password="x")
    second = store.upsert(_account(login="second"), password="x")
    store.set_active(second.id)
    assert store.active.id == second.id


def test_active_ignores_a_stale_id(tmp_path: Path, store: AccountStore) -> None:
    account = store.upsert(_account(), password="x")
    store.set_active(account.id)
    store.remove(account.id)
    store.upsert(_account(login="other"), password="x")

    reloaded = AccountStore(tmp_path / "accounts.json")
    reloaded.load()
    assert reloaded.active.login == "other"


def test_find_duplicate_matches_host_database_and_login(store: AccountStore) -> None:
    store.upsert(_account(), password="x")
    twin = _account(login="ADMIN")
    assert store.find_duplicate(twin) is not None


def test_find_duplicate_ignores_a_different_database(store: AccountStore) -> None:
    store.upsert(_account(), password="x")
    assert store.find_duplicate(_account(database="staging")) is None


def test_remember_path_skips_an_unchanged_value(
    tmp_path: Path, store: AccountStore
) -> None:
    account = store.upsert(_account(), password="x")
    path = tmp_path / "accounts.json"
    before = path.stat().st_mtime_ns

    store.remember_path(account.id, account.last_path)
    assert path.stat().st_mtime_ns == before

    store.remember_path(account.id, "/odoo/sales/9")
    assert store.get(account.id).last_path == "/odoo/sales/9"


def test_corrupt_file_raises_a_readable_error(tmp_path: Path) -> None:
    path = tmp_path / "accounts.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"accounts\.json"):
        AccountStore(path).load()


def test_future_format_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps({"version": 99, "accounts": []}), encoding="utf-8")
    with pytest.raises(ConfigError, match="newer version"):
        AccountStore(path).load()
