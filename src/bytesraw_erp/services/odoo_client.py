"""A minimal Odoo 19 JSON-RPC client.

Only the handful of endpoints the desktop shell actually needs are wrapped.
The client keeps its own cookie jar; after :meth:`authenticate` succeeds the
``session_id`` cookie can be handed to QtWebEngine via
:mod:`bytesraw_erp.services.profile_manager`, so the RPC client and the embedded
browser share a single Odoo session rather than logging in twice.

Odoo 19 note: RPC routes are declared ``type='jsonrpc'`` (renamed from ``json``
in earlier versions). The request/response envelope is unchanged.
"""

from __future__ import annotations

import itertools
import logging
from typing import Any

import httpx

from bytesraw_erp.constants import (
    COLOR_SCHEME_COOKIE,
    COLOR_SCHEME_FIELD,
    DARK_BUNDLE_MARKER,
    ODOO_HOME_PATH,
    RPC_AUTHENTICATE,
    RPC_CALL_KW,
    RPC_DB_LIST,
    RPC_SESSION_INFO,
    RPC_TIMEOUT,
    SESSION_COOKIE,
    SESSION_EXPIRED_CODE,
    SESSION_EXPIRED_NAME,
    USER_SETTINGS_MODEL,
)
from bytesraw_erp.core.errors import (
    BytesrawError,
    OdooAuthError,
    OdooConnectionError,
    OdooCredentialsRejected,
    OdooRpcError,
    OdooSessionExpired,
)

_log = logging.getLogger(__name__)


class OdooClient:
    """Synchronous JSON-RPC client for one Odoo server.

    Intended to be driven from a worker thread - every call blocks. The UI
    layer wraps it in :class:`~bytesraw_erp.services.tasks.Task`.
    """

    def __init__(self, base_url: str, *, verify_tls: bool = True) -> None:
        self.base_url = base_url.rstrip("/")
        self._ids = itertools.count(1)
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=RPC_TIMEOUT,
            verify=verify_tls,
            follow_redirects=True,
            headers={"User-Agent": "BytesrawERP/1.0 (+https://bytesraw.com)"},
        )

    # -- context management ------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OdooClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- transport ---------------------------------------------------------

    def _rpc(self, path: str, params: dict[str, Any]) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": params,
            "id": next(self._ids),
        }
        try:
            response = self._client.post(path, json=payload)
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            raise OdooConnectionError(
                f"{self.base_url} answered HTTP {exc.response.status_code} for {path}."
            ) from exc
        except httpx.HTTPError as exc:
            raise OdooConnectionError(f"Could not reach {self.base_url}: {exc}") from exc
        except ValueError as exc:
            raise OdooConnectionError(
                f"{self.base_url}{path} did not return JSON. "
                "Check that the URL points at an Odoo server."
            ) from exc

        if "error" in body:
            raise self._as_rpc_error(body["error"])
        return body.get("result")

    @staticmethod
    def _as_rpc_error(error: dict[str, Any]) -> BytesrawError:
        """Turn a JSON-RPC ``error`` member into a typed exception.

        An expired session is singled out here rather than at each call site,
        because every authenticated endpoint can answer with it and the
        recovery is the same everywhere: sign in again with the password
        already in the vault. Matched on ``data.name`` - Odoo translates
        ``message``, so "Odoo Session Expired" is not there on an Arabic
        server, and the same trap as the login refusals above.
        """
        data = error.get("data") or {}
        message = data.get("message") or error.get("message") or "Unknown Odoo error"
        name = data.get("name")
        try:
            code = int(error.get("code") or 0)
        except (TypeError, ValueError):
            code = 0

        if name == SESSION_EXPIRED_NAME or code == SESSION_EXPIRED_CODE:
            return OdooSessionExpired(
                "The Odoo session has expired. Signing in again..."
            )

        return OdooRpcError(
            str(message).strip(),
            debug=data.get("debug"),
            name=str(name) if name else None,
            code=code,
        )

    # -- endpoints ---------------------------------------------------------

    def list_databases(self) -> list[str]:
        """Databases exposed by the server.

        Returns an empty list when the server hides its database list
        (``list_db = False``), which is normal for Odoo Online and for
        hardened on-premise deployments - the user then types the name.
        """
        try:
            result = self._rpc(RPC_DB_LIST, {})
        except BytesrawError:
            _log.info("Database list is not exposed by %s", self.base_url)
            return []
        return [str(name) for name in (result or [])]

    def authenticate(self, database: str, login: str, password: str) -> dict[str, Any]:
        """Log in and return Odoo's ``session_info`` payload.

        Two different refusals, which the caller must be able to tell apart:

        * The server raises ``AccessDenied`` (bad password, unknown login, or a
          deactivated user) or ``AccessError`` (no such database). Measured
          against a live Odoo 19 server, both come back as a JSON-RPC *fault*,
          not as a falsy ``uid`` - so this never reaches the check below. These
          become :class:`OdooCredentialsRejected`: nothing about the account
          works until it is edited.
        * Odoo answers ``{'uid': None}`` with no fault when the login needs a
          second factor. That stays a plain :class:`OdooAuthError`, because the
          stored details are fine and only the extra step is missing.
        """
        try:
            result = self._rpc(
                RPC_AUTHENTICATE,
                {"db": database, "login": login, "password": password},
            )
        except OdooRpcError as exc:
            raise self._as_login_refusal(exc, database, login) from exc

        if not result or not result.get("uid"):
            raise OdooAuthError(
                "Odoo did not accept these credentials. If this account uses "
                "two-factor authentication, sign in once in the app window "
                "after saving."
            )
        return result

    @staticmethod
    def _as_login_refusal(
        exc: OdooRpcError, database: str, login: str
    ) -> BytesrawError:
        """Turn a fault from the login endpoint into something a user can act on.

        Branching on ``data.name`` rather than the message, which Odoo
        translates - a server running in Arabic answers ``AccessDenied`` with
        Arabic text, and matching "Access Denied" would miss it.
        """
        name = (exc.name or "").rsplit(".", 1)[-1]
        if name == "AccessDenied":
            return OdooCredentialsRejected(
                f"Odoo refused the login '{login}' on database '{database}'. "
                "The password may have been changed, or the user deactivated."
            )
        if name == "AccessError":
            return OdooCredentialsRejected(
                f"{exc} Check the server URL and the database name for this account."
            )
        return exc

    def session_info(self) -> dict[str, Any]:
        """Refresh ``session_info`` for the already-authenticated session.

        Doubles as the liveness probe. ``/web/session/get_session_info`` is
        declared ``auth='user'`` and calls ``request.session.touch()``
        (``addons/web/controllers/session.py:25``), so a call either raises
        :class:`OdooSessionExpired` or pushes the session's idle clock back -
        exactly what a till left alone between customers needs.
        """
        result = self._rpc(RPC_SESSION_INFO, {})
        if not result:
            raise OdooSessionExpired("The Odoo session has expired.")
        return result

    def call_kw(
        self,
        model: str,
        method: str,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> Any:
        return self._rpc(
            RPC_CALL_KW,
            {
                "model": model,
                "method": method,
                "args": args or [],
                "kwargs": kwargs or {},
            },
        )

    def supports_native_dark_mode(self) -> bool:
        """Does this server have a real dark stylesheet to serve?

        Asks for the web client once with ``color_scheme=dark`` and looks for
        the dark asset bundle in the response. Plain Odoo 19 Community
        hardcodes ``ir_http.color_scheme()`` to ``"light"`` and so never serves
        it; Enterprise and addons such as ``ica_web_responsive`` override that
        method. Probing beats guessing from the edition or the module list.

        Note this can answer ``True`` while the *cookie* is ignored - see
        :meth:`supports_user_color_scheme`.
        """
        try:
            response = self._client.get(ODOO_HOME_PATH)
            response.raise_for_status()
            if DARK_BUNDLE_MARKER in response.text:
                return True
            # The account may be pinned to light by its user setting, in which
            # case the cookie is what decides. Ask again with it set.
            self._client.cookies.set(COLOR_SCHEME_COOKIE, "dark")
            try:
                response = self._client.get(ODOO_HOME_PATH)
                response.raise_for_status()
                return DARK_BUNDLE_MARKER in response.text
            finally:
                self._client.cookies.delete(COLOR_SCHEME_COOKIE)
        except httpx.HTTPError as exc:
            _log.info("Could not probe dark-mode support: %s", exc)
            return False

    def supports_user_color_scheme(self) -> bool:
        """Is there a per-user colour-scheme setting to write?

        ``res.users.settings.color_scheme`` is added by an addon, not by Odoo 19
        core. When it exists it takes **precedence over the cookie** unless it
        holds ``"system"``, so writing it is the only reliable way to drive the
        theme on such a server. When it does not exist the write is skipped
        entirely, which is why an uninstalled addon causes no error here.
        """
        try:
            fields = self.call_kw(
                USER_SETTINGS_MODEL, "fields_get", [[COLOR_SCHEME_FIELD], ["type"]]
            )
        except BytesrawError as exc:
            _log.info("No per-user colour scheme on this server: %s", exc)
            return False
        return bool(fields) and COLOR_SCHEME_FIELD in fields

    def set_user_color_scheme(self, settings_id: int, scheme: str) -> None:
        """Write ``res.users.settings.color_scheme`` - ``system``/``light``/``dark``.

        This is the same record the theme-switcher addon's own JavaScript writes
        through ``user.setUserSettings``, so the app and Odoo's user menu stay
        in agreement instead of fighting over the cookie.
        """
        self.call_kw(
            USER_SETTINGS_MODEL, "write", [[settings_id], {COLOR_SCHEME_FIELD: scheme}]
        )

    # -- session handoff ---------------------------------------------------

    @property
    def session_id(self) -> str | None:
        """The ``session_id`` cookie value, once authenticated."""
        return self._client.cookies.get(SESSION_COOKIE)

    @property
    def cookies(self) -> dict[str, str]:
        """Every cookie the server set on this session.

        Handing the web view only ``session_id`` is not enough in front of a
        load balancer: deployments behind a reverse proxy also set a sticky
        routing cookie (``odoo-<build>-session`` and similar), and a request
        that arrives without it can be routed to a backend that has never seen
        the session. Transplanting the whole jar keeps the two clients on the
        same server as well as the same session.
        """
        return dict(self._client.cookies)
