"""Application bootstrap.

Import order matters: ``QtWebEngineWidgets`` must be imported before the
``QApplication`` is constructed, otherwise QtWebEngine cannot initialise its
Chromium process and the first web view aborts the app.

Two more things have to be settled before that construction, and each is read
once and never again: the application and organisation names, because
``QStandardPaths`` derives every directory from them, and the rendering mode,
because QtWebEngine reads Chromium's command line as it starts. The settings
file is therefore loaded here rather than inside the ``AppContext``, and the
loaded store is handed on so that it is read exactly once.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Final

from PySide6 import QtWebEngineWidgets  # noqa: F401  (import-order requirement)
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication

from bytesraw_erp.constants import APP_NAME, APP_VERSION, ORG_DOMAIN, ORG_NAME
from bytesraw_erp.core.logging_setup import setup_logging
from bytesraw_erp.core.resources import app_icon
from bytesraw_erp.data.models import RenderMode
from bytesraw_erp.data.settings_store import SettingsStore
from bytesraw_erp.services.graphics import configure_rendering
from bytesraw_erp.services.single_instance import SingleInstanceGuard
from bytesraw_erp.services.update_service import is_installed_build
from bytesraw_erp.ui.app_context import AppContext
from bytesraw_erp.ui.main_window import MainWindow
from bytesraw_erp.ui.theme import ThemeController

_log = logging.getLogger(__name__)


#: Identity Windows groups taskbar buttons and pinned shortcuts under.
_APP_USER_MODEL_ID = "BytesRaw.BytesrawERP"

#: Launch the shell in an ordinary resizable window instead of full screen.
#: A till wants the full-screen default; a developer, and a back-office machine
#: that also runs something else, want the desktop back. It decides only where
#: the window *starts*: the caption buttons carry a maximise/restore pair and a
#: full-screen toggle in either mode - see
#: :mod:`bytesraw_erp.ui.widgets.window_controls`.
_WINDOWED_FLAGS = ("--windowed", "-w")

#: Override the stored rendering mode for this launch only. The settings page
#: is the normal way in; these are for the machine whose web view is too
#: corrupt to read that page with, and for undoing a stored value that turned
#: out to be the wrong one. Deliberately not persisted - a flag on one shortcut
#: should not change how every other launch behaves.
_SOFTWARE_RENDER_FLAGS = ("--software-render",)
_GPU_RENDER_FLAGS = ("--gpu-render",)


@dataclass(frozen=True, slots=True)
class LaunchOptions:
    """What the command line asked for, with Qt's own arguments left intact."""

    #: ``argv`` with the app's own flags removed, ready to hand to Qt.
    argv: list[str]
    windowed: bool = False
    #: ``None`` means "whatever ``settings.json`` says".
    render_mode: RenderMode | None = None


def parse_arguments(argv: list[str]) -> LaunchOptions:
    """Split the app's own flags out of ``argv`` before Qt ever sees it.

    Qt parses its own switches out of the argument vector it is handed and
    warns about what it does not recognise, so the app's flags are removed
    here rather than left for it to complain about.
    """
    remaining: list[str] = []
    windowed = False
    render_mode: RenderMode | None = None
    for argument in argv:
        if argument in _WINDOWED_FLAGS:
            windowed = True
        elif argument in _SOFTWARE_RENDER_FLAGS:
            render_mode = RenderMode.SOFTWARE
        elif argument in _GPU_RENDER_FLAGS:
            render_mode = RenderMode.AUTO
        else:
            remaining.append(argument)
    return LaunchOptions(argv=remaining, windowed=windowed, render_mode=render_mode)


def _name_the_application() -> None:
    """Set the names ``QStandardPaths`` resolves every directory from.

    These are static properties of ``QCoreApplication``, so they can - and
    here must - be set before an instance exists: the settings file is read
    before the ``QApplication`` is constructed, and without a name it would be
    looked for in the wrong directory.
    """
    QCoreApplication.setApplicationName(APP_NAME)
    QCoreApplication.setApplicationVersion(APP_VERSION)
    QCoreApplication.setOrganizationName(ORG_NAME)
    QCoreApplication.setOrganizationDomain(ORG_DOMAIN)


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


#: What Windows may restart this process for. Only the patch case is wanted -
#: an installer replacing the files under a running till - so the crash, hang
#: and reboot cases are all declined. ``RESTART_NO_PATCH`` is deliberately not
#: among them: it is the one case this registration exists for.
_RESTART_NO_CRASH: Final[int] = 1
_RESTART_NO_HANG: Final[int] = 2
_RESTART_NO_REBOOT: Final[int] = 8


def _register_for_restart() -> None:
    """Let an upgrade put the till back the way it found it.

    ``/RESTARTAPPLICATIONS`` asks Inno to restart what it closed, and Inno asks
    the Restart Manager, which restarts **only** the processes that registered
    here. Measured against an installed 0.2.0 by driving the Restart Manager's
    own API: this process is listed as ``RmMainWindow`` with
    ``bRestartable=False``, so the switch was asking for something that could
    never happen and an upgrade left the till showing the desktop.

    ``None`` for the command line means "no arguments": Windows supplies the
    executable path itself, and passing it again would arrive as ``argv[1]``.
    Only for a frozen build, because restarting a source checkout would mean
    relaunching ``python.exe`` with nothing to run. Cosmetic in the same sense
    as :func:`_claim_windows_identity` - a failure here must never cost a
    startup, and the installer's own ``/RELAUNCH`` entry covers the same
    ground for builds that predate this.
    """
    if sys.platform != "win32" or not is_installed_build():
        return
    try:
        import ctypes

        result = int(
            ctypes.windll.kernel32.RegisterApplicationRestart(
                None, _RESTART_NO_CRASH | _RESTART_NO_HANG | _RESTART_NO_REBOOT
            )
        )
    except (AttributeError, OSError) as exc:  # pragma: no cover - Windows detail
        _log.debug("Could not register for restart after an upgrade: %s", exc)
        return
    if result != 0:  # pragma: no cover - Windows detail
        _log.debug("Windows declined the restart registration (0x%08X)", result & 0xFFFFFFFF)


def build_application(argv: list[str] | None = None) -> QApplication:
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    # Drives QStandardPaths, so this must happen before any path is resolved.
    # ``run`` has already called it, because it reads the settings file first;
    # it is idempotent, and a caller that builds the application on its own
    # still gets correctly named directories.
    _name_the_application()
    _claim_windows_identity()
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationDisplayName(APP_NAME)
    # Window, taskbar and alt-tab icon. Set on the application so
    # every window and dialog inherits it.
    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    return app


def run(argv: list[str] | None = None) -> int:
    options = parse_arguments(list(argv if argv is not None else sys.argv))
    windowed = options.windowed

    # Before the QApplication, both of them: the names so the settings file is
    # looked for in the right place, and the rendering mode because QtWebEngine
    # reads Chromium's command line out of the environment as it starts.
    # Logging comes first of all, so the rendering decision - the thing a
    # garbled web view has to be diagnosed from - is in the log file.
    _name_the_application()
    setup_logging()
    settings = SettingsStore()
    settings.load()
    configure_rendering(options.render_mode or settings.display.render_mode)

    app = build_application(options.argv)

    # Before anything with state behind it - a second copy would open the same
    # account profiles over the same on-disk storage. Claimed after the
    # QApplication because the pipe needs its event dispatcher, and before the
    # context because the context is the first thing that touches those
    # directories.
    guard = SingleInstanceGuard(app)
    if not guard.claim():
        _log.info("%s is already running; asked the running copy to come forward", APP_NAME)
        return 0

    _log.info(
        "Starting %s %s (%s)",
        APP_NAME,
        APP_VERSION,
        "windowed" if windowed else "full screen",
    )

    # After the guard, because the copy that loses the claim is about to exit
    # and has nothing to be restarted for.
    _register_for_restart()

    # The theme must be applied before any widget is built, so the first
    # paint is already correct rather than flashing light then re-styling.
    theme = ThemeController(app, app)
    theme.apply()

    context = AppContext(theme, app, windowed=windowed, settings=settings)
    window = MainWindow(context)
    # A later launch is a request to see the window that already exists, not
    # to start another one.
    guard.activation_requested.connect(window.present)
    if windowed:
        window.show()
    else:
        # Full screen from the first paint: ``show()`` first would flash a
        # framed window at its restore size before it switched.
        window.showFullScreen()
    window.start()
    # After the window, deliberately: the first check is delayed anyway, and
    # arming it earlier would only put an update request in front of the
    # sign-in that the user is actually waiting for.
    context.updates.start()

    app.aboutToQuit.connect(context.shutdown)
    app.aboutToQuit.connect(guard.close)
    return app.exec()
