"""Plain data objects shared across layers.

These are deliberately free of Qt and of any I/O so they can be constructed in
tests without a QApplication.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
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


class RenderMode(StrEnum):
    """How the embedded web view is rasterised.

    ``AUTO`` leaves the decision to Chromium, which is correct on any machine
    with a sound graphics driver. ``SOFTWARE`` takes the GPU out of the path
    entirely - the remedy for a driver that paints the web view as stripes,
    blank white or garbled tiles while the native app bar above it renders
    perfectly. Measured on Intel HD Graphics (Bay Trail, Celeron J1800) under
    Windows 10.

    Lives here, next to :class:`PrintMode`, for the same reason: a fact about
    the machine the app is installed on, stored in ``settings.json``.
    """

    #: Whatever Chromium's own GPU probing decides.
    AUTO = "auto"
    #: Rasterise and composite on the CPU, and give Qt the software OpenGL
    #: implementation too.
    SOFTWARE = "software"

    @property
    def label(self) -> str:
        return {
            "auto": "Use the graphics card (recommended)",
            "software": "Compatibility mode - render without the graphics card",
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


def coerce_update_channel(raw: object) -> UpdateChannel:
    """Tolerate an unknown channel in a hand-edited settings file."""
    try:
        return UpdateChannel(str(raw))
    except ValueError:
        return UpdateChannel.STABLE


def coerce_render_mode(raw: object) -> RenderMode:
    """Tolerate an unknown value in a hand-edited or older settings file."""
    try:
        return RenderMode(str(raw))
    except ValueError:
        return RenderMode.AUTO


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

    **Two printers, because a till has two kinds of paper.** A POS receipt is
    an 80mm thermal ticket and a QWeb report is an A4 page; one device cannot
    take both, and a single setting meant an invoice validated in POS went to
    the receipt roll. The device is therefore chosen by what is being printed,
    never by one global choice - see :meth:`printer_for`.
    """

    #: Dialog, preview or straight to paper. Shared by both devices on purpose:
    #: it says how much ceremony the user wants around a print, which is a
    #: preference about them rather than about the paper.
    mode: PrintMode = PrintMode.DIALOG
    #: The A4 device, for QWeb report PDFs. Empty means the Windows default,
    #: which is right for an office - and is exactly why a till must choose its
    #: receipt printer explicitly below instead of leaning on the same default.
    report_printer_name: str = SYSTEM_DEFAULT_PRINTER
    #: The thermal device, for POS receipts. Used for POS and nothing else.
    pos_printer_name: str = SYSTEM_DEFAULT_PRINTER
    #: Print QWeb report PDFs as they arrive from Odoo, instead of only saving
    #: them. This is what makes Odoo's own Print button reach paper.
    auto_print_reports: bool = True
    #: Also keep the PDF in the Downloads folder.
    keep_report_copy: bool = False

    def printer_for(self, *, pos: bool) -> str:
        """The device for this job: the receipt roll, or A4.

        ``pos`` is about the *page being printed*, not about the installation.
        A report rendered while standing in POS is still A4, so the report
        route never asks this - it always prints on :attr:`report_printer_name`.
        """
        return self.pos_printer_name if pos else self.report_printer_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "report_printer_name": self.report_printer_name,
            "pos_printer_name": self.pos_printer_name,
            "auto_print_reports": self.auto_print_reports,
            "keep_report_copy": self.keep_report_copy,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PrintSettings:
        # Before there were two printers there was one, named `printer_name`,
        # and on a till it was the thermal receipt printer - reports were not
        # printed at all then, they were saved. Reading the old key as the POS
        # printer preserves the only behaviour it ever produced, and leaves
        # reports on the Windows default rather than aiming A4 at a receipt
        # roll, which is the failure this split exists to end.
        legacy = str(raw.get("printer_name") or SYSTEM_DEFAULT_PRINTER)
        return cls(
            mode=coerce_print_mode(raw.get("mode")),
            report_printer_name=str(
                raw.get("report_printer_name") or SYSTEM_DEFAULT_PRINTER
            ),
            pos_printer_name=str(raw.get("pos_printer_name") or legacy),
            auto_print_reports=bool(raw.get("auto_print_reports", True)),
            keep_report_copy=bool(raw.get("keep_report_copy", False)),
        )

    def evolve(self, **changes: Any) -> PrintSettings:
        return replace(self, **changes)


@dataclass(frozen=True, slots=True)
class DisplaySettings:
    """How this installation paints the embedded Odoo client.

    App-wide rather than per-account, like :class:`PrintSettings`: a graphics
    driver is a property of the till, not of the database it talks to.
    """

    render_mode: RenderMode = RenderMode.AUTO

    @property
    def uses_gpu(self) -> bool:
        return self.render_mode is RenderMode.AUTO

    def to_dict(self) -> dict[str, Any]:
        return {"render_mode": self.render_mode.value}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DisplaySettings:
        return cls(render_mode=coerce_render_mode(raw.get("render_mode")))

    def evolve(self, **changes: Any) -> DisplaySettings:
        return replace(self, **changes)


class UpdateChannel(StrEnum):
    """Which manifest a till follows.

    One file per channel rather than one file with a channel field, so a beta
    that goes wrong cannot be published into the path stable clients read.
    """

    STABLE = "stable"
    BETA = "beta"

    @property
    def label(self) -> str:
        return {
            "stable": "Stable - tested releases only",
            "beta": "Beta - early releases, for testing",
        }[self.value]


def new_install_id() -> str:
    """A random id for this installation, used only on this machine.

    Its one job is to give a staged rollout a *stable* answer: without it, a
    manifest offering an update to 25% of the fleet would re-roll the dice on
    every check and eventually reach everyone within the hour, which is not a
    staged rollout at all. It is never sent anywhere - the rollout is decided
    client-side, which is also why the update host needs no telemetry.
    """
    return uuid.uuid4().hex


def parse_version(text: object) -> tuple[int, ...]:
    """Split a dotted version into integers that compare correctly.

    ``"0.10.0" > "0.9.0"`` is the whole point: a string comparison gets that
    backwards, and a release that looks older than the build it replaces is an
    update nobody is ever offered. A leading ``v`` is tolerated because tags
    carry one, and a non-numeric suffix (``0.2.0-rc1``) truncates rather than
    raising - an unparseable tail must not make the version itself unreadable.
    """
    parts: list[int] = []
    for chunk in str(text or "").strip().lstrip("vV").split("."):
        digits = ""
        for char in chunk:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def is_newer_version(candidate: object, current: object) -> bool:
    """Whether ``candidate`` is a later version than ``current``.

    Padded to a common width so ``0.2`` and ``0.2.0`` are the same release. A
    candidate that cannot be parsed at all is never an upgrade: offering an
    update to a version the app cannot even read is worse than offering none.
    """
    left, right = parse_version(candidate), parse_version(current)
    if not left:
        return False
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) > right + (0,) * (width - len(right))


@dataclass(frozen=True, slots=True)
class UpdateSettings:
    """Whether and where this installation looks for a new version.

    App-wide, like :class:`PrintSettings` and :class:`DisplaySettings`: which
    build a machine runs is a fact about the machine.
    """

    #: Check on launch and every few hours. Off means the settings page's
    #: "Check now" is the only thing that ever looks.
    check_automatically: bool = True
    channel: UpdateChannel = UpdateChannel.STABLE
    #: Random, local-only, and stable for the life of the installation - see
    #: :func:`new_install_id`. Empty until first use.
    install_id: str = ""
    #: When the last check finished, ISO-8601 in UTC. Empty means never.
    #: Stored so the settings page can answer "is this thing even looking?"
    #: without having to check again to find out.
    last_check: str = ""

    @property
    def last_check_at(self) -> datetime | None:
        """:attr:`last_check` as a datetime, or ``None`` if never or unreadable."""
        if not self.last_check:
            return None
        try:
            return datetime.fromisoformat(self.last_check)
        except ValueError:
            return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_automatically": self.check_automatically,
            "channel": self.channel.value,
            "install_id": self.install_id,
            "last_check": self.last_check,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> UpdateSettings:
        return cls(
            check_automatically=bool(raw.get("check_automatically", True)),
            channel=coerce_update_channel(raw.get("channel")),
            install_id=str(raw.get("install_id") or ""),
            last_check=str(raw.get("last_check") or ""),
        )

    def evolve(self, **changes: Any) -> UpdateSettings:
        return replace(self, **changes)
