"""Multi-dimensional vehicle recommendations from tracked inventory data."""

from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from clutch_tracker.config import load_targets, project_root
from clutch_tracker.criteria import (
    format_recommendation_preferences,
    parse_recommendation_preferences,
    vehicle_matches_recommendation_preferences,
)
from clutch_tracker.models import CurrentInventory, TargetCriteria, VehicleRecord
from clutch_tracker.storage import (
    atomic_write,
    events_path,
    load_current_inventory,
    load_vehicles,
    now_iso,
    read_csv_rows,
    recommendations_report_dir,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VehicleSnapshot:
    """Active listing enriched with registry metadata."""

    vin: str
    year: str | None
    make: str | None
    model: str | None
    trim: str | None
    price_cad: str | None
    mileage_km: str | None
    listing_url: str | None
    first_seen_at: str | None
    last_seen_at: str | None
    accident_history_status: str
    accident_severity: str
    accident_details: str | None
    vehicle_history_report_status: str
    vehicle_history_report_provider: str | None
    vehicle_history_report_details: str | None
    maintenance_history_status: str
    maintenance_risk_level: str
    maintenance_details: str | None
    maintenance_records_count: int | None
    maintenance_locations_count: int | None
    maintenance_replaced_components_count: int | None
    recommendation_eligible: bool


@dataclass(frozen=True)
class RankedPick:
    """A ranked recommendation entry for markdown rendering."""

    vin: str
    year: str
    trim: str
    price_cad: str
    mileage_km: str
    metric: str
    listing_url: str | None
    accident_history: str
    vehicle_history_report: str
    maintenance_history: str
    first_seen_at: str | None = None
    days_listed: str | None = None
    old_price_cad: str | None = None
    new_price_cad: str | None = None
    drop_cad: str | None = None
    drop_pct: str | None = None


def generate_recommendations_report(
    root: Path | None,
    target_id: str,
    *,
    top_n: int = 3,
) -> Path:
    """Generate a multi-dimensional markdown recommendations report."""
    root_path = project_root(root)
    timestamp = now_iso(root_path)

    inventory = load_current_inventory(root_path, target_id)
    vehicles = load_vehicles(root_path, target_id)
    events = read_csv_rows(events_path(root_path, target_id))
    targets_by_id = {target.target_id: target for target in load_targets(root_path)}
    target = targets_by_id.get(target_id)
    preferences = (
        parse_recommendation_preferences(target.criteria)
        if target is not None
        else parse_recommendation_preferences(TargetCriteria())
    )
    preference_summary = format_recommendation_preferences(preferences)

    report_dir = recommendations_report_dir(root_path, target_id)
    report_dir.mkdir(parents=True, exist_ok=True)

    date_part = timestamp[:10]
    report_path = report_dir / f"{date_part}.md"

    active = _active_snapshots(inventory, vehicles)
    recommendable = [vehicle for vehicle in active if vehicle.recommendation_eligible]
    matched = [
        vehicle
        for vehicle in recommendable
        if _matches_preferences(vehicle, preferences)
    ]
    matched_vins = {vehicle.vin for vehicle in matched}
    outside_preferences = [
        vehicle for vehicle in recommendable if vehicle.vin not in matched_vins
    ]
    excluded = len(active) - len(recommendable)
    scanned_history_reports = sum(1 for vehicle in active if vehicle.vehicle_history_report_status == "scanned")
    failed_history_reports = sum(
        1 for vehicle in active if vehicle.vehicle_history_report_status in {"blocked", "error", "unavailable"}
    )
    high_maintenance_risk = sum(1 for vehicle in active if vehicle.maintenance_risk_level == "high")
    reference_at = inventory.updated_at if inventory else timestamp

    lines = [
        f"# Recommendations: {target_id}",
        "",
        f"Generated at: {timestamp}",
        "",
        "## Summary",
        "",
        f"- Active listings analyzed: {len(recommendable)}",
        f"- Preference-matched listings: {len(matched)}",
        f"- Preference profile: {preference_summary}",
        f"- Active listings outside preference profile: {len(outside_preferences)}",
        f"- Active listings tracked but excluded from recommendations: {excluded}",
        f"- Active listings with scanned history reports: {scanned_history_reports}",
        f"- Active listings with failed/unavailable history report scans: {failed_history_reports}",
        f"- Active listings with high maintenance risk: {high_maintenance_risk}",
        f"- Recommendations per dimension: top {top_n}",
        "",
    ]

    if inventory:
        lines.extend(
            [
                f"- Last scan ID: {inventory.scan_id or 'unknown'}",
                f"- Last scan complete: {inventory.scan_complete}",
                f"- Inventory updated at: {inventory.updated_at}",
                "",
            ]
        )
        if not inventory.scan_complete:
            lines.extend(
                [
                    "> **Data quality warning:** The latest scan was partial "
                    "(`scan_complete: false`). Inventory may be incomplete and "
                    "recommendations may omit listings not captured in that scan.",
                    "",
                ]
            )

    sections = [
        (
            "Best Relative Price (Preference Match)",
            "Listings matching the configured trim profile, ranked by price below the cohort median.",
            _rank_relative_price(matched, top_n),
            ["Rank", "VIN", "Year", "Trim", "Price (CAD)", "Mileage (km)", "vs Median", "Accident", "Maintenance", "Link"],
        ),
        (
            "Best Price by Year (Preference Match)",
            "Cheapest preference-matched listing within each model year.",
            _rank_best_price_by_year(matched, top_n),
            ["Year", "VIN", "Trim", "Price (CAD)", "Mileage (km)", "Accident", "Maintenance", "Link"],
        ),
        (
            "Lowest Mileage (Preference Match)",
            "Lowest odometer among preference-matched listings.",
            _rank_low_mileage(matched, top_n),
            ["Rank", "VIN", "Year", "Trim", "Price (CAD)", "Mileage (km)", "Accident", "Maintenance", "Link"],
        ),
        (
            "New Listings (Preference Match)",
            "Most recently appeared preference-matched listings.",
            _rank_new_listings(matched, reference_at, top_n),
            ["Rank", "VIN", "Year", "Trim", "Price (CAD)", "First Seen", "Days Listed", "Accident", "Maintenance", "Link"],
        ),
        (
            "Recent Price Drops (Preference Match)",
            "Largest recent price reductions among active preference-matched listings.",
            _rank_price_drops(events, matched, top_n),
            ["Rank", "VIN", "Year", "Trim", "Old Price", "New Price", "Drop", "Drop %", "Accident", "Maintenance", "Link"],
        ),
        (
            "Outside Preference Profile",
            "Recommendable listings that do not match the configured trim profile.",
            _rank_outside_preferences(outside_preferences, top_n),
            ["Rank", "VIN", "Year", "Trim", "Price (CAD)", "Mileage (km)", "Accident", "Maintenance", "Link"],
        ),
        (
            "Accident Risk Watchlist",
            "Reported accident/damage history among active listings, including vehicles excluded from recommendations.",
            _rank_accident_risk(active, top_n),
            ["Rank", "VIN", "Year", "Trim", "Price (CAD)", "Mileage (km)", "Accident", "History Report", "Link"],
        ),
        (
            "Vehicle History Scan Watchlist",
            "Active listings whose full vehicle-history report was not successfully scanned.",
            _rank_history_report_scan_gaps(active, top_n),
            ["Rank", "VIN", "Year", "Trim", "Price (CAD)", "Mileage (km)", "History Report", "Accident", "Link"],
        ),
        (
            "Maintenance Risk Watchlist",
            "Known moderate/high maintenance complexity among active listings, including vehicles excluded from recommendations.",
            _rank_maintenance_risk(active, top_n),
            ["Rank", "VIN", "Year", "Trim", "Price (CAD)", "Mileage (km)", "Maintenance", "Accident", "Link"],
        ),
    ]

    for title, description, picks, headers in sections:
        lines.extend([f"## {title}", "", description, ""])
        lines.extend(_render_section(picks, headers))
        lines.append("")

    content = "\n".join(lines).rstrip() + "\n"
    atomic_write(report_path, content)
    logger.info("Wrote recommendations report to %s", report_path)
    return report_path


def _active_snapshots(
    inventory: CurrentInventory | None,
    registry: dict[str, VehicleRecord],
) -> list[VehicleSnapshot]:
    if not inventory:
        return []

    snapshots: list[VehicleSnapshot] = []
    for vehicle in inventory.vehicles:
        if vehicle.status != "active":
            continue
        reg = registry.get(vehicle.vin)
        snapshots.append(
            VehicleSnapshot(
                vin=vehicle.vin,
                year=vehicle.year or (reg.year if reg else None),
                make=vehicle.make or (reg.make if reg else None),
                model=vehicle.model or (reg.model if reg else None),
                trim=vehicle.trim or (reg.trim if reg else None),
                price_cad=vehicle.price_cad,
                mileage_km=vehicle.mileage_km,
                listing_url=vehicle.listing_url or (reg.listing_url if reg else None),
                first_seen_at=reg.first_seen_at if reg else None,
                last_seen_at=vehicle.last_seen_at,
                accident_history_status=vehicle.accident_history_status,
                accident_severity=vehicle.accident_severity,
                accident_details=vehicle.accident_details,
                vehicle_history_report_status=vehicle.vehicle_history_report_status,
                vehicle_history_report_provider=vehicle.vehicle_history_report_provider,
                vehicle_history_report_details=vehicle.vehicle_history_report_details,
                maintenance_history_status=vehicle.maintenance_history_status,
                maintenance_risk_level=vehicle.maintenance_risk_level,
                maintenance_details=vehicle.maintenance_details,
                maintenance_records_count=vehicle.maintenance_records_count,
                maintenance_locations_count=vehicle.maintenance_locations_count,
                maintenance_replaced_components_count=vehicle.maintenance_replaced_components_count,
                recommendation_eligible=vehicle.recommendation_eligible,
            )
        )
    return snapshots


def _matches_preferences(vehicle: VehicleSnapshot, preferences) -> bool:
    return vehicle_matches_recommendation_preferences(vehicle.trim, preferences)


def _parse_positive_number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _display(value: str | None, fallback: str = "unknown") -> str:
    if value is None or str(value).strip() == "":
        return fallback
    return str(value)


def _format_link(url: str | None) -> str:
    if not url:
        return "—"
    return f"[view]({url})"


def _format_accident_history(vehicle: VehicleSnapshot) -> str:
    if vehicle.accident_history_status == "reported":
        if vehicle.accident_details:
            return f"{vehicle.accident_severity}: {vehicle.accident_details}"
        return vehicle.accident_severity
    return vehicle.accident_history_status


def _format_vehicle_history_report(vehicle: VehicleSnapshot) -> str:
    parts = [vehicle.vehicle_history_report_status]
    if vehicle.vehicle_history_report_provider:
        parts.append(vehicle.vehicle_history_report_provider)
    if vehicle.vehicle_history_report_details:
        parts.append(vehicle.vehicle_history_report_details)
    return "; ".join(parts)


def _format_maintenance_history(vehicle: VehicleSnapshot) -> str:
    if vehicle.maintenance_history_status == "unknown":
        return "unknown"
    parts = [vehicle.maintenance_risk_level]
    if vehicle.maintenance_records_count is not None:
        parts.append(f"{vehicle.maintenance_records_count} records")
    if vehicle.maintenance_locations_count is not None:
        parts.append(f"{vehicle.maintenance_locations_count} locations")
    if vehicle.maintenance_replaced_components_count is not None:
        parts.append(f"{vehicle.maintenance_replaced_components_count} replaced")
    if vehicle.maintenance_details:
        parts.append(vehicle.maintenance_details)
    return ": " + "; ".join(parts) if not parts[0] else "; ".join(parts)


def _days_between(start_iso: str | None, end_iso: str) -> int | None:
    if not start_iso:
        return None
    try:
        start = datetime.fromisoformat(start_iso)
        end = datetime.fromisoformat(end_iso)
    except (TypeError, ValueError):
        return None
    return max((end.date() - start.date()).days, 0)


def _rank_relative_price(active: list[VehicleSnapshot], top_n: int) -> list[RankedPick]:
    priced: list[tuple[float, VehicleSnapshot]] = []
    for vehicle in active:
        price = _parse_positive_number(vehicle.price_cad)
        if price is None:
            continue
        priced.append((price, vehicle))

    if not priced:
        return []

    prices = [price for price, _ in priced]
    median_price = statistics.median(prices)
    scored = [(price - median_price, price, vehicle) for price, vehicle in priced]
    scored.sort(key=lambda item: (item[0], item[1]))

    picks: list[RankedPick] = []
    for delta, price, vehicle in scored[:top_n]:
        picks.append(
            RankedPick(
                vin=vehicle.vin,
                year=_display(vehicle.year),
                trim=_display(vehicle.trim),
                price_cad=_display(vehicle.price_cad),
                mileage_km=_display(vehicle.mileage_km),
                metric=f"{delta:+,.0f} CAD",
                listing_url=vehicle.listing_url,
                accident_history=_format_accident_history(vehicle),
                vehicle_history_report=_format_vehicle_history_report(vehicle),
                maintenance_history=_format_maintenance_history(vehicle),
            )
        )
    return picks


def _rank_outside_preferences(active: list[VehicleSnapshot], top_n: int) -> list[RankedPick]:
    scored: list[tuple[float, VehicleSnapshot]] = []
    for vehicle in active:
        price = _parse_positive_number(vehicle.price_cad)
        if price is None:
            continue
        scored.append((price, vehicle))

    scored.sort(key=lambda item: item[0])
    return [
        RankedPick(
            vin=vehicle.vin,
            year=_display(vehicle.year),
            trim=_display(vehicle.trim),
            price_cad=_display(vehicle.price_cad),
            mileage_km=_display(vehicle.mileage_km),
            metric=_display(vehicle.price_cad),
            listing_url=vehicle.listing_url,
            accident_history=_format_accident_history(vehicle),
            vehicle_history_report=_format_vehicle_history_report(vehicle),
            maintenance_history=_format_maintenance_history(vehicle),
        )
        for _, vehicle in scored[:top_n]
    ]


def _rank_low_mileage(active: list[VehicleSnapshot], top_n: int) -> list[RankedPick]:
    scored: list[tuple[float, VehicleSnapshot]] = []
    for vehicle in active:
        mileage = _parse_positive_number(vehicle.mileage_km)
        if mileage is None:
            continue
        scored.append((mileage, vehicle))

    scored.sort(key=lambda item: item[0])
    return [
        RankedPick(
            vin=vehicle.vin,
            year=_display(vehicle.year),
            trim=_display(vehicle.trim),
            price_cad=_display(vehicle.price_cad),
            mileage_km=_display(vehicle.mileage_km),
            metric=_display(vehicle.mileage_km),
            listing_url=vehicle.listing_url,
            accident_history=_format_accident_history(vehicle),
            vehicle_history_report=_format_vehicle_history_report(vehicle),
            maintenance_history=_format_maintenance_history(vehicle),
        )
        for _, vehicle in scored[:top_n]
    ]


def _rank_best_price_by_year(active: list[VehicleSnapshot], top_n: int) -> list[RankedPick]:
    by_year: dict[str, tuple[float, VehicleSnapshot]] = {}
    for vehicle in active:
        if vehicle.year is None:
            continue
        price = _parse_positive_number(vehicle.price_cad)
        if price is None:
            continue
        year_key = str(vehicle.year)
        current = by_year.get(year_key)
        if current is None or price < current[0]:
            by_year[year_key] = (price, vehicle)

    ranked = sorted(by_year.items(), key=lambda item: int(item[0]), reverse=True)
    picks: list[RankedPick] = []
    for year, (_, vehicle) in ranked[:top_n]:
        picks.append(
            RankedPick(
                vin=vehicle.vin,
                year=year,
                trim=_display(vehicle.trim),
                price_cad=_display(vehicle.price_cad),
                mileage_km=_display(vehicle.mileage_km),
                metric=year,
                listing_url=vehicle.listing_url,
                accident_history=_format_accident_history(vehicle),
                vehicle_history_report=_format_vehicle_history_report(vehicle),
                maintenance_history=_format_maintenance_history(vehicle),
            )
        )
    return picks


def _rank_new_listings(
    active: list[VehicleSnapshot],
    reference_at: str,
    top_n: int,
) -> list[RankedPick]:
    dated: list[tuple[str, VehicleSnapshot]] = []
    for vehicle in active:
        if not vehicle.first_seen_at:
            continue
        dated.append((vehicle.first_seen_at, vehicle))

    dated.sort(key=lambda item: item[0], reverse=True)
    picks: list[RankedPick] = []
    for first_seen_at, vehicle in dated[:top_n]:
        days = _days_between(first_seen_at, reference_at)
        picks.append(
            RankedPick(
                vin=vehicle.vin,
                year=_display(vehicle.year),
                trim=_display(vehicle.trim),
                price_cad=_display(vehicle.price_cad),
                mileage_km=_display(vehicle.mileage_km),
                metric=first_seen_at,
                listing_url=vehicle.listing_url,
                accident_history=_format_accident_history(vehicle),
                vehicle_history_report=_format_vehicle_history_report(vehicle),
                maintenance_history=_format_maintenance_history(vehicle),
                first_seen_at=first_seen_at,
                days_listed=str(days) if days is not None else "unknown",
            )
        )
    return picks


def _rank_price_drops(
    events: list[dict[str, str]],
    active: list[VehicleSnapshot],
    top_n: int,
) -> list[RankedPick]:
    active_vins = {vehicle.vin for vehicle in active}
    active_by_vin = {vehicle.vin: vehicle for vehicle in active}

    latest_drop: dict[str, tuple[str, float, float, float]] = {}
    for event in events:
        if event.get("event_type") != "price_changed":
            continue
        vin = event.get("vin", "").strip()
        if not vin or vin not in active_vins:
            continue
        try:
            details = json.loads(event.get("details_json") or "{}")
        except json.JSONDecodeError:
            continue
        old_price = _parse_positive_number(details.get("old_price_cad"))
        new_price = _parse_positive_number(details.get("new_price_cad"))
        if old_price is None or new_price is None or new_price >= old_price:
            continue
        drop = old_price - new_price
        event_at = event.get("event_at", "")
        current = latest_drop.get(vin)
        if current is None or event_at >= current[0]:
            latest_drop[vin] = (event_at, old_price, new_price, drop)

    ranked = sorted(
        ((vin, data) for vin, data in latest_drop.items()),
        key=lambda item: item[1][3],
        reverse=True,
    )
    picks: list[RankedPick] = []
    for vin, (_, old_price, new_price, drop) in ranked[:top_n]:
        vehicle = active_by_vin[vin]
        drop_pct = (drop / old_price) * 100
        picks.append(
            RankedPick(
                vin=vin,
                year=_display(vehicle.year),
                trim=_display(vehicle.trim),
                price_cad=f"{int(new_price)}",
                mileage_km=_display(vehicle.mileage_km),
                metric=f"-{int(drop)} CAD",
                listing_url=vehicle.listing_url,
                accident_history=_format_accident_history(vehicle),
                vehicle_history_report=_format_vehicle_history_report(vehicle),
                maintenance_history=_format_maintenance_history(vehicle),
                old_price_cad=f"{int(old_price)}",
                new_price_cad=f"{int(new_price)}",
                drop_cad=f"-{int(drop)}",
                drop_pct=f"{drop_pct:.1f}%",
            )
        )
    return picks


def _rank_accident_risk(active: list[VehicleSnapshot], top_n: int) -> list[RankedPick]:
    severity_order = {"major": 0, "unknown": 1, "minor": 2}
    candidates = [vehicle for vehicle in active if vehicle.accident_history_status == "reported"]
    candidates.sort(
        key=lambda vehicle: (
            severity_order.get(vehicle.accident_severity, 1),
            vehicle.accident_details or "",
            vehicle.vin,
        )
    )

    return [
        RankedPick(
            vin=vehicle.vin,
            year=_display(vehicle.year),
            trim=_display(vehicle.trim),
            price_cad=_display(vehicle.price_cad),
            mileage_km=_display(vehicle.mileage_km),
            metric=vehicle.accident_severity,
            listing_url=vehicle.listing_url,
            accident_history=_format_accident_history(vehicle),
            vehicle_history_report=_format_vehicle_history_report(vehicle),
            maintenance_history=_format_maintenance_history(vehicle),
        )
        for vehicle in candidates[:top_n]
    ]


def _rank_history_report_scan_gaps(active: list[VehicleSnapshot], top_n: int) -> list[RankedPick]:
    status_order = {"blocked": 0, "error": 1, "not_scanned": 2, "unavailable": 3}
    candidates = [
        vehicle
        for vehicle in active
        if vehicle.vehicle_history_report_status != "scanned"
    ]
    candidates.sort(
        key=lambda vehicle: (
            status_order.get(vehicle.vehicle_history_report_status, 4),
            vehicle.vin,
        )
    )

    return [
        RankedPick(
            vin=vehicle.vin,
            year=_display(vehicle.year),
            trim=_display(vehicle.trim),
            price_cad=_display(vehicle.price_cad),
            mileage_km=_display(vehicle.mileage_km),
            metric=vehicle.vehicle_history_report_status,
            listing_url=vehicle.listing_url,
            accident_history=_format_accident_history(vehicle),
            vehicle_history_report=_format_vehicle_history_report(vehicle),
            maintenance_history=_format_maintenance_history(vehicle),
        )
        for vehicle in candidates[:top_n]
    ]


def _rank_maintenance_risk(active: list[VehicleSnapshot], top_n: int) -> list[RankedPick]:
    risk_order = {"high": 0, "moderate": 1}
    candidates = [
        vehicle
        for vehicle in active
        if vehicle.maintenance_risk_level in risk_order
    ]
    candidates.sort(
        key=lambda vehicle: (
            risk_order[vehicle.maintenance_risk_level],
            -(vehicle.maintenance_records_count or 0),
            -(vehicle.maintenance_locations_count or 0),
            -(vehicle.maintenance_replaced_components_count or 0),
            vehicle.vin,
        )
    )

    return [
        RankedPick(
            vin=vehicle.vin,
            year=_display(vehicle.year),
            trim=_display(vehicle.trim),
            price_cad=_display(vehicle.price_cad),
            mileage_km=_display(vehicle.mileage_km),
            metric=vehicle.maintenance_risk_level,
            listing_url=vehicle.listing_url,
            accident_history=_format_accident_history(vehicle),
            vehicle_history_report=_format_vehicle_history_report(vehicle),
            maintenance_history=_format_maintenance_history(vehicle),
        )
        for vehicle in candidates[:top_n]
    ]


def _render_section(picks: list[RankedPick], headers: list[str]) -> list[str]:
    if not picks:
        return ["_No qualifying listings._"]

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]

    for index, pick in enumerate(picks, start=1):
        if headers[0] == "Year":
            row = [
                pick.year,
                pick.vin,
                pick.trim,
                pick.price_cad,
                pick.mileage_km,
                pick.accident_history,
                pick.maintenance_history,
                _format_link(pick.listing_url),
            ]
        elif "Drop %" in headers:
            row = [
                str(index),
                pick.vin,
                pick.year,
                pick.trim,
                pick.old_price_cad or "unknown",
                pick.new_price_cad or "unknown",
                pick.drop_cad or "unknown",
                pick.drop_pct or "unknown",
                pick.accident_history,
                pick.maintenance_history,
                _format_link(pick.listing_url),
            ]
        elif "Days Listed" in headers:
            row = [
                str(index),
                pick.vin,
                pick.year,
                pick.trim,
                pick.price_cad,
                pick.first_seen_at or "unknown",
                pick.days_listed or "unknown",
                pick.accident_history,
                pick.maintenance_history,
                _format_link(pick.listing_url),
            ]
        elif "vs Median" in headers:
            row = [
                str(index),
                pick.vin,
                pick.year,
                pick.trim,
                pick.price_cad,
                pick.mileage_km,
                pick.metric,
                pick.accident_history,
                pick.maintenance_history,
                _format_link(pick.listing_url),
            ]
        elif "CAD/km" in headers:
            row = [
                str(index),
                pick.vin,
                pick.year,
                pick.trim,
                pick.price_cad,
                pick.mileage_km,
                pick.metric,
                pick.accident_history,
                pick.maintenance_history,
                _format_link(pick.listing_url),
            ]
        elif "History Report" in headers and headers.index("Accident") < headers.index("History Report"):
            row = [
                str(index),
                pick.vin,
                pick.year,
                pick.trim,
                pick.price_cad,
                pick.mileage_km,
                pick.accident_history,
                pick.vehicle_history_report,
                _format_link(pick.listing_url),
            ]
        elif "History Report" in headers and headers.index("History Report") < headers.index("Accident"):
            row = [
                str(index),
                pick.vin,
                pick.year,
                pick.trim,
                pick.price_cad,
                pick.mileage_km,
                pick.vehicle_history_report,
                pick.accident_history,
                _format_link(pick.listing_url),
            ]
        elif "Maintenance" in headers and headers.index("Maintenance") < headers.index("Accident"):
            row = [
                str(index),
                pick.vin,
                pick.year,
                pick.trim,
                pick.price_cad,
                pick.mileage_km,
                pick.maintenance_history,
                pick.accident_history,
                _format_link(pick.listing_url),
            ]
        else:
            row = [
                str(index),
                pick.vin,
                pick.year,
                pick.trim,
                pick.price_cad,
                pick.mileage_km,
                pick.accident_history,
                pick.maintenance_history,
                _format_link(pick.listing_url),
            ]
        lines.append("| " + " | ".join(row) + " |")

    return lines
