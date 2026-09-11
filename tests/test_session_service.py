"""Tests for turning Odoo's ``session_info`` into a :class:`SessionContext`.

The payload shapes below mirror ``addons/web/models/ir_http.py`` in Odoo 19.
"""

from __future__ import annotations

from typing import Any

from bytesraw_erp.services.session_service import _companies_from_session, build_session_context


class FakeClient:
    """Stands in for :class:`OdooClient` without any network access."""

    def __init__(
        self,
        languages: list[dict[str, str]] | None = None,
        native_dark: bool = False,
        user_scheme_field: bool = False,
    ) -> None:
        self._languages = languages if languages is not None else [
            {"code": "en_US", "name": "English (US)"}
        ]
        self.native_dark = native_dark
        self.user_scheme_field = user_scheme_field
        self.scheme_writes: list[tuple[int, str]] = []
        self.calls: list[tuple[str, str, tuple]] = []

    def call_kw(self, model: str, method: str, *args: Any, **_kwargs: Any) -> Any:
        self.calls.append((model, method, args))
        if (model, method) == ("res.lang", "search_read"):
            return self._languages
        return True

    def set_user_color_scheme(self, settings_id: int, scheme: str) -> None:
        self.scheme_writes.append((settings_id, scheme))

    def supports_native_dark_mode(self) -> bool:
        return self.native_dark

    def supports_user_color_scheme(self) -> bool:
        return self.user_scheme_field


def _session_info(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "uid": 2,
        "name": "Mitchell Admin",
        "username": "admin",
        "db": "prod",
        "server_version": "19.0",
        "user_context": {"lang": "en_US", "tz": "Europe/Brussels", "uid": 2},
        "user_companies": {
            "current_company": 1,
            "allowed_companies": {
                "1": {"id": 1, "name": "My Company", "sequence": 10},
            },
        },
    }
    payload.update(overrides)
    return payload


def test_builds_a_context_from_session_info() -> None:
    client = FakeClient()
    context = build_session_context(client, _session_info())

    assert context.uid == 2
    assert context.user_name == "Mitchell Admin"
    assert context.login == "admin"
    assert context.database == "prod"
    assert context.language == "en_US"
    assert context.current_company_name == "My Company"


def test_companies_are_sorted_by_name() -> None:
    info = _session_info(
        user_companies={
            "current_company": 3,
            "allowed_companies": {
                "1": {"id": 1, "name": "Zulu"},
                "3": {"id": 3, "name": "Alpha"},
            },
        }
    )
    companies, current = _companies_from_session(info)
    assert [c.name for c in companies] == ["Alpha", "Zulu"]
    assert current == 3


def test_non_internal_user_has_no_companies() -> None:
    """Odoo omits ``user_companies`` entirely for portal and public users."""
    info = _session_info()
    del info["user_companies"]
    companies, current = _companies_from_session(info)
    assert companies == ()
    assert current == 0


def test_single_language_marks_the_slot_read_only() -> None:
    context = build_session_context(FakeClient(), _session_info())
    assert context.has_multiple_languages is False


def test_several_languages_make_the_slot_interactive() -> None:
    client = FakeClient(
        languages=[
            {"code": "en_US", "name": "English (US)"},
            {"code": "fr_FR", "name": "French"},
        ]
    )
    context = build_session_context(client, _session_info())
    assert context.has_multiple_languages is True


def test_language_lookup_failure_is_tolerated() -> None:
    """A missing language list must not block the user from reaching Odoo."""

    class Failing(FakeClient):
        def call_kw(self, *_args: Any, **_kwargs: Any) -> Any:
            from bytesraw_erp.core.errors import OdooRpcError

            raise OdooRpcError("Access denied")

    context = build_session_context(Failing(), _session_info())
    assert context.languages == ()
    assert context.has_multiple_languages is False


def test_native_dark_mode_is_recorded() -> None:
    """Enterprise swaps its own dark bundle; Community does not."""
    assert build_session_context(FakeClient(), _session_info()).native_dark_mode is False
    enterprise = FakeClient(native_dark=True)
    assert build_session_context(enterprise, _session_info()).native_dark_mode is True


# -- Odoo theme integration --------------------------------------------------


def test_user_settings_id_comes_from_session_info() -> None:
    info = _session_info(user_settings={"id": 7, "color_scheme": "dark"})
    context = build_session_context(FakeClient(user_scheme_field=True), info)
    assert context.user_settings_id == 7
    assert context.user_color_scheme_supported is True
    assert context.can_set_odoo_theme is True


def test_missing_colour_scheme_field_disables_the_write() -> None:
    """Plain Odoo 19 Community has no such field; the app must not try."""
    context = build_session_context(FakeClient(user_scheme_field=False), _session_info())
    assert context.user_color_scheme_supported is False
    assert context.can_set_odoo_theme is False


def test_scheme_write_is_skipped_without_support() -> None:
    from bytesraw_erp.services.session_service import apply_odoo_color_scheme

    client = FakeClient(user_scheme_field=False)
    context = build_session_context(client, _session_info())
    apply_odoo_color_scheme(client, context, "dark")
    assert client.scheme_writes == []


def test_scheme_write_happens_when_supported() -> None:
    from bytesraw_erp.services.session_service import apply_odoo_color_scheme

    client = FakeClient(user_scheme_field=True)
    context = build_session_context(
        client, _session_info(user_settings={"id": 4, "color_scheme": "system"})
    )
    apply_odoo_color_scheme(client, context, "dark")
    assert client.scheme_writes == [(4, "dark")]


def test_set_user_language_writes_the_user_record() -> None:
    from bytesraw_erp.services.session_service import set_user_language

    client = FakeClient()
    set_user_language(client, 2, "fr_FR")
    assert ("res.users", "write", ([[2], {"lang": "fr_FR"}],)) in client.calls
