# Changelog

All notable changes to Bytesraw ERP are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

The version lives in exactly one place, `constants.APP_VERSION`, and the release
workflow refuses to build a tag that disagrees with it.

> Versions 0.1.0 through 0.1.7 were developed before the project was published.
> Their entries are reconstructed from `ROADMAP.md` and dated by commit, and no
> binaries were distributed for them.

## [0.1.10] - unreleased

### Added

- **One layout for every screen outside Odoo.** The account list, the account
  form and the settings page now share a header that stays put while the page
  scrolls, carrying the product mark, the screen's title, the build badge and
  that screen's actions. The minimise and close buttons sit in that header on
  every screen, in the same corner the app bar puts them in.
- **Two-column settings** on a wide screen, collapsing to one on a narrow one,
  so the whole page fits without scrolling.
- **A maximise/restore button and a full-screen toggle** in the caption buttons,
  on every screen. The window starts full screen as before, but it can now be
  brought down to a maximised or a loose window and put back, from the app bar
  or from any page's header.
- **The app bar *is* the title bar.** The window has no frame of its own any
  more, so restoring it down no longer reveals a second, native title bar under
  the app bar with a duplicate set of buttons. Dragging the bar by its blank
  space moves the window and double-clicking it maximises or restores - the
  same on the header band of the account list, the account form and settings.
  Aero Snap, shake-to-minimise and dragging a maximised window back down all
  behave as they do on any other window, because the move is handed to Windows
  rather than done by the app.
- **Resizing from every edge and corner** of the restored window, through a
  six-pixel border the window insets itself by. The border is only there while
  the window is loose: maximised and full screen have no size to change, so it
  goes and the app fills the screen exactly as before.
- **"Save to the Downloads folder" as a way for Odoo to print.** Chosen under
  Settings -> Printing -> When Odoo prints, it sends nothing to a printer: a
  report Odoo renders is kept in Downloads, and a Point of Sale receipt is
  written there as a PDF instead of coming out on paper. For a machine with no
  printer attached, where Odoo's Print button previously produced nothing
  anybody could find. The app bar's print menu gained the same entry, so one
  page can be saved without changing the setting.
- **Either printer can be left unassigned.** "Not assigned - do not print" is
  now a choice in both the report and the receipt picker, and it is not the
  same as leaving one on the Windows default: nothing of that kind is printed
  at all. A back office with no receipt printer and a till with no A4 printer
  are both ordinary, and neither should have its documents sent to whatever
  device Windows happens to name. A report that is not printed is still saved
  in Downloads and named in a notification, so it cannot go missing.

### Changed

- The full-screen toggle is no longer limited to a `--windowed` launch, and the
  window is no longer pinned to full screen. A window that drops out of full
  screen by accident - Alt+Tab, the shell, Qt - is still put straight back; only
  a deliberate press of one of the caption buttons, F11 or a double-click on the
  app bar is left alone. A till nobody touches behaves exactly as before.
- The account form's **Cancel** and **Connect and save** moved from below the
  last field into the header, where they stay in view on a short screen. Enter
  in the password field still submits.

### Fixed

- **A session that expires twice is now recovered twice.** The one silent
  re-authentication allowed per expiry was being spent for the rest of the run:
  a till recovered the first expired session of the day and then answered every
  later one with "The Odoo session keeps expiring" instead of signing back in.
  The allowance is restored as soon as a keepalive probe confirms the
  replacement session is live, so a server that refuses the fresh session
  straight away is still stopped after one attempt.
- **Checking for updates on the Beta channel no longer reports an error.** No
  pre-release has been published yet, so the beta manifest does not exist and
  the host answers 404 - which is what an empty channel looks like, not a
  fault. Settings now says nothing has been published to that channel instead
  of showing "The update service answered HTTP 404". A missing manifest that a
  redirect pointed at is still reported as the error it is.

## [0.1.9] - 2026-09-13

### Added

- **In-app updates.** Bytesraw ERP now checks a BytesRaw-hosted manifest on
  launch and every few hours, and offers a new version in a toast. Choosing to
  update downloads the installer, verifies its SHA-256, and hands it to
  `setup.exe /SILENT /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS` - Inno's Restart
  Manager then closes the app, upgrades it in place and starts it again.
  Nothing is ever downloaded or installed without the user choosing to.
- **Settings -> Updates**: automatic checking, the release channel (stable or
  beta), a "check now" that reports back, and release notes.
- **Authenticode verification** of a downloaded installer, enforced whenever the
  manifest says the artifact is signed. It says `false` today because signing
  is still waiting on a certificate, so enforcement begins with a manifest edit
  rather than a new release.
- **Staged rollouts.** A manifest can offer a release to a percentage of
  installations. Each machine works out its own place from a random local id,
  so the update host needs no telemetry and no idea who is asking. A release
  below the manifest's `minimum_supported` floor, or marked mandatory, ignores
  the rollout - as does a check the user asked for by hand.

## [0.1.8] - 2026-09-12

### Added

- **Compatibility rendering mode** for machines whose graphics driver garbles
  the Odoo view. Measured on a Windows 10 till with Intel HD Graphics of the
  Bay Trail generation: the app bar painted correctly while the web view below
  it came out as vertical stripes. **Settings -> Display -> Rendering** now
  offers a mode that draws the view on the processor instead, stored per
  machine and applied on the next launch.
- `--software-render` and `--gpu-render` command-line flags, which override the
  stored setting for a single launch without changing it - for a till too
  corrupted on screen to reach Settings at all.

## [0.1.7] - 2026-09-12

### Added

- **Windows installer and portable zip.** A PyInstaller one-folder bundle, an
  Inno Setup installer that upgrades an existing install in place, and a zip
  for machines where installers are blocked. `.github/workflows/release.yml`
  builds all three on a published release and attaches them with checksums.
- `--windowed`, which launches the shell in an ordinary resizable window. Only
  then do the caption buttons carry a full-screen toggle, as does F11.

### Changed

- **Closing is immediate.** The window hides before the teardown rather than
  after it, pages release their web views before the profiles they depend on
  are freed, and the background thread pool stops accepting work instead of
  being waited on at process exit.
- **The app bar's print button opens its menu and nothing else.** Its former
  action half printed straight to paper on a till configured that way, from a
  click aimed at the icon eight pixels away; every mode is now a menu entry,
  preview included.

### Fixed

- Ctrl+P did nothing. A `QAction`'s shortcut only fires while the action
  belongs to a widget in the active window, and sitting in a menu that has
  never been opened is not that.
- A queued full-screen correction could resurrect a window that was already on
  its way down, so closing sometimes took two clicks.

## [0.1.6] - 2026-09-12

### Added

- **Full-screen kiosk shell.** One window for its whole life: no title bar, and
  the Windows taskbar stays covered. Minimise and close live in the app bar,
  and float in the same corner on the screens that carry no app bar - so the
  first-launch account form is never a screen with no way to quit.

### Removed

- The company name and logo from the app bar, and with them the per-session
  logo round trip. The shell takes no part in multi-company at all: Odoo's own
  navbar owns that choice, and a second control over one session can only
  disagree with the first.

## [0.1.5] - 2026-09-11

### Added

- A print **preview** mode. Windows' own print dialog has no preview pane, so
  the preview is drawn by the app through `QPrintPreviewDialog`.
- Download notifications with **Show in folder**, and a confirmation toast when
  a print job is sent.
- The product logo and a real application icon, carried into the window, the
  taskbar and the account form.

### Removed

- The "match system" theme. Resolving it re-entered the theme controller, so
  every switch applied twice and raced two web-view reloads against each other,
  leaving the embedded client stuck loading. Light and Dark only, Light by
  default.

## [0.1.4] - 2026-09-11

### Removed

- The company switcher. Making a switch stick meant writing
  `res.users.company_id`, which changes that user's default company everywhere
  including their other browsers - a heavier side effect than Odoo's own
  switch has.

## [0.1.3] - 2026-09-11

### Fixed

- **Odoo report auto-printing fired only on a second click.** Odoo 19 answers
  its Print button with an XHR POST to `/report/download` and then a `blob:`
  URL, so the download that arrives carries no report URL to match on. A
  per-profile request interceptor now notes the outgoing report request and
  claims the blob PDF that follows.
- An unrelated PDF downloaded moments after a report could be claimed as that
  report. Claims are one-for-one and expire.

## [0.1.2] - 2026-09-11

### Added

- A **Settings page** and a local settings store, separate from accounts:
  appearance, print mode, printer, auto-print and keep-a-copy.
- **Odoo report PDFs are intercepted and printed** per those settings. This is
  what makes Odoo's own Print button reach paper instead of dropping a file in
  Downloads. Ordinary attachments and exports still go to Downloads.
- The theme is written to `res.users.settings.color_scheme`, which outranks the
  `color_scheme` cookie on servers that have the field. Capability is probed at
  sign-in, so a server without it is a silent no-op rather than an error.

### Changed

- Print mode moved from per-account to app-wide. Which printer is attached is a
  fact about the machine, not about the Odoo database.

## [0.1.1] - 2026-09-11

### Added

- Light and dark themes, carried into Odoo itself where the server supports it,
  with Chromium's ForceDarkMode as the fallback for servers that have no dark
  stylesheet at all.
- Printing: straight to the Windows default printer, or through the system
  print dialog. `window.print()` from a POS receipt is routed the same way.
- WCAG AA contrast tests across both palettes.

### Fixed

- Invisible menu text in dark mode. Every self-painting widget now declares
  both a background and a foreground.

## [0.1.0] - 2026-09-11

The walking skeleton: the app opens, connects to an Odoo 19 server, and renders
the web client under a native app bar.

### Added

- **Multiple accounts,** each with its own URL, database and login. Passwords go
  to the Windows Credential Manager and never to a file.
- **Sign-in over JSON-RPC,** with the resulting cookie jar planted into that
  account's browser profile - including the sticky routing cookie a load
  balancer sets - so the embedded client opens already authenticated instead of
  logging in twice.
- **Isolated sessions:** a `QWebEngineProfile` per account, with its own
  persistent cookie storage.
- The last Odoo screen visited, remembered per account and restored on the next
  launch.
- A native app bar carrying the product mark, the build number, navigation, the
  signed-in user and the interface language.

[0.1.10]: https://github.com/BytesRaw/bytesraw-erp-desktop/compare/v0.1.9...HEAD
[0.1.9]: https://github.com/BytesRaw/bytesraw-erp-desktop/compare/v0.1.8...v0.1.9
[0.1.8]: https://github.com/BytesRaw/bytesraw-erp-desktop/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/BytesRaw/bytesraw-erp-desktop/releases/tag/v0.1.7
