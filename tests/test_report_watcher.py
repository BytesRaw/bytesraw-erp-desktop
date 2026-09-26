"""Claim semantics for the report-request watcher.

The watcher is the only evidence that a ``blob:`` download is an Odoo report,
so its accounting has to be exact: claim once per request, never claim a stale
one, and never claim one that was not a report.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QByteArray, QUrl

from bytesraw_erp.constants import REPORT_NAME_HEADER
from bytesraw_erp.services import report_watcher as rw
from bytesraw_erp.services.report_watcher import ReportRequestWatcher


class FakeRequestInfo:
    """Stands in for QWebEngineUrlRequestInfo, which cannot be constructed."""

    def __init__(self, url: str, method: str = "POST", headers: dict | None = None) -> None:
        self._url = QUrl(url)
        self._method = method
        self._headers = {
            QByteArray(k.encode()): QByteArray(v.encode()) for k, v in (headers or {}).items()
        }

    def httpHeaders(self) -> dict:
        return self._headers

    def requestUrl(self) -> QUrl:
        return self._url

    def requestMethod(self) -> str:
        return self._method


@pytest.fixture
def watcher() -> ReportRequestWatcher:
    return ReportRequestWatcher()


def _see(
    watcher: ReportRequestWatcher, url: str, method: str = "POST", report: str = ""
) -> None:
    headers = {REPORT_NAME_HEADER: report} if report else None
    watcher.interceptRequest(FakeRequestInfo(url, method, headers))


BASE = "https://erp.example.com"


def test_nothing_to_claim_initially(watcher: ReportRequestWatcher) -> None:
    assert watcher.claim() is None
    assert watcher.pending_count == 0


def test_report_post_becomes_claimable(watcher: ReportRequestWatcher) -> None:
    _see(watcher, f"{BASE}/report/download")
    assert watcher.pending_count == 1
    assert watcher.claim() is not None


def test_a_request_is_claimable_only_once(watcher: ReportRequestWatcher) -> None:
    """Otherwise one report would license every later blob download."""
    _see(watcher, f"{BASE}/report/download")
    assert watcher.claim() is not None
    assert watcher.claim() is None


def test_inline_report_url_also_counts(watcher: ReportRequestWatcher) -> None:
    _see(watcher, f"{BASE}/report/pdf/account.report_invoice/9", method="GET")
    assert watcher.claim() is not None


def test_query_string_does_not_prevent_a_match(watcher: ReportRequestWatcher) -> None:
    _see(watcher, f"{BASE}/report/download?data=%5B%22x%22%5D")
    assert watcher.claim() is not None


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
    assert watcher.claim() is None


def test_several_reports_queue_up(watcher: ReportRequestWatcher) -> None:
    for _ in range(3):
        _see(watcher, f"{BASE}/report/download")
    assert watcher.pending_count == 3
    assert [watcher.claim() is not None for _ in range(4)] == [True, True, True, False]


def test_stale_requests_expire_rather_than_being_claimed(
    watcher: ReportRequestWatcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A report that never produced a download must not be claimed later.

    Without the TTL, a failed report would leave a token behind that hijacks
    whatever PDF the user downloads next, however much later.
    """
    _see(watcher, f"{BASE}/report/download")
    monkeypatch.setattr(rw, "_TTL_SECONDS", -1.0)
    assert watcher.claim() is None
    assert watcher.pending_count == 0


def test_clear_drops_everything(watcher: ReportRequestWatcher) -> None:
    _see(watcher, f"{BASE}/report/download")
    watcher.clear()
    assert watcher.claim() is None


# -- which report ------------------------------------------------------------


def test_the_claim_names_the_report_the_page_stamped(watcher: ReportRequestWatcher) -> None:
    """The name rides the request itself, so it cannot pair with the wrong one."""
    _see(watcher, f"{BASE}/report/download", report="product.report_producttemplatelabel_dymo")
    claimed = watcher.claim()
    assert claimed is not None
    assert claimed.name == "product.report_producttemplatelabel_dymo"


def test_the_header_is_matched_whatever_its_case(watcher: ReportRequestWatcher) -> None:
    """HTTP header names are case-insensitive, and Chromium may normalise them."""
    info = FakeRequestInfo(
        f"{BASE}/report/download", headers={REPORT_NAME_HEADER.lower(): "sale.report_saleorder"}
    )
    watcher.interceptRequest(info)
    assert watcher.claim() == rw.ClaimedReport("sale.report_saleorder")


def test_names_come_back_in_the_order_they_went_out(watcher: ReportRequestWatcher) -> None:
    for name in ("stock.report_deliveryslip", "", "account.report_invoice"):
        _see(watcher, f"{BASE}/report/download", report=name)
    assert [watcher.claim().name for _ in range(3)] == [  # type: ignore[union-attr]
        "stock.report_deliveryslip",
        "",
        "account.report_invoice",
    ]


def test_a_report_without_a_name_is_still_a_report(watcher: ReportRequestWatcher) -> None:
    """The script missing is a routing loss, never a lost print."""
    _see(watcher, f"{BASE}/report/download")
    assert watcher.claim() == rw.ClaimedReport("")


def test_an_inline_report_is_named_by_its_path(watcher: ReportRequestWatcher) -> None:
    _see(watcher, f"{BASE}/report/pdf/stock.report_deliveryslip/7,8", method="GET")
    assert watcher.claim() == rw.ClaimedReport("stock.report_deliveryslip")


@pytest.mark.parametrize(
    ("path", "name"),
    [
        ("/report/pdf/account.report_invoice/1", "account.report_invoice"),
        (
            "/report/pdf/product.report_producttemplatelabel2x7",
            "product.report_producttemplatelabel2x7",
        ),
        ("/report/text/stock.label_product_product_view/3", "stock.label_product_product_view"),
        ("/report/download", ""),
        ("/account/download_invoice_documents/114/pdf", ""),
        ("/odoo/sales", ""),
    ],
)
def test_report_name_from_path(path: str, name: str) -> None:
    assert rw.report_name_from_path(path) == name
