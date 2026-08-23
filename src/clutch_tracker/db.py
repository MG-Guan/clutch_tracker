"""SQLite local database for clutch_tracker content."""

from __future__ import annotations

import csv
import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from clutch_tracker.config import load_settings, project_root

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS targets_registry (
    target_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    enabled TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vehicles (
    vin TEXT NOT NULL,
    target_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    year TEXT,
    make TEXT,
    model TEXT,
    trim TEXT,
    listing_id TEXT,
    listing_url TEXT,
    PRIMARY KEY (target_id, vin)
);

CREATE TABLE IF NOT EXISTS observations (
    observation_id TEXT PRIMARY KEY,
    vin TEXT NOT NULL,
    target_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    scan_id TEXT NOT NULL,
    price_cad TEXT,
    mileage_km TEXT,
    listing_url TEXT,
    listing_id TEXT,
    year TEXT,
    make TEXT,
    model TEXT,
    trim TEXT
);
CREATE INDEX IF NOT EXISTS idx_observations_target_at
    ON observations (target_id, observed_at);

CREATE TABLE IF NOT EXISTS listing_events (
    event_id TEXT PRIMARY KEY,
    vin TEXT NOT NULL,
    target_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_at TEXT NOT NULL,
    scan_id TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_listing_events_target_at
    ON listing_events (target_id, event_at);

CREATE TABLE IF NOT EXISTS current_inventory (
    target_id TEXT PRIMARY KEY,
    updated_at TEXT NOT NULL,
    scan_id TEXT,
    scan_complete INTEGER NOT NULL DEFAULT 0,
    vehicles_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS raw_scans (
    scan_id TEXT PRIMARY KEY,
    target_id TEXT,
    scanned_at TEXT,
    scan_complete INTEGER,
    archived_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
"""


def database_path(root: Path | None = None) -> Path:
    """Return configured SQLite database path."""
    root_path = project_root(root)
    settings = load_settings(root_path)
    storage = settings.get("storage") or {}
    configured = storage.get("database_path", "data/clutch_tracker.db")
    path = Path(configured)
    if not path.is_absolute():
        path = root_path / path
    return path


def connect(root: Path | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with schema ensured and legacy files migrated."""
    root_path = project_root(root)
    path = database_path(root_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_schema(conn)
    _migrate_legacy_files_if_needed(conn, root_path)
    return conn


@contextmanager
def connection(root: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Context manager that commits on success and closes the connection."""
    conn = connect(root)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    row = conn.execute("SELECT value FROM meta WHERE key = ?", ("schema_version",)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
    conn.commit()


def _meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _migrate_legacy_files_if_needed(conn: sqlite3.Connection, root: Path) -> None:
    if _meta_get(conn, "legacy_migrated") == "1":
        return

    registry_csv = root / "data" / "registry" / "targets.csv"
    targets_root = root / "data" / "targets"
    scans_root = root / "data" / "raw_scans"

    has_legacy = registry_csv.exists() or (
        targets_root.is_dir() and any(targets_root.glob("*/*.csv"))
    ) or (scans_root.is_dir() and any(scans_root.glob("*.json")))

    if not has_legacy:
        _meta_set(conn, "legacy_migrated", "1")
        conn.commit()
        return

    logger.info("Migrating legacy CSV/JSON content into SQLite at %s", database_path(root))

    if registry_csv.exists():
        for row in _read_csv_rows(registry_csv):
            target_id = (row.get("target_id") or "").strip()
            if not target_id:
                continue
            conn.execute(
                """
                INSERT INTO targets_registry (target_id, label, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(target_id) DO UPDATE SET
                    label = excluded.label,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    target_id,
                    row.get("label") or target_id,
                    row.get("enabled") or "true",
                    row.get("created_at") or "",
                    row.get("updated_at") or "",
                ),
            )

    if targets_root.is_dir():
        for target_dir in sorted(p for p in targets_root.iterdir() if p.is_dir()):
            target_id = target_dir.name
            _migrate_target_dir(conn, target_dir, target_id)

    if scans_root.is_dir():
        for scan_path in sorted(scans_root.glob("*.json")):
            try:
                payload = json.loads(scan_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Skipping unreadable scan %s: %s", scan_path, exc)
                continue
            scan_id = payload.get("scan_id") or scan_path.stem
            conn.execute(
                """
                INSERT INTO raw_scans (
                    scan_id, target_id, scanned_at, scan_complete, archived_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(scan_id) DO NOTHING
                """,
                (
                    scan_id,
                    payload.get("target_id"),
                    payload.get("scanned_at"),
                    1 if payload.get("scan_complete") else 0,
                    "",
                    json.dumps(payload, sort_keys=True),
                ),
            )

    _meta_set(conn, "legacy_migrated", "1")
    conn.commit()
    logger.info("Legacy file migration complete")


def _migrate_target_dir(conn: sqlite3.Connection, target_dir: Path, target_id: str) -> None:
    vehicle_rows = []
    for row in _read_csv_rows(target_dir / "vehicles.csv"):
        vin = (row.get("vin") or "").strip()
        if not vin:
            continue
        vehicle_rows.append(
            (
                vin,
                row.get("target_id") or target_id,
                row.get("first_seen_at") or "",
                row.get("last_seen_at") or "",
                row.get("year") or None,
                row.get("make") or None,
                row.get("model") or None,
                row.get("trim") or None,
                row.get("listing_id") or None,
                row.get("listing_url") or None,
            )
        )
    if vehicle_rows:
        conn.executemany(
            """
            INSERT INTO vehicles (
                vin, target_id, first_seen_at, last_seen_at, year, make, model, trim,
                listing_id, listing_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(target_id, vin) DO UPDATE SET
                first_seen_at = excluded.first_seen_at,
                last_seen_at = excluded.last_seen_at,
                year = excluded.year,
                make = excluded.make,
                model = excluded.model,
                trim = excluded.trim,
                listing_id = excluded.listing_id,
                listing_url = excluded.listing_url
            """,
            vehicle_rows,
        )

    observation_rows = []
    for row in _read_csv_rows(target_dir / "observations.csv"):
        obs_id = (row.get("observation_id") or "").strip()
        if not obs_id:
            continue
        observation_rows.append(
            (
                obs_id,
                row.get("vin") or "",
                row.get("target_id") or target_id,
                row.get("observed_at") or "",
                row.get("scan_id") or "",
                row.get("price_cad") or None,
                row.get("mileage_km") or None,
                row.get("listing_url") or None,
                row.get("listing_id") or None,
                row.get("year") or None,
                row.get("make") or None,
                row.get("model") or None,
                row.get("trim") or None,
            )
        )
    if observation_rows:
        conn.executemany(
            """
            INSERT INTO observations (
                observation_id, vin, target_id, observed_at, scan_id, price_cad, mileage_km,
                listing_url, listing_id, year, make, model, trim
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(observation_id) DO NOTHING
            """,
            observation_rows,
        )

    event_rows = []
    for row in _read_csv_rows(target_dir / "listing_events.csv"):
        event_id = (row.get("event_id") or "").strip()
        if not event_id:
            continue
        event_rows.append(
            (
                event_id,
                row.get("vin") or "",
                row.get("target_id") or target_id,
                row.get("event_type") or "",
                row.get("event_at") or "",
                row.get("scan_id") or "",
                row.get("details_json") or "{}",
            )
        )
    if event_rows:
        conn.executemany(
            """
            INSERT INTO listing_events (
                event_id, vin, target_id, event_type, event_at, scan_id, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO NOTHING
            """,
            event_rows,
        )

    inventory_path = target_dir / "current_inventory.json"
    if inventory_path.exists():
        try:
            data = json.loads(inventory_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Skipping unreadable inventory %s: %s", inventory_path, exc)
            return
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
                data.get("target_id") or target_id,
                data.get("updated_at") or "",
                data.get("scan_id"),
                1 if data.get("scan_complete") else 0,
                json.dumps(data.get("vehicles") or [], sort_keys=True),
            ),
        )


def row_to_str_dict(row: sqlite3.Row) -> dict[str, str]:
    """Convert a SQLite row to a string-valued dict (CSV-compatible)."""
    result: dict[str, str] = {}
    for key in row.keys():
        value = row[key]
        result[key] = "" if value is None else str(value)
    return result
