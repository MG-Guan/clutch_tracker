#!/usr/bin/env python3
"""
Development launcher — works without pip install when run from repo root.

Usage:
    python run.py list-targets
    python run.py show-target ford-f150-ontario
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
src_str = str(SRC)
if src_str not in sys.path:
    sys.path.insert(0, src_str)

from clutch_tracker.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
