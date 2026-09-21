"""Odoo's own client losing the session, seen from inside the web view.

This is the detector the other three could not cover. When a session is revoked
while someone is working, Odoo's web client catches
``odoo.http.SessionExpiredException`` from its own RPC call and hands it to
``SessionExpiredDialog`` (``addons/web/static/src/core/errors/error_dialogs.js``),
which shows "Your Odoo session expired. The current page is about to be
refreshed." and then waits for a click. So nothing navigates, no RPC of *ours*
fails, and the shell learns nothing until its five-minute probe comes round -
the user sits in front of a dead client the whole time.

The script injected by :mod:`bytesraw_erp.ui.widgets.web_view` reads the fault
out of the response as it arrives. The tests below drive a real ``XMLHttpRequest``
against a local server answering exactly the JSON-RPC shapes Odoo answers with,
because the thing being tested is a piece of JavaScript running inside
Chromium: asserting on the Python around it would prove nothing.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bytesraw_erp.constants import SESSION_EXPIRED_NAME
from bytesraw_erp.data.models import Account
from bytesraw_erp.services.profile_manager import ProfileManager
from bytesraw_erp.ui.widgets.web_view import OdooWebView

#: The fault Odoo puts on the wire for an expired session - an HTTP **200**
#: carrying an ``error`` member, which is what makes the status code useless as
#: a signal and ``data.name`` the thing to look for.
_EXPIRED = {
    "jsonrpc": "2.0",
    "id": 7,
    "error": {
        "code": 100,
        "message": "Odoo Session Expired",
        "data": {"name": SESSION_EXPIRED_NAME, "message": "Session expired", "debug": ""},
    },
}

#: A different fault entirely, and one the shell must not treat as an expiry:
#: recovering from it would sign the user in again over a validation error.
_USER_ERROR = {
    "jsonrpc": "2.0",
    "id": 8,
    "error": {
        "code": 200,
        "message": "Odoo Server Error",
        "data": {"name": "odoo.exceptions.UserError", "message": "No.", "debug": ""},
    },
}

_OK = {"jsonrpc": "2.0", "id": 9, "result": {"uid": 2, "name": "Mitchell Admin"}}

#: Odoo's own RPC goes through ``browser.XMLHttpRequest``
#: (``addons/web/static/src/core/network/rpc.js``), so this is the call shape
#: the injected script has to catch.
#:
#: ``__done`` is incremented from a listener registered **after** ``send``, so
#: that it can only be raised once the listener the injected script adds
#: *during* ``send`` has already run. A test that waited on a listener
#: registered first would be reading the counter one callback too early.
_PAGE = """<html><body><div id="ready">page</div><script>
window.__done = 0;
window.callOdoo = function (path) {
  const xhr = new XMLHttpRequest();
  xhr.open('POST', path);
  xhr.setRequestHeader('Content-Type', 'application/json');
  xhr.send(JSON.stringify({jsonrpc: '2.0', method: 'call', params: {}}));
  xhr.addEventListener('load', function () { window.__done += 1; });
};
</script></body></html>"""

_BODIES = {
    "/expired": _EXPIRED,
    "/user-error": _USER_ERROR,
    "/ok": _OK,
}


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        payload = _BODIES.get(self.path.split("?")[0])
        if payload is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        body = _PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        """Silence the access log."""


@pytest.fixture(scope="module")
def server() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


#: A profile freed while a page still uses it takes the process with it, and
#: ``qtbot`` destroys the widget after the test body has dropped its locals.
_KEEP_ALIVE: list[object] = []


def _view(base_url: str, qtbot) -> OdooWebView:
    profiles = ProfileManager()
    account = Account(url=base_url, database="demo", login="demo")
    view = OdooWebView(account, profiles.profile_for(account))
    _KEEP_ALIVE.extend((profiles, view))
    qtbot.addWidget(view)
    view.resize(600, 400)
    view.show()
    qtbot.waitExposed(view)
    with qtbot.waitSignal(view.loadFinished, timeout=20000):
        view.open_path("/")
    return view


def _answered(view: OdooWebView, qtbot) -> int:
    """How many of the page's calls have come back."""
    box: list[object] = []
    view.page().runJavaScript("window.__done", box.append)
    qtbot.waitUntil(lambda: bool(box), timeout=10000)
    return int(box[0] or 0)


def test_an_expired_session_is_reported_the_moment_odoo_sees_it(
    server: str, qtbot
) -> None:
    """The fix: no navigation, no click on Odoo's dialog, no five-minute wait."""
    view = _view(server, qtbot)

    with qtbot.waitSignal(view.session_expired, timeout=20000):
        view.page().runJavaScript("window.callOdoo('/expired')")


def test_an_ordinary_fault_is_not_an_expiry(server: str, qtbot) -> None:
    """Signing the user in again over a validation error would be absurd."""
    view = _view(server, qtbot)
    seen: list[int] = []
    view.session_expired.connect(lambda: seen.append(1))

    view.page().runJavaScript("window.callOdoo('/user-error')")
    qtbot.waitUntil(lambda: _answered(view, qtbot) >= 1, timeout=20000)

    assert seen == []


def test_a_successful_call_is_not_an_expiry(server: str, qtbot) -> None:
    view = _view(server, qtbot)
    seen: list[int] = []
    view.session_expired.connect(lambda: seen.append(1))

    view.page().runJavaScript("window.callOdoo('/ok')")
    qtbot.waitUntil(lambda: _answered(view, qtbot) >= 1, timeout=20000)

    assert seen == []
