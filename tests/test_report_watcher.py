"""Claim semantics for the report-request watcher.

The watcher is the only evidence that a ``blob:`` download is an Odoo report,
so its accounting has to be exact: claim once per request, never claim a stale
one, and never claim one that was not a report.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QUrl

from bytesraw_erp.services import report_watcher as rw
from bytesraw_erp.services.report_watcher import ReportRequestWatcher


class FakeRequestInfo:
    """Stands in for QWebEngineUrlRequestInfo, which cannot be constructed."""

    def __init__(self, url: str, method: str = "POST") -> None:
        self._url = QUrl(url)
        self._method = method

    def requestUrl(self) -> QUrl:
        return self._url

    def requestMethod(self) -> str:
        return self._method


@pytest.fixture
def watcher() -> ReportRequestWatcher:
    return ReportRequestWatcher()


def _see(watcher: ReportRequestWatcher, url: str, method: str = "POST") -> None:
    watcher.interceptRequest(FakeRequestInfo(url, method))


BASE = "https://erp.example.com"


def test_nothing_to_claim_initially(watcher: ReportRequestWatcher) -> None:
    assert watcher.claim() is False
    assert watcher.pending_count == 0


def test_report_post_becomes_claimable(watcher: ReportRequestWatcher) -> None:
    _see(watcher, f"{BASE}/report/download")
    assert watcher.pending_count == 1
    assert watcher.claim() is True


def test_a_request_is_claimable_only_once(watcher: ReportRequestWatcher) -> None:
    """Otherwise one report would license every later blob download."""
    _see(watcher, f"{BASE}/report/download")
    assert watcher.claim() is True
    assert watcher.claim() is False


def test_inline_report_url_also_counts(watcher: ReportRequestWatcher) -> None:
    _see(watcher, f"{BASE}/report/pdf/account.report_invoice/9", method="GET")
    assert watcher.claim() is True


def test_query_string_does_not_prevent_a_match(watcher: ReportRequestWatcher) -> None:
    _see(watcher, f"{BASE}/report/download?data=%5B%22x%22%5D")
    assert watcher.claim() is True


@pytest.mark.parametrize(
    "url",
    [
        f"{BASE}/web/content/12?download=true",
        f"{BASE}/report/barcode/EAN13/123",
        f"{BASE}/odoo/sales",
        f"{BASE}/web/dataset/call_kw",
    ],
)
def test_other_requests_are_ignored(watcher: ReportRequestWatcher, url: str) -> None:
    _see(watcher, url)
    assert watcher.pending_count == 0
    assert watcher.claim() is False


def test_several_reports_queue_up(watcher: ReportRequestWatcher) -> None:
    for _ in range(3):
        _see(watcher, f"{BASE}/report/download")
    assert watcher.pending_count == 3
    assert [watcher.claim() for _ in range(4)] == [True, True, True, False]


def test_stale_requests_expire_rather_than_being_claimed(
    watcher: ReportRequestWatcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A report that never produced a download must not be claimed later.

    Without the TTL, a failed report would leave a token behind that hijacks
    whatever PDF the user downloads next, however much later.
    """
    _see(watcher, f"{BASE}/report/download")
    monkeypatch.setattr(rw, "_TTL_SECONDS", -1.0)
    assert watcher.claim() is False
    assert watcher.pending_count == 0


def test_clear_drops_everything(watcher: ReportRequestWatcher) -> None:
    _see(watcher, f"{BASE}/report/download")
    watcher.clear()
    assert watcher.claim() is False
