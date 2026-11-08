"""Search criteria helpers for browser agents and model matching."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from clutch_tracker.models import Target, TargetCriteria

_MODEL_TOKEN_RE = re.compile(r"[\s\-_/]+")


def normalize_model_token(value: str) -> str:
    """Normalize a model token for fuzzy comparison (F-150, F150, f 150 -> f150)."""
    return _MODEL_TOKEN_RE.sub("", value.strip().lower())


@dataclass(frozen=True)
class RecommendationProfile:
    """One alternate trim/feature cohort. Tokens inside a profile are combined with AND."""

    name: str
    trim_must_contain: tuple[str, ...] = ()
    trim_must_not_contain: tuple[str, ...] = ()
    text_any_of: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecommendationPreferences:
    """Target-specific filters for recommendation cohorts (from targets.yaml extra)."""

    trim_must_contain: tuple[str, ...] = ()
    trim_must_not_contain: tuple[str, ...] = ()
    profiles: tuple[RecommendationProfile, ...] = ()


def _parse_string_list(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"recommendation_preferences.{field_name} must be a list")
    return tuple(str(item).strip() for item in value if item is not None and str(item).strip())


def _parse_profiles(value: Any) -> tuple[RecommendationProfile, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("recommendation_preferences.profiles must be a list")
    profiles: list[RecommendationProfile] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"recommendation_preferences.profiles[{index}] must be a mapping")
        name = str(item.get("name") or "").strip() or f"profile {index + 1}"
        profiles.append(
            RecommendationProfile(
                name=name,
                trim_must_contain=_parse_string_list(
                    item.get("trim_must_contain"),
                    f"profiles[{index}].trim_must_contain",
                ),
                trim_must_not_contain=_parse_string_list(
                    item.get("trim_must_not_contain"),
                    f"profiles[{index}].trim_must_not_contain",
                ),
                text_any_of=_parse_string_list(
                    item.get("text_any_of"),
                    f"profiles[{index}].text_any_of",
                ),
            )
        )
    return tuple(profiles)


def parse_recommendation_preferences(criteria: TargetCriteria) -> RecommendationPreferences:
    """Load recommendation cohort filters from criteria.extra."""
    raw = criteria.extra.get("recommendation_preferences")
    if raw is None:
        return RecommendationPreferences()
    if not isinstance(raw, dict):
        raise ValueError("criteria.extra.recommendation_preferences must be a mapping")
    return RecommendationPreferences(
        trim_must_contain=_parse_string_list(raw.get("trim_must_contain"), "trim_must_contain"),
        trim_must_not_contain=_parse_string_list(raw.get("trim_must_not_contain"), "trim_must_not_contain"),
        profiles=_parse_profiles(raw.get("profiles")),
    )


def _contains_all(haystack: str, tokens: tuple[str, ...]) -> bool:
    return all(token.lower() in haystack for token in tokens)


def _legacy_trim_match(trim: str | None, preferences: RecommendationPreferences) -> bool:
    if not preferences.trim_must_contain and not preferences.trim_must_not_contain:
        return True
    if trim is None or not str(trim).strip():
        return False
    normalized = str(trim).lower()
    if not _contains_all(normalized, preferences.trim_must_contain):
        return False
    return all(token.lower() not in normalized for token in preferences.trim_must_not_contain)


def _profile_match(
    trim: str | None,
    feature_text: str | None,
    profile: RecommendationProfile,
) -> bool:
    if not profile.trim_must_contain and not profile.trim_must_not_contain and not profile.text_any_of:
        return False
    normalized_trim = str(trim or "").lower()
    if profile.trim_must_contain and (trim is None or not str(trim).strip()):
        return False
    if not _contains_all(normalized_trim, profile.trim_must_contain):
        return False
    if any(token.lower() in normalized_trim for token in profile.trim_must_not_contain):
        return False
    if profile.text_any_of:
        haystack = f"{normalized_trim} {str(feature_text or '').lower()}"
        if not any(token.lower() in haystack for token in profile.text_any_of):
            return False
    return True


def vehicle_matches_recommendation_preferences(
    trim: str | None,
    preferences: RecommendationPreferences,
    feature_text: str | None = None,
) -> bool:
    """
    Return whether a listing satisfies configured recommendation filters.

    Top-level trim tokens are one cohort. Each profile is another cohort.
    A listing matches when it satisfies the top-level filters or any profile.
    Unknown trim never satisfies a non-empty trim_must_contain list.
    """
    has_legacy = bool(preferences.trim_must_contain or preferences.trim_must_not_contain)
    if preferences.profiles:
        if any(_profile_match(trim, feature_text, profile) for profile in preferences.profiles):
            return True
        if has_legacy:
            return _legacy_trim_match(trim, preferences)
        return False
    return _legacy_trim_match(trim, preferences)


def format_recommendation_preferences(preferences: RecommendationPreferences) -> str:
    """Return a compact human-readable recommendation preference summary."""
    parts: list[str] = []
    if preferences.trim_must_contain:
        parts.append("trim contains " + ", ".join(preferences.trim_must_contain))
    if preferences.trim_must_not_contain:
        parts.append("trim excludes " + ", ".join(preferences.trim_must_not_contain))
    for profile in preferences.profiles:
        detail: list[str] = []
        if profile.trim_must_contain:
            detail.append("trim " + "+".join(profile.trim_must_contain))
        if profile.text_any_of:
            detail.append("text " + "/".join(profile.text_any_of))
        if profile.trim_must_not_contain:
            detail.append("excludes " + "+".join(profile.trim_must_not_contain))
        suffix = f" ({', '.join(detail)})" if detail else ""
        parts.append(profile.name + suffix)
    return "; ".join(parts) if parts else "(no preference filters)"


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


def clutch_search_url(criteria: TargetCriteria) -> str | None:
    """Build a Clutch.ca search URL from make/model. Returns None if either is missing."""
    if not criteria.make or not criteria.model:
        return None
    make_slug = criteria.make.strip().lower().replace(" ", "-")
    model_slug = criteria.model.strip().lower().replace(" ", "-")
    if not make_slug or not model_slug:
        return None
    return f"https://www.clutch.ca/cars/{make_slug}-{model_slug}"


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
    search_url = clutch_search_url(c)
    if search_url:
        payload["search_url"] = search_url
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
