"""Router tests. These need a QApplication, provided by ``pytest-qt``."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLabel, QStackedWidget, QWidget

from bytesraw_erp.ui.router import Router


class RecordingPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.entries: list[dict[str, str]] = []
        self.leaves = 0

    def on_enter(self, params: dict[str, str]) -> None:
        self.entries.append(params)

    def on_leave(self) -> None:
        self.leaves += 1


@pytest.fixture
def router(qtbot) -> Router:
    stack = QStackedWidget()
    qtbot.addWidget(stack)
    return Router(stack)


def test_unknown_route_is_refused(router: Router) -> None:
    assert router.go("/nowhere") is False
    assert router.current_route is None


def test_navigating_sets_the_current_route(router: Router) -> None:
    router.register("/accounts", QLabel)
    assert router.go("/accounts") is True
    assert router.current_route == "/accounts"


def test_dynamic_segments_reach_the_page(router: Router) -> None:
    page = RecordingPage()
    router.register("/accounts/:account_id/edit", lambda: page)
    router.go("/accounts/abc123/edit")
    assert page.entries == [{"account_id": "abc123"}]


def test_on_leave_fires_when_navigating_away(router: Router) -> None:
    page = RecordingPage()
    router.register("/a", lambda: page)
    router.register("/b", QLabel)
    router.go("/a")
    router.go("/b")
    assert page.leaves == 1


def test_keep_alive_reuses_the_same_widget(router: Router) -> None:
    built: list[QWidget] = []

    def factory() -> QWidget:
        widget = QLabel()
        built.append(widget)
        return widget

    router.register("/a", factory, keep_alive=True)
    router.register("/b", QLabel)
    router.go("/a")
    router.go("/b")
    router.go("/a")
    assert len(built) == 1


def test_transient_pages_are_rebuilt(router: Router) -> None:
    built: list[QWidget] = []

    def factory() -> QWidget:
        widget = QLabel()
        built.append(widget)
        return widget

    router.register("/form", factory, keep_alive=False)
    router.register("/other", QLabel)
    router.go("/form")
    router.go("/other")
    router.go("/form")
    assert len(built) == 2


def test_back_returns_to_the_previous_route(router: Router) -> None:
    router.register("/a", QLabel)
    router.register("/b", QLabel)
    router.go("/a")
    router.go("/b")
    assert router.back() is True
    assert router.current_route == "/a"


def test_back_at_the_root_is_a_no_op(router: Router) -> None:
    router.register("/a", QLabel)
    router.go("/a")
    assert router.back() is False
    assert router.current_route == "/a"


def test_reset_to_clears_the_history(router: Router) -> None:
    router.register("/a", QLabel)
    router.register("/b", QLabel)
    router.go("/a")
    router.go("/b")
    router.reset_to("/a")
    assert router.can_go_back() is False


def test_trailing_slashes_are_equivalent(router: Router) -> None:
    router.register("/accounts", QLabel)
    assert router.go("/accounts/") is True
    assert router.current_route == "/accounts"


def test_route_changed_is_emitted(router: Router, qtbot) -> None:
    router.register("/a", QLabel)
    with qtbot.waitSignal(router.route_changed) as blocker:
        router.go("/a")
    assert blocker.args == ["/a"]
