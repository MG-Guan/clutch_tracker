"""Deterministic scan comparison and history updates."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from clutch_tracker.carfax import parse_carfax, summarize_carfax, CarfaxReport
from clutch_tracker.config import project_root
from clutch_tracker.events import build_listing_events
from clutch_tracker.models import (
    CurrentInventory,
    InventoryVehicle,
    Observation,
    ScanPayload,
    ScanVehicle,
    VehicleRecord,
)
from clutch_tracker.storage import (
    append_events,
    append_observations,
    load_current_inventory,
    load_vehicles,
    new_id,
    normalize_optional_str,
    now_iso,
    raw_scan_archive_path,
    save_current_inventory,
    save_vehicles,
    atomic_write_json,
)

logger = logging.getLogger(__name__)


def parse_scan_payload(data: dict[str, Any]) -> ScanPayload:
    """Parse raw scan JSON into a ScanPayload."""
    target_id = data.get("target_id")
    if not target_id or not isinstance(target_id, str):
        raise ValueError("Scan JSON must include string target_id")

    scan_id = data.get("scan_id")
    if not scan_id or not isinstance(scan_id, str):
        raise ValueError("Scan JSON must include string scan_id")

    scanned_at = data.get("scanned_at")
    if not scanned_at or not isinstance(scanned_at, str):
        raise ValueError("Scan JSON must include ISO 8601 scanned_at")

    scan_complete = data.get("scan_complete", False)
    if not isinstance(scan_complete, bool):
        raise ValueError("scan_complete must be a boolean")

    vehicles_raw = data.get("vehicles", [])
    if not isinstance(vehicles_raw, list):
        raise ValueError("vehicles must be a list")

    vehicles: list[ScanVehicle] = []
    for index, item in enumerate(vehicles_raw):
        if not isinstance(item, dict):
            raise ValueError(f"vehicles[{index}] must be a mapping")
        vin = item.get("vin")
        if not vin or not isinstance(vin, str):
            raise ValueError(f"vehicles[{index}] must include string vin")

        known = {
            "vin",
            "listing_id",
            "listing_url",
            "year",
            "make",
            "model",
            "trim",
            "price_cad",
            "mileage_km",
            "carfax",
        }
        extra = {k: v for k, v in item.items() if k not in known}
        carfax_raw = item.get("carfax")
        if carfax_raw is not None:
            carfax = parse_carfax(carfax_raw)
        else:
            carfax = None
        vehicles.append(
            ScanVehicle(
                vin=vin.strip().upper(),
                listing_id=normalize_optional_str(item.get("listing_id")),
                listing_url=normalize_optional_str(item.get("listing_url")),
                year=item.get("year"),
                make=normalize_optional_str(item.get("make")),
                model=normalize_optional_str(item.get("model")),
                trim=normalize_optional_str(item.get("trim")),
                price_cad=item.get("price_cad"),
                mileage_km=item.get("mileage_km"),
                extra=extra,
                carfax=carfax,
            )
        )

    return ScanPayload(
        target_id=target_id,
        scan_id=scan_id,
        scanned_at=scanned_at,
        scan_complete=scan_complete,
        vehicles=vehicles,
        source=normalize_optional_str(data.get("source")),
        notes=normalize_optional_str(data.get("notes")),
    )


def _merge_vehicle_fields(existing: VehicleRecord | None, scanned: ScanVehicle, observed_at: str, target_id: str) -> VehicleRecord:
    """Update vehicle registry fields without guessing unknown values."""
    if existing is None:
        return VehicleRecord(
            vin=scanned.vin,
            target_id=target_id,
            first_seen_at=observed_at,
            last_seen_at=observed_at,
            year=normalize_optional_str(scanned.year),
            make=scanned.make,
            model=scanned.model,
            trim=scanned.trim,
            listing_id=scanned.listing_id,
            listing_url=scanned.listing_url,
        )

    return VehicleRecord(
        vin=existing.vin,
        target_id=existing.target_id,
        first_seen_at=existing.first_seen_at,
        last_seen_at=observed_at,
        year=normalize_optional_str(scanned.year) or existing.year,
        make=scanned.make or existing.make,
        model=scanned.model or existing.model,
        trim=scanned.trim or existing.trim,
        listing_id=scanned.listing_id or existing.listing_id,
        listing_url=scanned.listing_url or existing.listing_url,
    )


def _merge_carfax(
    scanned: ScanVehicle,
    previous: InventoryVehicle | None = None,
) -> tuple[CarfaxReport | None, str | None]:
    """Use scan Carfax when present; otherwise keep previous inventory Carfax."""
    carfax = scanned.carfax if scanned.carfax is not None else (previous.carfax if previous else None)
    summary = summarize_carfax(carfax)
    return carfax, summary


def _build_observation(scanned: ScanVehicle, payload: ScanPayload) -> Observation:
    summary = summarize_carfax(scanned.carfax)
    return Observation(
        observation_id=new_id("obs"),
        vin=scanned.vin,
        target_id=payload.target_id,
        observed_at=payload.scanned_at,
        scan_id=payload.scan_id,
        price_cad=normalize_optional_str(scanned.price_cad),
        mileage_km=normalize_optional_str(scanned.mileage_km),
        listing_url=scanned.listing_url,
        listing_id=scanned.listing_id,
        year=normalize_optional_str(scanned.year),
        make=scanned.make,
        model=scanned.model,
        trim=scanned.trim,
        raw_fields=scanned.extra,
        carfax=scanned.carfax,
        carfax_summary=summary,
    )


def _inventory_from_scan(payload: ScanPayload, previous: CurrentInventory | None) -> CurrentInventory:
    """Build updated inventory from scan results."""
    previous_by_vin: dict[str, InventoryVehicle] = {}
    if previous:
        previous_by_vin = {v.vin: v for v in previous.vehicles}

    scanned_vins = {v.vin for v in payload.vehicles}
    updated_by_vin: dict[str, InventoryVehicle] = {}

    for scanned in payload.vehicles:
        prev = previous_by_vin.get(scanned.vin)
        carfax, carfax_summary = _merge_carfax(scanned, prev)
        updated_by_vin[scanned.vin] = InventoryVehicle(
            vin=scanned.vin,
            last_seen_at=payload.scanned_at,
            listing_id=scanned.listing_id or (prev.listing_id if prev else None),
            listing_url=scanned.listing_url or (prev.listing_url if prev else None),
            year=normalize_optional_str(scanned.year) or (prev.year if prev else None),
            make=scanned.make or (prev.make if prev else None),
            model=scanned.model or (prev.model if prev else None),
            trim=scanned.trim or (prev.trim if prev else None),
            price_cad=normalize_optional_str(scanned.price_cad) or (prev.price_cad if prev else None),
            mileage_km=normalize_optional_str(scanned.mileage_km) or (prev.mileage_km if prev else None),
            status="active",
            carfax=carfax,
            carfax_summary=carfax_summary,
        )

    if payload.scan_complete:
        for prev in previous_by_vin.values():
            if prev.vin not in scanned_vins:
                updated_by_vin[prev.vin] = InventoryVehicle(
                    vin=prev.vin,
                    last_seen_at=prev.last_seen_at,
                    listing_id=prev.listing_id,
                    listing_url=prev.listing_url,
                    year=prev.year,
                    make=prev.make,
                    model=prev.model,
                    trim=prev.trim,
                    price_cad=prev.price_cad,
                    mileage_km=prev.mileage_km,
                    status="removed",
                    carfax=prev.carfax,
                    carfax_summary=prev.carfax_summary,
                )
    else:
        for prev in previous_by_vin.values():
            if prev.vin not in scanned_vins and prev.status == "active":
                updated_by_vin[prev.vin] = InventoryVehicle(
                    vin=prev.vin,
                    last_seen_at=prev.last_seen_at,
                    listing_id=prev.listing_id,
                    listing_url=prev.listing_url,
                    year=prev.year,
                    make=prev.make,
                    model=prev.model,
                    trim=prev.trim,
                    price_cad=prev.price_cad,
                    mileage_km=prev.mileage_km,
                    status="active",
                    carfax=prev.carfax,
                    carfax_summary=prev.carfax_summary,
                )

    return CurrentInventory(
        target_id=payload.target_id,
        updated_at=payload.scanned_at,
        scan_id=payload.scan_id,
        scan_complete=payload.scan_complete,
        vehicles=sorted(updated_by_vin.values(), key=lambda v: v.vin),
    )


def import_scan(root: Path | None, scan_data: dict[str, Any]) -> dict[str, Any]:
    """
    Import a scan JSON payload and update deterministic history.

    Returns summary statistics about the import.
    """
    root_path = project_root(root)
    payload = parse_scan_payload(scan_data)

    archive_path = raw_scan_archive_path(root_path, payload.scan_id)
    atomic_write_json(archive_path, scan_data)
    logger.info("Archived raw scan to %s", archive_path)

    existing_vehicles = load_vehicles(root_path, payload.target_id)
    previous_inventory = load_current_inventory(root_path, payload.target_id)

    observations: list[Observation] = []
    for scanned in payload.vehicles:
        observations.append(_build_observation(scanned, payload))
        existing_vehicles[scanned.vin] = _merge_vehicle_fields(
            existing_vehicles.get(scanned.vin),
            scanned,
            payload.scanned_at,
            payload.target_id,
        )

    append_observations(root_path, observations)
    save_vehicles(root_path, payload.target_id, existing_vehicles)

    new_inventory = _inventory_from_scan(payload, previous_inventory)
    events = build_listing_events(
        payload,
        previous_inventory,
        new_inventory,
        existing_before=previous_inventory.vehicles if previous_inventory else [],
    )
    append_events(root_path, events)
    save_current_inventory(root_path, new_inventory)

    active_count = sum(1 for v in new_inventory.vehicles if v.status == "active")
    removed_count = sum(1 for v in new_inventory.vehicles if v.status == "removed")

    summary = {
        "target_id": payload.target_id,
        "scan_id": payload.scan_id,
        "scan_complete": payload.scan_complete,
        "vehicles_scanned": len(payload.vehicles),
        "observations_added": len(observations),
        "events_added": len(events),
        "active_inventory": active_count,
        "removed_inventory": removed_count,
    }
    logger.info("Scan import complete: %s", summary)
    return summary
