"""Typed errors raised by the service layer.

The UI never inspects transport exceptions directly; it catches
:class:`BytesrawError` and shows ``str(exc)``, so every message raised here is
written to be readable by an end user.
"""

from __future__ import annotations


class BytesrawError(Exception):
    """Base class for every error the UI is expected to render."""


class OdooConnectionError(BytesrawError):
    """The Odoo server could not be reached, or answered with non-JSON."""


class OdooAuthError(BytesrawError):
    """Credentials were rejected, or the login needs a step we cannot do."""


class OdooCredentialsRejected(OdooAuthError):
    """The server refused the stored connection details outright.

    Deliberately distinct from its base class, because the *fix* differs. A
    plain :class:`OdooAuthError` - a two-factor prompt, an expired session -
    still has usable details behind it and only needs another step. This one
    means nothing about the account can work until the user edits it, which is
    what lets the UI discard it rather than leaving the user on a screen that
    can never load.
    """


class OdooRpcError(BytesrawError):
    """The server answered with a JSON-RPC ``error`` member."""

    def __init__(
        self,
        message: str,
        *,
        debug: str | None = None,
        name: str | None = None,
    ) -> None:
        super().__init__(message)
        self.debug = debug
        #: Odoo's own exception class, e.g. ``odoo.exceptions.AccessDenied``.
        #: Odoo puts it in ``error.data.name`` (``serialize_exception`` in
        #: ``odoo/http.py``). It is the only part of the fault that is not
        #: translated, so it is the only safe thing to branch on.
        self.name = name


class CredentialError(BytesrawError):
    """The OS credential vault could not be read or written."""


class ConfigError(BytesrawError):
    """``accounts.json`` is unreadable, corrupt, or of an unknown version."""
