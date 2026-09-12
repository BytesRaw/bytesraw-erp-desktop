# Bytesraw ERP

A native desktop client for **Odoo 19**. It embeds the Odoo web client in a real
Chromium view, under a native app bar, and manages several Odoo accounts so the
login screen appears once per server rather than once per launch.

> Built on PySide6 + QtWebEngine. One requirement decides that: a
> Chromium-class web view embedded **in-process on Windows**, in the same
> layout as native widgets and sharing a cookie jar with the app's own HTTP
> client. `QWebEngineView` is the only option that meets it without leaving
> Python or splitting the app across two windows.

## Features

- **Multiple accounts.** Add any number of Odoo servers, each with its own URL,
  database and login. Passwords go to Windows Credential Manager, never to a file.
- **No login gate.** Once an account is saved, the app opens straight into Odoo.
- **Resume where you left.** The last Odoo screen is remembered per account and
  restored on the next launch.
- **Isolated sessions.** Each account gets its own browser profile and cookie
  jar, so two logins on the same server never collide.
- **Full screen, always.** One window, no title bar, no taskbar showing: the
  display belongs to the app. Minimise and close live at the right-hand end of
  the app bar, and on the screens that have no app bar they float in the same
  corner.
- **Native app bar.** The product mark and build number, a segmented navigation
  cluster, the signed-in user, the interface language and the window controls
  sit above the web view, on a surface of their own so the native chrome never
  reads as part of the Odoo navbar below it. The language slot renders as plain
  text when there is only one to choose from. Nothing here is about the
  company: Odoo's own navbar owns that entirely.
- **The build number is always on screen.** Next to the mark in the app bar, on
  the account form, and in Settings under About, with the folders the app writes
  to - the things support asks for first.
- **Light and dark themes.** The theme is carried into Odoo itself where the
  server supports it.
- **Download notifications.** A file saved from Odoo says so, with a shortcut to
  the folder it landed in.
- **Printing that works the way a desk does.** Choose a printer and whether to
  show the print dialog, once, in Settings. Odoo's own Print button then reaches
  paper instead of dropping a PDF in Downloads.

## Requirements

- Windows 10 or 11
- Python 3.11 or newer (developed on 3.14)
- An Odoo 19 server, on-premise or Odoo Online

## Getting started

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe run.py
```

On first launch the app shows the **Add account** form. Enter the server URL and
press **Load** to list the databases it exposes; servers that hide their
database list simply need the name typed in. Saving authenticates first, so an
account is never stored with credentials that do not work.

The shell runs full screen, with its own minimise and close buttons in place of
a title bar. Add `--windowed` to launch it in an ordinary resizable window
instead - the caption buttons then also carry a full-screen toggle, as does F11.

```bash
.venv/Scripts/python.exe run.py --windowed
```

## When the Odoo view looks wrong

On a machine with an old graphics driver - measured on Intel HD Graphics of the
Bay Trail generation under Windows 10 - the app bar paints correctly while the
Odoo view below it comes out as vertical stripes, blank white, or garbled
tiles. That split is the diagnosis: the bar is drawn by Qt, the view by
Chromium, and only the second one goes through the driver's compositing path.

**Settings -> Display -> Rendering -> Compatibility mode** draws the view on
the processor instead. It is stored per machine and applied on the next launch,
so close and reopen the app after choosing it. A single launch can be forced
either way without changing the setting:

```bash
.venv/Scripts/python.exe run.py --software-render
.venv/Scripts/python.exe run.py --gpu-render
```

## How sign-in works

The app authenticates over Odoo's JSON-RPC endpoint and then plants the
resulting `session_id` cookie into that account's browser profile. The embedded
client therefore opens already signed in, sharing one Odoo session with the RPC
client rather than logging in twice. Credentials are never typed into the HTML
login form.

Because both clients share that session, they also lose it together. When the
server forgets it - a restart, a cleared session store, a long idle spell - the
app signs in again by itself with the password already in the vault and returns
to the page you were on. A probe every five minutes both notices an expiry a
navigation has not yet revealed and, because Odoo touches the session when it
answers, keeps an idle till from expiring in the first place.

## Printing

Odoo prints in two different ways, and Bytesraw ERP handles both from one set of
preferences in **Settings**:

- **PDF reports.** Pressing Print in Odoo makes the web client download a
  rendered PDF. The app catches it and sends it to your printer, rather than
  leaving it in Downloads. Turn this off, or ask for a copy to be kept, in
  Settings.
- **Receipts and portal pages,** which call `window.print()`. These are rendered
  and printed directly.

For each, you choose whether the job goes straight to paper, shows the system
print dialog, or opens a preview first - and which printer to use, or leave it on
the Windows default, which is then resolved fresh at print time.

Pick **Show a print preview first** if you want to see the pages before they
print: Windows' own print dialog has no preview pane, so the preview is drawn by
the app itself.

The **print button in the app bar is separate**: it prints the page you are
looking at, the way Ctrl+P does in a browser. Odoo's own reports do not need it -
they print by themselves according to the settings above.

## Where things are stored

| What | Where |
| --- | --- |
| Account metadata (URL, database, login, last path) | `%APPDATA%\BytesRaw\Bytesraw ERP\accounts.json` |
| Printing and appearance preferences | `%APPDATA%\BytesRaw\Bytesraw ERP\settings.json` |
| Passwords | Windows Credential Manager, service `BytesRaw ERP` |
| Per-account cookies and local storage | `%APPDATA%\BytesRaw\Bytesraw ERP\profiles\<account>` |
| Web cache | `%LOCALAPPDATA%\BytesRaw\Bytesraw ERP\cache` |
| Logs | `%APPDATA%\BytesRaw\Bytesraw ERP\logs` |

Removing an account deletes its stored password and its browsing data from this
computer. Nothing changes on the Odoo server.

## Project layout

```
src/bytesraw_erp/
  core/        paths, logging, typed errors
  data/        account registry and plain data models
  services/    Odoo JSON-RPC client, session assembly, web profiles, threading
  ui/          router, pages, widgets, theme
```

`data` and `core` are free of Qt widgets, so the model and storage layers are
testable without a running application.

## Development

```bash
.venv/Scripts/python.exe -m pip install -e ".[dev]"
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m pytest
```

## Building a Windows installer

Everything the build needs is in `packaging/`. Inno Setup 6.3 or newer has to
be on the machine (`choco install innosetup`); the rest comes from the dev
extra.

```powershell
.\packaging\build.ps1
```

That produces three things in `dist\`, all named from the version in
`constants.py` so it is never typed twice:

| | |
| --- | --- |
| `BytesrawERP\` | the application folder, `BytesrawERP.exe` beside `_internal\` |
| `BytesrawERP-<version>-setup.exe` | the installer |
| `BytesrawERP-<version>-windows-x64.zip` | the same folder, for machines where installers are blocked |

Pass `-SkipApp` to rebuild only the installer, which turns a several-minute
cycle into a few seconds while iterating on the `.iss`.

**It is a one-folder build, not `--onefile`.** QtWebEngine is the reason: a
one-file bundle re-extracts more than 400 MB into `%TEMP%` on every single
launch before Python starts, and Chromium then has to spawn its sandboxed
helper process out of a randomly named temp directory. The installer is what
makes the folder a single thing to hand over.

Installing is per-machine, into Program Files, so a till is set up once for
every user of it. `/VERYSILENT` works for unattended deployment. The app writes
nothing beside its executable - see *Where things are stored* above - so the
program directory can stay read-only.

### Releases

`.github/workflows/release.yml` runs the same build on a Windows runner when a
GitHub release is published, and attaches the installer, the portable zip and
`SHA256SUMS.txt` to it. It refuses to build when the release tag disagrees with
`constants.APP_VERSION`, so a `v0.2.0` release cannot ship binaries that call
themselves 0.1.7.

**Releases are currently unsigned**, so Windows SmartScreen warns whoever
downloads one. The workflow already has the signing steps and enables them on
its own as soon as the certificate secrets are present - nothing to change in
it. Since mid-2023 an Authenticode key must live on a hardware token or in an
HSM, so a CI build needs a cloud signing service (Azure Trusted Signing,
DigiCert KeyLocker, SSL.com eSigner) rather than a certificate file in a
secret.

## Roadmap

[`ROADMAP.md`](ROADMAP.md) tracks the path to 1.0.0, including receipt printing,
barcode scanners and kiosk mode for point-of-sale use.

## Licence

Copyright (C) 2026 BytesRaw LLP <admin@bytesraw.com>

LGPL-3.0-or-later, matching the PySide6 dependency: the app links QtWebEngine
and the rest of Qt through PySide6. [`LICENSE`](LICENSE) carries the Lesser
General Public License and [`LICENSE.GPL`](LICENSE.GPL) the General Public
License it builds on. The installer shows the first and puts both beside the
executable.
