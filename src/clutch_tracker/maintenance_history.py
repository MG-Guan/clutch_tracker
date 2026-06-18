"""Maintenance-history assessment helpers for scanned vehicle metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MaintenanceAssessment:
    """Normalized maintenance-history assessment for recommendation decisions."""

    status: str
    risk_level: str
    details: str | None
    records_count: int | None
    locations_count: int | None
    replaced_components_count: int | None
    recommendation_eligible: bool


UNKNOWN_ASSESSMENT = MaintenanceAssessment(
    status="unknown",
    risk_level="unknown",
    details=None,
    records_count=None,
    locations_count=None,
    replaced_components_count=None,
    recommendation_eligible=True,
)

_MAINTENANCE_CONTEXT_KEYS = {
    "carfax",
    "history",
    "maintenance",
    "maintenancehistory",
    "maintenancerecords",
    "repair",
    "repairhistory",
    "repairs",
    "service",
    "servicehistory",
    "servicerecords",
    "vehiclehistory",
}
_RECORD_COUNT_KEYS = {
    "maintenancecount",
    "maintenancerecordcount",
    "maintenancerecordscount",
    "maintenancerecords",
    "recordcount",
    "recordscount",
    "repaircount",
    "servicerecordcount",
    "servicerecordscount",
    "servicerecords",
}
_LOCATION_COUNT_KEYS = {
    "locationcount",
    "locationscount",
    "maintenance locationcount",
    "maintenancelocationcount",
    "servicelocationcount",
    "servicelocationscount",
}
_LOCATION_KEYS = {
    "city",
    "dealer",
    "facility",
    "location",
    "maintenance location",
    "province",
    "servicecenter",
    "servicelocation",
    "shop",
    "state",
}
_REPLACED_COMPONENT_COUNT_KEYS = {
    "componentsreplacedcount",
    "partsreplacedcount",
    "replacedcomponentscount",
    "replacedpartscount",
    "replacementcount",
    "replacementpartscount",
}
_REPLACED_COMPONENT_KEYS = {
    "componentreplaced",
    "componentsreplaced",
    "partreplaced",
    "partsreplaced",
    "replacedcomponent",
    "replacedcomponents",
    "replacedpart",
    "replacedparts",
    "replacement",
    "replacementpart",
    "replacementparts",
}
_ROUTINE_TEXT = {
    "air filter",
    "brake service",
    "inspection",
    "maintenance inspection",
    "oil change",
    "regular maintenance",
    "routine",
    "scheduled maintenance",
    "service completed",
    "tire rotation",
}
_COMPLEX_TEXT = {
    "complex",
    "diagnostic",
    "electrical",
    "engine repair",
    "extensive",
    "major repair",
    "many repairs",
    "multiple locations",
    "multiple repairs",
    "multiple replaced",
    "recurring",
    "repeated",
    "several locations",
    "transmission",
}
_REPLACEMENT_TEXT = {
    "replaced",
    "replacement",
    "replacements",
}
_MODERATE_RECORD_COUNT = 8
_HIGH_RECORD_COUNT = 12
_MODERATE_LOCATION_COUNT = 2
_HIGH_LOCATION_COUNT = 4
_MODERATE_REPLACED_COMPONENT_COUNT = 1
_HIGH_REPLACED_COMPONENT_COUNT = 3


def assess_maintenance_history(extra: dict[str, Any]) -> MaintenanceAssessment:
    """Assess maintenance risk from arbitrary browser-agent scan fields.

    Only explicit service/maintenance/repair fields affect the assessment.
    Missing history stays unknown; routine-only service history remains eligible.
    """

    if not extra:
        return UNKNOWN_ASSESSMENT

    reported = False
    routine = False
    complex_signal = False
    details: list[str] = []
    locations: set[str] = set()
    records_count: int | None = None
    locations_count: int | None = None
    replaced_components_count: int | None = None

    for path, value in _flatten(extra):
        normalized_path = [_normalize_key(part) for part in path]
        key = normalized_path[-1] if normalized_path else ""
        context = _has_maintenance_context(normalized_path)
        text_blob = _normalize_text(value) if isinstance(value, str) else ""
        path_blob = " ".join(normalized_path)

        if key in _RECORD_COUNT_KEYS or (context and "recordcount" in key):
            count = _parse_count(value)
            if count is not None:
                reported = count > 0 or reported
                records_count = max(records_count or 0, count)
                if count > 0:
                    _append_detail(details, path, value)

        if key in _LOCATION_COUNT_KEYS or (context and "locationcount" in key):
            count = _parse_count(value)
            if count is not None:
                reported = count > 0 or reported
                locations_count = max(locations_count or 0, count)
                if count > 0:
                    _append_detail(details, path, value)

        if key in _REPLACED_COMPONENT_COUNT_KEYS:
            count = _parse_count(value)
            if count is not None:
                reported = count > 0 or reported
                replaced_components_count = max(replaced_components_count or 0, count)
                if count > 0:
                    _append_detail(details, path, value)

        if key in _REPLACED_COMPONENT_KEYS:
            count = _count_items(value)
            if count is not None:
                reported = count > 0 or reported
                replaced_components_count = max(replaced_components_count or 0, count)
                if count > 0:
                    _append_detail(details, path, value)

        if context and isinstance(value, list):
            count = len(value)
            if count > 0 and ("record" in key or "history" in key or "service" in key or "repair" in key):
                reported = True
                records_count = max(records_count or 0, count)
                _append_detail(details, path, f"{count} entries")

        if context and isinstance(value, str):
            if text_blob:
                reported = True
            if _contains_any(text_blob, _ROUTINE_TEXT):
                routine = True
            if _contains_any(text_blob, _COMPLEX_TEXT):
                complex_signal = True
                _append_detail(details, path, value)
            if _contains_any(text_blob, _REPLACEMENT_TEXT):
                complex_signal = True
                replaced_components_count = max(replaced_components_count or 0, 1)
                _append_detail(details, path, value)

        if context and (_contains_any(path_blob, _COMPLEX_TEXT) or _contains_any(path_blob, _REPLACEMENT_TEXT)):
            complex_signal = True
            reported = True
            _append_detail(details, path, value)

        if context and key in _LOCATION_KEYS:
            location = _location_value(value)
            if location:
                reported = True
                locations.add(location)

    inferred_locations_count = len(locations) if locations else None
    if inferred_locations_count is not None:
        locations_count = max(locations_count or 0, inferred_locations_count)
        if inferred_locations_count > 0:
            _append_detail(details, ("service_locations",), f"{inferred_locations_count} unique")

    if not reported:
        return UNKNOWN_ASSESSMENT

    risk_level = _risk_level(
        records_count=records_count,
        locations_count=locations_count,
        replaced_components_count=replaced_components_count,
        complex_signal=complex_signal,
        routine=routine,
    )
    status = "complex" if risk_level in {"moderate", "high"} else "routine"
    return MaintenanceAssessment(
        status=status,
        risk_level=risk_level,
        details=_format_details(details),
        records_count=records_count,
        locations_count=locations_count,
        replaced_components_count=replaced_components_count,
        recommendation_eligible=risk_level != "high",
    )


def _risk_level(
    *,
    records_count: int | None,
    locations_count: int | None,
    replaced_components_count: int | None,
    complex_signal: bool,
    routine: bool,
) -> str:
    high_signals = 0
    moderate_signals = 0

    if records_count is not None:
        if records_count >= _HIGH_RECORD_COUNT:
            high_signals += 1
        elif records_count >= _MODERATE_RECORD_COUNT:
            moderate_signals += 1

    if locations_count is not None:
        if locations_count >= _HIGH_LOCATION_COUNT:
            high_signals += 1
        elif locations_count >= _MODERATE_LOCATION_COUNT:
            moderate_signals += 1

    if replaced_components_count is not None:
        if replaced_components_count >= _HIGH_REPLACED_COMPONENT_COUNT:
            high_signals += 1
        elif replaced_components_count >= _MODERATE_REPLACED_COMPONENT_COUNT:
            moderate_signals += 1

    if complex_signal:
        moderate_signals += 1

    if high_signals or moderate_signals >= 2:
        return "high"
    if moderate_signals:
        return "moderate"
    if routine or any(value is not None and value > 0 for value in (records_count, locations_count, replaced_components_count)):
        return "low"
    return "unknown"


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


def _has_maintenance_context(path: list[str]) -> bool:
    return any(part in _MAINTENANCE_CONTEXT_KEYS for part in path)


def _parse_count(value: Any) -> int | None:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 and value.is_integer() else None
    if isinstance(value, str):
        match = re.search(r"\d+", value)
        if match:
            return int(match.group(0))
    if isinstance(value, list):
        return len(value)
    return None


def _count_items(value: Any) -> int | None:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    if isinstance(value, str):
        parts = [part.strip() for part in re.split(r"[,;/]", value) if part.strip()]
        return len(parts) if parts else 1
    if isinstance(value, bool):
        return 1 if value else 0
    return _parse_count(value)


def _location_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float)):
        text = str(value).strip().lower()
        return text or None
    return None


def _contains_any(text: str, phrases: set[str]) -> bool:
    for phrase in phrases:
        if " " in phrase:
            if phrase in text:
                return True
            continue
        if re.search(rf"\b{re.escape(phrase)}\b", text):
            return True
    return False


def _append_detail(details: list[str], path: tuple[str, ...], value: Any) -> None:
    if len(details) >= 8:
        return
    rendered = f"{'.'.join(path)}={value}"
    if rendered not in details:
        details.append(rendered)


def _format_details(details: list[str]) -> str | None:
    if not details:
        return None
    rendered = "; ".join(details)
    return rendered[:700]
