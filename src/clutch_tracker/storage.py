"""Atomic file storage and CSV/JSON persistence."""

from __future__ import annotations

import csv
import json
import logging
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

from clutch_tracker.config import get_timezone_name, load_settings, project_root
from clutch_tracker.models import (
    CurrentInventory,
    InventoryVehicle,
    ListingEvent,
    Observation,
    VehicleRecord,
)

logger = logging.getLogger(__name__)

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
    """Atomically write JSON data."""
    content = json.dumps(data, indent=2, sort_keys=True) + "\n"
    atomic_write(path, content)


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    """Read CSV file into list of row dicts."""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader]


def append_csv_rows(path: Path, headers: Sequence[str], rows: Iterable[dict[str, Any]]) -> None:
    """Append rows to a CSV file, creating it with headers if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(headers), extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: _stringify(row.get(k)) for k in headers})


def write_csv_rows(path: Path, headers: Sequence[str], rows: Iterable[dict[str, Any]]) -> None:
    """Overwrite CSV file atomically."""
    lines: list[str] = []
    import io

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(headers), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _stringify(row.get(k)) for k in headers})
    atomic_write(path, buffer.getvalue())


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def new_id(prefix: str) -> str:
    """Generate a unique identifier."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def target_dir(root: Path, target_id: str) -> Path:
    return root / "data" / "targets" / target_id


def vehicles_path(root: Path, target_id: str) -> Path:
    return target_dir(root, target_id) / "vehicles.csv"


def observations_path(root: Path, target_id: str) -> Path:
    return target_dir(root, target_id) / "observations.csv"


def events_path(root: Path, target_id: str) -> Path:
    return target_dir(root, target_id) / "listing_events.csv"


def inventory_path(root: Path, target_id: str) -> Path:
    return target_dir(root, target_id) / "current_inventory.json"


def registry_path(root: Path) -> Path:
    return root / "data" / "registry" / "targets.csv"


def raw_scan_archive_path(root: Path, scan_id: str) -> Path:
    return root / "data" / "raw_scans" / f"{scan_id}.json"


def daily_report_dir(root: Path, target_id: str) -> Path:
    return root / "reports" / "daily" / target_id


def recommendations_report_dir(root: Path, target_id: str) -> Path:
    return root / "reports" / "recommendations" / target_id


def snapshot_dir(root: Path, target_id: str) -> Path:
    return root / "snapshots" / target_id


def load_vehicles(root: Path, target_id: str) -> dict[str, VehicleRecord]:
    """Load vehicle registry indexed by VIN."""
    rows = read_csv_rows(vehicles_path(root, target_id))
    result: dict[str, VehicleRecord] = {}
    for row in rows:
        vin = row.get("vin", "").strip()
        if not vin:
            continue
        result[vin] = VehicleRecord(
            vin=vin,
            target_id=row.get("target_id", target_id),
            first_seen_at=row.get("first_seen_at", ""),
            last_seen_at=row.get("last_seen_at", ""),
            year=row.get("year") or None,
            make=row.get("make") or None,
            model=row.get("model") or None,
            trim=row.get("trim") or None,
            listing_id=row.get("listing_id") or None,
            listing_url=row.get("listing_url") or None,
        )
    return result


def save_vehicles(root: Path, target_id: str, vehicles: dict[str, VehicleRecord]) -> None:
    """Persist vehicle registry."""
    rows = [
        {
            "vin": v.vin,
            "target_id": v.target_id,
            "first_seen_at": v.first_seen_at,
            "last_seen_at": v.last_seen_at,
            "year": v.year,
            "make": v.make,
            "model": v.model,
            "trim": v.trim,
            "listing_id": v.listing_id,
            "listing_url": v.listing_url,
        }
        for v in sorted(vehicles.values(), key=lambda x: x.vin)
    ]
    write_csv_rows(vehicles_path(root, target_id), VEHICLES_HEADERS, rows)


def append_observations(root: Path, observations: list[Observation]) -> None:
    """Append observations without overwriting history."""
    if not observations:
        return
    by_target: dict[str, list[Observation]] = {}
    for obs in observations:
        by_target.setdefault(obs.target_id, []).append(obs)

    for tid, obs_list in by_target.items():
        rows = [
            {
                "observation_id": o.observation_id,
                "vin": o.vin,
                "target_id": o.target_id,
                "observed_at": o.observed_at,
                "scan_id": o.scan_id,
                "price_cad": o.price_cad,
                "mileage_km": o.mileage_km,
                "listing_url": o.listing_url,
                "listing_id": o.listing_id,
                "year": o.year,
                "make": o.make,
                "model": o.model,
                "trim": o.trim,
            }
            for o in obs_list
        ]
        append_csv_rows(observations_path(root, tid), OBSERVATIONS_HEADERS, rows)
        logger.info("Appended %d observations for target %s", len(rows), tid)


def append_events(root: Path, events: list[ListingEvent]) -> None:
    """Append listing events."""
    if not events:
        return
    by_target: dict[str, list[ListingEvent]] = {}
    for event in events:
        by_target.setdefault(event.target_id, []).append(event)

    for tid, event_list in by_target.items():
        rows = [
            {
                "event_id": e.event_id,
                "vin": e.vin,
                "target_id": e.target_id,
                "event_type": e.event_type,
                "event_at": e.event_at,
                "scan_id": e.scan_id,
                "details_json": json.dumps(e.details, sort_keys=True),
            }
            for e in event_list
        ]
        append_csv_rows(events_path(root, tid), EVENTS_HEADERS, rows)
        logger.info("Appended %d listing events for target %s", len(rows), tid)


def load_current_inventory(root: Path, target_id: str) -> CurrentInventory | None:
    """Load current inventory snapshot."""
    path = inventory_path(root, target_id)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    vehicles = [
        InventoryVehicle(
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
            maintenance_replaced_components_count=_optional_int(v.get("maintenance_replaced_components_count")),
            previous_use=v.get("previous_use", "unknown"),
            previous_use_details=v.get("previous_use_details"),
            interprovincial_history=v.get("interprovincial_history", "unknown"),
            province_history=v.get("province_history"),
            interprovincial_details=v.get("interprovincial_details"),
            recommendation_eligible=v.get("recommendation_eligible", True),
        )
        for v in data.get("vehicles", [])
    ]
    return CurrentInventory(
        target_id=data.get("target_id", target_id),
        updated_at=data.get("updated_at", ""),
        scan_id=data.get("scan_id"),
        scan_complete=data.get("scan_complete", False),
        vehicles=vehicles,
    )


def save_current_inventory(root: Path, inventory: CurrentInventory) -> None:
    """Persist current inventory snapshot."""
    payload = {
        "target_id": inventory.target_id,
        "updated_at": inventory.updated_at,
        "scan_id": inventory.scan_id,
        "scan_complete": inventory.scan_complete,
        "vehicles": [
            {
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
            for v in sorted(inventory.vehicles, key=lambda x: x.vin)
        ],
    }
    atomic_write_json(inventory_path(root, inventory.target_id), payload)


def load_registry(root: Path) -> list[dict[str, str]]:
    return read_csv_rows(registry_path(root))


def save_registry(root: Path, rows: list[dict[str, Any]]) -> None:
    write_csv_rows(registry_path(root), REGISTRY_HEADERS, rows)


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
