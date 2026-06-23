"""Usage- and ownership-history assessment helpers for scanned vehicle metadata.

Two disclosure-worthy signals are extracted from arbitrary browser-agent scan
fields:

* **Previous use** — whether the vehicle was used commercially (fleet, rental,
  taxi, delivery, police, government, etc.) rather than as a personal vehicle.
* **Interprovincial history** — whether the vehicle was registered in more than
  one Canadian province/territory over its life (e.g. British Columbia and then
  Ontario).

Both signals are *disclosure only*: they are surfaced in recommendations but
never change recommendation eligibility. Missing signals stay ``unknown`` and
are never guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class UsageHistoryAssessment:
    """Normalized usage/ownership-history assessment for disclosure."""

    previous_use: str  # "commercial" | "personal" | "unknown"
    previous_use_details: str | None
    interprovincial: str  # "yes" | "no" | "unknown"
    provinces: tuple[str, ...]
    interprovincial_details: str | None


UNKNOWN_ASSESSMENT = UsageHistoryAssessment(
    previous_use="unknown",
    previous_use_details=None,
    interprovincial="unknown",
    provinces=(),
    interprovincial_details=None,
)

_USAGE_CONTEXT_KEYS = {
    "carfax",
    "carfaxreport",
    "detailedhistory",
    "history",
    "ownership",
    "ownershiphistory",
    "previoususe",
    "records",
    "registration",
    "registrationhistory",
    "registrationrecords",
    "summary",
    "title",
    "titlehistory",
    "usage",
    "use",
    "vehiclehistory",
    "vehiclehistoryreport",
}

_PREVIOUS_USE_KEYS = {
    "formeruse",
    "previoususe",
    "prioruse",
    "use",
    "usecategory",
    "usageclass",
    "usagedescription",
    "usagetype",
    "usage",
    "usetype",
    "vehicleuse",
}

_COMMERCIAL_TEXT = {
    "business use",
    "commercial",
    "courier",
    "daily rental",
    "delivery",
    "driver education",
    "driving school",
    "fleet",
    "government",
    "limo",
    "limousine",
    "livery",
    "municipal",
    "police",
    "rental",
    "ride hail",
    "ride share",
    "ridehail",
    "rideshare",
    "taxi",
}

_PERSONAL_TEXT = {
    "family",
    "individual",
    "personal",
    "personal use",
    "personal vehicle",
    "private",
    "private use",
}

_PROVINCE_KEYS = {
    "jurisdiction",
    "lastregisteredprovince",
    "previousprovince",
    "province",
    "provinceofregistration",
    "region",
    "registeredprovince",
    "registrationprovince",
    "state",
}

_PROVINCE_NAMES = {
    "alberta": "Alberta",
    "british columbia": "British Columbia",
    "manitoba": "Manitoba",
    "new brunswick": "New Brunswick",
    "newfoundland": "Newfoundland and Labrador",
    "newfoundland and labrador": "Newfoundland and Labrador",
    "northwest territories": "Northwest Territories",
    "nova scotia": "Nova Scotia",
    "nunavut": "Nunavut",
    "ontario": "Ontario",
    "prince edward island": "Prince Edward Island",
    "quebec": "Quebec",
    "québec": "Quebec",
    "saskatchewan": "Saskatchewan",
    "yukon": "Yukon",
}

_PROVINCE_CODES = {
    "ab": "Alberta",
    "bc": "British Columbia",
    "mb": "Manitoba",
    "nb": "New Brunswick",
    "nl": "Newfoundland and Labrador",
    "ns": "Nova Scotia",
    "nt": "Northwest Territories",
    "nu": "Nunavut",
    "on": "Ontario",
    "pe": "Prince Edward Island",
    "pei": "Prince Edward Island",
    "qc": "Quebec",
    "sk": "Saskatchewan",
    "yt": "Yukon",
}

_INTERPROVINCIAL_TEXT = {
    "imported from",
    "interprovincial",
    "out of province",
    "out-of-province",
    "previously registered in",
    "registered in multiple provinces",
    "transferred from",
}

_SINGLE_PROVINCE_TEXT = {
    "always registered in",
    "no out of province",
    "no out-of-province",
    "only registered in",
    "registered only in",
    "single province",
}


def assess_usage_history(extra: dict[str, Any]) -> UsageHistoryAssessment:
    """Assess commercial use and interprovincial history from scan fields.

    Only explicit usage/registration signals are considered. Missing data stays
    ``unknown`` and never affects recommendation eligibility.
    """

    if not extra:
        return UNKNOWN_ASSESSMENT

    commercial = False
    personal = False
    use_details: list[str] = []

    provinces: dict[str, None] = {}
    explicit_interprovincial = False
    explicit_single_province = False
    province_details: list[str] = []

    for path, value in _flatten(extra):
        normalized_path = [_normalize_key(part) for part in path]
        key = normalized_path[-1] if normalized_path else ""
        context = _has_usage_context(normalized_path)
        text_blob = _normalize_text(value) if isinstance(value, str) else ""

        if key in _PREVIOUS_USE_KEYS and text_blob:
            if _contains_any(text_blob, _COMMERCIAL_TEXT):
                commercial = True
                _append_detail(use_details, path, value)
            elif _contains_any(text_blob, _PERSONAL_TEXT):
                personal = True

        if context and text_blob:
            if "previous use" in text_blob or "prior use" in text_blob:
                if _contains_any(text_blob, _COMMERCIAL_TEXT):
                    commercial = True
                    _append_detail(use_details, path, value)
                elif _contains_any(text_blob, _PERSONAL_TEXT):
                    personal = True
            elif _contains_any(text_blob, _COMMERCIAL_TEXT) and "use" in key:
                commercial = True
                _append_detail(use_details, path, value)

        if key in _PROVINCE_KEYS:
            province = _province_from_value(value)
            if province:
                provinces.setdefault(province, None)
                _append_detail(province_details, path, value)

        if context and text_blob:
            for province in _provinces_in_text(text_blob):
                provinces.setdefault(province, None)
            if _contains_any(text_blob, _INTERPROVINCIAL_TEXT):
                explicit_interprovincial = True
                _append_detail(province_details, path, value)
            if _contains_any(text_blob, _SINGLE_PROVINCE_TEXT):
                explicit_single_province = True

    previous_use = "commercial" if commercial else "personal" if personal else "unknown"

    province_tuple = tuple(sorted(provinces))
    if len(province_tuple) >= 2 or explicit_interprovincial:
        interprovincial = "yes"
    elif explicit_single_province:
        interprovincial = "no"
    else:
        interprovincial = "unknown"

    if province_tuple:
        _append_detail(province_details, ("provinces",), ", ".join(province_tuple))

    return UsageHistoryAssessment(
        previous_use=previous_use,
        previous_use_details=_format_details(use_details),
        interprovincial=interprovincial,
        provinces=province_tuple,
        interprovincial_details=_format_details(province_details),
    )


def _flatten(value: Any, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Any]]:
    if isinstance(value, dict):
        rows: list[tuple[tuple[str, ...], Any]] = []
        for key, child in value.items():
            rows.extend(_flatten(child, path + (str(key),)))
        return rows
    if isinstance(value, list):
        rows = []
        for index, child in enumerate(value):
            rows.extend(_flatten(child, path + (str(index),)))
        return rows
    return [(path, value)]


def _normalize_key(key: Any) -> str:
    return "".join(ch for ch in str(key).lower() if ch.isalnum())


def _normalize_text(value: Any) -> str:
    text = str(value).strip().lower()
    for char in ("_", "-", "/", "\\", ",", "."):
        text = text.replace(char, " ")
    return " ".join(text.split())


def _has_usage_context(path: list[str]) -> bool:
    return any(part in _USAGE_CONTEXT_KEYS for part in path)


def _contains_any(text: str, phrases: set[str]) -> bool:
    for phrase in phrases:
        if " " in phrase:
            if phrase in text:
                return True
            continue
        if re.search(rf"\b{re.escape(phrase)}\b", text):
            return True
    return False


def _province_from_value(value: Any) -> str | None:
    if not isinstance(value, (str, int, float)):
        return None
    text = _normalize_text(value)
    if not text:
        return None
    if text in _PROVINCE_NAMES:
        return _PROVINCE_NAMES[text]
    compact = text.replace(" ", "")
    if compact in _PROVINCE_CODES:
        return _PROVINCE_CODES[compact]
    found = _provinces_in_text(text)
    return found[0] if found else None


def _provinces_in_text(text: str) -> list[str]:
    found: dict[str, None] = {}
    for name, canonical in _PROVINCE_NAMES.items():
        if " " in name:
            if name in text:
                found.setdefault(canonical, None)
        elif re.search(rf"\b{re.escape(name)}\b", text):
            found.setdefault(canonical, None)
    return list(found)


def _append_detail(details: list[str], path: tuple[str, ...], value: Any) -> None:
    if len(details) >= 6:
        return
    rendered = f"{'.'.join(path)}={value}"
    if rendered not in details:
        details.append(rendered)


def _format_details(details: list[str]) -> str | None:
    if not details:
        return None
    rendered = "; ".join(details)
    return rendered[:500]
