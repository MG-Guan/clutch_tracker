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


def test_import_records_carfax_accident_history_without_excluding(project_copy: Path):
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
    assert summary["vehicles_with_reported_accident_history"] == 1
    assert summary["vehicles_with_non_recommendable_accident_history"] == 1
    assert summary["observations_added"] == 3
    assert summary["active_inventory"] == 3

    inventory = load_current_inventory(project_copy, "ford-f150-ontario")
    assert inventory is not None
    assert {v.vin for v in inventory.vehicles if v.status == "active"} == {
        "1FTFW1E50NFA12345",
        "1FTFW1E55NFA99999",
        "2C3CCAAG5JH123456",
    }
    accident_vehicle = next(v for v in inventory.vehicles if v.vin == "1FTFW1E55NFA99999")
    assert accident_vehicle.accident_history_status == "reported"
    assert accident_vehicle.accident_severity == "unknown"
    assert accident_vehicle.recommendation_eligible is False

    observations = read_csv_rows(observations_path(project_copy, "ford-f150-ontario"))
    assert "1FTFW1E55NFA99999" in {row["vin"] for row in observations}

    events = read_csv_rows(events_path(project_copy, "ford-f150-ontario"))
    accident_events = [e for e in events if e["event_type"] == "accident_history_assessed"]
    assert len(accident_events) == 1

    archive = json.loads(raw_scan_archive_path(project_copy, "scan_with_accident_history").read_text(encoding="utf-8"))
    assert len(archive["vehicles"]) == 3


def test_partial_scan_records_tracked_accident_history_vehicle(project_copy: Path):
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
    assert by_vin["1FTFW1E50NFA12345"].status == "active"
    assert by_vin["1FTFW1E50NFA12345"].accident_history_status == "reported"
    assert by_vin["1FTFW1E50NFA12345"].recommendation_eligible is False
    assert by_vin["2C3CCAAG5JH123456"].status == "active"
    assert summary["vehicles_with_reported_accident_history"] == 1
    assert summary["active_inventory"] == 2
    assert summary["removed_inventory"] == 0


def test_minor_accident_history_remains_recommendation_eligible(project_copy: Path):
    data = _load_fixture("scan_complete.json")
    data["scan_id"] = "scan_minor_accident_history"
    data["vehicles"][0]["carfax"] = {
        "accident_reported": True,
        "repair_description": "minor rear bumper cosmetic repair",
        "repair_cost_cad": 1200,
    }

    summary = import_scan(project_copy, data)

    inventory = load_current_inventory(project_copy, "ford-f150-ontario")
    assert inventory is not None
    vehicle = next(v for v in inventory.vehicles if v.vin == "1FTFW1E50NFA12345")
    assert vehicle.accident_history_status == "reported"
    assert vehicle.accident_severity == "minor"
    assert vehicle.recommendation_eligible is True
    assert summary["vehicles_with_reported_accident_history"] == 1
    assert summary["vehicles_with_minor_accident_history"] == 1
    assert summary["vehicles_with_non_recommendable_accident_history"] == 0


def test_import_records_complex_maintenance_history(project_copy: Path):
    data = _load_fixture("scan_complete.json")
    data["scan_id"] = "scan_complex_maintenance_history"
    data["vehicles"][0]["service_history"] = {
        "records_count": 9,
        "locations_count": 3,
        "replaced_parts": ["turbo", "water pump", "control module"],
        "notes": "Maintenance records are complex across multiple locations",
    }

    summary = import_scan(project_copy, data)

    inventory = load_current_inventory(project_copy, "ford-f150-ontario")
    assert inventory is not None
    vehicle = next(v for v in inventory.vehicles if v.vin == "1FTFW1E50NFA12345")
    assert vehicle.maintenance_history_status == "complex"
    assert vehicle.maintenance_risk_level == "high"
    assert vehicle.maintenance_records_count == 9
    assert vehicle.maintenance_locations_count == 3
    assert vehicle.maintenance_replaced_components_count == 3
    assert vehicle.recommendation_eligible is False
    assert summary["vehicles_with_reported_maintenance_history"] == 1
    assert summary["vehicles_with_high_maintenance_risk"] == 1

    events = read_csv_rows(events_path(project_copy, "ford-f150-ontario"))
    maintenance_events = [e for e in events if e["event_type"] == "maintenance_history_assessed"]
    assert len(maintenance_events) == 1
    details = json.loads(maintenance_events[0]["details_json"])
    assert details["risk_level"] == "high"
    assert details["recommendation_eligible"] is False


def test_partial_scan_preserves_previous_maintenance_history(project_copy: Path):
    complete = _load_fixture("scan_complete.json")
    complete["vehicles"][0]["service_history"] = {
        "records_count": 9,
        "locations_count": 3,
        "replaced_parts": ["turbo", "water pump", "control module"],
    }
    import_scan(project_copy, complete)

    partial = _load_fixture("scan_partial.json")
    summary = import_scan(project_copy, partial)

    inventory = load_current_inventory(project_copy, "ford-f150-ontario")
    assert inventory is not None
    by_vin = {v.vin: v for v in inventory.vehicles}
    assert by_vin["1FTFW1E50NFA12345"].maintenance_risk_level == "high"
    assert by_vin["1FTFW1E50NFA12345"].recommendation_eligible is False
    assert by_vin["2C3CCAAG5JH123456"].status == "active"
    assert summary["vehicles_with_reported_maintenance_history"] == 0


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
