"""Console entry point: ``python -m bytesraw_erp``."""

from __future__ import annotations

import sys

from bytesraw_erp.app import run


def main() -> int:
    return run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
