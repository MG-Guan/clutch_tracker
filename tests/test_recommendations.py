"""Tests for multi-dimensional recommendations."""

from __future__ import annotations

import json
from pathlib import Path

from clutch_tracker.comparison import import_scan
from clutch_tracker.recommendations import build_recommendations, generate_recommendations_report
from clutch_tracker.storage import recommendations_report_dir


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_fixture(name: str) -> dict:
    with (FIXTURES / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_generate_recommendations_empty_target(project_copy: Path):
    report_path = generate_recommendations_report(project_copy, "ford-f150-ontario")
    content = report_path.read_text(encoding="utf-8")

    assert report_path.parent == recommendations_report_dir(project_copy, "ford-f150-ontario")
    assert "# Recommendations: ford-f150-ontario" in content
    assert "- Active listings analyzed: 0" in content
    assert "- Preference-matched listings: 0" in content
    assert "_No qualifying listings._" in content


def test_generate_recommendations_dimensions(project_copy: Path):
    import_scan(project_copy, _load_fixture("scan_complete.json"))
    import_scan(project_copy, _load_fixture("scan_partial.json"))

    report_path = generate_recommendations_report(project_copy, "ford-f150-ontario", top_n=2)
    content = report_path.read_text(encoding="utf-8")

    assert "- Active listings analyzed: 2" in content
    assert "- Preference-matched listings: 1" in content
    assert "trim contains 502A, Crew Cab, Short Bed" in content
    assert "Data quality warning" in content
    assert "## Best Relative Price (Preference Match)" in content
    assert "## Best Price by Year (Preference Match)" in content
    assert "## Lowest Mileage (Preference Match)" in content
    assert "## New Listings (Preference Match)" in content
    assert "## Recent Price Drops (Preference Match)" in content
    assert "## Outside Preference Profile" in content
    assert "## Accident Risk Watchlist" in content
    assert "## Vehicle History Scan Watchlist" in content
    assert "1FTFW1E50NFA12345" in content
    assert "-1000" in content or "-1000 CAD" in content


def test_generate_recommendations_filters_non_recommendable_accidents(project_copy: Path):
    data = _load_fixture("scan_complete.json")
    data["scan_id"] = "scan_accident_recommendations"
    data["vehicles"] = [
        {
            **data["vehicles"][0],
            "vin": "VINSEVERE1234567",
            "price_cad": 1000,
            "mileage_km": 1000,
            "trim": "LARIAT 502A Crew Cab Short Bed",
            "carfax": {"accident_reported": True},
        },
        {
            **data["vehicles"][0],
            "vin": "VINMINOR12345678",
            "price_cad": 2000,
            "mileage_km": 2000,
            "trim": "LARIAT 502A Crew Cab Short Bed",
            "carfax": {
                "accident_reported": True,
                "repair_description": "minor cosmetic bumper repair",
                "repair_cost_cad": 900,
            },
        },
    ]
    import_scan(project_copy, data)

    report_path = generate_recommendations_report(project_copy, "ford-f150-ontario", top_n=3)
    content = report_path.read_text(encoding="utf-8")

    assert "- Active listings analyzed: 1" in content
    assert "- Active listings tracked but excluded from recommendations: 1" in content
    relative_price = content.split("## Best Relative Price (Preference Match)", 1)[1].split(
        "## Best Price by Year (Preference Match)", 1
    )[0]
    assert "VINSEVERE1234567" not in relative_price
    assert "VINMINOR12345678" in content
    assert "minor: carfax.accident_reported=True" in content
    accident_watchlist = content.split("## Accident Risk Watchlist", 1)[1].split(
        "## Vehicle History Scan Watchlist", 1
    )[0]
    assert "VINSEVERE1234567" in accident_watchlist


def test_generate_recommendations_flags_complex_maintenance(project_copy: Path):
    data = _load_fixture("scan_complete.json")
    data["scan_id"] = "scan_maintenance_recommendations"
    data["vehicles"] = [
        {
            **data["vehicles"][0],
            "vin": "VINHIGHMAINT12345",
            "price_cad": 1000,
            "mileage_km": 1000,
            "trim": "LARIAT 502A Crew Cab Short Bed",
            "service_history": {
                "records_count": 9,
                "locations_count": 3,
                "replaced_parts": ["turbo", "water pump", "control module"],
                "notes": "complex maintenance across multiple locations",
            },
        },
        {
            **data["vehicles"][0],
            "vin": "VINLOWMAINT123456",
            "price_cad": 2000,
            "mileage_km": 2000,
            "trim": "LARIAT 502A Crew Cab Short Bed",
            "service_history": {
                "records_count": 3,
                "notes": "routine oil change and tire rotation",
            },
        },
    ]
    import_scan(project_copy, data)

    report_path = generate_recommendations_report(project_copy, "ford-f150-ontario", top_n=3)
    content = report_path.read_text(encoding="utf-8")

    assert "- Active listings analyzed: 1" in content
    assert "- Active listings tracked but excluded from recommendations: 1" in content
    assert "- Active listings with high maintenance risk: 1" in content
    assert "## Maintenance Risk Watchlist" in content
    assert "VINHIGHMAINT12345" in content
    assert "high; 9 records; 3 locations; 3 replaced" in content
    relative_price = content.split("## Best Relative Price (Preference Match)", 1)[1].split(
        "## Best Price by Year (Preference Match)", 1
    )[0]
    assert "VINHIGHMAINT12345" not in relative_price
    assert "VINLOWMAINT123456" in relative_price


def test_generate_recommendations_excludes_unknown_trim_from_preference_match(project_copy: Path):
    data = _load_fixture("scan_complete.json")
    data["scan_id"] = "scan_unknown_trim"
    data["vehicles"] = [
        {
            **data["vehicles"][0],
            "vin": "VINUNKNOWNTRIM1",
            "trim": None,
        },
        {
            **data["vehicles"][0],
            "vin": "VIN302A123456789",
            "trim": "XLT 302A Crew Cab Short Bed",
            "price_cad": 50000,
        },
    ]
    import_scan(project_copy, data)

    report_path = generate_recommendations_report(project_copy, "ford-f150-ontario", top_n=3)
    content = report_path.read_text(encoding="utf-8")

    assert "- Preference-matched listings: 0" in content
    outside = content.split("## Outside Preference Profile", 1)[1].split("## Accident Risk Watchlist", 1)[0]
    assert "VINUNKNOWNTRIM1" in outside
    assert "VIN302A123456789" in outside


def test_build_recommendations_skips_removed_listings(project_copy: Path):
    import_scan(project_copy, _load_fixture("scan_complete.json"))
    remaining = _load_fixture("scan_complete.json")
    remaining["scan_id"] = "scan_test_removed"
    remaining["scanned_at"] = "2026-06-17T10:00:00-04:00"
    remaining["vehicles"] = [remaining["vehicles"][0]]
    import_scan(project_copy, remaining)

    result = build_recommendations(project_copy, "ford-f150-ontario", top_n=3)
    vins = {pick.vin for section in result.sections for pick in section.picks}
    assert result.active_listings == 1
    assert "1FTFW1E50NFA12345" in vins
    assert "2C3CCAAG5JH123456" not in vins
