"""Shared test helpers."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from clutch_tracker.target_manager import initialize_targets


@pytest.fixture
def project_copy(tmp_path: Path) -> Path:
    """Copy project config into a temp directory for isolated tests."""
    root = Path(__file__).resolve().parents[1]
    dest = tmp_path / "clutch_tracker"
    shutil.copytree(root / "config", dest / "config")
    (dest / "data").mkdir(parents=True)
    (dest / "reports" / "daily").mkdir(parents=True)
    (dest / "reports" / "summary").mkdir(parents=True)
    (dest / "reports" / "recommendations").mkdir(parents=True)
    (dest / "snapshots").mkdir(parents=True)
    initialize_targets(dest)
    return dest
