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


@dataclass(frozen=True)
class VehicleHistoryReportAssessment:
    """Normalized browser-agent coverage metadata for vehicle-history reports."""

    status: str
    provider: str | None
    details: str | None


UNKNOWN_ASSESSMENT = AccidentAssessment(
    status="unknown",
    severity="unknown",
    details=None,
    recommendation_eligible=True,
)
UNKNOWN_REPORT_ASSESSMENT = VehicleHistoryReportAssessment(
    status="not_scanned",
    provider=None,
    details=None,
)

_ACCIDENT_POSITIVE_KEYS = {
    "accident",
    "accidentdamage",
    "accidentdamagerecords",
    "accidentclaim",
    "accidentclaims",
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
    "damageclaim",
    "damageclaims",
    "damagerecords",
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
    "accidentclaimamount",
    "accidentdamageamount",
    "claimamount",
    "damageamount",
    "estimatedrepaircost",
    "repairamount",
    "repaircost",
    "totalaccidentdamageamount",
    "totaldamageamount",
}
_CONTEXT_KEYS = {
    "accidenthistory",
    "carfax",
    "carfaxreport",
    "collision",
    "damagehistory",
    "history",
    "incidents",
    "repairs",
    "vehiclehistory",
    "vehiclehistoryreport",
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
    "no accident damage records found",
    "no accidents",
    "no accidents reported",
    "no collision",
    "no damage",
    "no damage records found",
    "no claims found",
    "no reported accidents",
    "not reported",
    "unknown",
}
_ACCIDENT_TRUE_TEXT = {
    "1",
    "accident",
    "accident claim",
    "accident damage records found",
    "accident reported",
    "accidents reported",
    "claim found",
    "claims found",
    "collision",
    "collision reported",
    "damage",
    "damage claim",
    "damage records found",
    "damage reported",
    "has accident",
    "records found",
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

_REPORT_CONTEXT_KEYS = {
    "carfax",
    "carfaxreport",
    "historyreport",
    "vehiclehistory",
    "vehiclehistoryreport",
}
_REPORT_STATUS_KEYS = {
    "carfaxreportstatus",
    "historystatus",
    "reportstatus",
    "scanstatus",
    "status",
    "vehiclehistoryreportstatus",
}
_REPORT_PROVIDER_KEYS = {
    "provider",
    "reportprovider",
    "vehiclehistoryprovider",
}
_REPORT_SCANNED_TEXT = {
    "available",
    "complete",
    "completed",
    "fetched",
    "scanned",
    "success",
    "successful",
}
_REPORT_UNAVAILABLE_TEXT = {
    "no report",
    "not available",
    "not found",
    "unavailable",
}
_REPORT_BLOCKED_TEXT = {
    "blocked",
    "captcha",
    "forbidden",
    "waf",
}
_REPORT_ERROR_TEXT = {
    "error",
    "failed",
    "timeout",
}


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

        if context and _key_mentions_accident_or_damage_record(key):
            count = _parse_count(value)
            if count is not None and count > 0:
                reported = True
                _append_detail(details, path, value)

        if context and isinstance(value, list) and _key_mentions_accident_or_damage_record(key):
            if len(value) > 0:
                reported = True
                _append_detail(details, path, f"{len(value)} entries")

        if context and isinstance(value, str):
            text = _normalize_text(value)
            if _text_indicates_clean(text):
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


def assess_vehicle_history_report(extra: dict[str, Any]) -> VehicleHistoryReportAssessment:
    """Assess whether a browser agent opened and extracted a history report."""

    if not extra:
        return UNKNOWN_REPORT_ASSESSMENT

    status: str | None = None
    provider: str | None = None
    details: list[str] = []

    for path, value in _flatten(extra):
        normalized_path = [_normalize_key(part) for part in path]
        key = normalized_path[-1] if normalized_path else ""
        context = _has_report_context(normalized_path)

        if key in _REPORT_PROVIDER_KEYS and isinstance(value, str):
            provider = _normalize_provider(value)
            _append_detail(details, path, value)

        if "carfax" in normalized_path:
            provider = provider or "carfax"

        if context and key in _REPORT_STATUS_KEYS:
            parsed = _parse_report_status(value)
            if parsed is not None:
                status = _merge_report_status(status, parsed)
                _append_detail(details, path, value)

        if context and _value_shows_report_content(key, value):
            status = _merge_report_status(status, "scanned")
            _append_detail(details, path, value)

    if status is None:
        return UNKNOWN_REPORT_ASSESSMENT

    return VehicleHistoryReportAssessment(
        status=status,
        provider=provider,
        details=_format_details(details),
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
    for char in ("_", "-", "/", "\\"):
        text = text.replace(char, " ")
    return " ".join(text.split())


def _has_history_context(path: list[str]) -> bool:
    return any(part in _CONTEXT_KEYS or part in _ACCIDENT_POSITIVE_KEYS for part in path)


def _has_report_context(path: list[str]) -> bool:
    return any(part in _REPORT_CONTEXT_KEYS for part in path)


def _value_indicates_accident(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        text = _normalize_text(value)
        if _text_indicates_clean(text):
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
        return _text_indicates_clean(text)
    return False


def _text_indicates_clean(text: str) -> bool:
    if text in _ACCIDENT_FALSE_TEXT:
        return True
    return any(
        phrase in text
        for phrase in _ACCIDENT_FALSE_TEXT
        if len(phrase) > 3 and phrase not in {"none", "clean", "unknown"}
    )


def _text_mentions_accident(text: str) -> bool:
    if _text_indicates_clean(text):
        return False
    return any(
        phrase in text
        for phrase in (
            "accident claim",
            "accident damage records found",
            "accident reported",
            "claim found",
            "claims found",
            "collision reported",
            "damage claim",
            "damage records found",
            "damage reported",
            "reported accident",
            "reported collision",
            "reported damage",
        )
    )


def _key_mentions_accident_or_damage_record(key: str) -> bool:
    has_topic = "accident" in key or "collision" in key or "damage" in key or "claim" in key
    has_quantity = "count" in key or "record" in key or "claim" in key
    return has_topic and has_quantity


def _contains_any(text: str, phrases: set[str]) -> bool:
    for phrase in phrases:
        if " " in phrase:
            if phrase in text:
                return True
            continue
        if re.search(rf"\b{re.escape(phrase)}\b", text):
            return True
    return False


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


def _parse_count(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if not isinstance(value, str):
        return None
    match = re.search(r"\d+", value)
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def _parse_report_status(value: Any) -> str | None:
    if isinstance(value, bool):
        return "scanned" if value else "not_scanned"
    if not isinstance(value, str):
        return None
    text = _normalize_text(value)
    if not text:
        return None
    if text in _REPORT_SCANNED_TEXT or any(phrase in text for phrase in _REPORT_SCANNED_TEXT):
        return "scanned"
    if text in _REPORT_BLOCKED_TEXT or any(phrase in text for phrase in _REPORT_BLOCKED_TEXT):
        return "blocked"
    if text in _REPORT_ERROR_TEXT or any(phrase in text for phrase in _REPORT_ERROR_TEXT):
        return "error"
    if text in _REPORT_UNAVAILABLE_TEXT or any(phrase in text for phrase in _REPORT_UNAVAILABLE_TEXT):
        return "unavailable"
    if text == "not scanned":
        return "not_scanned"
    return None


def _merge_report_status(current: str | None, candidate: str) -> str:
    priority = {
        "blocked": 4,
        "error": 3,
        "scanned": 2,
        "unavailable": 1,
        "not_scanned": 0,
    }
    if current is None or priority[candidate] > priority[current]:
        return candidate
    return current


def _normalize_provider(value: str) -> str:
    normalized = _normalize_text(value)
    if "carfax" in normalized:
        return "carfax"
    return normalized


def _value_shows_report_content(key: str, value: Any) -> bool:
    if key in {"sourceurl", "url", "reporturl"}:
        return False
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, dict):
        return len(value) > 0
    if value is None:
        return False
    if key in _REPORT_PROVIDER_KEYS or key in _REPORT_STATUS_KEYS:
        return False
    text = _normalize_text(value)
    if not text:
        return False
    return (
        "accident" in key
        or "damage" in key
        or "claim" in key
        or "record" in key
        or "summary" in key
        or "section" in key
        or "report" in key
    )


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
