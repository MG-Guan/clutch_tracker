"""Tests for scan comparison and import logic."""

from __future__ import annotations

import json
from pathlib import Path

from clutch_tracker.comparison import import_scan, parse_scan_payload
from clutch_tracker.storage import (
    events_path,
    load_current_inventory,
    observations_path,
    read_csv_rows,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_fixture(name: str) -> dict:
    with (FIXTURES / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_parse_scan_payload(project_copy: Path):
    data = _load_fixture("scan_complete.json")
    payload = parse_scan_payload(data)
    assert payload.target_id == "ford-f150-ontario"
    assert payload.scan_complete is True
    assert len(payload.vehicles) == 2
    assert payload.vehicles[0].vin == "1FTFW1E50NFA12345"


def test_import_complete_scan(project_copy: Path):
    data = _load_fixture("scan_complete.json")
    summary = import_scan(project_copy, data)
    assert summary["observations_added"] == 2
    assert summary["active_inventory"] == 2

    inventory = load_current_inventory(project_copy, "ford-f150-ontario")
    assert inventory is not None
    assert len([v for v in inventory.vehicles if v.status == "active"]) == 2

    observations = read_csv_rows(observations_path(project_copy, "ford-f150-ontario"))
    assert len(observations) == 2


def test_partial_scan_does_not_remove_vehicles(project_copy: Path):
    complete = _load_fixture("scan_complete.json")
    import_scan(project_copy, complete)

    partial = _load_fixture("scan_partial.json")
    summary = import_scan(project_copy, partial)

    inventory = load_current_inventory(project_copy, "ford-f150-ontario")
    active = [v for v in inventory.vehicles if v.status == "active"]
    assert len(active) == 2, "Partial scan must not mark missing vehicles as removed"
    assert summary["removed_inventory"] == 0

    events = read_csv_rows(events_path(project_copy, "ford-f150-ontario"))
    removed_events = [e for e in events if e["event_type"] == "listing_removed"]
    assert len(removed_events) == 0


def test_complete_rescan_marks_removed(project_copy: Path):
    complete = _load_fixture("scan_complete.json")
    import_scan(project_copy, complete)

    removal_scan = _load_fixture("scan_complete.json")
    removal_scan["scan_id"] = "scan_removal"
    removal_scan["scanned_at"] = "2026-06-16T12:00:00-04:00"
    removal_scan["vehicles"] = removal_scan["vehicles"][:1]

    summary = import_scan(project_copy, removal_scan)
    assert summary["active_inventory"] == 1
    assert summary["removed_inventory"] == 1

    events = read_csv_rows(events_path(project_copy, "ford-f150-ontario"))
    removed_events = [e for e in events if e["event_type"] == "listing_removed"]
    assert len(removed_events) == 1


def test_price_change_event(project_copy: Path):
    complete = _load_fixture("scan_complete.json")
    import_scan(project_copy, complete)

    partial = _load_fixture("scan_partial.json")
    partial["scan_complete"] = True
    partial["scan_id"] = "scan_price_change"
    import_scan(project_copy, partial)

    events = read_csv_rows(events_path(project_copy, "ford-f150-ontario"))
    price_events = [e for e in events if e["event_type"] == "price_changed"]
    assert len(price_events) >= 1


def test_import_persists_carfax_fields(project_copy: Path):
    data = _load_fixture("scan_complete.json")
    import_scan(project_copy, data)

    observations = read_csv_rows(observations_path(project_copy, "ford-f150-ontario"))
    assert len(observations) == 2
    clean_row = next(row for row in observations if row["vin"] == "1FTFW1E50NFA12345")
    assert clean_row["carfax_accident_reported"] == "false"
    assert clean_row["carfax_commercial_use"] == "false"
    assert clean_row["carfax_service_records"] == "8"
    assert "Clean history" in clean_row["carfax_summary"]
    assert clean_row["raw_fields_json"]
    assert '"availability_status"' in clean_row["raw_fields_json"]

    inventory = load_current_inventory(project_copy, "ford-f150-ontario")
    clean_vehicle = next(v for v in inventory.vehicles if v.vin == "1FTFW1E50NFA12345")
    assert clean_vehicle.carfax is not None
    assert clean_vehicle.carfax_summary is not None
    assert "Clean history" in clean_vehicle.carfax_summary


def test_carfax_flag_changed_event(project_copy: Path):
    complete = _load_fixture("scan_complete.json")
    import_scan(project_copy, complete)

    updated = _load_fixture("scan_complete.json")
    updated["scan_id"] = "scan_carfax_change"
    updated["scanned_at"] = "2026-06-16T12:00:00-04:00"
    updated["vehicles"] = [updated["vehicles"][0]]
    updated["vehicles"][0]["carfax"]["accident_reported"] = True
    updated["vehicles"][0]["carfax"]["accident_count"] = 1
    import_scan(project_copy, updated)

    events = read_csv_rows(events_path(project_copy, "ford-f150-ontario"))
    carfax_events = [e for e in events if e["event_type"] == "carfax_flag_changed"]
    assert len(carfax_events) >= 1
