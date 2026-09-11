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

Threading
---------
``interceptRequest`` is called on QtWebEngine's IO thread while ``claim`` is
called on the GUI thread, so the pending queue is guarded by a lock.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from threading import Lock

from PySide6.QtWebEngineCore import QWebEngineUrlRequestInfo, QWebEngineUrlRequestInterceptor

from bytesraw_erp.constants import REPORT_URL_PREFIXES

_log = logging.getLogger(__name__)

#: How long a seen report request stays claimable. Generous, because rendering
#: a large PDF server-side can take a while before the blob appears; short
#: enough that a failed report cannot be claimed by an unrelated download much
#: later in the session.
_TTL_SECONDS = 180.0


class ReportRequestWatcher(QWebEngineUrlRequestInterceptor):
    """Notes outgoing Odoo report requests so their blob download can be tagged."""

    def __init__(self) -> None:
        super().__init__()
        self._pending: deque[float] = deque()
        self._lock = Lock()

    # -- IO thread ---------------------------------------------------------

    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:
        path = info.requestUrl().path()
        if not any(path.startswith(prefix) for prefix in REPORT_URL_PREFIXES):
            return
        with self._lock:
            self._pending.append(time.monotonic())
        _log.debug("Report request seen: %s %s", info.requestMethod(), path)

    # -- GUI thread --------------------------------------------------------

    def claim(self) -> bool:
        """Consume one pending report request, if any is still fresh.

        Returns ``True`` when the caller may treat the download it is holding
        as a report. Expired entries are dropped rather than claimed.
        """
        now = time.monotonic()
        with self._lock:
            while self._pending and now - self._pending[0] > _TTL_SECONDS:
                dropped = self._pending.popleft()
                _log.debug("Dropping report request seen %.0fs ago", now - dropped)
            if not self._pending:
                return False
            self._pending.popleft()
            return True

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def clear(self) -> None:
        with self._lock:
            self._pending.clear()
