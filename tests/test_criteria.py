"""Tests for search criteria and model matching."""

from __future__ import annotations

from clutch_tracker.config import load_targets, parse_target, project_root
from clutch_tracker.criteria import (
    build_search_criteria,
    format_criteria_summary,
    format_recommendation_preferences,
    model_search_terms,
    normalize_model_token,
    parse_recommendation_preferences,
    vehicle_matches_criteria,
    vehicle_matches_recommendation_preferences,
)


def test_normalize_model_token_variants():
    assert normalize_model_token("F-150") == normalize_model_token("F150")
    assert normalize_model_token("F 150") == normalize_model_token("f150")


def test_parse_target_with_model_aliases():
    target = parse_target(
        {
            "target_id": "ford-f150",
            "label": "Ford F-150",
            "enabled": True,
            "criteria": {
                "make": "Ford",
                "model": "F-150",
                "model_aliases": ["F150", "F 150"],
                "province": "ON",
            },
        }
    )
    assert target.criteria.make == "Ford"
    assert target.criteria.model == "F-150"
    assert target.criteria.model_aliases == ("F150", "F 150")


def test_model_search_terms_deduplicates():
    target = parse_target(
        {
            "target_id": "ford-f150",
            "criteria": {"make": "Ford", "model": "F-150", "model_aliases": ["F150", "F-150"]},
        }
    )
    terms = model_search_terms(target.criteria)
    assert "F-150" in terms
    assert "F150" in terms
    assert len(terms) == 2


def test_vehicle_matches_f150_aliases():
    target = parse_target(
        {
            "target_id": "ford-f150",
            "criteria": {"make": "Ford", "model": "F-150", "model_aliases": ["F150"]},
        }
    )
    c = target.criteria
    assert vehicle_matches_criteria(c, make="Ford", model="F-150", year=2022)
    assert vehicle_matches_criteria(c, make="Ford", model="F150", year=2022)
    assert not vehicle_matches_criteria(c, make="Ford", model="F-250", year=2022)
    assert not vehicle_matches_criteria(c, make="Chevrolet", model="F-150", year=2022)


def test_build_search_criteria_for_f150_target():
    targets = {t.target_id: t for t in load_targets(project_root())}
    assert "ford-f150-ontario" in targets
    payload = build_search_criteria(targets["ford-f150-ontario"])
    assert payload["criteria"]["make"] == "Ford"
    assert payload["criteria"]["model"] == "F-150"
    assert "F150" in payload["criteria"]["model_aliases"]
    assert "F-150" in payload["criteria"]["model_search_terms"]
    history_requirement = payload["scan_requirements"]["vehicle_history_report"]
    assert history_requirement["required"] is True
    assert history_requirement["provider"] == "carfax"
    assert history_requirement["output_field"] == "vehicle_history_report"
    assert "accident_damage_records_count" in history_requirement["capture_fields"]
    prefs = payload["criteria"]["extra"]["recommendation_preferences"]
    assert "502A" in prefs["trim_must_contain"]


def test_parse_recommendation_preferences_from_target():
    targets = {t.target_id: t for t in load_targets(project_root())}
    preferences = parse_recommendation_preferences(targets["ford-f150-ontario"].criteria)
    assert "502A" in preferences.trim_must_contain
    assert vehicle_matches_recommendation_preferences(
        "LARIAT 502A Crew Cab Short Bed",
        preferences,
    )
    assert not vehicle_matches_recommendation_preferences(
        "XLT 302A Crew Cab Short Bed",
        preferences,
    )
    assert not vehicle_matches_recommendation_preferences(None, preferences)
    assert format_recommendation_preferences(preferences).startswith("trim contains")


def test_format_criteria_summary_includes_make_model():
    target = parse_target(
        {
            "target_id": "ford-f150",
            "criteria": {"make": "Ford", "model": "F-150", "model_aliases": ["F150"]},
        }
    )
    summary = format_criteria_summary(target.criteria)
    assert "make=Ford" in summary
    assert "model=F-150" in summary
    assert "aliases=F150" in summary
