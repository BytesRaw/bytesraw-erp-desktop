# Bytesraw ERP

A native desktop client for **Odoo 19**. It embeds the Odoo web client in a real
Chromium view, under a native app bar, and manages several Odoo accounts so the
login screen appears once per server rather than once per launch.

> **Why not Flet?** The project was originally specified in Flet. Flet's WebView
> control is unsupported on Windows and Linux, which makes the app's main screen
> impossible to build there. See [ADR 0001](docs/adr/0001-stack-choice.md).

## Features

- **Multiple accounts.** Add any number of Odoo servers, each with its own URL,
  database and login. Passwords go to Windows Credential Manager, never to a file.
- **No login gate.** Once an account is saved, the app opens straight into Odoo.
- **Resume where you left.** The last Odoo screen is remembered per account and
  restored on the next launch.
- **Isolated sessions.** Each account gets its own browser profile and cookie
  jar, so two logins on the same server never collide.
- **Native app bar.** Company logo and name, signed-in user, interface language
  and navigation actions sit above the web view. The company is shown but not
  changed here - Odoo's own menu owns that - and the language slot renders as
  plain text when there is only one to choose from.
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

## How sign-in works

The app authenticates over Odoo's JSON-RPC endpoint and then plants the
resulting `session_id` cookie into that account's browser profile. The embedded
client therefore opens already signed in, sharing one Odoo session with the RPC
client rather than logging in twice. Credentials are never typed into the HTML
login form.

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
docs/adr/      architecture decision records
```

`data` and `core` are free of Qt widgets, so the model and storage layers are
testable without a running application.

## Development

```bash
.venv/Scripts/python.exe -m pip install -e ".[dev]"
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m pytest
```

## Roadmap

[`ROADMAP.md`](ROADMAP.md) tracks the path to 1.0.0, including receipt printing,
barcode scanners and kiosk mode for point-of-sale use.

## Licence

LGPL-3.0-or-later, matching the PySide6 dependency.
