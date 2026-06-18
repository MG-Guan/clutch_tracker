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

from clutch_tracker.carfax import carfax_from_dict, carfax_to_dict
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
    "carfax_summary",
    "carfax_accident_reported",
    "carfax_commercial_use",
    "carfax_service_records",
    "carfax_json",
    "raw_fields_json",
]

LEGACY_OBSERVATIONS_HEADERS = OBSERVATIONS_HEADERS[:13]

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


def _bool_to_csv(value: bool | None) -> str:
    if value is None:
        return ""
    return "true" if value else "false"


def _csv_to_bool(value: str | None) -> bool | None:
    if value is None or str(value).strip() == "":
        return None
    normalized = str(value).strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return None


def ensure_observations_schema(root: Path, target_id: str) -> None:
    """Upgrade observations.csv to the current schema when needed."""
    path = observations_path(root, target_id)
    if not path.exists() or path.stat().st_size == 0:
        return

    with path.open("r", encoding="utf-8") as handle:
        first_line = handle.readline().strip()
    if not first_line:
        return

    actual = [h.strip() for h in first_line.split(",")]
    if actual == OBSERVATIONS_HEADERS:
        return

    if actual != LEGACY_OBSERVATIONS_HEADERS:
        logger.warning(
            "observations.csv for %s has unexpected headers; skipping schema upgrade",
            target_id,
        )
        return

    rows = read_csv_rows(path)
    upgraded = []
    for row in rows:
        upgraded.append(
            {
                **{header: row.get(header, "") for header in LEGACY_OBSERVATIONS_HEADERS},
                "carfax_summary": "",
                "carfax_accident_reported": "",
                "carfax_commercial_use": "",
                "carfax_service_records": "",
                "carfax_json": "",
                "raw_fields_json": "",
            }
        )
    write_csv_rows(path, OBSERVATIONS_HEADERS, upgraded)
    logger.info("Upgraded observations.csv schema for target %s", target_id)


def append_observations(root: Path, observations: list[Observation]) -> None:
    """Append observations without overwriting history."""
    if not observations:
        return
    by_target: dict[str, list[Observation]] = {}
    for obs in observations:
        by_target.setdefault(obs.target_id, []).append(obs)

    for tid, obs_list in by_target.items():
        ensure_observations_schema(root, tid)
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
                "carfax_summary": o.carfax_summary,
                "carfax_accident_reported": _bool_to_csv(
                    o.carfax.accident_reported if o.carfax else None
                ),
                "carfax_commercial_use": _bool_to_csv(
                    o.carfax.commercial_use if o.carfax else None
                ),
                "carfax_service_records": (
                    o.carfax.service_record_count if o.carfax and o.carfax.service_record_count is not None else ""
                ),
                "carfax_json": json.dumps(carfax_to_dict(o.carfax), sort_keys=True)
                if o.carfax is not None
                else "",
                "raw_fields_json": json.dumps(o.raw_fields, sort_keys=True) if o.raw_fields else "",
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
            carfax=carfax_from_dict(v.get("carfax")),
            carfax_summary=v.get("carfax_summary"),
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
                "carfax_summary": v.carfax_summary,
                "carfax": carfax_to_dict(v.carfax) if v.carfax is not None else None,
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
