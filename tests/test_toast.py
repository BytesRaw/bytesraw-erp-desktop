"""Download and print notifications.

These exist because Odoo gives no feedback of its own once the browser takes
over a download: a file saved from the chatter simply appeared to do nothing.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QPushButton, QWidget

from bytesraw_erp.ui.widgets.toast import Toast, ToastArea


@pytest.fixture
def host(qtbot) -> QWidget:
    widget = QWidget()
    widget.resize(800, 600)
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitExposed(widget)
    return widget


def test_a_message_appears_over_the_host(host: QWidget, qtbot) -> None:
    area = ToastArea(host)
    toast = area.show_message("Saved invoice.pdf to Downloads")
    assert toast.isVisible()
    assert toast.parent() is host


def test_the_toast_sits_inside_the_host(host: QWidget) -> None:
    area = ToastArea(host)
    toast = area.show_message("Saved invoice.pdf to Downloads")
    geometry = toast.geometry()
    assert geometry.right() <= host.width()
    assert geometry.bottom() <= host.height()
    assert geometry.left() >= 0
    assert geometry.top() >= 0


def test_it_follows_the_host_being_resized(host: QWidget, qtbot) -> None:
    area = ToastArea(host)
    toast = area.show_message("Saved invoice.pdf")
    host.resize(500, 400)
    qtbot.wait(80)
    assert toast.geometry().right() <= 500
    assert toast.geometry().bottom() <= 400


def test_the_action_runs_and_dismisses(host: QWidget, qtbot) -> None:
    area = ToastArea(host)
    clicked: list[bool] = []
    toast = area.show_message(
        "Saved invoice.pdf",
        action_text="Show in folder",
        on_action=lambda: clicked.append(True),
    )
    button = toast.findChild(QPushButton)
    assert button is not None
    button.click()
    assert clicked == [True]


def test_a_failing_action_does_not_propagate(host: QWidget) -> None:
    """A notification must never be able to take the app down."""
    area = ToastArea(host)

    def boom() -> None:
        raise RuntimeError("nope")

    toast = area.show_message("x", action_text="Do it", on_action=boom)
    toast.findChild(QPushButton).click()  # must not raise


def test_it_disappears_on_its_own(host: QWidget, qtbot) -> None:
    area = ToastArea(host)
    toast = Toast(host, "Saved invoice.pdf", linger_ms=50)
    toast.show()
    area._toasts.append(toast)
    toast.closed.connect(lambda: area._forget(toast))

    with qtbot.waitSignal(toast.closed, timeout=5000):
        pass
    assert area._toasts == []


def test_only_a_few_are_shown_at_once(host: QWidget, qtbot) -> None:
    """A burst of downloads must not bury the page."""
    area = ToastArea(host)
    for index in range(6):
        area.show_message(f"file-{index}.pdf")
    qtbot.wait(700)
    assert len([t for t in area._toasts if t.isVisible()]) <= 3


def test_hovering_stops_the_countdown(host: QWidget, qtbot) -> None:
    """So a toast cannot vanish from under the pointer mid-click."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QEnterEvent

    area = ToastArea(host)
    toast = Toast(host, "Saved invoice.pdf", linger_ms=60)
    toast.show()
    area._toasts.append(toast)
    centre = QPointF(toast.rect().center())
    toast.enterEvent(QEnterEvent(centre, centre, centre))
    qtbot.wait(300)
    assert toast.isVisible()
