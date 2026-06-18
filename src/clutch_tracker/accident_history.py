"""Accident-history assessment helpers for scanned vehicle metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AccidentAssessment:
    """Normalized accident-history assessment for recommendation decisions."""

    status: str
    severity: str
    details: str | None
    recommendation_eligible: bool


UNKNOWN_ASSESSMENT = AccidentAssessment(
    status="unknown",
    severity="unknown",
    details=None,
    recommendation_eligible=True,
)

_ACCIDENT_POSITIVE_KEYS = {
    "accident",
    "accidenthistory",
    "accidentreported",
    "accidents",
    "accidentsreported",
    "accidentcount",
    "accidentscount",
    "carfaxaccident",
    "carfaxaccidenthistory",
    "carfaxaccidentreported",
    "carfaxaccidents",
    "carfaxhasaccident",
    "carfaxhasaccidents",
    "collision",
    "collisionreported",
    "damage",
    "damagehistory",
    "damagereported",
    "hasaccident",
    "hasaccidenthistory",
    "hasaccidents",
    "hascollision",
    "hasdamage",
    "hasdamagehistory",
    "numberofaccidents",
    "reportedaccidents",
}
_ACCIDENT_NEGATIVE_KEYS = {
    "accidentfree",
    "carfaxaccidentfree",
    "noaccident",
    "noaccidents",
    "noaccidentsreported",
}
_AMOUNT_KEYS = {
    "accidentamount",
    "claimamount",
    "damageamount",
    "estimatedrepaircost",
    "repairamount",
    "repaircost",
}
_CONTEXT_KEYS = {
    "accidenthistory",
    "carfax",
    "collision",
    "damagehistory",
    "history",
    "incidents",
    "repairs",
    "vehiclehistory",
}
_ACCIDENT_FALSE_TEXT = {
    "",
    "0",
    "0 accidents",
    "accident free",
    "clean",
    "clean carfax",
    "false",
    "none",
    "no",
    "no accident",
    "no accidents",
    "no accidents reported",
    "no collision",
    "no damage",
    "no reported accidents",
    "not reported",
    "unknown",
}
_ACCIDENT_TRUE_TEXT = {
    "1",
    "accident",
    "accident reported",
    "accidents reported",
    "collision",
    "collision reported",
    "damage",
    "damage reported",
    "has accident",
    "reported",
    "true",
    "yes",
}
_MINOR_TEXT = {
    "cosmetic",
    "dent",
    "glass",
    "minor",
    "paint",
    "scratch",
    "simple",
    "windshield",
}
_MAJOR_TEXT = {
    "airbag",
    "branded",
    "fire",
    "flood",
    "frame",
    "major",
    "rebuilt",
    "salvage",
    "severe",
    "structural",
    "theft",
    "total loss",
    "write off",
}
_MINOR_REPAIR_LIMIT_CAD = 3000
_MAJOR_REPAIR_LIMIT_CAD = 5000


def assess_accident_history(extra: dict[str, Any]) -> AccidentAssessment:
    """Assess accident history from arbitrary browser-agent scan fields.

    Only explicit accident/damage signals are treated as reported history. A
    reported accident remains recommendation-eligible only when the scan gives a
    clear minor/simple repair signal.
    """

    if not extra:
        return UNKNOWN_ASSESSMENT

    reported = False
    clean = False
    minor = False
    major = False
    details: list[str] = []

    for path, value in _flatten(extra):
        normalized_path = [_normalize_key(part) for part in path]
        key = normalized_path[-1] if normalized_path else ""
        context = _has_history_context(normalized_path)

        if key in _ACCIDENT_NEGATIVE_KEYS and _value_indicates_clean(value):
            clean = True
            _append_detail(details, path, value)
            continue

        if key in _ACCIDENT_POSITIVE_KEYS or (context and key in _ACCIDENT_POSITIVE_KEYS):
            if _value_indicates_accident(value):
                reported = True
                _append_detail(details, path, value)

        if context and isinstance(value, str):
            text = _normalize_text(value)
            if text in _ACCIDENT_FALSE_TEXT:
                clean = True
            elif _text_mentions_accident(text):
                reported = True
                _append_detail(details, path, value)

        if key in _AMOUNT_KEYS or (context and "amount" in key) or (context and "cost" in key):
            amount = _parse_money(value)
            if amount is not None and amount > 0:
                reported = True
                _append_detail(details, path, value)
                if amount <= _MINOR_REPAIR_LIMIT_CAD:
                    minor = True
                elif amount >= _MAJOR_REPAIR_LIMIT_CAD:
                    major = True

        text_blob = _normalize_text(value) if isinstance(value, str) else ""
        key_blob = " ".join(normalized_path)
        if _contains_any(key_blob, _MAJOR_TEXT) or _contains_any(text_blob, _MAJOR_TEXT):
            major = True
            if context:
                reported = True
                _append_detail(details, path, value)
        if _contains_any(key_blob, _MINOR_TEXT) or _contains_any(text_blob, _MINOR_TEXT):
            minor = True
            if context:
                reported = True
                _append_detail(details, path, value)

    if not reported:
        if clean:
            return AccidentAssessment(
                status="clean",
                severity="none",
                details=_format_details(details),
                recommendation_eligible=True,
            )
        return UNKNOWN_ASSESSMENT

    severity = "major" if major else "minor" if minor else "unknown"
    return AccidentAssessment(
        status="reported",
        severity=severity,
        details=_format_details(details),
        recommendation_eligible=severity == "minor",
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
    return " ".join(str(value).strip().lower().replace("_", " ").replace("-", " ").split())


def _has_history_context(path: list[str]) -> bool:
    return any(part in _CONTEXT_KEYS or part in _ACCIDENT_POSITIVE_KEYS for part in path)


def _value_indicates_accident(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        text = _normalize_text(value)
        if text in _ACCIDENT_FALSE_TEXT:
            return False
        if text in _ACCIDENT_TRUE_TEXT:
            return True
        if text.isdigit():
            return int(text) > 0
        return _text_mentions_accident(text)
    return False


def _value_indicates_clean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        text = _normalize_text(value)
        return text in _ACCIDENT_FALSE_TEXT
    return False


def _text_mentions_accident(text: str) -> bool:
    return any(
        phrase in text
        for phrase in (
            "accident reported",
            "collision reported",
            "damage reported",
            "reported accident",
            "reported collision",
            "reported damage",
        )
    )


def _contains_any(text: str, phrases: set[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def _parse_money(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    match = re.search(r"\d+(?:[,.]\d{3})*(?:\.\d+)?", value)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


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
