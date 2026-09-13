"""The single application window and its route table.

The window is full screen for its whole life. That is a product decision, not a
default: this is a till and back-office shell, and a resizable window invites
someone to leave a sliver of the desktop showing, drag Odoo half off the screen,
or lose the app behind Explorer mid-transaction. Full screen also covers the
Windows taskbar, so the only chrome on the display is the app's own.

The cost of that is the title bar, and with it the minimise and close buttons.
They come back as :class:`WindowControls` inside the app bar, and inside the
header band of every page built on
:class:`~bytesraw_erp.ui.widgets.page.PageShell` - the account list, the
account form and the settings page - so the caption buttons are in the same
corner of every screen in the product.

The floating set this window owns is the net under that, not the normal path.
It appears only over a page that provides no controls of its own, because a
first launch with no saved account lands on the account form and a screen with
no way to quit the application is not a screen this app is allowed to show. In
the shipped routes nothing reaches it any more; it stays because the cost of
keeping it is a hidden QFrame and the cost of being wrong about it is a till
that cannot be closed.

Launching with ``--windowed`` suspends all of that: the window keeps its frame,
keeps its taskbar button, and the caption buttons grow a full-screen toggle,
because in that mode the window really does have two sizes. That is the only
configuration in which the toggle exists - see :mod:`.widgets.window_controls`.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QKeySequence, QResizeEvent, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QWidget,
)

from bytesraw_erp.constants import (
    APP_NAME,
    ROUTE_ACCOUNT_EDIT,
    ROUTE_ACCOUNT_NEW,
    ROUTE_ACCOUNTS,
    ROUTE_ODOO,
    ROUTE_SETTINGS,
)
from bytesraw_erp.core.errors import BytesrawError
from bytesraw_erp.ui.app_context import AppContext
from bytesraw_erp.ui.pages.account_form_page import AccountFormPage
from bytesraw_erp.ui.pages.accounts_page import AccountsPage
from bytesraw_erp.ui.pages.odoo_page import OdooPage
from bytesraw_erp.ui.pages.settings_page import SettingsPage
from bytesraw_erp.ui.router import Router
from bytesraw_erp.ui.theme import Palette
from bytesraw_erp.ui.widgets.icons import set_icon_color
from bytesraw_erp.ui.widgets.window_controls import WindowControls

_log = logging.getLogger(__name__)

#: Gap between the floating window controls and the top-right corner.
_OVERLAY_MARGIN = 14

#: Size the windowed launch opens at, before the user resizes it. Wide enough
#: for Odoo's own navbar not to collapse into its burger menu.
_WINDOWED_SIZE = (1440, 900)


class MainWindow(QMainWindow):
    """Hosts the router. All navigation happens inside one full-screen window."""

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self._context = context
        self.setWindowTitle(APP_NAME)
        #: Set the moment a close is accepted. Everything that could re-show
        #: the window, or start work whose result nobody will be alive to
        #: receive, checks it first.
        self._closing = False

        # Before any page is built: an icon is stroked in whatever colour was
        # last set, and the default is the light palette's near-black. The app
        # bar used to be the only caller, so a first launch straight into the
        # account form under the dark theme drew every icon on it invisible -
        # there is no app bar on that screen.
        set_icon_color(context.theme.palette.text)

        self._stack = QStackedWidget(self)
        self.setCentralWidget(self._stack)
        self.router = Router(self._stack, self)

        # Forms are rebuilt on each visit so they never show stale input; the
        # Odoo page is kept alive so the embedded browser survives navigation.
        self.router.register(
            ROUTE_ACCOUNTS, lambda: AccountsPage(context, self.router), keep_alive=True
        )
        self.router.register(
            ROUTE_ACCOUNT_NEW, lambda: AccountFormPage(context, self.router), keep_alive=False
        )
        self.router.register(
            ROUTE_ACCOUNT_EDIT, lambda: AccountFormPage(context, self.router), keep_alive=False
        )
        self.router.register(ROUTE_ODOO, lambda: OdooPage(context, self.router), keep_alive=True)
        # Rebuilt per visit so it always shows the stored values and the
        # current list of printers, which can change while the app runs.
        self.router.register(
            ROUTE_SETTINGS, lambda: SettingsPage(context, self.router), keep_alive=False
        )

        self._overlay = self._build_overlay()
        self.router.route_changed.connect(lambda _path: self._sync_overlay())

        if context.windowed:
            self.resize(*_WINDOWED_SIZE)
            # F11 is the only shortcut a full-screen toggle is ever bound to,
            # and a user who found the button will try it.
            QShortcut(QKeySequence(Qt.Key.Key_F11), self, self._toggle_full_screen)

        # Connected before any page exists, so this runs first on every theme
        # change: the new icon colour is in place by the time a page's own
        # handler re-renders its icons.
        context.theme.theme_changed.connect(self._on_theme_changed)

    # -- startup -----------------------------------------------------------

    def start(self) -> None:
        """Pick the landing route.

        With at least one saved account the app goes straight to Odoo - the
        account form is a setup step, not a login gate shown on every launch.
        """
        try:
            has_accounts = not self._context.store.is_empty
        except BytesrawError as exc:
            _log.error("Could not read the account registry: %s", exc)
            QMessageBox.critical(self, APP_NAME, str(exc))
            has_accounts = False
        self.router.reset_to(ROUTE_ODOO if has_accounts else ROUTE_ACCOUNT_NEW)

    def _on_theme_changed(self, palette: Palette) -> None:
        set_icon_color(palette.text)
        self._overlay_controls.apply_theme(palette)
        self._repolish(palette)

    def _repolish(self, _palette: object) -> None:
        for widget in self.findChildren(QWidget):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        self.update()

    # -- full screen -------------------------------------------------------

    def changeEvent(self, event: QEvent) -> None:
        """Put the window straight back into full screen if anything leaves it.

        Alt+Tab, a shell command or Qt itself can drop the flag; minimising is
        the one departure that is allowed, because the app bar offers it. A
        windowed launch opts out of the whole mechanism - there the user owns
        the size.

        The correction is queued rather than applied here: calling
        ``showFullScreen`` from inside the state change that triggered it
        re-enters the transition Qt is still running, and the new state is
        dropped on the floor. Measured - the direct call leaves the window
        normal.
        """
        super().changeEvent(event)
        if event.type() is not QEvent.Type.WindowStateChange:
            return
        self._sync_full_screen_controls()
        if not self._context.windowed:
            QTimer.singleShot(0, self._enforce_full_screen)

    def _enforce_full_screen(self) -> None:
        # The queued correction can outlive the click that closed the window.
        # Without this guard `showFullScreen` puts a window that is on its way
        # out back on screen, and the close has to be asked for twice.
        if self._closing or self._context.windowed:
            return
        state = self.windowState()
        if state & Qt.WindowState.WindowMinimized:
            return
        if not (state & Qt.WindowState.WindowFullScreen):
            self.showFullScreen()

    def _toggle_full_screen(self) -> None:
        """F11, and the caption button's twin. Windowed launches only."""
        if not self._context.windowed:
            return
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        self._sync_full_screen_controls()

    def _sync_full_screen_controls(self) -> None:
        """Point every toggle on screen at the state it will move the window to.

        There are two sets - the app bar's and the floating overlay's - and
        full screen can also be left by F11 or by the shell, so neither can
        rely on having been the thing that changed it.
        """
        # Swept rather than addressed: the overlay's set is a child of this
        # window and the app bar's is buried in a page, and a state change can
        # arrive before either exists.
        for controls in self.findChildren(WindowControls):
            controls.sync_full_screen()

    # -- floating window controls ------------------------------------------

    def _build_overlay(self) -> QFrame:
        """The minimise/close pair for a page that carries none of its own.

        A child of the window rather than of a page, so it is positioned
        against the physical top-right corner. This is why a page's own set has
        to sit in a band that spans the window and does not scroll, rather than
        in the centred column of content: controls placed in that column would
        drift towards the middle of a wide screen and scroll away with the
        cards. :class:`~bytesraw_erp.ui.widgets.page.PageShell` puts them in
        the band for exactly that reason, and ``_sync_overlay`` then leaves
        this pair hidden.
        """
        frame = QFrame(self)
        frame.setObjectName("WindowControlsOverlay")
        row = QHBoxLayout(frame)
        row.setContentsMargins(5, 5, 5, 5)
        row.setSpacing(0)
        self._overlay_controls = WindowControls(
            allow_full_screen=self._context.windowed
        )
        row.addWidget(self._overlay_controls)
        self._overlay_controls.apply_theme(self._context.theme.palette)
        frame.hide()
        return frame

    def _sync_overlay(self) -> None:
        """Show the floating pair only where the page provides no other set."""
        page = self._stack.currentWidget()
        has_own = page is not None and page.findChild(WindowControls) is not None
        self._overlay.setVisible(not has_own)
        if not has_own:
            self._position_overlay()

    def _position_overlay(self) -> None:
        self._overlay.adjustSize()
        self._overlay.move(
            self.width() - self._overlay.width() - _OVERLAY_MARGIN, _OVERLAY_MARGIN
        )
        self._overlay.raise_()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._overlay.isVisible():
            self._position_overlay()

    # -- shutdown ----------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        """Go away immediately, then tear down.

        The order here is the whole point, and it is what the close button was
        missing. Closing used to run the teardown first and leave the window on
        screen throughout it, so the app looked hung for as long as an RPC call
        in flight, a page still holding a web view and Chromium's flush of its
        cookie jar took between them.

        Now:

        1. ``_closing`` is raised, so the queued full-screen correction cannot
           put the window back up while it is going down;
        2. every page releases its web view - profiles must outlive their
           pages, so this has to happen before the profiles are freed;
        3. the window hides, which is the frame the user actually waits for;
        4. the context shuts down - background work, session, profiles.
        5. the application is told to quit explicitly, rather than left to
           notice that its last window went.
        """
        self._closing = True
        self._release_pages()
        self.hide()
        self._context.shutdown()
        super().closeEvent(event)
        instance = QApplication.instance()
        if instance is not None:
            instance.quit()

    def _release_pages(self) -> None:
        """Ask every built page to drop what it holds open.

        Only the Odoo page has anything - its web view - but the hook is
        looked up by name so a later page can join in without this method
        learning about it.
        """
        for index in range(self._stack.count()):
            page = self._stack.widget(index)
            release = getattr(page, "release", None)
            if callable(release):
                try:
                    release()
                except Exception:  # pragma: no cover - never block a close
                    _log.exception("A page failed to release cleanly")
