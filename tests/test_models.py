"""Model-layer tests. These run without a QApplication by design."""

from __future__ import annotations

from dataclasses import replace

import pytest

from bytesraw_erp.data.models import (
    Account,
    Company,
    Language,
    SessionContext,
    normalize_base_url,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("mycompany.odoo.com", "https://mycompany.odoo.com"),
        ("https://erp.example.com/", "https://erp.example.com"),
        ("https://erp.example.com/odoo/sales", "https://erp.example.com"),
        ("http://localhost:8069", "http://localhost:8069"),
        ("  HTTPS://ERP.example.com  ", "https://ERP.example.com"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalize_base_url(raw: str, expected: str) -> None:
    assert normalize_base_url(raw) == expected


def test_account_defaults_name_to_host() -> None:
    account = Account(url="https://erp.example.com", database="prod", login="a@b.c")
    assert account.name == "erp.example.com"


def test_account_url_for_joins_without_double_slash() -> None:
    account = Account(url="https://erp.example.com/")
    assert account.url_for("/odoo/sales") == "https://erp.example.com/odoo/sales"
    assert account.url_for("odoo/sales") == "https://erp.example.com/odoo/sales"


def test_home_url_uses_remembered_path() -> None:
    account = Account(url="https://erp.example.com", last_path="/odoo/sales/7")
    assert account.home_url == "https://erp.example.com/odoo/sales/7"


def test_home_url_falls_back_to_odoo_root() -> None:
    account = Account(url="https://erp.example.com", last_path="")
    assert account.home_url == "https://erp.example.com/odoo"


def test_account_round_trips_through_dict() -> None:
    account = Account(
        url="https://erp.example.com",
        database="prod",
        login="a@b.c",
        last_path="/odoo/crm",
        allow_untrusted_certificate=True,
    )
    assert Account.from_dict(account.to_dict()) == account


def test_account_dict_never_carries_a_password() -> None:
    account = Account(url="https://erp.example.com", database="prod", login="a@b.c")
    assert "password" not in account.to_dict()


def test_evolve_keeps_identity() -> None:
    account = Account(url="https://erp.example.com", database="prod", login="a@b.c")
    moved = account.evolve(last_path="/odoo/stock")
    assert moved.id == account.id
    assert moved.last_path == "/odoo/stock"
    assert account.last_path == "/odoo"


def _context(companies: tuple[Company, ...], languages: tuple[Language, ...]) -> SessionContext:
    return SessionContext(
        uid=2,
        user_name="Mitchell Admin",
        login="admin",
        database="prod",
        language="en_US",
        server_version="19.0",
        companies=companies,
        current_company_id=companies[0].id if companies else 0,
        languages=languages,
    )


def test_single_language_is_not_multiple() -> None:
    context = _context((Company(1, "My Company"),), (Language("en_US", "English"),))
    assert context.has_multiple_languages is False
    assert context.current_company_name == "My Company"
    assert context.current_language_name == "English"


def test_several_languages_are_multiple() -> None:
    context = _context(
        (Company(1, "My Company"), Company(3, "Branch")),
        (Language("en_US", "English"), Language("fr_FR", "French")),
    )
    assert context.has_multiple_languages is True


def test_company_name_resolves_from_the_current_id() -> None:
    """The app bar shows this as plain text; switching is Odoo's job."""
    context = _context((Company(1, "Alpha"), Company(3, "Beta")), ())
    assert context.current_company_name == "Alpha"
    assert replace(context, current_company_id=3).current_company_name == "Beta"


def test_unknown_current_company_yields_an_empty_name() -> None:
    """Better a blank slot than a crash if the session and list disagree."""
    context = replace(_context((Company(1, "Alpha"),), ()), current_company_id=99)
    assert context.current_company is None
    assert context.current_company_name == ""


def test_current_language_name_falls_back_to_the_code() -> None:
    context = _context((Company(1, "My Company"),), ())
    assert context.current_language_name == "en_US"
