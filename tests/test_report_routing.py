"""Which downloads count as an Odoo QWeb report.

This is the gate that decides whether a PDF is sent to a printer or merely
saved, so a false positive would print somebody's XLSX export and a false
negative would silently drop their invoice into Downloads.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from bytesraw_erp.constants import REPORT_URL_PREFIXES
from bytesraw_erp.services import profile_manager as pm
from bytesraw_erp.services.profile_manager import is_report_url

BASE = "https://erp.example.com"


@pytest.mark.parametrize(
    "url",
    [
        # What the web client's Print button actually hits: the report URL is
        # JSON-encoded into the query string.
        f"{BASE}/report/download?data=%5B%22%2Freport%2Fpdf%2Fsale.report%22%5D",
        f"{BASE}/report/download",
        # The inline form, and with a document id.
        f"{BASE}/report/pdf/sale.report_saleorder",
        f"{BASE}/report/pdf/account.report_invoice/42",
        f"{BASE}/report/pdf/account.report_invoice/42,43,44",
    ],
)
def test_report_urls_are_recognised(url: str) -> None:
    assert is_report_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        # An attachment or export - a real download the user wants kept.
        f"{BASE}/web/content/1234?download=true",
        f"{BASE}/web/content/ir.attachment/9/datas/invoice.pdf",
        # Barcode images are served under /report but are not documents.
        f"{BASE}/report/barcode/EAN13/1234567890128",
        # Database backups.
        f"{BASE}/web/database/backup",
        # The web client itself.
        f"{BASE}/odoo",
        f"{BASE}/odoo/sales/12",
        # Nothing at all.
        "",
    ],
)
def test_other_urls_are_not_reports(url: str) -> None:
    assert is_report_url(url) is False


def test_text_reports_are_not_treated_as_pdfs() -> None:
    """``/report/text/`` exists but is not something to send to a printer."""
    assert is_report_url(f"{BASE}/report/text/sale.report_saleorder/1") is False


def test_query_string_cannot_smuggle_a_match() -> None:
    """Matching on the path, not the whole URL, keeps this honest."""
    assert is_report_url(f"{BASE}/web/content/5?name=/report/pdf/x") is False


def test_prefixes_are_absolute_paths() -> None:
    for prefix in REPORT_URL_PREFIXES:
        assert prefix.startswith("/"), f"{prefix} is not an absolute path"


# -- where a report lands ---------------------------------------------------


@pytest.fixture
def reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Patch the helper where profile_manager imported it, not where it lives."""
    directory = tmp_path / "reports"
    directory.mkdir()
    monkeypatch.setattr(pm, "reports_dir", lambda: directory)
    return directory


def test_a_second_print_of_one_report_gets_its_own_file(reports: Path) -> None:
    """Two prints of the same invoice arrive with one suggested name.

    Chromium sometimes renames around a collision and sometimes overwrites -
    measured both ways - so the app does not leave it to chance.
    """
    assert pm._unique_report_name("invoice.pdf") == "invoice.pdf"

    (reports / "invoice.pdf").write_bytes(b"%PDF-first")
    assert pm._unique_report_name("invoice.pdf") == "invoice (1).pdf"

    (reports / "invoice (1).pdf").write_bytes(b"%PDF-second")
    assert pm._unique_report_name("invoice.pdf") == "invoice (2).pdf"


def test_a_report_with_no_extension_still_gets_one(reports: Path) -> None:
    assert pm._unique_report_name("invoice") == "invoice.pdf"
    assert pm._unique_report_name("") == "report.pdf"


def test_old_reports_are_pruned_but_recent_ones_are_kept(reports: Path) -> None:
    """One file per printed invoice would otherwise accumulate forever."""
    fresh = reports / "today.pdf"
    stale = reports / "last-week.pdf"
    unrelated = reports / "notes.txt"
    for item in (fresh, stale, unrelated):
        item.write_bytes(b"%PDF-x")

    week_ago = time.time() - 7 * 24 * 60 * 60
    os.utime(stale, (week_ago, week_ago))
    os.utime(unrelated, (week_ago, week_ago))

    pm._prune_reports()

    assert fresh.exists()
    assert not stale.exists()
    assert unrelated.exists(), "only report PDFs are ours to delete"
