"""Application-wide constants.

Everything that is a fixed, build-time fact about the product lives here so
that no module has to hardcode a name, a path fragment or an Odoo route.
"""

from __future__ import annotations

from typing import Final

#: Window title and QApplication name.
APP_NAME: Final[str] = "Bytesraw ERP"
APP_VERSION: Final[str] = "0.1.11"

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

#: Everything Odoo's Point of Sale client is served under - both spellings of
#: the route (``addons/point_of_sale/controllers/main.py:36`` registers
#: ``/pos/web`` and ``/pos/ui``, plus ``/pos/ui/<config_id>``). Used to decide
#: which printer a page print belongs on: a page under this prefix is a receipt.
POS_PATH_PREFIX: Final[str] = "/pos/"

RPC_AUTHENTICATE: Final[str] = "/web/session/authenticate"
RPC_SESSION_INFO: Final[str] = "/web/session/get_session_info"
RPC_CALL_KW: Final[str] = "/web/dataset/call_kw"
RPC_DB_LIST: Final[str] = "/web/database/list"

#: URL prefixes that carry a rendered QWeb report. ``/report/download`` is what
#: the web client's Print button hits - it returns the PDF with a
#: ``Content-Disposition: attachment`` header (``addons/web/controllers/report.py:138``),
#: which QtWebEngine surfaces as a download. ``/report/pdf/`` is the inline form.
#:
#: The three ``/account/download_*`` routes are a separate controller
#: (``addons/account/controllers/download_docs.py``) that POS's invoice button
#: hits via an ``ir.actions.act_url`` with ``target: "download"`` - not
#: ``/report/download`` at all, and not the XHR-blob path either: the web
#: client's action service opens it as a plain navigation. It always answers
#: with ``Content-Disposition: attachment`` too, so it belongs in this list for
#: the same reason.
REPORT_URL_PREFIXES: Final[tuple[str, ...]] = (
    "/report/download",
    "/report/pdf/",
    "/account/download_invoice_documents/",
    "/account/download_invoice_attachments/",
    "/account/download_move_attachments/",
)

#: The subset of the above that Odoo *always* answers with
#: ``Content-Disposition: attachment`` - every entry except ``/report/pdf/``,
#: which is the inline viewer form and must still be navigated to so Chromium's
#: built-in PDF viewer can render it. A navigation to one of these never
#: commits a document, only turns into a download, so it must never be allowed
#: to navigate the visible view - see ``OdooWebPage._on_new_window_requested``.
ATTACHMENT_DOWNLOAD_URL_PREFIXES: Final[tuple[str, ...]] = tuple(
    prefix for prefix in REPORT_URL_PREFIXES if prefix != "/report/pdf/"
)

#: Odoo's own class name for an expired session, as ``serialize_exception``
#: writes it into ``error.data.name`` (``odoo/http.py:349``). The accompanying
#: ``error.code`` is 100 and ``error.message`` is "Odoo Session Expired", but
#: only the class name is left untranslated, so that is what the client matches.
SESSION_EXPIRED_NAME: Final[str] = "odoo.http.SessionExpiredException"
#: The numeric code Odoo assigns the same fault (``odoo/http.py:2617``). Used
#: only to corroborate the name, never on its own.
SESSION_EXPIRED_CODE: Final[int] = 100

#: How often the shell asks Odoo whether its session is still alive. Odoo's own
#: ``get_session_info`` calls ``session.touch()``, so this doubles as a keepalive
#: for a till left idle between customers.
SESSION_PROBE_SECONDS: Final[int] = 5 * 60

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

# --- Updates ---------------------------------------------------------------
# The manifest URL is compiled into every shipped binary and can never be
# changed for a till already in the field, so it points at a domain BytesRaw
# owns rather than at the GitHub API or at a github.io address. Artifacts still
# live on GitHub releases; this indirection is what lets the hosting move later
# without stranding the installed base - and ``next_manifest_url`` in the
# manifest is how clients that only know this URL are told to look elsewhere.
#
# Served by GitHub Pages out of the ``updates`` orphan branch. The
# ``bytesraw.github.io`` address still answers and now 301s here, so a binary
# compiled against the old URL keeps working - verified, not assumed.
UPDATE_BASE_URL: Final[str] = "https://updates.bytesraw.com/erp"

#: Layout version of the manifest. A manifest declaring a higher one is refused
#: rather than misread, exactly as ``accounts.json`` and ``settings.json`` are.
UPDATE_SCHEMA: Final[int] = 1

#: ``product`` a manifest must declare. A redirect that lands on somebody
#: else's manifest is a configuration error, not an update.
UPDATE_PRODUCT: Final[str] = "bytesraw-erp"

#: How many ``next_manifest_url`` hops to follow before giving up. Two moves of
#: the hosting is already more than any installed base should need to chase,
#: and the cap is what stops a mistyped manifest pointing at itself forever.
UPDATE_MAX_HOPS: Final[int] = 3

#: A manifest is a few hundred bytes. Anything of this size is not one, and
#: reading it into memory unbounded is how a wrong URL becomes a hang.
UPDATE_MANIFEST_MAX_BYTES: Final[int] = 64 * 1024

#: Network timeouts, in seconds. The manifest is small and should answer at
#: once; the installer is ~140 MB, so its timeout is per-chunk rather than for
#: the whole transfer - see :mod:`bytesraw_erp.services.update_service`.
UPDATE_TIMEOUT: Final[float] = 15.0
UPDATE_DOWNLOAD_TIMEOUT: Final[float] = 60.0

#: Delay before the launch-time check. Sign-in, the first page load and
#: Chromium's start-up all want the network in the first seconds of a launch,
#: and an update is never urgent enough to compete with them.
UPDATE_LAUNCH_DELAY_SECONDS: Final[int] = 40

#: Interval between automatic checks. A till stays open for days at a time, so
#: "on launch" alone would never see a release.
UPDATE_CHECK_SECONDS: Final[int] = 4 * 60 * 60

#: Installer copies to keep in the updates cache. One is the update being
#: offered; the second is there so a failed install can be retried without
#: fetching 140 MB again.
UPDATE_KEEP_INSTALLERS: Final[int] = 2
