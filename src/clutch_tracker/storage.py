"""Local SQLite persistence and atomic file helpers for generated reports."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from clutch_tracker.config import get_timezone_name, load_settings, project_root
from clutch_tracker.db import connection, database_path, row_to_str_dict
from clutch_tracker.models import (
    CurrentInventory,
    InventoryVehicle,
    ListingEvent,
    Observation,
    VehicleRecord,
)

logger = logging.getLogger(__name__)

# Retained for validation/docs compatibility with prior CSV column order.
VEHICLES_HEADERS = [
    "vin",
    "target_id",
    "first_seen_at",
    "last_seen_at",
    "year",
    "make",
    "model",
    "trim",
    "listing_id",
    "listing_url",
]

OBSERVATIONS_HEADERS = [
    "observation_id",
    "vin",
    "target_id",
    "observed_at",
    "scan_id",
    "price_cad",
    "mileage_km",
    "listing_url",
    "listing_id",
    "year",
    "make",
    "model",
    "trim",
]

EVENTS_HEADERS = [
    "event_id",
    "vin",
    "target_id",
    "event_type",
    "event_at",
    "scan_id",
    "details_json",
]

REGISTRY_HEADERS = ["target_id", "label", "enabled", "created_at", "updated_at"]


def now_iso(root: Path | None = None) -> str:
    """Return current timestamp as ISO 8601 in configured timezone."""
    settings = load_settings(root)
    tz_name = get_timezone_name(settings)
    tz = ZoneInfo(tz_name)
    return datetime.now(tz).isoformat(timespec="seconds")


def atomic_write(path: Path, content: str | bytes, *, binary: bool = False) -> None:
    """Write content to path atomically via temp file + replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if binary else "w"
    encoding = None if binary else "utf-8"

    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, mode, encoding=encoding) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, data: Any) -> None:
    """Atomically write JSON data (used for generated snapshots/reports)."""
    content = json.dumps(data, indent=2, sort_keys=True) + "\n"
    atomic_write(path, content)


def new_id(prefix: str) -> str:
    """Generate a unique identifier."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def daily_report_dir(root: Path, target_id: str) -> Path:
    return root / "reports" / "daily" / target_id


def recommendations_report_dir(root: Path, target_id: str) -> Path:
    return root / "reports" / "recommendations" / target_id


def snapshot_dir(root: Path, target_id: str) -> Path:
    return root / "snapshots" / target_id


def ensure_database(root: Path | None = None) -> Path:
    """Ensure the local SQLite database exists and return its path."""
    root_path = project_root(root)
    with connection(root_path):
        pass
    return database_path(root_path)


def load_vehicles(root: Path, target_id: str) -> dict[str, VehicleRecord]:
    """Load vehicle registry indexed by VIN."""
    with connection(root) as conn:
        rows = conn.execute(
            "SELECT * FROM vehicles WHERE target_id = ? ORDER BY vin",
            (target_id,),
        ).fetchall()
    result: dict[str, VehicleRecord] = {}
    for row in rows:
        vin = (row["vin"] or "").strip()
        if not vin:
            continue
        result[vin] = VehicleRecord(
            vin=vin,
            target_id=row["target_id"] or target_id,
            first_seen_at=row["first_seen_at"] or "",
            last_seen_at=row["last_seen_at"] or "",
            year=row["year"],
            make=row["make"],
            model=row["model"],
            trim=row["trim"],
            listing_id=row["listing_id"],
            listing_url=row["listing_url"],
        )
    return result


def save_vehicles(root: Path, target_id: str, vehicles: dict[str, VehicleRecord]) -> None:
    """Persist vehicle registry for a target (replace all rows for that target)."""
    with connection(root) as conn:
        conn.execute("DELETE FROM vehicles WHERE target_id = ?", (target_id,))
        for v in sorted(vehicles.values(), key=lambda x: x.vin):
            conn.execute(
                """
                INSERT INTO vehicles (
                    vin, target_id, first_seen_at, last_seen_at, year, make, model, trim,
                    listing_id, listing_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    v.vin,
                    v.target_id,
                    v.first_seen_at,
                    v.last_seen_at,
                    v.year,
                    v.make,
                    v.model,
                    v.trim,
                    v.listing_id,
                    v.listing_url,
                ),
            )


def append_observations(root: Path, observations: list[Observation]) -> None:
    """Append observations without overwriting history."""
    if not observations:
        return
    with connection(root) as conn:
        for o in observations:
            conn.execute(
                """
                INSERT INTO observations (
                    observation_id, vin, target_id, observed_at, scan_id, price_cad, mileage_km,
                    listing_url, listing_id, year, make, model, trim
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    o.observation_id,
                    o.vin,
                    o.target_id,
                    o.observed_at,
                    o.scan_id,
                    o.price_cad,
                    o.mileage_km,
                    o.listing_url,
                    o.listing_id,
                    o.year,
                    o.make,
                    o.model,
                    o.trim,
                ),
            )
    logger.info("Appended %d observations", len(observations))


def load_observations(root: Path, target_id: str) -> list[dict[str, str]]:
    """Load observation rows for a target as string dicts."""
    with connection(root) as conn:
        rows = conn.execute(
            """
            SELECT * FROM observations
            WHERE target_id = ?
            ORDER BY observed_at, observation_id
            """,
            (target_id,),
        ).fetchall()
    return [row_to_str_dict(row) for row in rows]


def append_events(root: Path, events: list[ListingEvent]) -> None:
    """Append listing events."""
    if not events:
        return
    with connection(root) as conn:
        for e in events:
            conn.execute(
                """
                INSERT INTO listing_events (
                    event_id, vin, target_id, event_type, event_at, scan_id, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    e.event_id,
                    e.vin,
                    e.target_id,
                    e.event_type,
                    e.event_at,
                    e.scan_id,
                    json.dumps(e.details, sort_keys=True),
                ),
            )
    logger.info("Appended %d listing events", len(events))


def load_events(root: Path, target_id: str) -> list[dict[str, str]]:
    """Load listing event rows for a target as string dicts."""
    with connection(root) as conn:
        rows = conn.execute(
            """
            SELECT * FROM listing_events
            WHERE target_id = ?
            ORDER BY event_at, event_id
            """,
            (target_id,),
        ).fetchall()
    return [row_to_str_dict(row) for row in rows]


def _inventory_vehicle_from_dict(v: dict[str, Any]) -> InventoryVehicle:
    return InventoryVehicle(
        vin=v["vin"],
        last_seen_at=v.get("last_seen_at", ""),
        listing_id=v.get("listing_id"),
        listing_url=v.get("listing_url"),
        year=v.get("year"),
        make=v.get("make"),
        model=v.get("model"),
        trim=v.get("trim"),
        price_cad=v.get("price_cad"),
        mileage_km=v.get("mileage_km"),
        status=v.get("status", "active"),
        accident_history_status=v.get("accident_history_status", "unknown"),
        accident_severity=v.get("accident_severity", "unknown"),
        accident_details=v.get("accident_details"),
        vehicle_history_report_status=v.get("vehicle_history_report_status", "not_scanned"),
        vehicle_history_report_provider=v.get("vehicle_history_report_provider"),
        vehicle_history_report_details=v.get("vehicle_history_report_details"),
        maintenance_history_status=v.get("maintenance_history_status", "unknown"),
        maintenance_risk_level=v.get("maintenance_risk_level", "unknown"),
        maintenance_details=v.get("maintenance_details"),
        maintenance_records_count=_optional_int(v.get("maintenance_records_count")),
        maintenance_locations_count=_optional_int(v.get("maintenance_locations_count")),
        maintenance_replaced_components_count=_optional_int(
            v.get("maintenance_replaced_components_count")
        ),
        previous_use=v.get("previous_use", "unknown"),
        previous_use_details=v.get("previous_use_details"),
        interprovincial_history=v.get("interprovincial_history", "unknown"),
        province_history=v.get("province_history"),
        interprovincial_details=v.get("interprovincial_details"),
        recommendation_eligible=v.get("recommendation_eligible", True),
    )


def _inventory_vehicle_to_dict(v: InventoryVehicle) -> dict[str, Any]:
    return {
        "vin": v.vin,
        "last_seen_at": v.last_seen_at,
        "listing_id": v.listing_id,
        "listing_url": v.listing_url,
        "year": v.year,
        "make": v.make,
        "model": v.model,
        "trim": v.trim,
        "price_cad": v.price_cad,
        "mileage_km": v.mileage_km,
        "status": v.status,
        "accident_history_status": v.accident_history_status,
        "accident_severity": v.accident_severity,
        "accident_details": v.accident_details,
        "vehicle_history_report_status": v.vehicle_history_report_status,
        "vehicle_history_report_provider": v.vehicle_history_report_provider,
        "vehicle_history_report_details": v.vehicle_history_report_details,
        "maintenance_history_status": v.maintenance_history_status,
        "maintenance_risk_level": v.maintenance_risk_level,
        "maintenance_details": v.maintenance_details,
        "maintenance_records_count": v.maintenance_records_count,
        "maintenance_locations_count": v.maintenance_locations_count,
        "maintenance_replaced_components_count": v.maintenance_replaced_components_count,
        "previous_use": v.previous_use,
        "previous_use_details": v.previous_use_details,
        "interprovincial_history": v.interprovincial_history,
        "province_history": v.province_history,
        "interprovincial_details": v.interprovincial_details,
        "recommendation_eligible": v.recommendation_eligible,
    }


def load_current_inventory(root: Path, target_id: str) -> CurrentInventory | None:
    """Load current inventory snapshot."""
    with connection(root) as conn:
        row = conn.execute(
            "SELECT * FROM current_inventory WHERE target_id = ?",
            (target_id,),
        ).fetchone()
    if row is None:
        return None
    vehicles_data = json.loads(row["vehicles_json"] or "[]")
    vehicles = [_inventory_vehicle_from_dict(v) for v in vehicles_data]
    return CurrentInventory(
        target_id=row["target_id"] or target_id,
        updated_at=row["updated_at"] or "",
        scan_id=row["scan_id"],
        scan_complete=bool(row["scan_complete"]),
        vehicles=vehicles,
    )


def save_current_inventory(root: Path, inventory: CurrentInventory) -> None:
    """Persist current inventory snapshot."""
    vehicles_payload = [
        _inventory_vehicle_to_dict(v) for v in sorted(inventory.vehicles, key=lambda x: x.vin)
    ]
    with connection(root) as conn:
        conn.execute(
            """
            INSERT INTO current_inventory (
                target_id, updated_at, scan_id, scan_complete, vehicles_json
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(target_id) DO UPDATE SET
                updated_at = excluded.updated_at,
                scan_id = excluded.scan_id,
                scan_complete = excluded.scan_complete,
                vehicles_json = excluded.vehicles_json
            """,
            (
                inventory.target_id,
                inventory.updated_at,
                inventory.scan_id,
                1 if inventory.scan_complete else 0,
                json.dumps(vehicles_payload, sort_keys=True),
            ),
        )


def ensure_empty_inventory(root: Path, target_id: str, *, force: bool = False) -> None:
    """Create an empty inventory row for a target if missing (or when force=True)."""
    with connection(root) as conn:
        existing = conn.execute(
            "SELECT 1 FROM current_inventory WHERE target_id = ?",
            (target_id,),
        ).fetchone()
        if existing is not None and not force:
            return
        conn.execute(
            """
            INSERT INTO current_inventory (
                target_id, updated_at, scan_id, scan_complete, vehicles_json
            ) VALUES (?, ?, NULL, 0, '[]')
            ON CONFLICT(target_id) DO UPDATE SET
                updated_at = excluded.updated_at,
                scan_id = NULL,
                scan_complete = 0,
                vehicles_json = '[]'
            """,
            (target_id, now_iso(root)),
        )


def delete_target_inventory(root: Path, target_id: str) -> None:
    """Remove inventory row (used by tests / force re-init)."""
    with connection(root) as conn:
        conn.execute("DELETE FROM current_inventory WHERE target_id = ?", (target_id,))


def load_registry(root: Path) -> list[dict[str, str]]:
    with connection(root) as conn:
        rows = conn.execute(
            "SELECT * FROM targets_registry ORDER BY target_id"
        ).fetchall()
    return [row_to_str_dict(row) for row in rows]


def save_registry(root: Path, rows: list[dict[str, Any]]) -> None:
    with connection(root) as conn:
        conn.execute("DELETE FROM targets_registry")
        for row in rows:
            conn.execute(
                """
                INSERT INTO targets_registry (target_id, label, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(row.get("target_id") or ""),
                    str(row.get("label") or ""),
                    str(row.get("enabled") or ""),
                    str(row.get("created_at") or ""),
                    str(row.get("updated_at") or ""),
                ),
            )


def save_raw_scan(root: Path, scan_id: str, scan_data: dict[str, Any]) -> None:
    """Archive a raw scan payload in the local database."""
    with connection(root) as conn:
        conn.execute(
            """
            INSERT INTO raw_scans (
                scan_id, target_id, scanned_at, scan_complete, archived_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(scan_id) DO UPDATE SET
                target_id = excluded.target_id,
                scanned_at = excluded.scanned_at,
                scan_complete = excluded.scan_complete,
                archived_at = excluded.archived_at,
                payload_json = excluded.payload_json
            """,
            (
                scan_id,
                scan_data.get("target_id"),
                scan_data.get("scanned_at"),
                1 if scan_data.get("scan_complete") else 0,
                now_iso(root),
                json.dumps(scan_data, sort_keys=True),
            ),
        )


def load_raw_scan(root: Path, scan_id: str) -> dict[str, Any] | None:
    """Load an archived raw scan payload."""
    with connection(root) as conn:
        row = conn.execute(
            "SELECT payload_json FROM raw_scans WHERE scan_id = ?",
            (scan_id,),
        ).fetchone()
    if row is None:
        return None
    return json.loads(row["payload_json"])


def list_raw_scans(root: Path) -> list[dict[str, Any]]:
    """List archived raw scans (newest first)."""
    with connection(root) as conn:
        rows = conn.execute(
            """
            SELECT scan_id, target_id, scanned_at, scan_complete, archived_at,
                   LENGTH(payload_json) AS size_bytes
            FROM raw_scans
            ORDER BY COALESCE(scanned_at, archived_at, scan_id) DESC
            """
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "scan_id": row["scan_id"],
                "target_id": row["target_id"],
                "scanned_at": row["scanned_at"],
                "scan_complete": bool(row["scan_complete"]),
                "archived_at": row["archived_at"],
                "size_bytes": row["size_bytes"],
            }
        )
    return result


def normalize_optional_str(value: Any) -> str | None:
    """Convert value to string or None; never guess unknowns."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    return str(value)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
