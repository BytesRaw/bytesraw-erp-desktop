"""Losing an Odoo session, and getting it back without asking anyone.

Two halves, and the seam between them is the part worth pinning.

The client half has to *recognise* the fault. Odoo answers an expired session
with a JSON-RPC error whose only untranslated member is
``data.name`` - the message is translated, and the development target runs in
Arabic - so a classifier that reads the message is a classifier that works on
one server.

The page half has to recover from it without doing any of the three things that
would be worse than the expiry: asking for a password that is already in the
vault, deleting an account the server never rejected, or signing itself in over
and over because each attempt lands back on the login page.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pytest
from PySide6.QtWidgets import QApplication

from bytesraw_erp.constants import SESSION_EXPIRED_NAME
from bytesraw_erp.core.errors import (
    OdooConnectionError,
    OdooRpcError,
    OdooSessionExpired,
)
from bytesraw_erp.data import account_store as store_module
from bytesraw_erp.data.account_store import AccountStore
from bytesraw_erp.data.models import Account, SessionContext
from bytesraw_erp.services.odoo_client import OdooClient
from bytesraw_erp.ui import app_context as app_context_module
from bytesraw_erp.ui.app_context import AppContext
from bytesraw_erp.ui.pages import odoo_page as odoo_page_module
from bytesraw_erp.ui.pages.odoo_page import OdooPage
from bytesraw_erp.ui.theme import ThemeController

# -- the client half ---------------------------------------------------------


def _fault(name: str, message: str, code: int = 100) -> dict[str, Any]:
    """The shape Odoo's JSON dispatcher puts on the wire (``odoo/http.py``)."""
    return {
        "code": code,
        "message": "Odoo Session Expired",
        "data": {"name": name, "message": message, "debug": ""},
    }


def test_an_expired_session_is_recognised_by_its_class_name() -> None:
    error = OdooClient._as_rpc_error(_fault(SESSION_EXPIRED_NAME, "Session expired"))
    assert isinstance(error, OdooSessionExpired)


def test_a_translated_message_does_not_hide_it() -> None:
    """The demo target runs in Arabic. ``data.name`` is what survives that."""
    error = OdooClient._as_rpc_error(_fault(SESSION_EXPIRED_NAME, "انتهت الجلسة"))
    assert isinstance(error, OdooSessionExpired)


def test_odoos_own_code_is_enough_on_its_own() -> None:
    """Code 100 is assigned to this fault and to nothing else."""
    error = OdooClient._as_rpc_error(_fault("", "whatever", code=100))
    assert isinstance(error, OdooSessionExpired)


def test_an_ordinary_fault_stays_an_ordinary_fault() -> None:
    error = OdooClient._as_rpc_error(
        _fault("odoo.exceptions.UserError", "You cannot delete this.", code=0)
    )
    assert isinstance(error, OdooRpcError)
    assert not isinstance(error, OdooSessionExpired)
    assert error.name == "odoo.exceptions.UserError"


def test_an_expired_session_is_not_a_rejected_account() -> None:
    """The distinction the account-removal branch depends on.

    ``OdooCredentialsRejected`` deletes the account. An expiry must never be
    mistaken for one: the stored details are still correct.
    """
    from bytesraw_erp.core.errors import OdooAuthError, OdooCredentialsRejected

    error = OdooClient._as_rpc_error(_fault(SESSION_EXPIRED_NAME, "Session expired"))
    assert isinstance(error, OdooAuthError)
    assert not isinstance(error, OdooCredentialsRejected)


# -- the page half -----------------------------------------------------------


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
    def __init__(self) -> None:
        self.visited: list[str] = []

    def go(self, path: str, **_kwargs: object) -> bool:
        self.visited.append(path)
        return True

    def reset_to(self, path: str) -> bool:
        return self.go(path)

    def back(self) -> bool:
        return False


class _FakeClient:
    """Stands in for a live :class:`OdooClient` without any transport."""

    session_id = "sid"
    cookies: ClassVar[dict[str, str]] = {}

    def close(self) -> None:
        pass


def _session() -> SessionContext:
    return SessionContext(
        uid=2,
        user_name="Mitchell Admin",
        login="demo",
        database="demo",
        language="en_US",
        server_version="19.0",
        companies=(),
        current_company_id=0,
    )


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """Run every ``run_async`` submission inline, and record it.

    The recovery path is three signals deep - a detector, a background
    sign-in, a resume - and testing it against a real thread pool would test
    the pool. What matters here is which call was made and what was done with
    its answer.
    """
    made: list[tuple[Any, ...]] = []

    def fake_run_async(
        fn: Any,
        *args: Any,
        on_success: Any = None,
        on_error: Any = None,
        **kwargs: Any,
    ) -> None:
        made.append((fn, args))
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # mirrors Task.run
            if on_error is not None:
                on_error(exc)
        else:
            if on_success is not None:
                on_success(result)

    monkeypatch.setattr(odoo_page_module, "run_async", fake_run_async)
    return made


@pytest.fixture
def context(tmp_path: Path, qtbot) -> AppContext:
    instance = AppContext(ThemeController(QApplication.instance()))
    instance.store = AccountStore(tmp_path / "accounts.json")
    instance.store.load()
    return instance


@pytest.fixture
def page(context: AppContext, calls: list[Any], qtbot) -> tuple[OdooPage, _FakeRouter]:
    router = _FakeRouter()
    widget = OdooPage(context, router)  # type: ignore[arg-type]
    qtbot.addWidget(widget)
    return widget, router


def _save(context: AppContext) -> Account:
    account = context.store.upsert(
        Account(url="https://erp.example.com", database="prod", login="admin", name="Till"),
        password="secret",
    )
    context.store.set_active(account.id)
    return account


@pytest.fixture
def signed_in(
    monkeypatch: pytest.MonkeyPatch, context: AppContext
) -> list[tuple[str, str]]:
    """Make ``open_session`` succeed, recording the credentials it was given."""
    attempts: list[tuple[str, str]] = []

    def fake_open_session(account: Account, password: str) -> tuple[Any, SessionContext]:
        attempts.append((account.login, password))
        return _FakeClient(), _session()

    monkeypatch.setattr(odoo_page_module, "open_session", fake_open_session)
    # ``adopt_session`` would otherwise reach QtWebEngine's cookie store.
    monkeypatch.setattr(
        app_context_module.ProfileManager,
        "inject_session",
        lambda *_args, **_kwargs: None,
    )
    return attempts


def test_the_login_page_is_read_as_an_expiry(
    page: tuple[OdooPage, _FakeRouter],
    context: AppContext,
    signed_in: list[tuple[str, str]],
) -> None:
    """The redirect to /web/login is what the user sees first, so it is a signal."""
    _save(context)
    widget = page[0]
    widget._on_path_changed("/odoo/sales/12")

    widget._on_path_changed("/web/login?redirect=%2Fodoo")

    assert signed_in == [("admin", "secret")], "signed back in, unprompted"
    assert context.session is not None


def test_the_login_page_is_never_remembered(
    page: tuple[OdooPage, _FakeRouter],
    context: AppContext,
    signed_in: list[tuple[str, str]],
) -> None:
    """Storing it would make the login screen the landing page next launch."""
    account = _save(context)
    widget = page[0]
    widget._on_path_changed("/odoo/sales/12")

    widget._on_path_changed("/web/login")

    stored = context.store.get(account.id)
    assert stored is not None
    assert stored.last_path == "/odoo/sales/12"


def test_recovery_returns_the_user_to_the_page_they_were_on(
    page: tuple[OdooPage, _FakeRouter],
    context: AppContext,
    signed_in: list[tuple[str, str]],
) -> None:
    _save(context)
    widget = page[0]
    widget._on_path_changed("/odoo/sales/12")

    widget._on_path_changed("/web/login")

    assert widget._resume_path is None, "consumed by the resume"
    assert widget._last_path == "/odoo/sales/12"


def test_an_rpc_fault_recovers_the_same_way(
    page: tuple[OdooPage, _FakeRouter],
    context: AppContext,
    signed_in: list[tuple[str, str]],
) -> None:
    """A background call can notice the expiry before any navigation does."""
    _save(context)

    assert page[0]._on_session_expired(OdooSessionExpired("expired")) is True
    assert signed_in == [("admin", "secret")]


def test_an_unrelated_failure_is_left_to_its_caller(
    page: tuple[OdooPage, _FakeRouter],
    context: AppContext,
    signed_in: list[tuple[str, str]],
) -> None:
    _save(context)

    assert page[0]._on_session_expired(OdooConnectionError("offline")) is False
    assert signed_in == []


def test_recovery_is_attempted_once_not_in_a_loop(
    page: tuple[OdooPage, _FakeRouter],
    context: AppContext,
    signed_in: list[tuple[str, str]],
) -> None:
    """A server that bounces the fresh session too would otherwise spin forever."""
    _save(context)
    widget = page[0]

    widget._on_path_changed("/web/login")
    widget._on_path_changed("/web/login")
    widget._on_path_changed("/web/login")

    assert len(signed_in) == 1
    assert "keeps expiring" in widget._status_banner.text()


def test_the_account_survives_an_expiry(
    page: tuple[OdooPage, _FakeRouter],
    context: AppContext,
    fake_vault: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even when signing in again fails, the details were never the problem."""
    account = _save(context)

    def refuse(_account: Account, _password: str) -> None:
        raise OdooSessionExpired("The Odoo session has expired.")

    monkeypatch.setattr(odoo_page_module, "open_session", refuse)
    page[0]._on_path_changed("/web/login")

    assert context.store.get(account.id) is not None
    assert fake_vault, "the password is untouched"


def test_the_probe_stays_quiet_when_the_server_is_merely_unreachable(
    page: tuple[OdooPage, _FakeRouter],
    context: AppContext,
    signed_in: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A till between access points is not a till that lost its session."""
    _save(context)
    context.adopt_session(context.store.active, _FakeClient(), _session())  # type: ignore[arg-type]
    signed_in.clear()

    def unreachable(_client: Any) -> None:
        raise OdooConnectionError("Could not reach https://erp.example.com")

    monkeypatch.setattr(odoo_page_module, "probe_session", unreachable)
    page[0]._probe_session()

    assert signed_in == [], "no sign-in, no error screen, nothing"


def test_the_probe_recovers_a_session_the_server_has_forgotten(
    page: tuple[OdooPage, _FakeRouter],
    context: AppContext,
    signed_in: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The idle case: nothing navigated, nothing failed, the session just went."""
    _save(context)
    context.adopt_session(context.store.active, _FakeClient(), _session())  # type: ignore[arg-type]
    signed_in.clear()

    def expired(_client: Any) -> None:
        raise OdooSessionExpired("The Odoo session has expired.")

    monkeypatch.setattr(odoo_page_module, "probe_session", expired)
    page[0]._probe_session()

    assert signed_in == [("admin", "secret")]
