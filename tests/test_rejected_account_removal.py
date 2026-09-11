"""What happens to a saved account the server refuses to sign in.

An account whose password has been changed on the server, or whose user has
been deactivated, can never load - the app used to sit on an error screen with
the dead account still in the registry, re-failing on every launch. It is now
discarded, but *only* for a refusal the server actually issued: a refusal the
network invented must leave the account alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from bytesraw_erp.constants import ROUTE_ACCOUNT_NEW, ROUTE_ACCOUNTS
from bytesraw_erp.core.errors import (
    OdooAuthError,
    OdooConnectionError,
    OdooCredentialsRejected,
)
from bytesraw_erp.data import account_store as store_module
from bytesraw_erp.data.account_store import AccountStore
from bytesraw_erp.data.models import Account
from bytesraw_erp.ui.app_context import AppContext
from bytesraw_erp.ui.pages.odoo_page import OdooPage
from bytesraw_erp.ui.theme import ThemeController


@pytest.fixture(autouse=True)
def fake_vault(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
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


class _FakeRouter:
    """Records navigation instead of performing it."""

    def __init__(self) -> None:
        self.visited: list[str] = []

    def go(self, path: str, **_kwargs: object) -> bool:
        self.visited.append(path)
        return True

    def reset_to(self, path: str) -> bool:
        return self.go(path)

    def back(self) -> bool:
        return False


@pytest.fixture
def context(tmp_path: Path, qtbot) -> AppContext:
    instance = AppContext(ThemeController(QApplication.instance()))
    instance.store = AccountStore(tmp_path / "accounts.json")
    instance.store.load()
    return instance


@pytest.fixture
def page(context: AppContext, qtbot) -> tuple[OdooPage, _FakeRouter]:
    router = _FakeRouter()
    widget = OdooPage(context, router)  # type: ignore[arg-type]
    qtbot.addWidget(widget)
    return widget, router


def _save(context: AppContext, name: str = "Head office") -> Account:
    account = context.store.upsert(
        Account(url="https://erp.example.com", database="prod", login="admin", name=name),
        password="secret",
    )
    context.store.set_active(account.id)
    return account


def test_a_rejected_account_is_removed(
    page: tuple[OdooPage, _FakeRouter], context: AppContext, fake_vault: dict[str, str]
) -> None:
    account = _save(context)
    assert fake_vault

    page[0]._on_auth_failed(OdooCredentialsRejected("Odoo refused the login 'admin'."))

    assert context.store.get(account.id) is None
    assert context.store.is_empty
    assert fake_vault == {}, "the stored password goes with the account"


def test_removal_explains_itself_on_the_page_it_lands_on(
    page: tuple[OdooPage, _FakeRouter], context: AppContext
) -> None:
    """The page that removes the account is gone by the time the user reads this."""
    _save(context, name="Head office")

    page[0]._on_auth_failed(OdooCredentialsRejected("Odoo refused the login 'admin'."))

    assert page[1].visited == [ROUTE_ACCOUNT_NEW], "nothing left to list"
    notice = context.take_notice()
    assert notice is not None
    assert "Head office" in notice and "removed" in notice
    assert context.take_notice() is None, "shown once, not on every later visit"


def test_the_details_are_carried_into_the_form_but_not_the_password(
    page: tuple[OdooPage, _FakeRouter], context: AppContext
) -> None:
    account = _save(context)

    page[0]._on_auth_failed(OdooCredentialsRejected("refused"))

    prefill = context.take_prefill()
    assert prefill is not None
    assert (prefill.url, prefill.database, prefill.login) == (
        account.url,
        account.database,
        account.login,
    )


def test_the_list_is_offered_when_other_accounts_remain(
    page: tuple[OdooPage, _FakeRouter], context: AppContext
) -> None:
    context.store.upsert(
        Account(url="https://other.example.com", database="prod", login="admin"),
        password="other",
    )
    rejected = _save(context, name="Broken")

    page[0]._on_auth_failed(OdooCredentialsRejected("refused"))

    assert page[1].visited == [ROUTE_ACCOUNTS]
    assert context.store.get(rejected.id) is None
    assert len(context.store.accounts) == 1
    assert context.take_prefill() is None, "there is a list to choose from instead"


@pytest.mark.parametrize(
    "failure",
    [
        OdooConnectionError("Could not reach https://erp.example.com"),
        OdooAuthError("Odoo did not accept these credentials. ... two-factor ..."),
        RuntimeError("something else entirely"),
    ],
    ids=["offline", "two-factor", "unknown"],
)
def test_every_other_failure_keeps_the_account(
    page: tuple[OdooPage, _FakeRouter],
    context: AppContext,
    fake_vault: dict[str, str],
    failure: Exception,
) -> None:
    """Deleting on these would throw away a working account.

    Two-factor is the sharp one: the credentials are correct, Odoo simply wants
    another step, and the account must survive to reach it.
    """
    account = _save(context)

    page[0]._on_auth_failed(failure)

    assert context.store.get(account.id) is not None
    assert fake_vault, "the password is untouched"
    assert page[1].visited == [], "the user stays put, with the error on screen"
