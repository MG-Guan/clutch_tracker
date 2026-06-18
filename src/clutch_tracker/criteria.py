"""Search criteria helpers for browser agents and model matching."""

from __future__ import annotations

import re
from typing import Any

from clutch_tracker.models import Target, TargetCriteria

_MODEL_TOKEN_RE = re.compile(r"[\s\-_/]+")


def normalize_model_token(value: str) -> str:
    """Normalize a model token for fuzzy comparison (F-150, F150, f 150 -> f150)."""
    return _MODEL_TOKEN_RE.sub("", value.strip().lower())


def model_search_terms(criteria: TargetCriteria) -> list[str]:
    """Return deduplicated model search terms including canonical model and aliases."""
    terms: list[str] = []
    seen: set[str] = set()

    for candidate in [criteria.model, *(criteria.model_aliases or ())]:
        if not candidate:
            continue
        key = candidate.strip().lower()
        if key not in seen:
            seen.add(key)
            terms.append(candidate)

    return terms


def vehicle_matches_criteria(
    criteria: TargetCriteria,
    *,
    make: str | None,
    model: str | None,
    year: int | str | None = None,
) -> bool:
    """
    Check whether a vehicle matches target criteria.

    Unknown make/model never satisfies a make/model filter.
    """
    if criteria.make is not None:
        if make is None:
            return False
        if make.strip().lower() != criteria.make.strip().lower():
            return False

    if criteria.model is not None:
        if model is None:
            return False
        vehicle_token = normalize_model_token(model)
        allowed = {normalize_model_token(t) for t in model_search_terms(criteria)}
        if vehicle_token not in allowed:
            return False

    if criteria.min_year is not None and year is not None:
        try:
            if int(year) < int(criteria.min_year):
                return False
        except (TypeError, ValueError):
            return False

    if criteria.max_year is not None and year is not None:
        try:
            if int(year) > int(criteria.max_year):
                return False
        except (TypeError, ValueError):
            return False

    return True


def build_search_criteria(target: Target) -> dict[str, Any]:
    """Build a JSON-serializable search criteria payload for browser agents."""
    c = target.criteria
    payload: dict[str, Any] = {
        "target_id": target.target_id,
        "label": target.label,
        "enabled": target.enabled,
        "criteria": {},
    }

    if c.body_style is not None:
        payload["criteria"]["body_style"] = c.body_style
    if c.province is not None:
        payload["criteria"]["province"] = c.province
    if c.min_year is not None:
        payload["criteria"]["min_year"] = c.min_year
    if c.max_year is not None:
        payload["criteria"]["max_year"] = c.max_year
    if c.make is not None:
        payload["criteria"]["make"] = c.make
    if c.model is not None:
        payload["criteria"]["model"] = c.model
    if c.model_aliases:
        payload["criteria"]["model_aliases"] = list(c.model_aliases)

    payload["criteria"]["model_search_terms"] = model_search_terms(c)
    payload["scan_requirements"] = {
        "vehicle_history_report": {
            "required": True,
            "provider": "carfax",
            "source": "Open the vehicle detail page History section and follow the full Carfax report link.",
            "output_field": "vehicle_history_report",
            "capture_fields": [
                "status",
                "provider",
                "source_url",
                "summary",
                "accident_damage_records_count",
                "total_accident_damage_amount_cad",
                "records",
            ],
            "failure_statuses": ["blocked", "error", "unavailable"],
        }
    }

    if c.search_query is not None:
        payload["criteria"]["search_query"] = c.search_query

    if c.extra:
        payload["criteria"]["extra"] = dict(c.extra)

    return payload


def format_criteria_summary(criteria: TargetCriteria) -> str:
    """Return a compact human-readable criteria summary."""
    parts: list[str] = []
    if criteria.make:
        parts.append(f"make={criteria.make}")
    if criteria.model:
        parts.append(f"model={criteria.model}")
    if criteria.model_aliases:
        parts.append(f"aliases={','.join(criteria.model_aliases)}")
    if criteria.body_style is not None:
        parts.append(f"body={criteria.body_style}")
    if criteria.province is not None:
        parts.append(f"province={criteria.province}")
    if criteria.min_year is not None or criteria.max_year is not None:
        parts.append(f"years={criteria.min_year or '?'}-{criteria.max_year or '?'}")
    return " ".join(parts) if parts else "(no filters)"
