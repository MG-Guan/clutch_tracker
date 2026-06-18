"""Tests for Carfax parsing and summarization."""

from __future__ import annotations

from clutch_tracker.carfax import (
    carfax_field_changes,
    is_clean_history,
    parse_carfax,
    summarize_carfax,
)


def test_parse_carfax_clean_history():
    report = parse_carfax(
        {
            "accident_reported": False,
            "commercial_use": False,
            "service_record_count": 8,
            "ownership_count": 1,
        }
    )
    assert report is not None
    assert report.accident_reported is False
    assert report.service_record_count == 8
    assert is_clean_history(report) is True
    summary = summarize_carfax(report)
    assert summary is not None
    assert "Clean history" in summary
    assert "8 service record" in summary


def test_parse_carfax_risk_flags():
    report = parse_carfax(
        {
            "accident_reported": True,
            "accident_count": 2,
            "commercial_use": True,
            "flood_damage": False,
        }
    )
    assert report is not None
    assert is_clean_history(report) is False
    summary = summarize_carfax(report)
    assert summary is not None
    assert summary.startswith("CAUTION:")
    assert "accident" in summary


def test_carfax_field_changes_detects_updates():
    before = parse_carfax({"accident_reported": False, "service_record_count": 4})
    after = parse_carfax({"accident_reported": True, "service_record_count": 5})
    changes = carfax_field_changes(before, after)
    fields = {change["field"] for change in changes}
    assert "accident_reported" in fields
    assert "service_record_count" in fields


def test_parse_carfax_empty_returns_none():
    assert parse_carfax(None) is None
    assert parse_carfax({}) is None
