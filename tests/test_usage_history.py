"""Tests for commercial-use and interprovincial-history assessment."""

from __future__ import annotations

import json
from pathlib import Path

from clutch_tracker.comparison import import_scan
from clutch_tracker.recommendations import generate_recommendations_report
from clutch_tracker.storage import load_current_inventory, load_events
from clutch_tracker.usage_history import assess_usage_history


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_fixture(name: str) -> dict:
    with (FIXTURES / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_assess_usage_history_empty_is_unknown():
    assessment = assess_usage_history({})
    assert assessment.previous_use == "unknown"
    assert assessment.interprovincial == "unknown"
    assert assessment.provinces == ()


def test_assess_commercial_previous_use():
    assessment = assess_usage_history(
        {"vehicle_history_report": {"previous_use": "Commercial"}}
    )
    assert assessment.previous_use == "commercial"
    assert assessment.previous_use_details is not None


def test_assess_personal_previous_use():
    assessment = assess_usage_history({"carfax": {"previous_use": "Personal"}})
    assert assessment.previous_use == "personal"


def test_assess_interprovincial_from_registration_records():
    assessment = assess_usage_history(
        {
            "vehicle_history_report": {
                "registration_history": [
                    {"province": "British Columbia", "type": "Canadian Renewal"},
                    {"province": "Ontario", "type": "Canadian Renewal"},
                ]
            }
        }
    )
    assert assessment.interprovincial == "yes"
    assert assessment.provinces == ("British Columbia", "Ontario")


def test_assess_interprovincial_from_province_codes():
    assessment = assess_usage_history(
        {"registration": [{"province": "BC"}, {"province": "ON"}]}
    )
    assert assessment.interprovincial == "yes"
    assert set(assessment.provinces) == {"British Columbia", "Ontario"}


def test_assess_interprovincial_from_free_text():
    assessment = assess_usage_history(
        {"carfax": {"summary": "Vehicle registered in British Columbia then Ontario."}}
    )
    assert assessment.interprovincial == "yes"
    assert set(assessment.provinces) == {"British Columbia", "Ontario"}


def test_single_province_stays_unknown_interprovincial():
    assessment = assess_usage_history(
        {"carfax": {"summary": "Last registered in Ontario; not previously stolen."}}
    )
    assert assessment.interprovincial == "unknown"
    assert assessment.provinces == ("Ontario",)


def test_commercial_token_outside_usage_context_is_ignored():
    assessment = assess_usage_history({"description": "fleet pricing available"})
    assert assessment.previous_use == "unknown"


def test_import_scan_flags_commercial_and_interprovincial(project_copy: Path):
    data = _load_fixture("scan_complete.json")
    data["scan_id"] = "scan_usage_history"
    data["vehicles"][0]["vehicle_history_report"] = {
        "provider": "carfax",
        "status": "scanned",
        "previous_use": "Commercial",
        "registration_history": [
            {"date": "2022-06-24", "province": "British Columbia"},
            {"date": "2024-01-11", "province": "Ontario"},
        ],
    }

    summary = import_scan(project_copy, data)

    assert summary["vehicles_with_commercial_use"] == 1
    assert summary["vehicles_with_interprovincial_history"] == 1

    inventory = load_current_inventory(project_copy, "ford-f150-ontario")
    assert inventory is not None
    vehicle = next(v for v in inventory.vehicles if v.vin == "1FTFW1E50NFA12345")
    assert vehicle.previous_use == "commercial"
    assert vehicle.interprovincial_history == "yes"
    assert vehicle.province_history == "British Columbia, Ontario"
    # Disclosure must not change recommendation eligibility.
    assert vehicle.recommendation_eligible is True

    events = load_events(project_copy, "ford-f150-ontario")
    usage_events = [e for e in events if e["event_type"] == "usage_history_assessed"]
    assert len(usage_events) == 1
    details = json.loads(usage_events[0]["details_json"])
    assert details["previous_use"] == "commercial"
    assert details["interprovincial_history"] == "yes"


def test_partial_scan_preserves_previous_usage_history(project_copy: Path):
    complete = _load_fixture("scan_complete.json")
    complete["vehicles"][0]["vehicle_history_report"] = {
        "previous_use": "Commercial",
        "registration_history": [
            {"province": "British Columbia"},
            {"province": "Ontario"},
        ],
    }
    import_scan(project_copy, complete)

    partial = _load_fixture("scan_partial.json")
    summary = import_scan(project_copy, partial)

    inventory = load_current_inventory(project_copy, "ford-f150-ontario")
    assert inventory is not None
    by_vin = {v.vin: v for v in inventory.vehicles}
    assert by_vin["1FTFW1E50NFA12345"].previous_use == "commercial"
    assert by_vin["1FTFW1E50NFA12345"].interprovincial_history == "yes"
    assert by_vin["1FTFW1E50NFA12345"].province_history == "British Columbia, Ontario"
    assert summary["vehicles_with_commercial_use"] == 0


def test_recommendations_disclose_usage_history(project_copy: Path):
    data = _load_fixture("scan_complete.json")
    data["scan_id"] = "scan_usage_recommendations"
    data["vehicles"][0]["vehicle_history_report"] = {
        "previous_use": "Commercial",
        "registration_history": [
            {"province": "British Columbia"},
            {"province": "Ontario"},
        ],
    }
    import_scan(project_copy, data)

    report_path = generate_recommendations_report(project_copy, "ford-f150-ontario", top_n=3)
    content = report_path.read_text(encoding="utf-8")

    assert "- Active listings with commercial previous use: 1" in content
    assert "- Active listings with interprovincial registration history: 1" in content
    assert "## Commercial Use & Interprovincial Watchlist" in content
    assert "Usage/Registration" in content
    assert "commercial use" in content
    assert "interprovincial: British Columbia, Ontario" in content
