"""Configuration and repository validation."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from clutch_tracker.config import load_settings, load_targets, load_targets_config, project_root
from clutch_tracker.db import connection, database_path
from clutch_tracker.storage import ensure_database, load_current_inventory, load_registry, load_vehicles

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Aggregated validation outcome."""

    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        self.ok = False
        self.errors.append(message)

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)


def validate_targets_config(root: Path | None = None) -> ValidationResult:
    """Validate targets.yaml structure and uniqueness."""
    result = ValidationResult()
    root_path = project_root(root)

    try:
        entries = load_targets_config(root_path)
    except Exception as exc:
        result.add_error(f"Failed to load targets.yaml: {exc}")
        return result

    seen_ids: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            result.add_error(f"Target entry at index {index} must be a mapping")
            continue

        target_id = entry.get("target_id")
        if not target_id or not isinstance(target_id, str):
            result.add_error(f"Target entry at index {index} missing target_id")
            continue

        if target_id in seen_ids:
            result.add_error(f"Duplicate target_id: {target_id}")
        seen_ids.add(target_id)

        if "criteria" not in entry:
            result.add_error(f"Target {target_id} missing criteria")
        elif not isinstance(entry["criteria"], dict):
            result.add_error(f"Target {target_id} criteria must be a mapping")

        if "enabled" in entry and not isinstance(entry["enabled"], bool):
            result.add_error(f"Target {target_id} enabled must be boolean")

        criteria = entry.get("criteria", {})
        if isinstance(criteria, dict):
            aliases = criteria.get("model_aliases")
            if aliases is not None and not isinstance(aliases, list):
                result.add_error(f"Target {target_id} model_aliases must be a list")
            elif isinstance(aliases, list):
                for index, alias in enumerate(aliases):
                    if not isinstance(alias, str) or not alias.strip():
                        result.add_error(
                            f"Target {target_id} model_aliases[{index}] must be a non-empty string"
                        )
            if criteria.get("model") and not criteria.get("make"):
                result.add_warning(
                    f"Target {target_id} has model without make; consider setting make for precise search"
                )

    try:
        load_targets(root_path)
    except Exception as exc:
        result.add_error(f"Target parsing failed: {exc}")

    return result


def validate_settings(root: Path | None = None) -> ValidationResult:
    """Validate settings.yaml."""
    result = ValidationResult()
    try:
        settings = load_settings(root)
    except Exception as exc:
        result.add_error(f"Failed to load settings.yaml: {exc}")
        return result

    if "timezone" not in settings:
        result.add_warning("settings.yaml missing timezone; default America/Toronto will be used")

    storage = settings.get("storage", {})
    if storage and not isinstance(storage, dict):
        result.add_error("settings.storage must be a mapping")

    return result


def validate_config(root: Path | None = None) -> ValidationResult:
    """Validate all configuration files."""
    targets_result = validate_targets_config(root)
    settings_result = validate_settings(root)

    combined = ValidationResult()
    combined.ok = targets_result.ok and settings_result.ok
    combined.errors = targets_result.errors + settings_result.errors
    combined.warnings = targets_result.warnings + settings_result.warnings
    return combined


def validate_target_data(root: Path, target_id: str) -> ValidationResult:
    """Validate local database content for a single target."""
    result = ValidationResult()
    ensure_database(root)

    inventory = load_current_inventory(root, target_id)
    if inventory is None:
        result.add_error(f"Missing current inventory for {target_id}")
        return result

    if inventory.target_id != target_id:
        result.add_error(f"Inventory target_id mismatch for {target_id}")

    vehicles = load_vehicles(root, target_id)
    vins_seen: set[str] = set()
    for vin in vehicles:
        if not vin:
            result.add_error(f"{target_id}: vehicle row missing VIN")
            continue
        if vin in vins_seen:
            result.add_error(f"{target_id}: duplicate VIN in vehicles: {vin}")
        vins_seen.add(vin)

    with connection(root) as conn:
        for table, label in (
            ("observations", "observations"),
            ("listing_events", "listing_events"),
        ):
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE target_id = ?",
                (target_id,),
            ).fetchone()
            if row is None:
                result.add_warning(f"{target_id}: unable to count {label}")

    return result


def validate_repository(root: Path | None = None) -> ValidationResult:
    """Validate full repository consistency."""
    result = ValidationResult()
    root_path = project_root(root)

    config_result = validate_config(root_path)
    result.errors.extend(config_result.errors)
    result.warnings.extend(config_result.warnings)
    if not config_result.ok:
        result.ok = False

    ensure_database(root_path)
    db_path = database_path(root_path)
    if not db_path.exists():
        result.add_error(f"Local database missing: {db_path}")
    else:
        registry = load_registry(root_path)
        if not registry:
            result.add_warning("Target registry is empty; run initialize-targets")

    try:
        targets = load_targets(root_path)
    except Exception:
        return result

    for target in targets:
        target_result = validate_target_data(root_path, target.target_id)
        result.errors.extend(target_result.errors)
        result.warnings.extend(target_result.warnings)
        if not target_result.ok:
            result.ok = False

    daily_root = root_path / "reports" / "daily"
    snapshots_root = root_path / "snapshots"
    for target in targets:
        if not (daily_root / target.target_id).is_dir():
            result.add_warning(f"Daily report directory missing for {target.target_id}")
        if not (snapshots_root / target.target_id).is_dir():
            result.add_warning(f"Snapshot directory missing for {target.target_id}")

    return result
