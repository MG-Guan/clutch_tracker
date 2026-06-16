"""Target initialization and registry management."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from clutch_tracker.config import load_targets, project_root
from clutch_tracker.storage import (
    EVENTS_HEADERS,
    OBSERVATIONS_HEADERS,
    VEHICLES_HEADERS,
    daily_report_dir,
    events_path,
    inventory_path,
    now_iso,
    observations_path,
    registry_path,
    save_registry,
    snapshot_dir,
    vehicles_path,
    write_csv_rows,
    atomic_write_json,
)

logger = logging.getLogger(__name__)


def initialize_targets(root: Path | None = None, *, force: bool = False) -> list[str]:
    """
    Initialize on-disk structures for all configured targets.

    Returns list of initialized target_ids.
    """
    root_path = project_root(root)
    targets = load_targets(root_path)
    timestamp = now_iso(root_path)
    initialized: list[str] = []

    registry_rows: list[dict[str, str]] = []
    if registry_path(root_path).exists() and not force:
        from clutch_tracker.storage import load_registry

        existing_registry = {r["target_id"]: r for r in load_registry(root_path)}
    else:
        existing_registry = {}

    for target in targets:
        target_id = target.target_id
        data_dir = root_path / "data" / "targets" / target_id
        data_dir.mkdir(parents=True, exist_ok=True)

        _init_csv(vehicles_path(root_path, target_id), VEHICLES_HEADERS, force)
        _init_csv(observations_path(root_path, target_id), OBSERVATIONS_HEADERS, force)
        _init_csv(events_path(root_path, target_id), EVENTS_HEADERS, force)
        _init_inventory(root_path, target_id, force)

        daily_report_dir(root_path, target_id).mkdir(parents=True, exist_ok=True)
        snapshot_dir(root_path, target_id).mkdir(parents=True, exist_ok=True)

        if target_id in existing_registry:
            row = existing_registry[target_id]
            row["label"] = target.label
            row["enabled"] = str(target.enabled).lower()
            row["updated_at"] = timestamp
        else:
            row = {
                "target_id": target_id,
                "label": target.label,
                "enabled": str(target.enabled).lower(),
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        registry_rows.append(row)
        initialized.append(target_id)
        logger.info("Initialized target %s", target_id)

    save_registry(root_path, registry_rows)
    return initialized


def _init_csv(path: Path, headers: list[str], force: bool) -> None:
    if path.exists() and not force:
        return
    write_csv_rows(path, headers, [])


def _init_inventory(root: Path, target_id: str, force: bool) -> None:
    path = inventory_path(root, target_id)
    if path.exists() and not force:
        return
    payload = {
        "target_id": target_id,
        "updated_at": now_iso(root),
        "scan_id": None,
        "scan_complete": False,
        "vehicles": [],
    }
    atomic_write_json(path, payload)
