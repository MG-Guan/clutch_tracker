#!/usr/bin/env python3
"""Run CLI from repo root: python run.py list-targets"""

from __future__ import annotations

import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).resolve().parent / "scripts" / "clutch-tracker"), run_name="__main__")
