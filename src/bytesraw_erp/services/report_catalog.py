"""The PDF reports an Odoo database can print, for choosing printers by report.

Read over the signed-in RPC session, so the settings page can offer every
report by the name Odoo shows it under. Two things about it:

* **Only an administrator can read the list.** Odoo 19 grants
  ``ir.actions.report`` to ``base.group_system`` alone
  (``odoo/addons/base/security/ir.model.access.csv``); the web client gets its
  Print menus through a sudo'd binding lookup instead. A cashier's session is
  therefore refused, and that is reported as :class:`ReportListUnavailable`
  rather than as a failure - the settings page still offers the reports this
  computer has printed, which needs no rights at all.
* **The key is the technical name, never the title.** Titles are translated
  and are not unique - Odoo ships two reports called "Package Barcode (PDF)".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from bytesraw_erp.core.errors import BytesrawError, OdooRpcError
from bytesraw_erp.services.odoo_client import OdooClient

_log = logging.getLogger(__name__)

#: ``serialize_exception``'s name for a refused read - untranslated, unlike the
#: message, so it is what the refusal is recognised by.
_ACCESS_ERROR = "odoo.exceptions.AccessError"


class ReportListUnavailable(BytesrawError):
    """This Odoo user is not allowed to list reports. Message is end-user readable."""


@dataclass(frozen=True, slots=True)
class ReportInfo:
    """One ``ir.actions.report`` that renders a PDF."""

    report_name: str
    title: str
    model: str = ""


def fetch_reports(client: OdooClient, language: str = "") -> list[ReportInfo]:
    """Every PDF report on the database, ordered by title.

    Blocking - call through ``run_async``. ``language`` asks for the titles in
    the user's language, so the list reads the way Odoo's own Print menu does.
    """
    kwargs: dict[str, object] = {
        "fields": ["name", "report_name", "model"],
        "order": "name, report_name",
    }
    if language:
        kwargs["context"] = {"lang": language}
    try:
        rows = client.call_kw(
            "ir.actions.report",
            "search_read",
            [[["report_type", "=", "qweb-pdf"]]],
            kwargs,
        )
    except OdooRpcError as exc:
        if exc.name == _ACCESS_ERROR:
            raise ReportListUnavailable(
                "Only an Odoo administrator can list every report. Print a "
                "report once and it is offered here."
            ) from exc
        raise
    reports = [
        ReportInfo(
            report_name=str(row.get("report_name") or ""),
            title=str(row.get("name") or ""),
            model=str(row.get("model") or ""),
        )
        for row in rows or []
        if row.get("report_name")
    ]
    _log.info("Read %d PDF reports from %s", len(reports), client.base_url)
    return reports
