"""Turn Odoo's ``session_info`` into the view model the app bar consumes.

Odoo 19's ``session_info`` (``addons/web/models/ir_http.py``) already carries
the user name, login, database, active language and the allowed companies. Only
the language *list* needs an extra round trip, and it is tolerated to fail - an
unavailable language list must never block the user from reaching their Odoo
screen.
"""

from __future__ import annotations

import logging
from typing import Any

from bytesraw_erp.core.errors import BytesrawError
from bytesraw_erp.data.models import Account, Company, Language, SessionContext
from bytesraw_erp.services.odoo_client import OdooClient

_log = logging.getLogger(__name__)


def _companies_from_session(info: dict[str, Any]) -> tuple[tuple[Company, ...], int]:
    """Extract allowed companies, ordered the way Odoo orders them.

    Portal and public users have no ``user_companies`` key at all - Odoo only
    adds it for internal users - so this degrades to an empty tuple and a
    current id of zero.
    """
    user_companies = info.get("user_companies") or {}
    allowed = user_companies.get("allowed_companies") or {}
    companies = [
        Company(id=int(data["id"]), name=str(data.get("name") or ""))
        for data in allowed.values()
    ]
    companies.sort(key=lambda company: (company.name.lower(), company.id))
    current = int(user_companies.get("current_company") or 0)
    if not current and companies:
        current = companies[0].id
    return tuple(companies), current


def _fetch_languages(client: OdooClient) -> tuple[Language, ...]:
    try:
        rows = client.call_kw(
            "res.lang",
            "search_read",
            [[["active", "=", True]], ["code", "name"]],
            {"order": "name"},
        )
    except BytesrawError as exc:
        _log.info("Could not read the active language list: %s", exc)
        return ()
    return tuple(Language(code=str(r["code"]), name=str(r["name"])) for r in rows or [])


def build_session_context(client: OdooClient, info: dict[str, Any]) -> SessionContext:
    """Assemble a :class:`SessionContext` from an authenticated client."""
    companies, current_company_id = _companies_from_session(info)
    context = info.get("user_context") or {}
    languages = _fetch_languages(client)
    native_dark = client.supports_native_dark_mode()
    user_settings = info.get("user_settings") or {}
    supports_user_scheme = client.supports_user_color_scheme()

    return SessionContext(
        uid=int(info.get("uid") or 0),
        user_name=str(info.get("name") or ""),
        login=str(info.get("username") or ""),
        database=str(info.get("db") or ""),
        language=str(context.get("lang") or "en_US"),
        server_version=str(info.get("server_version") or ""),
        companies=companies,
        current_company_id=current_company_id,
        languages=languages,
        native_dark_mode=native_dark,
        user_settings_id=int(user_settings.get("id") or 0),
        user_color_scheme_supported=supports_user_scheme,
    )


def open_session(account: Account, password: str) -> tuple[OdooClient, SessionContext]:
    """Authenticate ``account`` and return a live client plus its context.

    The caller owns the returned client and must :meth:`~OdooClient.close` it.
    """
    client = OdooClient(
        account.url,
        verify_tls=not account.allow_untrusted_certificate,
    )
    try:
        info = client.authenticate(account.database, account.login, password)
        return client, build_session_context(client, info)
    except Exception:
        client.close()
        raise


def probe_session(client: OdooClient) -> None:
    """Check that ``client``'s Odoo session is still live - and keep it alive.

    Raises :class:`~bytesraw_erp.core.errors.OdooSessionExpired` when the server
    has forgotten the session, and :class:`OdooConnectionError` when it cannot
    be reached at all. The caller must tell those apart: the first needs a new
    sign-in, the second needs nothing but patience.

    ``/web/session/get_session_info`` is the probe because it is cheap,
    ``auth='user'`` (so an expired session cannot answer it), and calls
    ``request.session.touch()`` - which pushes the inactivity clock back and
    stops a till that is idle between customers from expiring at all.
    """
    client.session_info()


def set_user_language(client: OdooClient, uid: int, code: str) -> None:
    """Change the user's language the same way Odoo's own switcher does."""
    client.call_kw("res.users", "write", [[uid], {"lang": code}])


def apply_odoo_color_scheme(
    client: OdooClient,
    session: SessionContext,
    scheme: str,
) -> None:
    """Push the app's appearance onto the Odoo user record.

    ``scheme`` is Odoo's own vocabulary: ``system``, ``light`` or ``dark``.
    Silently does nothing when the server has no such field, which is the case
    on plain Odoo 19 Community - the caller then relies on the cookie and on
    Chromium's own darkening instead.
    """
    if not session.can_set_odoo_theme:
        _log.debug("Server has no per-user colour scheme; skipping the write")
        return
    client.set_user_color_scheme(session.user_settings_id, scheme)
    _log.info("Set Odoo color_scheme=%s for user settings %s", scheme, session.user_settings_id)
