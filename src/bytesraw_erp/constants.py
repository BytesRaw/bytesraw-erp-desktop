"""Application-wide constants.

Everything that is a fixed, build-time fact about the product lives here so
that no module has to hardcode a name, a path fragment or an Odoo route.
"""

from __future__ import annotations

from typing import Final

#: Window title / QApplication name. Equivalent to Flet's ``page.title``.
APP_NAME: Final[str] = "Bytesraw ERP"
APP_VERSION: Final[str] = "0.1.0"

ORG_NAME: Final[str] = "BytesRaw"
ORG_DOMAIN: Final[str] = "bytesraw.com"

#: Service name used to namespace passwords inside the OS credential vault.
KEYRING_SERVICE: Final[str] = "BytesRaw ERP"

#: Schema version of ``accounts.json``. Bump on any breaking layout change and
#: add a migration in :mod:`bytesraw_erp.data.account_store`.
CONFIG_VERSION: Final[int] = 1

#: Schema version of ``settings.json``.
SETTINGS_VERSION: Final[int] = 1

# --- Odoo 19 routes -------------------------------------------------------
# Verified against the Odoo 19 source tree. Note that Odoo 19 renamed the RPC
# route type from ``json`` to ``jsonrpc``; the wire format is unchanged.
ODOO_HOME_PATH: Final[str] = "/odoo"
ODOO_LOGIN_PATH: Final[str] = "/web/login"
ODOO_LOGOUT_PATH: Final[str] = "/web/session/logout"

RPC_AUTHENTICATE: Final[str] = "/web/session/authenticate"
RPC_SESSION_INFO: Final[str] = "/web/session/get_session_info"
RPC_CALL_KW: Final[str] = "/web/dataset/call_kw"
RPC_DB_LIST: Final[str] = "/web/database/list"

#: URL prefixes that carry a rendered QWeb report. ``/report/download`` is what
#: the web client's Print button hits - it returns the PDF with a
#: ``Content-Disposition: attachment`` header (``addons/web/controllers/report.py:138``),
#: which QtWebEngine surfaces as a download. ``/report/pdf/`` is the inline form.
REPORT_URL_PREFIXES: Final[tuple[str, ...]] = ("/report/download", "/report/pdf/")

#: Model holding the per-user colour scheme. Present only when an addon adds it
#: (e.g. ``ica_web_responsive``); absent on plain Odoo 19 Community.
USER_SETTINGS_MODEL: Final[str] = "res.users.settings"
COLOR_SCHEME_FIELD: Final[str] = "color_scheme"

#: Odoo's session cookie (``odoo/http.py``: ``response.set_cookie('session_id', ...)``).
SESSION_COOKIE: Final[str] = "session_id"
#: Odoo's dark-mode cookie. Read both server-side
#: (``odoo/addons/base/models/ir_ui_view.py:2715``) and client-side
#: (``addons/web/static/src/views/graph/graph_renderer.js``). Values: light/dark.
COLOR_SCHEME_COOKIE: Final[str] = "color_scheme"

#: Marker for the dark asset bundle in the rendered web client. Its presence is
#: how the app detects whether the server honours the colour-scheme cookie:
#: Community hardcodes ``ir_http.color_scheme()`` to "light", so only Enterprise
#: actually swaps the bundle (``addons/web/views/webclient_templates.xml:300``).
DARK_BUNDLE_MARKER: Final[str] = "assets_web_dark"

#: Network timeout, in seconds, for every JSON-RPC call.
RPC_TIMEOUT: Final[float] = 20.0

# --- Routes (in-app navigation) -------------------------------------------
ROUTE_ACCOUNTS: Final[str] = "/accounts"
ROUTE_ACCOUNT_NEW: Final[str] = "/accounts/new"
ROUTE_ACCOUNT_EDIT: Final[str] = "/accounts/:account_id/edit"
ROUTE_ODOO: Final[str] = "/odoo"
ROUTE_SETTINGS: Final[str] = "/settings"

DEFAULT_WINDOW_SIZE: Final[tuple[int, int]] = (1440, 900)
MIN_WINDOW_SIZE: Final[tuple[int, int]] = (1024, 640)
