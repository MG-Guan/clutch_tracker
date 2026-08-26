"""Tests for listing availability assessment."""

from __future__ import annotations

from clutch_tracker.availability import assess_listing_availability


def test_available_is_active():
    result = assess_listing_availability({"availability_status": "available"})
    assert result.listing_status == "active"
    assert result.availability_status == "available"


def test_start_purchase_is_active():
    result = assess_listing_availability({"availability_status": "start_purchase"})
    assert result.listing_status == "active"


def test_coming_soon_is_unavailable():
    result = assess_listing_availability({"availability_status": "coming_soon"})
    assert result.listing_status == "unavailable"


def test_reserved_notify_is_unavailable():
    result = assess_listing_availability({"availability_status": "reserved_notify_me"})
    assert result.listing_status == "unavailable"


def test_other_regions_is_unavailable():
    result = assess_listing_availability({"availability_status": "available_other_regions_notify_me"})
    assert result.listing_status == "unavailable"


def test_reserved_is_unavailable():
    result = assess_listing_availability({"availability_status": "reserved"})
    assert result.listing_status == "unavailable"


def test_listing_available_false_is_unavailable():
    result = assess_listing_availability({"listing_available": False})
    assert result.listing_status == "unavailable"


def test_availability_label_not_available_is_unavailable():
    result = assess_listing_availability({"availability_label": "Not available"})
    assert result.listing_status == "unavailable"


def test_missing_availability_defaults_active_for_legacy_scans():
    result = assess_listing_availability({})
    assert result.listing_status == "active"
    assert result.availability_status == "unknown"
