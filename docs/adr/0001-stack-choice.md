# ADR 0001 - Desktop stack: PySide6 + QtWebEngine, not Flet

- **Status:** Accepted
- **Date:** 2026-09-11
- **Supersedes:** the original plan to build this app in Flet

## Context

Bytesraw ERP is a Windows-first desktop shell around the Odoo 19 web client.
Its single most important screen embeds `/odoo` in-process, under a native app
bar. The original plan was Flet (>= 0.86.5) with `ft.Router`.

## The blocker

Flet's WebView control does not work on Windows. This was verified against the
Flet source tree at `D:\Flet\flet`, not inferred from documentation age:

| Evidence | Location |
| --- | --- |
| Platform table lists Windows and Linux as unsupported | `website/docs/controls/webview/index.md` |
| Windows/Linux dispatch returns an error placeholder | `sdk/python/packages/flet-webview/src/flutter/flet_webview/lib/src/webview.dart:24` |
| That placeholder is `ErrorControl("Webview is not yet supported on this Platform.")` | `.../webview_windows_and_linux.dart:9` |
| Only Android and web platform packages are declared | `.../flet_webview/pubspec.yaml` |

The same placeholder is still present on the in-development `1.0.0` branch, so
this is not a "wait for the next patch release" situation - there is no Windows
platform implementation to enable.

Everything else the app needs (routing, the account manager, the app bar) is
well within Flet's abilities. Only the one screen the product exists for is
impossible.

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
- **Flet hybrid** - Flet for the account manager and a detached WebView2 window
  for Odoo. Two windows, no shared chrome, and the app bar cannot sit above the
  web content. Rejected.

## Consequences

- QtWebEngine adds roughly 150 MB to the packaged application. Acceptable for an
  internally distributed line-of-business tool.
- The Chromium version is tied to the Qt release, so Qt must be kept reasonably
  current as Odoo's front end advances.
- macOS and Linux builds come essentially for free, since the same stack is
  supported there.
- Flet remains a reasonable choice for a future mobile companion app, where its
  WebView *is* supported. This decision is about the Windows desktop only.
