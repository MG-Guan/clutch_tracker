"""Tests for configuration loading and validation."""

from __future__ import annotations

from pathlib import Path

import yaml

from clutch_tracker.config import load_targets, parse_target, project_root
from clutch_tracker.validation import validate_config, validate_targets_config


def test_load_targets_from_project():
    targets = load_targets(project_root())
    ids = [t.target_id for t in targets]
    assert ids == ["ford-f150-ontario"]
    assert len(ids) == len(set(ids))


def test_parse_target_criteria():
    target = parse_target(
        {
            "target_id": "test-target",
            "label": "Test",
            "enabled": True,
            "criteria": {"body_style": "suv", "province": "ON", "custom_field": "value"},
        }
    )
    assert target.criteria.body_style == "suv"
    assert target.criteria.extra == {"custom_field": "value"}


def test_load_targets_province_is_string_not_yaml_boolean():
    targets = load_targets(project_root())
    ford = next(t for t in targets if t.target_id == "ford-f150-ontario")
    assert ford.criteria.province == "ON"
    assert isinstance(ford.criteria.province, str)


def test_parse_target_rejects_unquoted_on_province():
    import pytest

    with pytest.raises(ValueError, match="YAML boolean"):
        parse_target(
            {
                "target_id": "bad-province",
                "criteria": {"province": True},
            }
        )


def test_validate_config_passes():
    result = validate_config(project_root())
    assert result.ok, result.errors


def test_validate_config_rejects_duplicate_ids(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.yaml").write_text("timezone: America/Toronto\n")
    targets = {
        "targets": [
            {"target_id": "dup", "criteria": {}},
            {"target_id": "dup", "criteria": {}},
        ]
    }
    (config_dir / "targets.yaml").write_text(yaml.dump(targets))
    result = validate_targets_config(tmp_path)
    assert not result.ok
    assert any("Duplicate" in e for e in result.errors)


def test_validate_config_rejects_missing_target_id(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.yaml").write_text("timezone: America/Toronto\n")
    (config_dir / "targets.yaml").write_text(yaml.dump({"targets": [{"criteria": {}}]}))
    result = validate_targets_config(tmp_path)
    assert not result.ok
