"""Multi-dimensional vehicle recommendations from tracked inventory data."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from clutch_tracker.config import project_root
from clutch_tracker.models import CurrentInventory, InventoryVehicle, VehicleRecord
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

    report_dir = recommendations_report_dir(root_path, target_id)
    report_dir.mkdir(parents=True, exist_ok=True)

    date_part = timestamp[:10]
    report_path = report_dir / f"{date_part}.md"

    active = _active_snapshots(inventory, vehicles)
    reference_at = inventory.updated_at if inventory else timestamp

    lines = [
        f"# Recommendations: {target_id}",
        "",
        f"Generated at: {timestamp}",
        "",
        "## Summary",
        "",
        f"- Active listings analyzed: {len(active)}",
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
            "Best Value (Price per km)",
            "Lower price-per-km is better. Only listings with known price and mileage are ranked.",
            _rank_best_value(active, top_n),
            ["Rank", "VIN", "Year", "Trim", "Price (CAD)", "Mileage (km)", "CAD/km", "Link"],
        ),
        (
            "Lowest Price",
            "Lowest current asking price among active listings.",
            _rank_lowest_price(active, top_n),
            ["Rank", "VIN", "Year", "Trim", "Price (CAD)", "Mileage (km)", "Link"],
        ),
        (
            "Low Mileage",
            "Lowest odometer reading among active listings.",
            _rank_low_mileage(active, top_n),
            ["Rank", "VIN", "Year", "Trim", "Price (CAD)", "Mileage (km)", "Link"],
        ),
        (
            "Best Price by Year",
            "Cheapest active listing within each model year (one pick per year).",
            _rank_best_price_by_year(active, top_n),
            ["Year", "VIN", "Trim", "Price (CAD)", "Mileage (km)", "Link"],
        ),
        (
            "New Listings",
            "Most recently appeared listings (by first seen date).",
            _rank_new_listings(active, reference_at, top_n),
            ["Rank", "VIN", "Year", "Trim", "Price (CAD)", "First Seen", "Days Listed", "Link"],
        ),
        (
            "Recent Price Drops",
            "Largest recent price reductions among currently active listings.",
            _rank_price_drops(events, active, top_n),
            ["Rank", "VIN", "Year", "Trim", "Old Price", "New Price", "Drop", "Drop %", "Link"],
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
            )
        )
    return snapshots


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


def _days_between(start_iso: str | None, end_iso: str) -> int | None:
    if not start_iso:
        return None
    try:
        start = datetime.fromisoformat(start_iso)
        end = datetime.fromisoformat(end_iso)
    except (TypeError, ValueError):
        return None
    return max((end.date() - start.date()).days, 0)


def _rank_best_value(active: list[VehicleSnapshot], top_n: int) -> list[RankedPick]:
    scored: list[tuple[float, VehicleSnapshot]] = []
    for vehicle in active:
        price = _parse_positive_number(vehicle.price_cad)
        mileage = _parse_positive_number(vehicle.mileage_km)
        if price is None or mileage is None:
            continue
        scored.append((price / mileage, vehicle))

    scored.sort(key=lambda item: item[0])
    picks: list[RankedPick] = []
    for ratio, vehicle in scored[:top_n]:
        picks.append(
            RankedPick(
                vin=vehicle.vin,
                year=_display(vehicle.year),
                trim=_display(vehicle.trim),
                price_cad=_display(vehicle.price_cad),
                mileage_km=_display(vehicle.mileage_km),
                metric=f"{ratio:.2f}",
                listing_url=vehicle.listing_url,
            )
        )
    return picks


def _rank_lowest_price(active: list[VehicleSnapshot], top_n: int) -> list[RankedPick]:
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
                old_price_cad=f"{int(old_price)}",
                new_price_cad=f"{int(new_price)}",
                drop_cad=f"-{int(drop)}",
                drop_pct=f"{drop_pct:.1f}%",
            )
        )
    return picks


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
                _format_link(pick.listing_url),
            ]
        lines.append("| " + " | ".join(row) + " |")

    return lines
