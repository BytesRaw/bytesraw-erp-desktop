"""Application bootstrap.

Import order matters: ``QtWebEngineWidgets`` must be imported before the
``QApplication`` is constructed, otherwise QtWebEngine cannot initialise its
Chromium process and the first web view aborts the app.
"""

from __future__ import annotations

import logging
import sys

from PySide6 import QtWebEngineWidgets  # noqa: F401  (import-order requirement)
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication

from bytesraw_erp.constants import APP_NAME, APP_VERSION, ORG_DOMAIN, ORG_NAME
from bytesraw_erp.core.logging_setup import setup_logging
from bytesraw_erp.core.resources import app_icon
from bytesraw_erp.ui.app_context import AppContext
from bytesraw_erp.ui.main_window import MainWindow
from bytesraw_erp.ui.theme import ThemeController

_log = logging.getLogger(__name__)


#: Identity Windows groups taskbar buttons and pinned shortcuts under.
_APP_USER_MODEL_ID = "BytesRaw.BytesrawERP"

#: Launch the shell in an ordinary resizable window instead of full screen.
#: A till wants the full-screen default; a developer, and a back-office machine
#: that also runs something else, want the desktop back. Only in this mode does
#: the window carry a full-screen toggle - see
#: :mod:`bytesraw_erp.ui.widgets.window_controls`.
_WINDOWED_FLAGS = ("--windowed", "-w")


def _take_windowed_flag(argv: list[str]) -> tuple[list[str], bool]:
    """Split the flag out of ``argv`` before Qt ever sees it.

    Qt parses its own switches out of the argument vector it is handed and
    warns about what it does not recognise, so the app's flags are removed
    here rather than left for it to complain about.
    """
    remaining = [arg for arg in argv if arg not in _WINDOWED_FLAGS]
    return remaining, len(remaining) != len(argv)


def _claim_windows_identity() -> None:
    """Tell the Windows shell this process is Bytesraw ERP, not Python.

    Without an explicit AppUserModelID the shell inherits the host
    executable's - so a window whose icon is set correctly still shows the
    Python launcher's icon in the taskbar, and pins itself as Python. Purely
    cosmetic and entirely optional: any failure leaves the app running.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            _APP_USER_MODEL_ID
        )
    except (AttributeError, OSError) as exc:  # pragma: no cover - shell detail
        _log.debug("Could not set the AppUserModelID: %s", exc)


def build_application(argv: list[str] | None = None) -> QApplication:
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    _claim_windows_identity()
    app = QApplication(argv if argv is not None else sys.argv)
    # Drives QStandardPaths, so these must be set before any path is resolved.
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(ORG_NAME)
    app.setOrganizationDomain(ORG_DOMAIN)
    # Window, taskbar and alt-tab icon. Set on the application so
    # every window and dialog inherits it.
    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    return app


def run(argv: list[str] | None = None) -> int:
    arguments, windowed = _take_windowed_flag(
        list(argv if argv is not None else sys.argv)
    )
    app = build_application(arguments)
    setup_logging()
    _log.info(
        "Starting %s %s (%s)",
        APP_NAME,
        APP_VERSION,
        "windowed" if windowed else "full screen",
    )

    # The theme must be applied before any widget is built, so the first
    # paint is already correct rather than flashing light then re-styling.
    theme = ThemeController(app, app)
    theme.apply()

    context = AppContext(theme, app, windowed=windowed)
    window = MainWindow(context)
    if windowed:
        window.show()
    else:
        # Full screen from the first paint - the window has no other size, and
        # ``show()`` first would flash a framed window before it switched.
        window.showFullScreen()
    window.start()

    app.aboutToQuit.connect(context.shutdown)
    return app.exec()
