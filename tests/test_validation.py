"""Tests for repository validation."""

from __future__ import annotations

from pathlib import Path

from clutch_tracker.validation import validate_repository, validate_target_data


def test_validate_repository_after_init(project_copy: Path):
    result = validate_repository(project_copy)
    assert result.ok, result.errors


def test_validate_target_data_detects_missing_file(project_copy: Path):
    target_id = "ford-f150-ontario"
    inv = project_copy / "data" / "targets" / target_id / "current_inventory.json"
    inv.unlink()
    result = validate_target_data(project_copy, target_id)
    assert not result.ok
    assert any("current_inventory.json" in e for e in result.errors)
