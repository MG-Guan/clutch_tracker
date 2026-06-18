"""Tests for scan comparison and import logic."""

from __future__ import annotations

import json
from pathlib import Path

from clutch_tracker.comparison import import_scan, parse_scan_payload
from clutch_tracker.storage import (
    events_path,
    load_current_inventory,
    observations_path,
    raw_scan_archive_path,
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


def test_import_excludes_carfax_accident_history(project_copy: Path):
    data = _load_fixture("scan_complete.json")
    data["scan_id"] = "scan_with_accident_history"
    data["vehicles"].append(
        {
            "vin": "1FTFW1E55NFA99999",
            "listing_id": "clutch-accident",
            "year": 2022,
            "make": "Ford",
            "model": "F-150",
            "trim": "LARIAT",
            "price_cad": 39000,
            "mileage_km": 41000,
            "carfax": {"accident_reported": True},
        }
    )

    summary = import_scan(project_copy, data)

    assert summary["vehicles_scanned"] == 3
    assert summary["vehicles_excluded_accident_history"] == 1
    assert summary["observations_added"] == 2
    assert summary["active_inventory"] == 2

    inventory = load_current_inventory(project_copy, "ford-f150-ontario")
    assert inventory is not None
    assert {v.vin for v in inventory.vehicles if v.status == "active"} == {
        "1FTFW1E50NFA12345",
        "2C3CCAAG5JH123456",
    }

    observations = read_csv_rows(observations_path(project_copy, "ford-f150-ontario"))
    assert "1FTFW1E55NFA99999" not in {row["vin"] for row in observations}

    archive = json.loads(raw_scan_archive_path(project_copy, "scan_with_accident_history").read_text(encoding="utf-8"))
    assert len(archive["vehicles"]) == 3


def test_partial_scan_excludes_tracked_accident_history_vehicle(project_copy: Path):
    complete = _load_fixture("scan_complete.json")
    import_scan(project_copy, complete)

    partial = _load_fixture("scan_partial.json")
    partial["scan_id"] = "scan_partial_accident_history"
    partial["vehicles"] = [
        {
            **partial["vehicles"][0],
            "vin": "1FTFW1E50NFA12345",
            "carfax_has_accident": "yes",
        }
    ]

    summary = import_scan(project_copy, partial)

    inventory = load_current_inventory(project_copy, "ford-f150-ontario")
    assert inventory is not None
    by_vin = {v.vin: v for v in inventory.vehicles}
    assert by_vin["1FTFW1E50NFA12345"].status == "removed"
    assert by_vin["2C3CCAAG5JH123456"].status == "active"
    assert summary["vehicles_excluded_accident_history"] == 1
    assert summary["active_inventory"] == 1
    assert summary["removed_inventory"] == 1


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
