"""Configuration loading for clutch_tracker."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from clutch_tracker.models import Target, TargetCriteria

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "America/Toronto"


def project_root(start: Path | None = None) -> Path:
    """Return the clutch_tracker project root directory."""
    if start is not None:
        return start
    return Path(__file__).resolve().parents[2]


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file and return its contents."""
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}, got {type(data).__name__}")
    return data


def load_settings(root: Path | None = None) -> dict[str, Any]:
    """Load settings.yaml."""
    root_path = project_root(root)
    settings_path = root_path / "config" / "settings.yaml"
    logger.debug("Loading settings from %s", settings_path)
    return load_yaml(settings_path)


def load_targets_config(root: Path | None = None) -> list[dict[str, Any]]:
    """Load raw target entries from targets.yaml."""
    root_path = project_root(root)
    targets_path = root_path / "config" / "targets.yaml"
    logger.debug("Loading targets from %s", targets_path)
    data = load_yaml(targets_path)
    targets = data.get("targets", [])
    if not isinstance(targets, list):
        raise ValueError("targets.yaml 'targets' must be a list")
    return targets


def _parse_province(value: Any, target_id: str) -> str | None:
    """Parse province code; reject YAML boolean coercion (ON/OFF)."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(
            f"Target {target_id!r}: province {value!r} looks like a YAML boolean. "
            'Quote province codes in targets.yaml (e.g. province: "ON").'
        )
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Target {target_id!r}: province must be a non-empty string")
    return value.strip()


def parse_target(entry: dict[str, Any]) -> Target:
    """Parse a single target config entry into a Target model."""
    target_id = entry.get("target_id")
    if not target_id or not isinstance(target_id, str):
        raise ValueError("Each target must have a non-empty string target_id")

    label = entry.get("label", target_id)
    if not isinstance(label, str):
        raise ValueError(f"Target {target_id!r} label must be a string")

    enabled = entry.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError(f"Target {target_id!r} enabled must be a boolean")

    criteria_raw = entry.get("criteria", {})
    if not isinstance(criteria_raw, dict):
        raise ValueError(f"Target {target_id!r} criteria must be a mapping")

    known_keys = {
        "body_style",
        "province",
        "min_year",
        "max_year",
        "make",
        "model",
        "model_aliases",
        "search_query",
    }
    extra = {k: v for k, v in criteria_raw.items() if k not in known_keys}

    model_aliases_raw = criteria_raw.get("model_aliases", [])
    if model_aliases_raw is None:
        model_aliases_raw = []
    if not isinstance(model_aliases_raw, list):
        raise ValueError(f"Target {target_id!r} model_aliases must be a list")
    model_aliases = tuple(str(a) for a in model_aliases_raw if a)

    search_query = criteria_raw.get("search_query")
    if search_query is not None and not isinstance(search_query, str):
        raise ValueError(f"Target {target_id!r} search_query must be a string")

    criteria = TargetCriteria(
        body_style=criteria_raw.get("body_style"),
        province=_parse_province(criteria_raw.get("province"), target_id),
        min_year=criteria_raw.get("min_year"),
        max_year=criteria_raw.get("max_year"),
        make=criteria_raw.get("make"),
        model=criteria_raw.get("model"),
        model_aliases=model_aliases,
        search_query=search_query,
        extra=extra,
    )
    return Target(
        target_id=target_id,
        label=label,
        enabled=enabled,
        criteria=criteria,
    )


def load_targets(root: Path | None = None) -> list[Target]:
    """Load and parse all targets from config."""
    entries = load_targets_config(root)
    return [parse_target(entry) for entry in entries]


def get_timezone_name(settings: dict[str, Any] | None = None, root: Path | None = None) -> str:
    """Return configured timezone name."""
    if settings is None:
        settings = load_settings(root)
    return settings.get("timezone", DEFAULT_TIMEZONE)
