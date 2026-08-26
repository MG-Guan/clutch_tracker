"""Listing availability assessment from browser scan metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AvailabilityAssessment:
    """Whether a scanned listing should count as purchasable inventory."""

    availability_status: str
    listing_status: str
    details: str | None


_ACTIVE_TOKENS = frozenset(
    {
        "available",
        "start_purchase",
        "available_sale_badge_visible",
    }
)

_UNAVAILABLE_SUBSTRINGS = (
    "coming_soon",
    "reserved",
    "on_hold",
    "other_region",
    "not_available",
    "unavailable",
    "sold",
    "notify",
)

_UNAVAILABLE_TEXT_MARKERS = (
    "not available",
    "no longer available",
    "currently unavailable",
)

_FALSE_AVAILABILITY_KEYS = (
    "listing_available",
    "purchasable",
    "purchase_available",
    "is_available",
)


def _extra_availability_text(extra: dict[str, Any]) -> str | None:
    for key in (
        "availability_status",
        "availability",
        "availability_label",
        "detail_availability_status",
        "listing_status",
        "purchase_cta",
    ):
        token = normalize_availability_token(extra.get(key))
        if token:
            return token
    for key in _FALSE_AVAILABILITY_KEYS:
        value = extra.get(key)
        if value is False:
            return "not_available"
    for value in extra.values():
        if isinstance(value, str):
            lowered = value.strip().lower()
            if any(marker in lowered for marker in _UNAVAILABLE_TEXT_MARKERS):
                return "not_available"
    return None


def normalize_availability_token(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        token = value.strip().lower().replace(" ", "_")
        return token or None
    if isinstance(value, bool):
        return None
    token = str(value).strip().lower().replace(" ", "_")
    return token or None


def assess_listing_availability(extra: dict[str, Any]) -> AvailabilityAssessment:
    """
    Derive inventory listing status from scan metadata.

    Only clearly purchasable listings become ``active``. Coming-soon, reserved,
    other-region, and notify-me states become ``unavailable``. Missing metadata
    stays ``active`` for backward compatibility with older scans.
    """
    raw = extra.get("availability_status")
    if raw is None:
        raw = extra.get("availability")
    token = normalize_availability_token(raw)
    if token is None:
        token = _extra_availability_text(extra)
    if token is None:
        return AvailabilityAssessment(
            availability_status="unknown",
            listing_status="active",
            details=None,
        )

    if token in _ACTIVE_TOKENS:
        return AvailabilityAssessment(
            availability_status=token,
            listing_status="active",
            details=None,
        )

    if any(part in token for part in _UNAVAILABLE_SUBSTRINGS):
        return AvailabilityAssessment(
            availability_status=token,
            listing_status="unavailable",
            details=f"availability_status={token}",
        )

    if token.startswith("available"):
        return AvailabilityAssessment(
            availability_status=token,
            listing_status="active",
            details=None,
        )

    return AvailabilityAssessment(
        availability_status=token,
        listing_status="unavailable",
        details=f"availability_status={token}",
    )
