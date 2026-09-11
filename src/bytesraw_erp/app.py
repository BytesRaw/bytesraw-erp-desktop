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


def build_application(argv: list[str] | None = None) -> QApplication:
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
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
    app = build_application(argv)
    setup_logging()
    _log.info("Starting %s %s", APP_NAME, APP_VERSION)

    # The theme must be applied before any widget is built, so the first
    # paint is already correct rather than flashing light then re-styling.
    theme = ThemeController(app, app)
    theme.apply()

    context = AppContext(theme, app)
    window = MainWindow(context)
    window.show()
    window.start()

    app.aboutToQuit.connect(context.shutdown)
    return app.exec()
