"""Environment and install diagnostics."""

from __future__ import annotations

import os
import sys
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import clutch_tracker
from clutch_tracker.config import project_root


def _shadow_path(repo_root: Path) -> Path | None:
    """Return repo-root clutch_tracker/ if it exists (shadows src/clutch_tracker)."""
    shadow = repo_root / "clutch_tracker"
    if shadow.is_dir():
        return shadow
    return None


def collect_doctor_report() -> dict[str, Any]:
    """Collect environment diagnostics as a structured payload."""
    repo_root = project_root()
    shadow = _shadow_path(repo_root)
    spec = find_spec("clutch_tracker")
    pkg_file = getattr(clutch_tracker, "__file__", "") or ""

    try:
        dist_version = version("clutch-tracker")
        installed_dist = f"clutch-tracker {dist_version}"
    except PackageNotFoundError:
        dist_version = None
        installed_dist = "NOT FOUND (pip install -e . not run in this env)"

    ok = True
    warnings: list[str] = []

    if shadow is not None:
        ok = False
        warnings.append(
            f"Remove or rename {shadow} — it shadows src/clutch_tracker and breaks "
            "'python -m clutch_tracker.cli'."
        )

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

    if dist_version is None:
        ok = False
        warnings.append(
            "Editable install missing. From repo root: pip install -e \".[dev]\""
        )

    healthy = ok and not warnings
    return {
        "ok": ok,
        "healthy": healthy,
        "python": sys.executable,
        "repo_root": str(repo_root),
        "package_file": pkg_file or "unknown",
        "package_version": clutch_tracker.__version__,
        "installed_dist": installed_dist,
        "module_spec": spec.origin if spec else "NOT FOUND",
        "conda_default_env": os.environ.get("CONDA_DEFAULT_ENV") or "(not set)",
        "virtual_env": os.environ.get("VIRTUAL_ENV") or "(not set)",
        "warnings": warnings,
    }


def run_doctor() -> int:
    """Print environment diagnostics and return non-zero if install looks broken."""
    report = collect_doctor_report()

    print("clutch_tracker environment check")
    print(f"  python:           {report['python']}")
    print(f"  repo root:        {report['repo_root']}")
    print(f"  package file:     {report['package_file']}")
    print(f"  package version:  {report['package_version']}")
    print(f"  installed dist:   {report['installed_dist']}")
    print(f"  module spec:      {report['module_spec']}")
    print(f"  CONDA_DEFAULT_ENV:{report['conda_default_env']}")
    print(f"  VIRTUAL_ENV:      {report['virtual_env']}")

    if report["warnings"]:
        print("\nWarnings:")
        for item in report["warnings"]:
            print(f"  - {item}")

    if report["healthy"]:
        print("\nOK — environment looks healthy.")
        print("Recommended commands:")
        print("  clutch-tracker list-targets")
        print("  python -m clutch_tracker list-targets")
        print("  clutch-tracker serve")
        return 0

    print("\nSuggested fix:")
    print("  conda deactivate          # repeat until (base) is gone")
    print("  source .venv/bin/activate")
    print("  pip install -e \".[dev]\"")
    print("  clutch-tracker doctor")
    print("\nFallback (no pip install needed):")
    print("  python run.py list-targets")
    return 1
