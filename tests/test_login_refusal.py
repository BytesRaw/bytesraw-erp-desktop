"""Telling "these details are wrong" apart from every other sign-in failure.

Odoo does not answer a bad password with a falsy ``uid``; it raises, and the
fault arrives as a JSON-RPC ``error`` member. Measured against Odoo 19 at
``https://demo.fatoora.cloud``:

===================  ==============================  ==================
sent                 ``error.data.name``             ``message``
===================  ==============================  ==================
wrong password       ``odoo.exceptions.AccessDenied``  Access Denied
unknown login        ``odoo.exceptions.AccessDenied``  Access Denied
unknown database     ``odoo.exceptions.AccessError``   Database not found.
===================  ==============================  ==================

Only :class:`OdooCredentialsRejected` lets the UI discard an account, so what
is classified as one is load-bearing: a false positive deletes a working
account the moment the network hiccups.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from bytesraw_erp.core.errors import (
    OdooAuthError,
    OdooConnectionError,
    OdooCredentialsRejected,
    OdooRpcError,
)
from bytesraw_erp.services.odoo_client import OdooClient


def _client(handler) -> OdooClient:
    client = OdooClient("https://erp.example.com")
    client._client = httpx.Client(
        base_url="https://erp.example.com",
        transport=httpx.MockTransport(handler),
    )
    return client


def _fault(name: str, message: str) -> Any:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "error": {
                    "code": 200,
                    "message": "Odoo Server Error",
                    "data": {"name": name, "message": message, "debug": "traceback"},
                },
            },
        )

    return handler


def _result(payload: dict[str, Any]) -> Any:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": payload})

    return handler


def test_a_wrong_password_is_a_rejection() -> None:
    client = _client(_fault("odoo.exceptions.AccessDenied", "Access Denied"))
    with pytest.raises(OdooCredentialsRejected) as caught:
        client.authenticate("demo", "demo", "wrong")
    # The raw "Access Denied" is not something to show a user.
    assert "demo" in str(caught.value)
    assert "Access Denied" not in str(caught.value)


def test_an_unknown_database_is_a_rejection() -> None:
    client = _client(_fault("odoo.exceptions.AccessError", "Database not found."))
    with pytest.raises(OdooCredentialsRejected, match="database name"):
        client.authenticate("typo", "demo", "demo")


def test_the_message_is_not_what_is_matched_on() -> None:
    """Odoo translates the message; only ``data.name`` is stable.

    A server whose context is Arabic - as the development target's is - returns
    Arabic text for exactly the same fault.
    """
    client = _client(_fault("odoo.exceptions.AccessDenied", "تم رفض الوصول"))
    with pytest.raises(OdooCredentialsRejected):
        client.authenticate("demo", "demo", "wrong")


def test_two_factor_is_not_a_rejection() -> None:
    """Odoo answers ``uid: None`` with no fault. The stored details are fine.

    Classifying this as a rejection would delete a perfectly good account the
    first time someone turned on two-factor authentication.
    """
    client = _client(_result({"uid": None}))
    with pytest.raises(OdooAuthError) as caught:
        client.authenticate("demo", "demo", "right")
    assert not isinstance(caught.value, OdooCredentialsRejected)
    assert "two-factor" in str(caught.value)


def test_a_server_error_is_not_a_rejection() -> None:
    """An internal error says nothing about the credentials."""
    client = _client(_fault("builtins.ValueError", "something broke"))
    with pytest.raises(OdooRpcError) as caught:
        client.authenticate("demo", "demo", "right")
    assert not isinstance(caught.value, OdooCredentialsRejected)


def test_an_unreachable_server_is_not_a_rejection() -> None:
    """The one that matters most: a dropped network must not delete an account."""

    def refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = _client(refuse)
    with pytest.raises(OdooConnectionError) as caught:
        client.authenticate("demo", "demo", "right")
    assert not isinstance(caught.value, OdooCredentialsRejected)


def test_the_odoo_exception_name_is_carried_through() -> None:
    client = _client(_fault("odoo.exceptions.UserError", "nope"))
    with pytest.raises(OdooRpcError) as caught:
        client.call_kw("res.partner", "read")
    assert caught.value.name == "odoo.exceptions.UserError"
    assert caught.value.debug == "traceback"
