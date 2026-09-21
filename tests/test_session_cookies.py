"""The injected session cookie must *replace* Odoo's, not sit beside it.

This is the regression test for the bug that made session recovery look broken
while every part of it worked: the app signed back in over JSON-RPC, got a
valid session, injected it - and the web view was redirected to ``/web/login``
280ms later, over and over, until it gave up with "The Odoo session keeps
expiring".

The cause is a cookie identity rule. A ``QNetworkCookie`` with a domain set is
stored by Chromium as ``.erp.example.com``, a *domain* cookie; Odoo's own
``Set-Cookie`` carries no ``Domain`` attribute and is stored as
``erp.example.com``, host-only. RFC 6265 makes those two different cookies, so
they coexist - and Chromium sends both, oldest first, while werkzeug's
``request.cookies.get('session_id')`` (``odoo/http.py:1486``) takes the first
one it finds.

A fresh profile has no server cookie, so this never showed up on a first
sign-in. Odoo plants a host-only ``session_id`` of its own the first time a
session expires, and from that moment it is the *older* of the two and decides
every request. Measured in a real profile::

    created=2026-09-21 06:34:00  host=demo.fatoora.cloud   session_id  <- dead
    created=2026-09-21 07:25:09  host=.demo.fatoora.cloud  session_id  <- ours

so the shell was authenticating perfectly and being ignored.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView

from bytesraw_erp.data.models import Account
from bytesraw_erp.services.profile_manager import ProfileManager

#: The account's host has to be a *name*. Against ``127.0.0.1`` a ``Domain``
#: attribute cannot create a domain cookie at all, so the two forms collapse
#: into one and the bug this file is about cannot be reproduced - measured,
#: and the reason an earlier attempt at this test passed against a local
#: server while the real server kept failing.
_URL = "https://erp.example.com"
_HOST = "erp.example.com"

#: A profile freed while a page still uses it takes the process with it.
_KEEP_ALIVE: list[object] = []


class _Jar:
    """The cookie store's contents, rebuilt from its add/remove signals.

    ``QWebEngineCookieStore`` has no "list what you hold" call, so the only way
    to see the jar is to watch it change.
    """

    def __init__(self, store) -> None:
        self.cookies: dict[tuple[str, str], str] = {}
        store.cookieAdded.connect(self._added)
        store.cookieRemoved.connect(self._removed)

    def _key(self, cookie: QNetworkCookie) -> tuple[str, str]:
        return bytes(cookie.name()).decode(), cookie.domain()

    def _added(self, cookie: QNetworkCookie) -> None:
        self.cookies[self._key(cookie)] = bytes(cookie.value()).decode()

    def _removed(self, cookie: QNetworkCookie) -> None:
        self.cookies.pop(self._key(cookie), None)

    def named(self, name: str) -> dict[str, str]:
        """Every cookie of this name, keyed by the domain it is stored under."""
        return {domain: value for (n, domain), value in self.cookies.items() if n == name}


@pytest.fixture
def jar(qtbot) -> tuple[ProfileManager, Account, _Jar]:
    profiles = ProfileManager()
    account = Account(url=_URL, database="demo", login="demo")
    profile = profiles.profile_for(account)
    # A profile with no page has no network context, and its cookie store then
    # silently accepts nothing at all - no cookie stored, no signal emitted.
    # Measured; the view is what makes this test able to observe anything.
    view = QWebEngineView()
    view.setPage(QWebEnginePage(profile, view))
    _KEEP_ALIVE.extend((profiles, view))
    qtbot.addWidget(view)
    view.resize(400, 300)
    view.show()
    qtbot.waitExposed(view)
    return profiles, account, _Jar(profile.cookieStore())


def _plant(
    profiles: ProfileManager,
    account: Account,
    name: str,
    value: str,
    *,
    domain: str | None,
) -> None:
    """Put a cookie in the jar the way something other than this app would.

    ``account`` is passed rather than rebuilt: an ``Account`` invents a fresh
    id, so constructing one here would ask for a different profile and plant
    the cookie in a jar nothing else in the test can see.
    """
    cookie = QNetworkCookie(name.encode(), value.encode())
    if domain is not None:
        cookie.setDomain(domain)
    cookie.setPath("/")
    cookie.setHttpOnly(True)
    cookie.setSecure(True)
    profiles.profile_for(account).cookieStore().setCookie(cookie, QUrl(_URL))


def test_an_injection_replaces_the_cookie_odoo_itself_set(
    jar: tuple[ProfileManager, Account, _Jar], qtbot
) -> None:
    """The heart of it: one ``session_id`` afterwards, holding the new value.

    Odoo's is host-only, so the injected one has to be host-only too - that is
    what makes it the *same* cookie rather than a second one Chromium will
    happily carry alongside it.
    """
    profiles, account, seen = jar
    _plant(profiles, account, "session_id", "DEAD-SESSION", domain=None)
    qtbot.waitUntil(lambda: bool(seen.named("session_id")), timeout=10000)

    profiles.inject_session(account, {"session_id": "FRESH-SESSION"})

    qtbot.waitUntil(
        lambda: seen.named("session_id") == {_HOST: "FRESH-SESSION"}, timeout=10000
    )


def test_the_domain_twin_an_older_build_left_behind_is_retired(
    jar: tuple[ProfileManager, Account, _Jar], qtbot
) -> None:
    """Otherwise upgrading fixes nothing for anyone who already has the pair.

    The shadowing cookie is already in the user's profile by the time they get
    a build that stops writing it, and it outlives the fix: it is the older of
    the two, so it would keep deciding every request.
    """
    profiles, account, seen = jar
    _plant(profiles, account, "session_id", "OLD-BUILD-WROTE-THIS", domain=_HOST)
    qtbot.waitUntil(lambda: bool(seen.named("session_id")), timeout=10000)
    assert set(seen.named("session_id")) == {f".{_HOST}"}, "stored as a domain cookie"

    profiles.inject_session(account, {"session_id": "FRESH-SESSION"})

    qtbot.waitUntil(
        lambda: seen.named("session_id") == {_HOST: "FRESH-SESSION"}, timeout=10000
    )


def test_both_halves_of_the_shadowing_pair_are_cleared(
    jar: tuple[ProfileManager, Account, _Jar], qtbot
) -> None:
    """The real field state: Odoo's dead one *and* this app's twin, together."""
    profiles, account, seen = jar
    _plant(profiles, account, "session_id", "DEAD-SESSION", domain=None)
    _plant(profiles, account, "session_id", "OLD-BUILD-WROTE-THIS", domain=_HOST)
    qtbot.waitUntil(lambda: len(seen.named("session_id")) == 2, timeout=10000)

    profiles.inject_session(account, {"session_id": "FRESH-SESSION"})

    qtbot.waitUntil(
        lambda: seen.named("session_id") == {_HOST: "FRESH-SESSION"}, timeout=10000
    )


def test_the_routing_cookie_gets_the_same_treatment(
    jar: tuple[ProfileManager, Account, _Jar], qtbot
) -> None:
    """A load balancer sets its sticky cookie the same way Odoo sets its own.

    A shadowed routing cookie sends the request to a backend that never saw the
    session, which looks exactly like an expiry and is just as hard to read.
    """
    profiles, account, seen = jar
    _plant(profiles, account, "odoo-sticky", "OLD-BACKEND", domain=_HOST)
    qtbot.waitUntil(lambda: bool(seen.named("odoo-sticky")), timeout=10000)

    profiles.inject_session(account, {"odoo-sticky": "RIGHT-BACKEND"})

    qtbot.waitUntil(
        lambda: seen.named("odoo-sticky") == {_HOST: "RIGHT-BACKEND"}, timeout=10000
    )
