"""Plain data objects shared across layers.

These are deliberately free of Qt and of any I/O so they can be constructed in
tests without a QApplication.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse, urlunparse

from bytesraw_erp.constants import ODOO_HOME_PATH


class PrintMode(StrEnum):
    """How a print job reaches paper.

    Lives here rather than beside the print service so that `data` stays free
    of any dependency on `services` - the layering rule that keeps the model
    importable without Qt widgets.
    """

    #: Straight to the Windows default printer, no dialog.
    DIRECT = "direct"
    #: Show the system print dialog first.
    DIALOG = "dialog"
    #: Show a preview of the pages, with a print button.
    PREVIEW = "preview"

    @property
    def label(self) -> str:
        return {
            "direct": "Print directly, without a dialog",
            "dialog": "Show the print dialog",
            "preview": "Show a print preview first",
        }[self.value]


def new_account_id() -> str:
    return uuid.uuid4().hex


def normalize_base_url(raw: str) -> str:
    """Turn user input into a bare ``scheme://host[:port]`` origin.

    Accepts ``mycompany.odoo.com``, ``http://localhost:8069/``,
    ``https://erp.example.com/odoo`` and yields an origin with no trailing
    slash and no path, because every Odoo route is appended to it verbatim.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = f"https://{text}"
    parts = urlparse(text)
    if not parts.netloc:
        return ""
    return urlunparse((parts.scheme.lower(), parts.netloc, "", "", "", "")).rstrip("/")


def coerce_print_mode(raw: object) -> PrintMode:
    """Tolerate an unknown value in a hand-edited or older settings file."""
    try:
        return PrintMode(str(raw))
    except ValueError:
        return PrintMode.DIALOG


@dataclass(slots=True)
class Account:
    """One saved Odoo connection.

    The password is *not* a field: it is stored in the OS credential vault and
    looked up by :attr:`id`. Keeping it out of the dataclass means an
    ``Account`` can be logged or serialised without leaking a secret.
    """

    id: str = field(default_factory=new_account_id)
    name: str = ""
    url: str = ""
    database: str = ""
    login: str = ""
    #: Last Odoo path the user was on, restored on next launch.
    last_path: str = ODOO_HOME_PATH
    #: Allow self-signed / otherwise untrusted TLS certificates for this host.
    #: Off by default; only meaningful for on-premise servers.
    allow_untrusted_certificate: bool = False

    def __post_init__(self) -> None:
        self.url = normalize_base_url(self.url)
        if not self.name:
            self.name = self.display_host

    @property
    def display_host(self) -> str:
        return urlparse(self.url).netloc or self.url

    def url_for(self, path: str) -> str:
        """Absolute URL for an Odoo path such as ``/odoo/sales``."""
        return f"{self.url}/{path.lstrip('/')}"

    @property
    def home_url(self) -> str:
        return self.url_for(self.last_path or ODOO_HOME_PATH)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "database": self.database,
            "login": self.login,
            "last_path": self.last_path,
            "allow_untrusted_certificate": self.allow_untrusted_certificate,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Account:
        return cls(
            id=str(raw.get("id") or new_account_id()),
            name=str(raw.get("name") or ""),
            url=str(raw.get("url") or ""),
            database=str(raw.get("database") or ""),
            login=str(raw.get("login") or ""),
            last_path=str(raw.get("last_path") or ODOO_HOME_PATH),
            allow_untrusted_certificate=bool(raw.get("allow_untrusted_certificate", False)),
        )

    def evolve(self, **changes: Any) -> Account:
        """Return a copy with ``changes`` applied - accounts are treated as values."""
        return replace(self, **changes)


@dataclass(frozen=True, slots=True)
class Company:
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class Language:
    code: str
    name: str


@dataclass(frozen=True, slots=True)
class SessionContext:
    """Everything the app bar needs, distilled from Odoo's ``session_info``."""

    uid: int
    user_name: str
    login: str
    database: str
    language: str
    server_version: str
    companies: tuple[Company, ...]
    current_company_id: int
    languages: tuple[Language, ...] = ()
    #: True when the server has a real dark stylesheet to serve. False on
    #: plain Odoo 19 Community, which hardcodes ``ir_http.color_scheme()`` to
    #: "light" - there the app falls back to Chromium's ForceDarkMode so the
    #: embedded client still goes dark.
    native_dark_mode: bool = False
    #: Id of this user's ``res.users.settings`` record, from ``session_info``.
    user_settings_id: int = 0
    #: True when ``res.users.settings.color_scheme`` exists. That field takes
    #: precedence over the cookie unless it holds "system", so when it is
    #: present the app must write it rather than relying on the cookie alone.
    user_color_scheme_supported: bool = False

    @property
    def can_set_odoo_theme(self) -> bool:
        return self.user_color_scheme_supported and bool(self.user_settings_id)

    @property
    def has_multiple_languages(self) -> bool:
        return len(self.languages) > 1

    @property
    def current_company(self) -> Company | None:
        return next((c for c in self.companies if c.id == self.current_company_id), None)

    @property
    def current_company_name(self) -> str:
        company = self.current_company
        return company.name if company else ""

    @property
    def current_language_name(self) -> str:
        match = next((lang for lang in self.languages if lang.code == self.language), None)
        return match.name if match else self.language


#: Sentinel for "whatever Windows says is the default printer". Stored rather
#: than resolving the name at save time, so the setting keeps following the OS
#: default when the user changes it in Windows.
SYSTEM_DEFAULT_PRINTER = ""


@dataclass(frozen=True, slots=True)
class PrintSettings:
    """How this installation prints. Configured once, on the settings page.

    Deliberately app-wide rather than per-account: "which printer" is a fact
    about the machine standing in front of the user, not about the Odoo
    database they happen to be signed in to.
    """

    #: Dialog or straight to paper.
    mode: PrintMode = PrintMode.DIALOG
    #: Empty string means the Windows default printer.
    printer_name: str = SYSTEM_DEFAULT_PRINTER
    #: Print QWeb report PDFs as they arrive from Odoo, instead of only saving
    #: them. This is what makes Odoo's own Print button reach paper.
    auto_print_reports: bool = True
    #: Also keep the PDF in the Downloads folder after printing it.
    keep_report_copy: bool = False

    @property
    def uses_system_default(self) -> bool:
        return not self.printer_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "printer_name": self.printer_name,
            "auto_print_reports": self.auto_print_reports,
            "keep_report_copy": self.keep_report_copy,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PrintSettings:
        return cls(
            mode=coerce_print_mode(raw.get("mode")),
            printer_name=str(raw.get("printer_name") or SYSTEM_DEFAULT_PRINTER),
            auto_print_reports=bool(raw.get("auto_print_reports", True)),
            keep_report_copy=bool(raw.get("keep_report_copy", False)),
        )

    def evolve(self, **changes: Any) -> PrintSettings:
        return replace(self, **changes)
