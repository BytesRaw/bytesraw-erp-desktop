# Bytesraw ERP - Roadmap to 1.0.0

A native desktop shell for Odoo 19, built on PySide6 + QtWebEngine.
The README says why that stack.

Legend: `[x]` done · `[~]` in progress · `[ ]` not started

---

## v0.1.0 - Walking skeleton `[x]`

The app opens, connects to one Odoo server, and renders `/odoo` under a native
app bar.

- [x] **M1.1** Project layout, packaging metadata, dependency pinning
- [x] **M1.2** Stack decision taken and recorded, on the embedded-web-view requirement
- [x] **M1.3** Path, logging and typed-error infrastructure
- [x] **M1.4** `Account` / `SessionContext` data model
- [x] **M1.5** Account registry: JSON metadata + passwords in Windows Credential Manager
- [x] **M1.6** Odoo 19 JSON-RPC client (`authenticate`, `session_info`, `call_kw`, database list)
- [x] **M1.7** Per-account `QWebEngineProfile` with isolated, persistent cookie storage
- [x] **M1.8** Cookie-jar handoff from the RPC client to the web profile (session + proxy sticky cookies)
- [x] **M1.9** Path-based `Router` with dynamic segments and history
- [x] **M1.10** Add-account form with live database discovery and credential validation
- [x] **M1.11** Account list with open / edit / remove
- [x] **M1.12** Odoo page: embedded web client, external links to the system browser
- [x] **M1.13** App bar: company logo + name, user, language, navigation actions
- [x] **M1.14** Read-only company and language slots when only one record exists
- [x] **M1.15** Last-visited path remembered per account and restored on launch
- [x] **M1.16** Smoke test against a live Odoo 19 instance (`demo.fatoora.cloud`)

## v0.1.1 - Appearance and printing `[x]`

Delivered after v0.1.0 on direct request.

- [x] **M1.17** Light / dark / match-system theme with a picker in the app bar
- [x] **M1.18** Theme carried into Odoo via the `color_scheme` cookie, with
      Chromium ForceDarkMode as the fallback for servers that ignore it
- [x] **M1.19** Fix invisible menu text in dark mode; every self-painting widget
      now declares both background and foreground
- [x] **M1.20** WCAG AA contrast tests over both palettes
- [x] **M1.21** Printing: direct to the Windows default printer, or via the
      system print dialog
- [x] **M1.22** `window.print()` from Odoo (POS receipts) routed to the account's
      configured print mode
- [x] **M1.23** Odoo report PDFs opened in Chromium's viewer; PDFs on disk
      printable through `QPdfDocument`

## v0.1.2 - Settings page and report printing `[x]`

- [x] **M1.24** Local settings store (`settings.json`), separate from accounts
- [x] **M1.25** Settings page: appearance, print mode, printer, auto-print, keep-a-copy
- [x] **M1.26** Printer selection honoured by every print path, with fallback to
      the Windows default when a configured printer disappears
- [x] **M1.27** Odoo QWeb report downloads intercepted and printed per local
      settings - this is what makes Odoo's own Print button reach paper
- [x] **M1.28** Ordinary attachments and exports still go to Downloads
- [x] **M1.29** Theme written to `res.users.settings.color_scheme`, which
      outranks the cookie; capability-probed so an absent addon is a no-op
- [x] **M1.30** Per-account print mode removed in favour of app-wide settings

## v0.1.3 - Printing and company fixes `[x]`

- [x] **M1.31** Recognise Odoo 19's blob-based report download, so auto-printing
      fires on arrival with no second click (`ReportRequestWatcher`)
- [x] **M1.32** Guard against claiming an unrelated blob PDF as a report
- [x] **M1.33** Company switch writes `res.users.company_id`, re-reads the
      session and reloads - the same shape as the language switch

## v0.1.4 - Company switching removed `[x]`

- [x] **M1.34** Remove the company switcher from the app bar; Odoo's own navbar
      owns that choice. The company name and logo remained as read-only display
      until v0.1.6, which dropped those too.

## v0.1.5 - Theme, preview, logo, notifications `[x]`

- [x] **M1.35** Remove the "match system" theme: it re-entered `apply()` and
      double-applied, leaving the web view stuck loading. Light and Dark only,
      Light by default
- [x] **M1.36** `PrintMode.PREVIEW` using `QPrintPreviewDialog` - Windows' own
      print dialog has no preview pane
- [x] **M1.37** Product logo bundled inside the package; window, taskbar and
      account-form branding
- [x] **M1.38** Download notifications with "Show in folder", and a
      confirmation when a print job is sent

## v0.1.6 - Full-screen kiosk shell `[x]`

- [x] **M1.39** The window is full screen for its whole life: no title bar, and
      the Windows taskbar is covered. `changeEvent` puts it back if anything
      takes it out
- [x] **M1.40** `WindowControls` - minimise and close inside the app bar, plus
      a floating set for the pages that carry no app bar, so the first-launch
      account form is never a screen with no way to quit
- [x] **M1.41** Company name and logo removed from the app bar, and with them
      the per-session logo round trip. This shell does not take part in
      multi-company at all

## v0.1.7 - Closing, windowing, printing `[x]`

- [x] **M1.42** Closing is immediate: the window hides before the teardown, the
      pages release their web views before the profiles are freed, and the
      background pool stops accepting work rather than being waited on at
      process exit. The queued full-screen correction no longer resurrects a
      window that is on its way down
- [x] **M1.43** `--windowed` launches the shell in an ordinary resizable
      window, and only then do the caption buttons carry a full-screen toggle
      (F11 as well). The full-screen default still has exactly one size
- [x] **M1.44** The app bar's print button opens its menu and nothing else. The
      split button's action half - which printed straight to paper on a till
      configured that way, from a click aimed at the icon - is gone, and the
      menu gained the preview mode it was missing

## v0.2.0 - Robustness `[ ]`

The app survives the things a real deployment does to it.

- [x] **M2.1** Session expiry detection and silent re-authentication. Three
      detectors - the web view redirected to `/web/login`, an RPC fault of
      `odoo.http.SessionExpiredException`, and a five-minute probe that doubles
      as a keepalive - all recover by signing in again with the password
      already in the vault, once per expiry, landing back on the same page
- [ ] **M2.2** Offline / server-unreachable screen with retry
- [ ] **M2.3** Two-factor authentication: fall back to the HTML login form in-view
- [ ] **M2.4** Certificate-error UX (trust-once prompt instead of a config checkbox only)
- [ ] **M2.5** Download manager panel with progress and history (per-download notifications landed in v0.1.5)
- [ ] **M2.8** Notify an auto-print *failure* as a toast rather than a modal (successes already toast)
- [ ] **M2.6** Proxy configuration honouring Windows system settings
- [ ] **M2.7** Crash and error reporting to a local log with a "copy diagnostics" action

## v0.3.0 - Point of sale `[ ]`

- [x] **M3.1** Receipt printing via `QWebEngineView.print()` to a `QPrinter` (done in v0.1.1)
- [ ] **M3.2** Printer selection and paper profile per account
- [ ] **M3.3** ESC/POS direct printing for thermal printers over USB and serial
- [ ] **M3.4** Barcode scanner input (HID keyboard-wedge pass-through to the web view)
- [ ] **M3.5** Cash drawer kick signal
- [ ] **M3.6** Customer-facing second display on a secondary monitor
- [ ] **M3.7** Kiosk mode: frameless, always-on-top, no navigation chrome

## v0.4.0 - Multi-account fluency `[ ]`

- [ ] **M4.1** Account switcher in the app bar without a round trip to the list
- [ ] **M4.2** Multiple accounts open at once as tabs
- [ ] **M4.3** Per-account accent colour and window title
- [ ] **M4.6** Per-account print override, for a POS till beside an office desk
- [ ] **M4.5** Import and export of account metadata (never passwords)

## v0.5.0 - Polish `[ ]`

- [x] **M5.1** Dark theme following the Windows system setting (done in v0.1.1)
- [x] **M5.2** Application icon (done in v0.1.5); branded splash still open
- [ ] **M5.3** Global keyboard shortcuts (reload, home, switch account, zoom)
- [ ] **M5.4** Zoom level persisted per account
- [ ] **M5.5** System tray icon with quick account switching
- [ ] **M5.6** First-run onboarding

## v0.6.0 - Quality gates `[ ]`

- [ ] **M6.1** Unit tests for the account store, router and model layers
- [ ] **M6.2** `pytest-qt` tests for the app bar's read-only rule and the form
- [ ] **M6.3** Integration test against a disposable Odoo 19 container
- [ ] **M6.4** `ruff` clean in CI
- [ ] **M6.5** GitHub Actions matrix build for Windows

## v1.0.0 - Release `[ ]`

- [ ] **M7.1** PyInstaller one-folder build reproducible from a clean checkout
- [ ] **M7.2** Signed MSI or Inno Setup installer
- [ ] **M7.3** In-app update check
- [ ] **M7.4** Administrator deployment guide (silent install, pre-seeded accounts)
- [ ] **M7.5** End-user manual
- [ ] **M7.6** Verified against Odoo 19.0 stable, Online and on-premise
- [ ] **M7.7** Tagged `v1.0.0` release with changelog

---

## Out of scope for 1.0.0

- macOS and Linux builds. The stack supports both; they are simply not tested
  or released in this cycle.
- An offline POS mode. Odoo's own POS already caches orders client-side; a
  second offline layer in the shell would fight it.
- Anything to do with multi-company. Odoo's navbar owns the company: the shell
  neither shows it nor switches it, because a second control over the same
  session can only disagree with the first.
- Any modification of the Odoo server. This app is a client, and installs no
  addon on the target database.
