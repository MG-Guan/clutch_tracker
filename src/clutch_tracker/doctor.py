"""Environment and install diagnostics."""

from __future__ import annotations

import os
import sys
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
from pathlib import Path

import clutch_tracker
from clutch_tracker.config import project_root


def _shadow_path(repo_root: Path) -> Path | None:
    """Return repo-root clutch_tracker/ if it exists (shadows src/clutch_tracker)."""
    shadow = repo_root / "clutch_tracker"
    if shadow.is_dir():
        return shadow
    return None


def run_doctor() -> int:
    """Print environment diagnostics and return non-zero if install looks broken."""
    repo_root = project_root()
    shadow = _shadow_path(repo_root)
    spec = find_spec("clutch_tracker")

    print("clutch_tracker environment check")
    print(f"  python:           {sys.executable}")
    print(f"  repo root:        {repo_root}")
    print(f"  package file:     {getattr(clutch_tracker, '__file__', 'unknown')}")
    print(f"  package version:  {clutch_tracker.__version__}")

    try:
        dist_version = version("clutch-tracker")
        print(f"  installed dist:   clutch-tracker {dist_version}")
    except PackageNotFoundError:
        print("  installed dist:   NOT FOUND (pip install -e . not run in this env)")

    print(f"  module spec:      {spec.origin if spec else 'NOT FOUND'}")
    print(f"  CONDA_DEFAULT_ENV:{os.environ.get('CONDA_DEFAULT_ENV', '(not set)')}")
    print(f"  VIRTUAL_ENV:      {os.environ.get('VIRTUAL_ENV', '(not set)')}")

    ok = True
    warnings: list[str] = []

    if shadow is not None:
        ok = False
        warnings.append(
            f"Remove or rename {shadow} — it shadows src/clutch_tracker and breaks "
            "'python -m clutch_tracker.cli'."
        )

    pkg_file = getattr(clutch_tracker, "__file__", "") or ""
    if pkg_file and not pkg_file.startswith(str(repo_root / "src")):
        warnings.append(
            "Package is not loaded from this repo's src/. Re-run: pip install -e \".[dev]\" "
            "from the repo root."
        )

    if os.environ.get("CONDA_DEFAULT_ENV") and os.environ.get("VIRTUAL_ENV"):
        warnings.append(
            "Conda (base) and .venv are both active. Run 'conda deactivate' until (base) "
            "disappears, then reactivate .venv."
        )

    try:
        version("clutch-tracker")
    except PackageNotFoundError:
        ok = False
        warnings.append(
            "Editable install missing. From repo root: pip install -e \".[dev]\""
        )

    if warnings:
        print("\nWarnings:")
        for item in warnings:
            print(f"  - {item}")

    if ok and not warnings:
        print("\nOK — environment looks healthy.")
        print("Recommended commands:")
        print("  clutch-tracker list-targets")
        print("  python -m clutch_tracker list-targets")
        return 0

    print("\nSuggested fix:")
    print("  conda deactivate          # repeat until (base) is gone")
    print("  source .venv/bin/activate")
    print("  pip install -e \".[dev]\"")
    print("  clutch-tracker doctor")
    print("\nFallback (no pip install needed):")
    print("  python run.py list-targets")
    return 1
