"""Multi-dimensional vehicle recommendations from tracked inventory data."""

from __future__ import annotations

import json
import logging
import statistics
from dataclasses import asdict, dataclass
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
    previous_use: str
    previous_use_details: str | None
    interprovincial_history: str
    province_history: str | None
    interprovincial_details: str | None
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
    usage_history: str = "unknown"
    first_seen_at: str | None = None
    days_listed: str | None = None
    old_price_cad: str | None = None
    new_price_cad: str | None = None
    drop_cad: str | None = None
    drop_pct: str | None = None


@dataclass(frozen=True)
class RecommendationSection:
    """One ranked recommendation dimension."""

    key: str
    title: str
    description: str
    headers: tuple[str, ...]
    picks: tuple[RankedPick, ...]
    primary: bool = True


@dataclass(frozen=True)
class RecommendationsResult:
    """Structured recommendations built from current active inventory."""

    target_id: str
    generated_at: str
    preference_summary: str
    active_listings: int
    recommendable_listings: int
    preference_matched: int
    outside_preference_count: int
    excluded: int
    scanned_history_reports: int
    failed_history_reports: int
    high_maintenance_risk: int
    commercial_use: int
    interprovincial: int
    top_n: int
    scan_id: str | None
    scan_complete: bool
    inventory_updated_at: str | None
    sections: tuple[RecommendationSection, ...]
    current_picks: tuple[RankedPick, ...] = ()


def build_recommendations(
    root: Path | None,
    target_id: str,
    *,
    top_n: int = 3,
) -> RecommendationsResult:
    """Rank current active listings. Removed / unavailable vehicles are omitted."""
    root_path = project_root(root)
    timestamp = now_iso(root_path)
    if top_n < 1:
        raise ValueError("top_n must be >= 1")

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
    commercial_use = sum(1 for vehicle in active if vehicle.previous_use == "commercial")
    interprovincial = sum(1 for vehicle in active if vehicle.interprovincial_history == "yes")
    reference_at = inventory.updated_at if inventory else timestamp

    sections = (
        RecommendationSection(
            key="relative_price",
            title="Best Relative Price (Preference Match)",
            description="Listings matching the configured trim profile, ranked by price below the cohort median.",
            headers=(
                "Rank",
                "VIN",
                "Year",
                "Trim",
                "Price (CAD)",
                "Mileage (km)",
                "vs Median",
                "Accident",
                "Maintenance",
                "Usage/Registration",
                "Link",
            ),
            picks=tuple(_rank_relative_price(matched, top_n)),
        ),
        RecommendationSection(
            key="price_by_year",
            title="Best Price by Year (Preference Match)",
            description="Cheapest preference-matched listing within each model year.",
            headers=(
                "Year",
                "VIN",
                "Trim",
                "Price (CAD)",
                "Mileage (km)",
                "Accident",
                "Maintenance",
                "Usage/Registration",
                "Link",
            ),
            picks=tuple(_rank_best_price_by_year(matched, top_n)),
        ),
        RecommendationSection(
            key="low_mileage",
            title="Lowest Mileage (Preference Match)",
            description="Lowest odometer among preference-matched listings.",
            headers=(
                "Rank",
                "VIN",
                "Year",
                "Trim",
                "Price (CAD)",
                "Mileage (km)",
                "Accident",
                "Maintenance",
                "Usage/Registration",
                "Link",
            ),
            picks=tuple(_rank_low_mileage(matched, top_n)),
        ),
        RecommendationSection(
            key="new_listings",
            title="New Listings (Preference Match)",
            description="Most recently appeared preference-matched listings.",
            headers=(
                "Rank",
                "VIN",
                "Year",
                "Trim",
                "Price (CAD)",
                "First Seen",
                "Days Listed",
                "Accident",
                "Maintenance",
                "Usage/Registration",
                "Link",
            ),
            picks=tuple(_rank_new_listings(matched, reference_at, top_n)),
        ),
        RecommendationSection(
            key="price_drops",
            title="Recent Price Drops (Preference Match)",
            description="Largest recent price reductions among active preference-matched listings.",
            headers=(
                "Rank",
                "VIN",
                "Year",
                "Trim",
                "Old Price",
                "New Price",
                "Drop",
                "Drop %",
                "Accident",
                "Maintenance",
                "Usage/Registration",
                "Link",
            ),
            picks=tuple(_rank_price_drops(events, matched, top_n)),
        ),
        RecommendationSection(
            key="outside_preferences",
            title="Outside Preference Profile",
            description="Recommendable listings that do not match the configured trim profile.",
            headers=(
                "Rank",
                "VIN",
                "Year",
                "Trim",
                "Price (CAD)",
                "Mileage (km)",
                "Accident",
                "Maintenance",
                "Usage/Registration",
                "Link",
            ),
            picks=tuple(_rank_outside_preferences(outside_preferences, top_n)),
            primary=False,
        ),
        RecommendationSection(
            key="accident_watchlist",
            title="Accident Risk Watchlist",
            description="Reported accident/damage history among active listings, including vehicles excluded from recommendations.",
            headers=(
                "Rank",
                "VIN",
                "Year",
                "Trim",
                "Price (CAD)",
                "Mileage (km)",
                "Accident",
                "History Report",
                "Link",
            ),
            picks=tuple(_rank_accident_risk(active, top_n)),
            primary=False,
        ),
        RecommendationSection(
            key="history_scan_watchlist",
            title="Vehicle History Scan Watchlist",
            description="Active listings whose full vehicle-history report was not successfully scanned.",
            headers=(
                "Rank",
                "VIN",
                "Year",
                "Trim",
                "Price (CAD)",
                "Mileage (km)",
                "History Report",
                "Accident",
                "Link",
            ),
            picks=tuple(_rank_history_report_scan_gaps(active, top_n)),
            primary=False,
        ),
        RecommendationSection(
            key="maintenance_watchlist",
            title="Maintenance Risk Watchlist",
            description="Known moderate/high maintenance complexity among active listings, including vehicles excluded from recommendations.",
            headers=(
                "Rank",
                "VIN",
                "Year",
                "Trim",
                "Price (CAD)",
                "Mileage (km)",
                "Maintenance",
                "Accident",
                "Link",
            ),
            picks=tuple(_rank_maintenance_risk(active, top_n)),
            primary=False,
        ),
        RecommendationSection(
            key="usage_watchlist",
            title="Commercial Use & Interprovincial Watchlist",
            description="Active listings flagged with commercial previous use or registration in more than one province.",
            headers=(
                "Rank",
                "VIN",
                "Year",
                "Trim",
                "Price (CAD)",
                "Mileage (km)",
                "Usage/Registration",
                "Link",
            ),
            picks=tuple(_rank_usage_disclosure(active, top_n)),
            primary=False,
        ),
    )

    return RecommendationsResult(
        target_id=target_id,
        generated_at=timestamp,
        preference_summary=preference_summary,
        active_listings=len(active),
        recommendable_listings=len(recommendable),
        preference_matched=len(matched),
        outside_preference_count=len(outside_preferences),
        excluded=excluded,
        scanned_history_reports=scanned_history_reports,
        failed_history_reports=failed_history_reports,
        high_maintenance_risk=high_maintenance_risk,
        commercial_use=commercial_use,
        interprovincial=interprovincial,
        top_n=top_n,
        scan_id=inventory.scan_id if inventory else None,
        scan_complete=bool(inventory.scan_complete) if inventory else False,
        inventory_updated_at=inventory.updated_at if inventory else None,
        sections=sections,
        current_picks=tuple(_rank_relative_price(recommendable, max(top_n, 12))),
    )


def recommendations_payload(result: RecommendationsResult) -> dict:
    """JSON-ready recommendations for the local web UI."""
    return asdict(result)


def generate_recommendations_report(
    root: Path | None,
    target_id: str,
    *,
    top_n: int = 3,
    result: RecommendationsResult | None = None,
) -> Path:
    """Generate a multi-dimensional markdown recommendations report."""
    root_path = project_root(root)
    payload = result or build_recommendations(root_path, target_id, top_n=top_n)

    report_dir = recommendations_report_dir(root_path, target_id)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{payload.generated_at[:10]}.md"

    lines = [
        f"# Recommendations: {payload.target_id}",
        "",
        f"Generated at: {payload.generated_at}",
        "",
        "## Summary",
        "",
        f"- Active listings analyzed: {payload.recommendable_listings}",
        f"- Preference-matched listings: {payload.preference_matched}",
        f"- Preference profile: {payload.preference_summary}",
        f"- Active listings outside preference profile: {payload.outside_preference_count}",
        f"- Active listings tracked but excluded from recommendations: {payload.excluded}",
        f"- Active listings with scanned history reports: {payload.scanned_history_reports}",
        f"- Active listings with failed/unavailable history report scans: {payload.failed_history_reports}",
        f"- Active listings with high maintenance risk: {payload.high_maintenance_risk}",
        f"- Active listings with commercial previous use: {payload.commercial_use}",
        f"- Active listings with interprovincial registration history: {payload.interprovincial}",
        f"- Recommendations per dimension: top {payload.top_n}",
        "",
    ]

    if payload.inventory_updated_at or payload.scan_id:
        lines.extend(
            [
                f"- Last scan ID: {payload.scan_id or 'unknown'}",
                f"- Last scan complete: {payload.scan_complete}",
                f"- Inventory updated at: {payload.inventory_updated_at}",
                "",
            ]
        )
        if not payload.scan_complete:
            lines.extend(
                [
                    "> **Data quality warning:** The latest scan was partial "
                    "(`scan_complete: false`). Inventory may be incomplete and "
                    "recommendations may omit listings not captured in that scan.",
                    "",
                ]
            )

    for section in payload.sections:
        lines.extend([f"## {section.title}", "", section.description, ""])
        lines.extend(_render_section(list(section.picks), list(section.headers)))
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
                previous_use=vehicle.previous_use,
                previous_use_details=vehicle.previous_use_details,
                interprovincial_history=vehicle.interprovincial_history,
                province_history=vehicle.province_history,
                interprovincial_details=vehicle.interprovincial_details,
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


def _format_usage_history(vehicle: VehicleSnapshot) -> str:
    parts: list[str] = []
    if vehicle.previous_use == "commercial":
        parts.append("commercial use")
    elif vehicle.previous_use == "personal":
        parts.append("personal use")
    if vehicle.interprovincial_history == "yes":
        if vehicle.province_history:
            parts.append(f"interprovincial: {vehicle.province_history}")
        else:
            parts.append("interprovincial")
    elif vehicle.interprovincial_history == "no":
        parts.append("single province")
    if not parts:
        return "unknown"
    return "; ".join(parts)


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
                usage_history=_format_usage_history(vehicle),
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
            usage_history=_format_usage_history(vehicle),
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
            usage_history=_format_usage_history(vehicle),
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
                usage_history=_format_usage_history(vehicle),
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
                usage_history=_format_usage_history(vehicle),
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
                usage_history=_format_usage_history(vehicle),
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


def _rank_usage_disclosure(active: list[VehicleSnapshot], top_n: int) -> list[RankedPick]:
    def flagged(vehicle: VehicleSnapshot) -> bool:
        return vehicle.previous_use == "commercial" or vehicle.interprovincial_history == "yes"

    candidates = [vehicle for vehicle in active if flagged(vehicle)]
    candidates.sort(
        key=lambda vehicle: (
            0 if vehicle.previous_use == "commercial" else 1,
            0 if vehicle.interprovincial_history == "yes" else 1,
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
            metric=_format_usage_history(vehicle),
            listing_url=vehicle.listing_url,
            accident_history=_format_accident_history(vehicle),
            vehicle_history_report=_format_vehicle_history_report(vehicle),
            maintenance_history=_format_maintenance_history(vehicle),
            usage_history=_format_usage_history(vehicle),
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

    has_usage_column = "Usage/Registration" in headers

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
            ]
        elif has_usage_column and "Accident" not in headers:
            row = [
                str(index),
                pick.vin,
                pick.year,
                pick.trim,
                pick.price_cad,
                pick.mileage_km,
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
            ]

        if has_usage_column:
            row.append(pick.usage_history)
        row.append(_format_link(pick.listing_url))
        lines.append("| " + " | ".join(row) + " |")

    return lines
