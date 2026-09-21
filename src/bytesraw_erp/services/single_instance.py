"""One running copy of the shell, and what a second launch does instead.

Double-clicking the desktop icon while the app is already open used to start a
whole second process: a second Chromium, a second set of QtWebEngine profiles
over the same on-disk storage directories, and two windows showing the same
till. This claims a named pipe on the way up, so the second launch finds the
first, asks it to come to the front and exits before it has built anything.

Two details are worth stating, because both are easy to get wrong:

**The name is per user, not per machine.** On Windows a ``QLocalServer`` is a
named pipe, and the pipe namespace is machine-global - so on a machine with
fast user switching or an RDP session, one user's launch would otherwise find
*another* user's copy and raise a window they cannot see. The login name is
part of the pipe name for that reason.

**Windows will not let the running copy raise itself.** ``SetForegroundWindow``
is refused to a process the user is not currently interacting with, so
``raise_()`` and ``activateWindow()`` from the instance that is already running
flash the taskbar button and nothing more. The process being *launched* is the
one holding the foreground right, and ``AllowSetForegroundWindow`` is how it
hands that right over - so it is called here, in the copy that is about to
exit, immediately before it pings the one that stays.

Nothing here is allowed to cost a launch. A pipe that cannot be claimed is
logged and ignored, which degrades to the old behaviour rather than to a till
that will not start.
"""

from __future__ import annotations

import getpass
import logging
import sys

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

_log = logging.getLogger(__name__)

#: Sent by the second launch. Nothing reads it - a connection is the whole
#: message - but an empty write would leave the pipe looking idle.
_PING = b"present\n"

#: Both generous for a local pipe and short enough that a launch never appears
#: to hang on one: a running instance answers in single-digit milliseconds, and
#: a name nothing is listening on fails immediately rather than timing out.
_CONNECT_TIMEOUT_MS = 500
_WRITE_TIMEOUT_MS = 500

#: ``ASFW_ANY``: let any process take the foreground, rather than naming one.
#: Naming one would mean learning the running copy's process id first, which
#: is a round trip for no gain - the right is given up for the instant it takes
#: this process to exit.
_ASFW_ANY = -1


def _pipe_name() -> str:
    """The rendezvous name, scoped to the user this copy is running as."""
    try:
        user = getpass.getuser()
    except Exception:  # pragma: no cover - no name in the environment at all
        user = "default"
    return f"BytesrawERP-{user}"


def _hand_over_the_foreground() -> None:
    """Let the copy that is already running raise itself in front of us."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(_ASFW_ANY)
    except (AttributeError, OSError) as exc:  # pragma: no cover - shell detail
        _log.debug("Could not hand over the foreground right: %s", exc)


class SingleInstanceGuard(QObject):
    """Claims the running-instance name, and relays what later launches want."""

    #: Another launch asked for the existing window to be brought forward.
    activation_requested = Signal()

    def __init__(self, parent: QObject | None = None, *, name: str | None = None) -> None:
        super().__init__(parent)
        #: Overridable so a test can claim a name of its own. The application
        #: never passes one: two copies have to agree on it, and the only way
        #: they can is by both deriving it the same way.
        self._name = name or _pipe_name()
        self._server: QLocalServer | None = None

    def claim(self) -> bool:
        """Become *the* instance, or tell the one that already is.

        Returns ``True`` when this process should carry on starting up, and
        ``False`` when another copy answered and was asked to come forward -
        in which case the caller must exit without building a window.
        """
        name = self._name
        if self._ping(name):
            return False

        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(name):
            # Nothing answered the ping, so whatever is holding the name is a
            # leftover from a copy that did not shut down cleanly. Removing it
            # is only safe *because* of that ping, which is why the order here
            # is not an accident.
            QLocalServer.removeServer(name)
            if not server.listen(name):
                _log.warning(
                    "Could not claim the single-instance name %s: %s",
                    name,
                    server.errorString(),
                )
                return True

        server.newConnection.connect(self._on_connection)
        self._server = server
        _log.debug("Listening for later launches on %s", name)
        return True

    def close(self) -> None:
        """Stop listening, so the name is free for the next launch."""
        if self._server is not None:
            self._server.close()
            self._server = None

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _ping(name: str) -> bool:
        """Is a copy already listening? If so, ask it to come to the front."""
        socket = QLocalSocket()
        socket.connectToServer(name)
        if not socket.waitForConnected(_CONNECT_TIMEOUT_MS):
            return False
        # Before the write, not after: the running copy may raise its window
        # the moment the connection lands, and by then this process has to
        # have given up its claim on the foreground.
        _hand_over_the_foreground()
        socket.write(_PING)
        socket.flush()
        socket.waitForBytesWritten(_WRITE_TIMEOUT_MS)
        socket.disconnectFromServer()
        return True

    def _on_connection(self) -> None:
        server = self._server
        if server is None:
            return
        while server.hasPendingConnections():
            connection = server.nextPendingConnection()
            connection.disconnected.connect(connection.deleteLater)
            self.activation_requested.emit()
