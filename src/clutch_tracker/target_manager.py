"""Target initialization and registry management."""

from __future__ import annotations

import logging
from pathlib import Path

from clutch_tracker.config import load_targets, project_root
from clutch_tracker.db import connection
from clutch_tracker.storage import (
    daily_report_dir,
    ensure_database,
    ensure_empty_inventory,
    load_registry,
    now_iso,
    save_registry,
    snapshot_dir,
)

logger = logging.getLogger(__name__)


def initialize_targets(root: Path | None = None, *, force: bool = False) -> list[str]:
    """
    Initialize local database structures for all configured targets.

    Returns list of initialized target_ids.
    """
    root_path = project_root(root)
    ensure_database(root_path)
    targets = load_targets(root_path)
    timestamp = now_iso(root_path)
    initialized: list[str] = []

    existing_registry = {r["target_id"]: dict(r) for r in load_registry(root_path)}
    if force:
        existing_registry = {}

    registry_rows: list[dict[str, str]] = []

    for target in targets:
        target_id = target.target_id
        if force:
            _clear_target_content(root_path, target_id)
        ensure_empty_inventory(root_path, target_id, force=force)

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


def _clear_target_content(root: Path, target_id: str) -> None:
    with connection(root) as conn:
        conn.execute("DELETE FROM vehicles WHERE target_id = ?", (target_id,))
        conn.execute("DELETE FROM observations WHERE target_id = ?", (target_id,))
        conn.execute("DELETE FROM listing_events WHERE target_id = ?", (target_id,))
        conn.execute("DELETE FROM current_inventory WHERE target_id = ?", (target_id,))
