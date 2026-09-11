"""Declarative, path-based navigation over a :class:`QStackedWidget`.

Modelled on Flet's ``ft.Router``: routes are registered as path patterns with
dynamic ``:segments``, pages are built lazily by a factory the first time their
route is visited, and the router keeps a history stack so :meth:`back` works.

Pages may implement either hook; both are optional:

``on_enter(params: dict[str, str]) -> None``
    Called every time the page becomes current, with the matched path params.
``on_leave() -> None``
    Called when navigating away.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QStackedWidget, QWidget

_log = logging.getLogger(__name__)

PageFactory = Callable[[], QWidget]

_SEGMENT_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")


def _compile(pattern: str) -> re.Pattern[str]:
    """Turn ``/accounts/:account_id/edit`` into an anchored regex."""
    escaped = re.escape(pattern.rstrip("/") or "/")
    # re.escape leaves ':' alone but escapes nothing else we rely on.
    regex = _SEGMENT_RE.sub(lambda m: f"(?P<{m.group(1)}>[^/]+)", escaped)
    return re.compile(f"^{regex}/?$")


@dataclass(slots=True)
class _Route:
    pattern: str
    matcher: re.Pattern[str]
    factory: PageFactory
    keep_alive: bool
    widget: QWidget | None = None


class Router(QObject):
    """Owns the page stack and the navigation history."""

    route_changed = Signal(str)

    def __init__(self, stack: QStackedWidget, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._stack = stack
        self._routes: list[_Route] = []
        self._history: list[str] = []

    # -- registration ------------------------------------------------------

    def register(self, pattern: str, factory: PageFactory, *, keep_alive: bool = True) -> None:
        """Bind a path pattern to a page factory.

        ``keep_alive=False`` rebuilds the page on every visit - use it for
        forms, which must not show the previous visit's input. The Odoo page is
        kept alive so the embedded browser is never torn down and re-created.
        """
        self._routes.append(_Route(pattern, _compile(pattern), factory, keep_alive))

    # -- navigation --------------------------------------------------------

    @property
    def current_route(self) -> str | None:
        return self._history[-1] if self._history else None

    def can_go_back(self) -> bool:
        return len(self._history) > 1

    def go(self, path: str, *, replace: bool = False) -> bool:
        """Navigate to ``path``. Returns ``False`` when no route matches."""
        path = "/" + path.strip("/") if path.strip("/") else "/"
        resolved = self._resolve(path)
        if resolved is None:
            _log.error("No route registered for %s", path)
            return False

        route, params = resolved
        self._leave_current()

        widget = route.widget
        if widget is None or not route.keep_alive:
            if widget is not None:
                self._stack.removeWidget(widget)
                widget.deleteLater()
            widget = route.factory()
            route.widget = widget
            self._stack.addWidget(widget)

        self._stack.setCurrentWidget(widget)

        if replace and self._history:
            self._history[-1] = path
        else:
            self._history.append(path)

        enter = getattr(widget, "on_enter", None)
        if callable(enter):
            enter(params)

        _log.debug("Navigated to %s", path)
        self.route_changed.emit(path)
        return True

    def back(self) -> bool:
        """Pop the history stack. Returns ``False`` at the root."""
        if not self.can_go_back():
            return False
        self._history.pop()
        target = self._history.pop()
        return self.go(target)

    def reset_to(self, path: str) -> bool:
        """Clear the history and navigate - used when switching accounts."""
        self._history.clear()
        return self.go(path)

    # -- internals ---------------------------------------------------------

    def _resolve(self, path: str) -> tuple[_Route, dict[str, str]] | None:
        for route in self._routes:
            match = route.matcher.match(path)
            if match is not None:
                return route, match.groupdict()
        return None

    def _leave_current(self) -> None:
        widget = self._stack.currentWidget()
        leave = getattr(widget, "on_leave", None)
        if callable(leave):
            leave()
