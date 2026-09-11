"""Entry point for the frozen build.

Not ``run.py``: that one prepends ``src`` to ``sys.path`` so a developer can
launch the app from a checkout without installing it, and in a frozen bundle
that directory does not exist. The package is already importable here, because
the spec puts ``src`` on PyInstaller's ``pathex``.

Not ``__main__.py`` either: PyInstaller names the executable after the entry
script, and an ``exe`` called ``__main__`` is not what the taskbar should show.
"""

from __future__ import annotations

import sys

from bytesraw_erp.app import run

if __name__ == "__main__":
    raise SystemExit(run(sys.argv))
