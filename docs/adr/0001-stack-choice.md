# ADR 0001 - Desktop stack: PySide6 + QtWebEngine

- **Status:** Accepted
- **Date:** 2026-09-11

## Context

Bytesraw ERP is a Windows-first desktop shell around the Odoo 19 web client.
Its single most important screen embeds `/odoo` in-process, under a native app
bar, in one full-screen window.

## The hard requirement

Everything else this app does - routing, the account manager, the app bar, the
settings screen - is ordinary desktop work that almost any toolkit can do. One
requirement is not ordinary, and it decides the stack on its own:

> A Chromium-class web view, embedded **in-process on Windows**, in the same
> layout as native widgets, sharing a cookie jar with the app's own HTTP client.

That rules out every Python GUI toolkit whose web view is a mobile- or
web-target control, and every toolkit that can only open the page in a detached
browser window - two windows means no shared chrome, and the app bar cannot sit
above the web content. Odoo's OWL front end also needs a current engine, so an
embedded MSHTML-era control is not a substitute either.

## Decision

Build on **PySide6 6.11+ with QtWebEngine**.

| Requirement | How it is met |
| --- | --- |
| Embed the Odoo 19 web client | `QWebEngineView` - a full Chromium, which runs Odoo's OWL front end |
| Multiple accounts, no repeated login | One `QWebEngineProfile` per account, each with its own on-disk cookie jar |
| Native app bar over web content | Ordinary Qt widgets above the view in the same layout |
| POS hardware and receipt printing | `QWebEnginePage.print()` to `QPrinter`, plus native Python for serial and USB devices |
| Team fit | Stays in Python, which is what this team already writes for Odoo |
| Distribution | PyInstaller or Nuitka to a single `.exe` |
| Licensing | PySide6 is LGPL, so commercial distribution is unencumbered |

PySide6 6.11.2 is the first series publishing cp314 wheels, which matches the
Python 3.14 toolchain on the development machine.

## Alternatives considered

- **Tauri 2 (Rust + WebView2)** - the smallest binary and an excellent shell,
  but the front end is TypeScript and the back end Rust. Wrong stack for a team
  whose day job is Odoo in Python.
- **Electron** - capable and familiar, but a large runtime and the same
  language mismatch, with no offsetting advantage over Tauri.
- **pywebview + WebView2** - the lightest Python option, but the app bar and the
  account manager would have to be written in HTML inside the web view. At that
  point the app is a themed browser, and the native layer buys nothing.
- **A native shell with a detached WebView2 window** - two windows, no shared
  chrome, and the app bar cannot sit above the web content. Rejected.

## Consequences

- QtWebEngine adds roughly 150 MB to the packaged application. Acceptable for an
  internally distributed line-of-business tool.
- The Chromium version is tied to the Qt release, so Qt must be kept reasonably
  current as Odoo's front end advances.
- macOS and Linux builds come essentially for free, since the same stack is
  supported there.
- This decision covers the Windows desktop only. A future mobile companion app
  is a separate decision with a different set of constraints.
