# Contributing

Bytesraw ERP is maintained by [BytesRaw](https://bytesraw.com) and developed in
the open. Issues and pull requests are welcome.

## Before a large change

Open an issue first. The project has a published
[roadmap](ROADMAP.md) and a number of decisions that were made deliberately and
look like omissions until you know why - the shell takes no part in
multi-company, there is no "match system" theme, and the print button opens a
menu rather than printing. A short conversation saves a rewritten branch.

Small fixes, typos and test coverage need no preamble. Just send them.

## Setting up

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"
.venv/Scripts/python.exe run.py --windowed
```

Windows is the only supported platform. The stack is PySide6 with QtWebEngine;
the README explains why that choice is load-bearing rather than incidental.

## The gate

Both of these must pass, and CI runs them on every release build:

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m pytest
```

Two tests fail under `QT_QPA_PLATFORM=offscreen` and pass on the real platform,
so **do not reach for offscreen** to run the suite quietly:
`test_theme_controller.py::test_qt_is_never_left_on_an_unknown_scheme` and
`test_toast.py::test_it_disappears_on_its_own`.

## House rules

- Python 3.11 or newer, `from __future__ import annotations` in every module.
- `ruff` with the configuration in `pyproject.toml`; lines wrap at 100 columns.
- **Layering:** `ui` may import `services`, `data` and `core`; `services` may
  import `data` and `core`; `data` and `core` import nothing from above them.
  `data/models.py` has no Qt import at all, which is what makes the model layer
  testable without a `QApplication`.
- Every user-visible error is a `BytesrawError` subclass whose `str()` is
  written for an end user - the UI renders it directly, in an inline banner
  rather than a modal, so the message stays visible while the field it refers
  to is being fixed.
- Accounts are values: change one with `account.evolve(...)`.
- Passwords never enter an `Account`, a log line, or `accounts.json`.
- A behavioural fix wants a regression test. Most of the sharp edges in this
  codebase were found the expensive way and are pinned by one.

## Versioning

`constants.APP_VERSION` is the only place a version number is allowed to live.
The release workflow refuses to build a tag that disagrees with it, or a
`constants.py` that disagrees with `pyproject.toml`. Add a `CHANGELOG.md` entry
in the same change that bumps it.

## Licensing

Contributions are accepted under the project's licence, **LGPL-3.0-or-later**.
By opening a pull request you agree that your contribution ships under those
terms. There is no separate CLA.

## Reporting a vulnerability

Not here - see [SECURITY.md](SECURITY.md).
