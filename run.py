"""Development launcher: ``python run.py`` without installing the package."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from bytesraw_erp.app import run

if __name__ == "__main__":
    raise SystemExit(run(sys.argv))
