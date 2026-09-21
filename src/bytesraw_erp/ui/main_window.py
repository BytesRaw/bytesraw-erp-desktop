"""The single application window and its route table.

The window launches full screen and stays there unless someone deliberately
asks for otherwise. That is a product decision, not a default: this is a till
and back-office shell, and a window that drifts out of full screen by accident
leaves a sliver of the desktop showing, Odoo half off the screen, or the app
lost behind Explorer mid-transaction. Full screen also covers the Windows
taskbar, so the only chrome on the display is the app's own.

The window is **frameless**, so the app bar is the title bar rather than a
second strip of chrome under one. It did not have to be, and for a while it was
not: Windows draws no caption for a *full-screen* window, so an ordinary framed
window looked right for as long as full screen was the only size it had. The
moment restore and maximise became reachable the real frame appeared underneath
the app bar, with its own copy of the buttons the bar already carries.

Everything a caption did is therefore rebuilt: the buttons by
:class:`WindowControls`, the grip by :mod:`.widgets.window_drag`, and the resize
border by :class:`~bytesraw_erp.ui.widgets.window_resize.ResizeFrame`. The
buttons live in the app bar and in the header band of every page built on
:class:`~bytesraw_erp.ui.widgets.page.PageShell` - the account list, the
account form and the settings page - so they are in the same corner of every
screen in the product.

What a frameless window gives up, measured rather than assumed: the Windows 11
Snap Layouts flyout that appears when hovering a *native* maximise button, the
drop shadow, and the rounded corners. What it keeps, also measured: maximising
to the available area rather than over the taskbar, drag-to-edge Aero Snap, and
resizing from all eight edges. Getting the first three back means a Win32 custom
frame (``WM_NCCALCSIZE``), which is a second, Windows-only code path for a
window that is full screen nearly all of its life.

The floating set this window owns is the net under that, not the normal path.
It appears only over a page that provides no controls of its own, because a
first launch with no saved account lands on the account form and a screen with
no way to quit the application is not a screen this app is allowed to show. In
the shipped routes nothing reaches it any more; it stays because the cost of
keeping it is a hidden QFrame and the cost of being wrong about it is a till
that cannot be closed.

Launching with ``--windowed`` starts in an ordinary resizable window instead,
with its frame and its taskbar button.

The window is no longer *pinned* full screen in either mode, and the correction
above is what keeps that from being a change in behaviour. It is armed and
disarmed by the window's own show calls: ``showFullScreen`` raises
``_full_screen_intended``, ``showNormal`` and ``showMaximized`` lower it. So a
till nobody touches launches full screen, has every accidental departure from it
corrected exactly as before, and a user who deliberately presses the maximise
button, the full-screen toggle or F11 is not fought by the shell. Those three
are the *only* ways down, and each of them goes through one of the show calls -
which is why the arming lives there rather than in a handler that would have to
guess whether a state change was meant.
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
from bytesraw_erp.ui.widgets.window_resize import ResizeFrame

_log = logging.getLogger(__name__)

#: Gap between the floating window controls and the top-right corner.
_OVERLAY_MARGIN = 14

#: Size the windowed launch opens at, before the user resizes it. Wide enough
#: for Odoo's own navbar not to collapse into its burger menu.
_WINDOWED_SIZE = (1440, 900)


class MainWindow(QMainWindow):
    """Hosts the router. All navigation happens inside this one window."""

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self._context = context
        self.setWindowTitle(APP_NAME)
        # The app bar is the title bar. This has to be set before the window is
        # ever shown - changing the flags on a visible window destroys and
        # recreates its native handle, which would take the web view with it.
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        #: Set the moment a close is accepted. Everything that could re-show
        #: the window, or start work whose result nobody will be alive to
        #: receive, checks it first.
        self._closing = False
        #: Whether full screen is the state this window is supposed to be in.
        #: Raised and lowered by the show overrides below, and read by the
        #: correction that puts an accidental departure right.
        self._full_screen_intended = False

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

        # After the central widget, so the grips are on top of it; it inserts
        # the margin they sit in rather than laying them over the page.
        self._frame = ResizeFrame(self)

        # Sized even for the full-screen launch, which never shows this
        # geometry: it is what the window restores *down* to the first time
        # someone leaves full screen, and a window that has only ever been full
        # screen has no size of its own to come back to.
        self.resize(*_WINDOWED_SIZE)
        # F11 is the shortcut a full-screen toggle is always bound to, and a
        # user who found the button will try it.
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

    def present(self) -> None:
        """Bring this window forward, without changing what size it should be.

        What a second launch of the application reaches: the desktop icon was
        clicked again, so the user wants to *see* the app, not to start another
        copy of it. See
        :mod:`bytesraw_erp.services.single_instance` for how the launch that
        exits hands its foreground right over first - without that, Windows
        refuses ``SetForegroundWindow`` to a process it is not currently
        interacting with and this does no more than flash the taskbar button.

        ``showNormal()`` is deliberately not used to un-minimise: it is one of
        the three calls that record the user's *intent* about the window size,
        so restoring a till this way would quietly take it out of full screen
        for the rest of the session. Clearing the minimised bit leaves every
        other state bit - full screen, maximised - exactly as it was found.
        """
        if self._closing:
            return
        _log.info("Another launch asked for this window; bringing it forward")
        if self._full_screen_intended:
            self.showFullScreen()
        else:
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
            self.show()
        self.raise_()
        self.activateWindow()

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

    def showFullScreen(self) -> None:
        """Go full screen, and record that full screen is now the intent.

        Shadowing the three show calls is what keeps the correction below from
        fighting the user. Every deliberate change of size in the product goes
        through one of them - the caption buttons, F11 and a double-click on
        the app bar all call them by name from Python - while an *accidental*
        departure is Qt or the shell writing the window state directly, which
        reaches ``changeEvent`` without ever touching the flag. So the
        difference between "the user asked for this" and "something dropped
        it" is recorded at the one point where it is still known.
        """
        self._full_screen_intended = True
        super().showFullScreen()

    def showNormal(self) -> None:
        self._full_screen_intended = False
        super().showNormal()

    def showMaximized(self) -> None:
        self._full_screen_intended = False
        super().showMaximized()

    def changeEvent(self, event: QEvent) -> None:
        """Put the window straight back into full screen if anything drops it.

        Alt+Tab, a shell command or Qt itself can drop the flag; minimising is
        the one departure that is always allowed, because the app bar offers
        it. The correction only runs while full screen is the intended state -
        a windowed launch never sets it, and the caption buttons and F11 clear
        it when the user asks for a smaller window.

        The correction is queued rather than applied here: calling
        ``showFullScreen`` from inside the state change that triggered it
        re-enters the transition Qt is still running, and the new state is
        dropped on the floor. Measured - the direct call leaves the window
        normal.
        """
        super().changeEvent(event)
        if event.type() is not QEvent.Type.WindowStateChange:
            return
        self._sync_window_controls()
        QTimer.singleShot(0, self._enforce_full_screen)

    def _enforce_full_screen(self) -> None:
        # The queued correction can outlive the click that closed the window.
        # Without this guard `showFullScreen` puts a window that is on its way
        # out back on screen, and the close has to be asked for twice.
        if self._closing or not self._full_screen_intended:
            return
        state = self.windowState()
        if state & Qt.WindowState.WindowMinimized:
            return
        if not (state & Qt.WindowState.WindowFullScreen):
            self.showFullScreen()

    def _toggle_full_screen(self) -> None:
        """F11, and the caption button's twin."""
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        self._sync_window_controls()

    def _sync_window_controls(self) -> None:
        """Point every toggle on screen at the state it will move the window to.

        There are two sets - the app bar's or the page band's, and the floating
        overlay's - and the size can also change by F11, by a double-click on
        the app bar or by the shell, so neither set can rely on having been the
        thing that changed it.
        """
        # Swept rather than addressed: the overlay's set is a child of this
        # window and a page's is buried in the stack, and a state change can
        # arrive before either exists.
        for controls in self.findChildren(WindowControls):
            controls.sync_window_state()

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
        self._overlay_controls = WindowControls()
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
