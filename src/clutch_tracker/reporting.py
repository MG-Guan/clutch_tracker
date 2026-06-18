"""Report generation for tracked targets."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from clutch_tracker.carfax import is_clean_history
from clutch_tracker.config import load_settings, project_root
from clutch_tracker.storage import (
    atomic_write,
    daily_report_dir,
    load_current_inventory,
    load_vehicles,
    now_iso,
    read_csv_rows,
    snapshot_dir,
    events_path,
)

logger = logging.getLogger(__name__)


def generate_daily_report(root: Path | None, target_id: str) -> Path:
    """Generate a daily markdown report for a target."""
    root_path = project_root(root)
    timestamp = now_iso(root_path)

    inventory = load_current_inventory(root_path, target_id)
    vehicles = load_vehicles(root_path, target_id)
    events = read_csv_rows(events_path(root_path, target_id))

    report_dir = daily_report_dir(root_path, target_id)
    report_dir.mkdir(parents=True, exist_ok=True)

    date_part = timestamp[:10]
    report_path = report_dir / f"{date_part}.md"

    active = [v for v in (inventory.vehicles if inventory else []) if v.status == "active"]
    removed = [v for v in (inventory.vehicles if inventory else []) if v.status == "removed"]
    with_carfax = [v for v in active if v.carfax_summary]
    clean_history = [v for v in active if is_clean_history(v.carfax) is True]
    flagged_history = [v for v in active if is_clean_history(v.carfax) is False]

    lines = [
        f"# Daily Report: {target_id}",
        "",
        f"Generated at: {timestamp}",
        "",
        "## Summary",
        "",
        f"- Active listings: {len(active)}",
        f"- Removed listings: {len(removed)}",
        f"- Total vehicles tracked (VIN registry): {len(vehicles)}",
        f"- Total listing events: {len(events)}",
        f"- Active listings with Carfax summary: {len(with_carfax)}",
        f"- Clean Carfax history: {len(clean_history)}",
        f"- Carfax risk flags present: {len(flagged_history)}",
        "",
    ]

    if inventory:
        lines.extend(
            [
                f"- Last scan ID: {inventory.scan_id or 'unknown'}",
                f"- Last scan complete: {inventory.scan_complete}",
                f"- Inventory updated at: {inventory.updated_at}",
                "",
            ]
        )

    lines.extend(["## Active Inventory", ""])
    if not active:
        lines.append("_No active listings._")
    else:
        lines.append("| VIN | Year | Make | Model | Price (CAD) | Mileage (km) | Carfax Summary | Last Seen |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for v in sorted(active, key=lambda x: x.vin):
            reg = vehicles.get(v.vin)
            year = v.year or (reg.year if reg else "unknown")
            make = v.make or (reg.make if reg else "unknown")
            model = v.model or (reg.model if reg else "unknown")
            price = v.price_cad or "unknown"
            mileage = v.mileage_km or "unknown"
            carfax_summary = v.carfax_summary or "—"
            lines.append(
                f"| {v.vin} | {year} | {make} | {model} | {price} | {mileage} | {carfax_summary} | {v.last_seen_at} |"
            )

    if flagged_history:
        lines.extend(["", "## Carfax Risk Flags", ""])
        for v in sorted(flagged_history, key=lambda x: x.vin):
            lines.append(f"- `{v.vin}`: {v.carfax_summary or 'risk flags present'}")

    lines.extend(["", "## Recent Listing Events", ""])
    recent_events = events[-10:]
    if not recent_events:
        lines.append("_No listing events recorded._")
    else:
        for event in recent_events:
            event_type = event.get("event_type", "unknown")
            detail_suffix = ""
            if event_type == "carfax_flag_changed":
                try:
                    details = json.loads(event.get("details_json") or "{}")
                except json.JSONDecodeError:
                    details = {}
                field_name = details.get("field", "unknown")
                detail_suffix = f" — `{field_name}` changed"
            lines.append(
                f"- `{event.get('event_at', 'unknown')}` "
                f"**{event_type}** "
                f"VIN `{event.get('vin', 'unknown')}` "
                f"(scan: {event.get('scan_id', 'unknown')}){detail_suffix}"
            )

    content = "\n".join(lines) + "\n"
    atomic_write(report_path, content)
    logger.info("Wrote daily report to %s", report_path)

    snap_dir = snapshot_dir(root_path, target_id)
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_path = snap_dir / f"{date_part}_inventory.json"
    if inventory:
        payload = {
            "target_id": inventory.target_id,
            "snapshot_at": timestamp,
            "updated_at": inventory.updated_at,
            "scan_id": inventory.scan_id,
            "scan_complete": inventory.scan_complete,
            "vehicles": [
                {
                    "vin": v.vin,
                    "status": v.status,
                    "last_seen_at": v.last_seen_at,
                    "price_cad": v.price_cad,
                    "mileage_km": v.mileage_km,
                    "carfax_summary": v.carfax_summary,
                }
                for v in inventory.vehicles
            ],
        }
        atomic_write(snap_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
        logger.info("Wrote inventory snapshot to %s", snap_path)

    return report_path
