"""Carfax report parsing, summarization, and change tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CARFAX_KNOWN_FIELDS = {
    "report_available",
    "report_url",
    "accident_reported",
    "accident_count",
    "damage_reported",
    "commercial_use",
    "rental_use",
    "taxi_use",
    "salvage_title",
    "rebuilt_title",
    "flood_damage",
    "lemon",
    "stolen",
    "total_loss",
    "service_record_count",
    "ownership_count",
    "last_service_date",
    "registration_provinces",
    "odometer_rollback",
    "import_vehicle",
    "notes",
    "raw_highlights",
}

CARFAX_TRACKED_FIELDS = (
    "accident_reported",
    "accident_count",
    "damage_reported",
    "commercial_use",
    "rental_use",
    "taxi_use",
    "salvage_title",
    "rebuilt_title",
    "flood_damage",
    "lemon",
    "stolen",
    "total_loss",
    "service_record_count",
    "ownership_count",
    "odometer_rollback",
    "import_vehicle",
)


@dataclass(frozen=True)
class CarfaxReport:
    """Structured Carfax findings from a browser agent scan."""

    report_available: bool | None = None
    report_url: str | None = None
    accident_reported: bool | None = None
    accident_count: int | None = None
    damage_reported: bool | None = None
    commercial_use: bool | None = None
    rental_use: bool | None = None
    taxi_use: bool | None = None
    salvage_title: bool | None = None
    rebuilt_title: bool | None = None
    flood_damage: bool | None = None
    lemon: bool | None = None
    stolen: bool | None = None
    total_loss: bool | None = None
    service_record_count: int | None = None
    ownership_count: int | None = None
    last_service_date: str | None = None
    registration_provinces: tuple[str, ...] = ()
    odometer_rollback: bool | None = None
    import_vehicle: bool | None = None
    notes: str | None = None
    raw_highlights: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)


def _normalize_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    return str(value)


def _parse_optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "unknown", "null", "none", "n/a"}:
            return None
        if normalized in {"true", "yes", "y", "1"}:
            return True
        if normalized in {"false", "no", "n", "0"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return None


def _parse_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(float(value.strip()))
        except ValueError:
            return None
    return None


def _parse_string_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        return tuple(items)
    return ()


def parse_carfax(data: Any) -> CarfaxReport | None:
    """Parse a Carfax object from scan JSON. Returns None when absent or empty."""
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError("carfax must be a mapping when provided")

    extra = {k: v for k, v in data.items() if k not in CARFAX_KNOWN_FIELDS}
    report = CarfaxReport(
        report_available=_parse_optional_bool(data.get("report_available")),
        report_url=_normalize_optional_str(data.get("report_url")),
        accident_reported=_parse_optional_bool(data.get("accident_reported")),
        accident_count=_parse_optional_int(data.get("accident_count")),
        damage_reported=_parse_optional_bool(data.get("damage_reported")),
        commercial_use=_parse_optional_bool(data.get("commercial_use")),
        rental_use=_parse_optional_bool(data.get("rental_use")),
        taxi_use=_parse_optional_bool(data.get("taxi_use")),
        salvage_title=_parse_optional_bool(data.get("salvage_title")),
        rebuilt_title=_parse_optional_bool(data.get("rebuilt_title")),
        flood_damage=_parse_optional_bool(data.get("flood_damage")),
        lemon=_parse_optional_bool(data.get("lemon")),
        stolen=_parse_optional_bool(data.get("stolen")),
        total_loss=_parse_optional_bool(data.get("total_loss")),
        service_record_count=_parse_optional_int(data.get("service_record_count")),
        ownership_count=_parse_optional_int(data.get("ownership_count")),
        last_service_date=_normalize_optional_str(data.get("last_service_date")),
        registration_provinces=_parse_string_list(data.get("registration_provinces")),
        odometer_rollback=_parse_optional_bool(data.get("odometer_rollback")),
        import_vehicle=_parse_optional_bool(data.get("import_vehicle")),
        notes=_normalize_optional_str(data.get("notes")),
        raw_highlights=_parse_string_list(data.get("raw_highlights")),
        extra=extra,
    )
    if not has_carfax_data(report):
        return None
    return report


def has_carfax_data(report: CarfaxReport | None) -> bool:
    """Return True when at least one Carfax field is known."""
    if report is None:
        return False
    if report.report_available is not None:
        return True
    if report.report_url:
        return True
    for field_name in CARFAX_TRACKED_FIELDS:
        if getattr(report, field_name) is not None:
            return True
    if report.last_service_date:
        return True
    if report.registration_provinces:
        return True
    if report.notes:
        return True
    if report.raw_highlights:
        return True
    if report.extra:
        return True
    return False


def carfax_to_dict(report: CarfaxReport | None) -> dict[str, Any]:
    """Serialize a Carfax report for JSON storage."""
    if report is None:
        return {}
    payload: dict[str, Any] = {}
    for field_name in (
        "report_available",
        "report_url",
        "accident_reported",
        "accident_count",
        "damage_reported",
        "commercial_use",
        "rental_use",
        "taxi_use",
        "salvage_title",
        "rebuilt_title",
        "flood_damage",
        "lemon",
        "stolen",
        "total_loss",
        "service_record_count",
        "ownership_count",
        "last_service_date",
        "odometer_rollback",
        "import_vehicle",
        "notes",
    ):
        value = getattr(report, field_name)
        if value is not None:
            payload[field_name] = value
    if report.registration_provinces:
        payload["registration_provinces"] = list(report.registration_provinces)
    if report.raw_highlights:
        payload["raw_highlights"] = list(report.raw_highlights)
    if report.extra:
        payload.update(report.extra)
    return payload


def carfax_from_dict(data: dict[str, Any] | None) -> CarfaxReport | None:
    """Deserialize a Carfax report from stored JSON."""
    if not data:
        return None
    return parse_carfax(data)


def carfax_risk_issues(report: CarfaxReport | None) -> list[str]:
    """Return human-readable risk flags for a Carfax report."""
    if report is None:
        return []

    issues: list[str] = []
    if report.accident_reported is True:
        if report.accident_count and report.accident_count > 0:
            issues.append(f"{report.accident_count} accident(s) reported")
        else:
            issues.append("accident reported")
    if report.damage_reported is True:
        issues.append("damage reported")
    if report.commercial_use is True:
        issues.append("commercial use")
    if report.rental_use is True:
        issues.append("rental use")
    if report.taxi_use is True:
        issues.append("taxi use")
    if report.salvage_title is True:
        issues.append("salvage title")
    if report.rebuilt_title is True:
        issues.append("rebuilt title")
    if report.flood_damage is True:
        issues.append("flood damage")
    if report.lemon is True:
        issues.append("lemon")
    if report.stolen is True:
        issues.append("stolen record")
    if report.total_loss is True:
        issues.append("total loss")
    if report.odometer_rollback is True:
        issues.append("odometer rollback")
    if report.import_vehicle is True:
        issues.append("import vehicle")
    return issues


def is_clean_history(report: CarfaxReport | None) -> bool | None:
    """
    Return True when Carfax shows no risk flags, False when risks exist,
    or None when history is unknown.
    """
    if report is None:
        return None
    if not has_carfax_data(report):
        return None

    issues = carfax_risk_issues(report)
    if issues:
        return False

    positive_signals = (
        report.accident_reported is False
        or report.commercial_use is False
        or report.damage_reported is False
        or report.salvage_title is False
        or report.flood_damage is False
        or report.service_record_count is not None
        or report.ownership_count is not None
        or report.raw_highlights
    )
    if positive_signals:
        return True
    if report.report_available is False:
        return None
    return None


def summarize_carfax(report: CarfaxReport | None) -> str | None:
    """Build a concise Carfax summary for tracking and reports."""
    if report is None or not has_carfax_data(report):
        return None

    if report.report_available is False:
        return "Carfax report unavailable"

    issues = carfax_risk_issues(report)
    positives: list[str] = []

    if report.accident_reported is False:
        positives.append("no accidents")
    if report.commercial_use is False and report.rental_use is False and report.taxi_use is False:
        positives.append("no commercial/rental/taxi use")
    elif report.commercial_use is False:
        positives.append("no commercial use")
    if report.damage_reported is False:
        positives.append("no damage")
    if report.salvage_title is False and report.rebuilt_title is False:
        positives.append("no salvage/rebuilt title")
    if report.flood_damage is False:
        positives.append("no flood damage")
    if report.service_record_count is not None:
        positives.append(f"{report.service_record_count} service record(s)")
    if report.ownership_count is not None:
        positives.append(f"{report.ownership_count} owner(s)")
    if report.last_service_date:
        positives.append(f"last service {report.last_service_date}")
    if report.registration_provinces:
        positives.append(f"registered in {', '.join(report.registration_provinces)}")

    if issues:
        prefix = "CAUTION: " + "; ".join(issues)
        if positives:
            return f"{prefix} | {', '.join(positives)}"
        return prefix

    if positives:
        return "Clean history: " + "; ".join(positives)

    if report.raw_highlights:
        return "; ".join(report.raw_highlights[:4])

    if report.notes:
        return report.notes

    if report.report_available is True:
        return "Carfax report available (details not parsed)"
    return None


def carfax_field_changes(
    previous: CarfaxReport | None,
    current: CarfaxReport | None,
) -> list[dict[str, Any]]:
    """Return tracked Carfax field changes between two reports."""
    changes: list[dict[str, Any]] = []
    for field_name in CARFAX_TRACKED_FIELDS:
        old_value = getattr(previous, field_name) if previous else None
        new_value = getattr(current, field_name) if current else None
        if old_value == new_value:
            continue
        if old_value is None and new_value is None:
            continue
        changes.append(
            {
                "field": field_name,
                "old_value": old_value,
                "new_value": new_value,
            }
        )
    return changes
