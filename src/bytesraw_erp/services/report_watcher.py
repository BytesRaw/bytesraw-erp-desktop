"""Recognising an Odoo report download.

Why this exists
---------------
Odoo 19 does not navigate to ``/report/download``. Its ``download()`` helper
(``addons/web/static/src/core/network/download.js``) sends an **XHR POST** with
``responseType = "blob"``, then hands the blob to ``URL.createObjectURL()`` and
clicks a hidden ``<a download>``. QtWebEngine therefore reports the download
with a **``blob:`` URL**, not the report URL::

    url:       blob:https://erp.example.com/83137414-06b4-...
    mimeType:  application/pdf
    suggested: invoice.pdf

Matching on the download URL alone can never spot that, which is exactly why
report auto-printing did nothing: every report looked like an anonymous blob.

A ``QWebEngineUrlRequestInterceptor`` *does* see the underlying request -
verified as ``('POST', '/report/download', ResourceTypeXhr)`` - so this class
notes each report request as it goes out and lets the download handler claim it
when the blob arrives moments later.

Which report
------------
The claim also says *which* report it was, so a report can be sent to a printer
of its own. Odoo names the report only in the POST body -
``data=["/report/pdf/<report_name>/<ids>", "qweb-pdf"]`` - and an interceptor
cannot read a body. It can read headers, so the injected
``web_view._REPORT_NAME_SCRIPT`` copies the name into
:data:`~bytesraw_erp.constants.REPORT_NAME_HEADER` as the request goes out.
The name and the request therefore arrive here *together*, in one call, and
nothing has to line two separate streams of evidence back up. A request with
no header - the script failed, or a future Odoo sends its reports differently -
is still a report, just an unnamed one, and prints where every report did.

Threading
---------
``interceptRequest`` is called on QtWebEngine's IO thread while ``claim`` is
called on the GUI thread, so the pending queue is guarded by a lock.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from threading import Lock

from PySide6.QtWebEngineCore import QWebEngineUrlRequestInfo, QWebEngineUrlRequestInterceptor

from bytesraw_erp.constants import (
    REPORT_NAME_HEADER,
    REPORT_NAME_PATH_PREFIXES,
    REPORT_URL_PREFIXES,
)

_log = logging.getLogger(__name__)

#: How long a seen report request stays claimable. Generous, because rendering
#: a large PDF server-side can take a while before the blob appears; short
#: enough that a failed report cannot be claimed by an unrelated download much
#: later in the session.
_TTL_SECONDS = 180.0

_HEADER_KEY = REPORT_NAME_HEADER.lower().encode("ascii")


def report_name_from_path(path: str) -> str:
    """The report a ``/report/pdf/<name>/...`` path renders, or ``""``.

    Only the two routes that carry the name in the path answer; every other
    report route - ``/report/download``, the ``/account/download_*`` family -
    names nothing here.
    """
    for prefix in REPORT_NAME_PATH_PREFIXES:
        if path.startswith(prefix):
            return path[len(prefix) :].split("/", 1)[0]
    return ""


def _header_name(info: QWebEngineUrlRequestInfo) -> str:
    """The report name the page stamped on this request, or ``""``."""
    try:
        headers = info.httpHeaders()
    except AttributeError:  # pragma: no cover - Qt before 6.5
        return ""
    for key, value in headers.items():
        if bytes(key).lower() == _HEADER_KEY:
            return bytes(value).decode("ascii", "replace").strip()
    return ""


@dataclass(frozen=True, slots=True)
class ClaimedReport:
    """A report request that a download has been matched to."""

    #: Odoo's technical name for the report, or ``""`` when nothing said.
    name: str


class ReportRequestWatcher(QWebEngineUrlRequestInterceptor):
    """Notes outgoing Odoo report requests so their blob download can be tagged."""

    def __init__(self) -> None:
        super().__init__()
        #: ``(seen_at, report_name)``, oldest first.
        self._pending: deque[tuple[float, str]] = deque()
        self._lock = Lock()

    # -- IO thread ---------------------------------------------------------

    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:
        path = info.requestUrl().path()
        if not any(path.startswith(prefix) for prefix in REPORT_URL_PREFIXES):
            return
        name = _header_name(info) or report_name_from_path(path)
        with self._lock:
            self._pending.append((time.monotonic(), name))
        _log.debug(
            "Report request seen: %s %s (%s)", info.requestMethod(), path, name or "unnamed"
        )

    # -- GUI thread --------------------------------------------------------

    def claim(self) -> ClaimedReport | None:
        """Consume one pending report request, if any is still fresh.

        Returns the claimed report when the caller may treat the download it
        is holding as one, and ``None`` otherwise. Expired entries are dropped
        rather than claimed.

        Oldest first, which pairs each download with its own request as long
        as Odoo answers them in the order they were asked - true of the one
        report a person prints at a time. Two printed within the same few
        seconds, the second rendering faster, would swap names.
        """
        now = time.monotonic()
        with self._lock:
            while self._pending and now - self._pending[0][0] > _TTL_SECONDS:
                seen_at, _name = self._pending.popleft()
                _log.debug("Dropping report request seen %.0fs ago", now - seen_at)
            if not self._pending:
                return None
            _seen_at, name = self._pending.popleft()
            return ClaimedReport(name)

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def clear(self) -> None:
        with self._lock:
            self._pending.clear()
