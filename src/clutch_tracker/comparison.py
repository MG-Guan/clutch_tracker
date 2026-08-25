"""Deterministic scan comparison and history updates."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from clutch_tracker.accident_history import (
    AccidentAssessment,
    VehicleHistoryReportAssessment,
    assess_accident_history,
    assess_vehicle_history_report,
)
from clutch_tracker.config import load_settings, project_root
from clutch_tracker.events import build_listing_events
from clutch_tracker.maintenance_history import MaintenanceAssessment, assess_maintenance_history
from clutch_tracker.usage_history import UsageHistoryAssessment, assess_usage_history
from clutch_tracker.models import (
    CurrentInventory,
    InventoryVehicle,
    ListingEvent,
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
    save_current_inventory,
    save_raw_scan,
    save_vehicles,
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

        known = {"vin", "listing_id", "listing_url", "year", "make", "model", "trim", "price_cad", "mileage_km"}
        extra = {k: v for k, v in item.items() if k not in known}
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


def _build_observation(scanned: ScanVehicle, payload: ScanPayload) -> Observation:
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
    )


def _removed_inventory_vehicle(prev: InventoryVehicle) -> InventoryVehicle:
    return InventoryVehicle(
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
        accident_history_status=prev.accident_history_status,
        accident_severity=prev.accident_severity,
        accident_details=prev.accident_details,
        vehicle_history_report_status=prev.vehicle_history_report_status,
        vehicle_history_report_provider=prev.vehicle_history_report_provider,
        vehicle_history_report_details=prev.vehicle_history_report_details,
        maintenance_history_status=prev.maintenance_history_status,
        maintenance_risk_level=prev.maintenance_risk_level,
        maintenance_details=prev.maintenance_details,
        maintenance_records_count=prev.maintenance_records_count,
        maintenance_locations_count=prev.maintenance_locations_count,
        maintenance_replaced_components_count=prev.maintenance_replaced_components_count,
        previous_use=prev.previous_use,
        previous_use_details=prev.previous_use_details,
        interprovincial_history=prev.interprovincial_history,
        province_history=prev.province_history,
        interprovincial_details=prev.interprovincial_details,
        recommendation_eligible=prev.recommendation_eligible,
    )


def _merge_accident_assessment(
    scanned: ScanVehicle,
    previous: InventoryVehicle | None,
) -> AccidentAssessment:
    assessment = assess_accident_history(scanned.extra)
    if assessment.status != "unknown" or previous is None:
        return assessment
    return AccidentAssessment(
        status=previous.accident_history_status,
        severity=previous.accident_severity,
        details=previous.accident_details,
        recommendation_eligible=_accident_recommendation_eligible(previous),
    )


def _merge_maintenance_assessment(
    scanned: ScanVehicle,
    previous: InventoryVehicle | None,
) -> MaintenanceAssessment:
    assessment = assess_maintenance_history(scanned.extra)
    if assessment.status != "unknown" or previous is None:
        return assessment
    return MaintenanceAssessment(
        status=previous.maintenance_history_status,
        risk_level=previous.maintenance_risk_level,
        details=previous.maintenance_details,
        records_count=previous.maintenance_records_count,
        locations_count=previous.maintenance_locations_count,
        replaced_components_count=previous.maintenance_replaced_components_count,
        recommendation_eligible=previous.maintenance_risk_level != "high",
    )


def _merge_usage_assessment(
    scanned: ScanVehicle,
    previous: InventoryVehicle | None,
) -> UsageHistoryAssessment:
    assessment = assess_usage_history(scanned.extra)
    has_signal = (
        assessment.previous_use != "unknown"
        or assessment.interprovincial != "unknown"
        or bool(assessment.provinces)
    )
    if has_signal or previous is None:
        return assessment
    return UsageHistoryAssessment(
        previous_use=previous.previous_use,
        previous_use_details=previous.previous_use_details,
        interprovincial=previous.interprovincial_history,
        provinces=tuple(p for p in (previous.province_history or "").split(", ") if p),
        interprovincial_details=previous.interprovincial_details,
    )


def _merge_vehicle_history_report_assessment(
    scanned: ScanVehicle,
    previous: InventoryVehicle | None,
) -> VehicleHistoryReportAssessment:
    assessment = assess_vehicle_history_report(scanned.extra)
    if assessment.status != "not_scanned" or previous is None:
        return assessment
    return VehicleHistoryReportAssessment(
        status=previous.vehicle_history_report_status,
        provider=previous.vehicle_history_report_provider,
        details=previous.vehicle_history_report_details,
    )


def _accident_recommendation_eligible(vehicle: InventoryVehicle) -> bool:
    if vehicle.accident_history_status != "reported":
        return True
    return vehicle.accident_severity == "minor"


def _inventory_from_scan(
    payload: ScanPayload,
    previous: CurrentInventory | None,
) -> CurrentInventory:
    """Build updated inventory from scan results."""
    previous_by_vin: dict[str, InventoryVehicle] = {}
    if previous:
        previous_by_vin = {v.vin: v for v in previous.vehicles}

    scanned_vins = {v.vin for v in payload.vehicles}
    updated_by_vin: dict[str, InventoryVehicle] = {}

    for scanned in payload.vehicles:
        prev = previous_by_vin.get(scanned.vin)
        accident_assessment = _merge_accident_assessment(scanned, prev)
        history_report_assessment = _merge_vehicle_history_report_assessment(scanned, prev)
        maintenance_assessment = _merge_maintenance_assessment(scanned, prev)
        usage_assessment = _merge_usage_assessment(scanned, prev)
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
            accident_history_status=accident_assessment.status,
            accident_severity=accident_assessment.severity,
            accident_details=accident_assessment.details,
            vehicle_history_report_status=history_report_assessment.status,
            vehicle_history_report_provider=history_report_assessment.provider,
            vehicle_history_report_details=history_report_assessment.details,
            maintenance_history_status=maintenance_assessment.status,
            maintenance_risk_level=maintenance_assessment.risk_level,
            maintenance_details=maintenance_assessment.details,
            maintenance_records_count=maintenance_assessment.records_count,
            maintenance_locations_count=maintenance_assessment.locations_count,
            maintenance_replaced_components_count=maintenance_assessment.replaced_components_count,
            previous_use=usage_assessment.previous_use,
            previous_use_details=usage_assessment.previous_use_details,
            interprovincial_history=usage_assessment.interprovincial,
            province_history=", ".join(usage_assessment.provinces) or None,
            interprovincial_details=usage_assessment.interprovincial_details,
            recommendation_eligible=(
                accident_assessment.recommendation_eligible and maintenance_assessment.recommendation_eligible
            ),
        )

    if payload.scan_complete:
        for prev in previous_by_vin.values():
            if prev.vin not in scanned_vins:
                updated_by_vin[prev.vin] = _removed_inventory_vehicle(prev)
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
                    accident_history_status=prev.accident_history_status,
                    accident_severity=prev.accident_severity,
                    accident_details=prev.accident_details,
                    vehicle_history_report_status=prev.vehicle_history_report_status,
                    vehicle_history_report_provider=prev.vehicle_history_report_provider,
                    vehicle_history_report_details=prev.vehicle_history_report_details,
                    maintenance_history_status=prev.maintenance_history_status,
                    maintenance_risk_level=prev.maintenance_risk_level,
                    maintenance_details=prev.maintenance_details,
                    maintenance_records_count=prev.maintenance_records_count,
                    maintenance_locations_count=prev.maintenance_locations_count,
                    maintenance_replaced_components_count=prev.maintenance_replaced_components_count,
                    previous_use=prev.previous_use,
                    previous_use_details=prev.previous_use_details,
                    interprovincial_history=prev.interprovincial_history,
                    province_history=prev.province_history,
                    interprovincial_details=prev.interprovincial_details,
                    recommendation_eligible=prev.recommendation_eligible,
                )

    return CurrentInventory(
        target_id=payload.target_id,
        updated_at=payload.scanned_at,
        scan_id=payload.scan_id,
        scan_complete=payload.scan_complete,
        vehicles=sorted(updated_by_vin.values(), key=lambda v: v.vin),
    )


def _build_accident_history_events(
    payload: ScanPayload,
    previous_inventory: CurrentInventory | None,
    new_inventory: CurrentInventory,
) -> list[ListingEvent]:
    previous_by_vin = {}
    if previous_inventory:
        previous_by_vin = {vehicle.vin: vehicle for vehicle in previous_inventory.vehicles}
    current_by_vin = {vehicle.vin: vehicle for vehicle in new_inventory.vehicles}

    events: list[ListingEvent] = []
    for scanned in payload.vehicles:
        assessment = assess_accident_history(scanned.extra)
        if assessment.status == "unknown":
            continue
        current = current_by_vin.get(scanned.vin)
        if current is None:
            continue
        previous = previous_by_vin.get(scanned.vin)
        current_accident_eligible = _accident_recommendation_eligible(current)
        previous_accident_eligible = _accident_recommendation_eligible(previous) if previous else True
        if previous and (
            previous.accident_history_status,
            previous.accident_severity,
            previous.accident_details,
            previous_accident_eligible,
        ) == (
            current.accident_history_status,
            current.accident_severity,
            current.accident_details,
            current_accident_eligible,
        ):
            continue
        events.append(
            ListingEvent(
                event_id=new_id("evt"),
                vin=scanned.vin,
                target_id=payload.target_id,
                event_type="accident_history_assessed",
                event_at=payload.scanned_at,
                scan_id=payload.scan_id,
                details={
                    "status": current.accident_history_status,
                    "severity": current.accident_severity,
                    "recommendation_eligible": current_accident_eligible,
                    "details": current.accident_details,
                },
            )
        )
    return events


def _build_vehicle_history_report_events(
    payload: ScanPayload,
    previous_inventory: CurrentInventory | None,
    new_inventory: CurrentInventory,
) -> list[ListingEvent]:
    previous_by_vin = {}
    if previous_inventory:
        previous_by_vin = {vehicle.vin: vehicle for vehicle in previous_inventory.vehicles}
    current_by_vin = {vehicle.vin: vehicle for vehicle in new_inventory.vehicles}

    events: list[ListingEvent] = []
    for scanned in payload.vehicles:
        assessment = assess_vehicle_history_report(scanned.extra)
        if assessment.status == "not_scanned":
            continue
        current = current_by_vin.get(scanned.vin)
        if current is None:
            continue
        previous = previous_by_vin.get(scanned.vin)
        if previous and (
            previous.vehicle_history_report_status,
            previous.vehicle_history_report_provider,
            previous.vehicle_history_report_details,
        ) == (
            current.vehicle_history_report_status,
            current.vehicle_history_report_provider,
            current.vehicle_history_report_details,
        ):
            continue
        events.append(
            ListingEvent(
                event_id=new_id("evt"),
                vin=scanned.vin,
                target_id=payload.target_id,
                event_type="vehicle_history_report_checked",
                event_at=payload.scanned_at,
                scan_id=payload.scan_id,
                details={
                    "status": current.vehicle_history_report_status,
                    "provider": current.vehicle_history_report_provider,
                    "details": current.vehicle_history_report_details,
                },
            )
        )
    return events


def _build_maintenance_history_events(
    payload: ScanPayload,
    previous_inventory: CurrentInventory | None,
    new_inventory: CurrentInventory,
) -> list[ListingEvent]:
    previous_by_vin = {}
    if previous_inventory:
        previous_by_vin = {vehicle.vin: vehicle for vehicle in previous_inventory.vehicles}
    current_by_vin = {vehicle.vin: vehicle for vehicle in new_inventory.vehicles}

    events: list[ListingEvent] = []
    for scanned in payload.vehicles:
        assessment = assess_maintenance_history(scanned.extra)
        if assessment.status == "unknown":
            continue
        current = current_by_vin.get(scanned.vin)
        if current is None:
            continue
        previous = previous_by_vin.get(scanned.vin)
        if previous and (
            previous.maintenance_history_status,
            previous.maintenance_risk_level,
            previous.maintenance_details,
            previous.maintenance_records_count,
            previous.maintenance_locations_count,
            previous.maintenance_replaced_components_count,
            previous.maintenance_risk_level != "high",
        ) == (
            current.maintenance_history_status,
            current.maintenance_risk_level,
            current.maintenance_details,
            current.maintenance_records_count,
            current.maintenance_locations_count,
            current.maintenance_replaced_components_count,
            current.maintenance_risk_level != "high",
        ):
            continue
        events.append(
            ListingEvent(
                event_id=new_id("evt"),
                vin=scanned.vin,
                target_id=payload.target_id,
                event_type="maintenance_history_assessed",
                event_at=payload.scanned_at,
                scan_id=payload.scan_id,
                details={
                    "status": current.maintenance_history_status,
                    "risk_level": current.maintenance_risk_level,
                    "recommendation_eligible": current.maintenance_risk_level != "high",
                    "records_count": current.maintenance_records_count,
                    "locations_count": current.maintenance_locations_count,
                    "replaced_components_count": current.maintenance_replaced_components_count,
                    "details": current.maintenance_details,
                },
            )
        )
    return events


def _build_usage_history_events(
    payload: ScanPayload,
    previous_inventory: CurrentInventory | None,
    new_inventory: CurrentInventory,
) -> list[ListingEvent]:
    previous_by_vin = {}
    if previous_inventory:
        previous_by_vin = {vehicle.vin: vehicle for vehicle in previous_inventory.vehicles}
    current_by_vin = {vehicle.vin: vehicle for vehicle in new_inventory.vehicles}

    events: list[ListingEvent] = []
    for scanned in payload.vehicles:
        assessment = assess_usage_history(scanned.extra)
        if (
            assessment.previous_use == "unknown"
            and assessment.interprovincial == "unknown"
            and not assessment.provinces
        ):
            continue
        current = current_by_vin.get(scanned.vin)
        if current is None:
            continue
        previous = previous_by_vin.get(scanned.vin)
        if previous and (
            previous.previous_use,
            previous.previous_use_details,
            previous.interprovincial_history,
            previous.province_history,
            previous.interprovincial_details,
        ) == (
            current.previous_use,
            current.previous_use_details,
            current.interprovincial_history,
            current.province_history,
            current.interprovincial_details,
        ):
            continue
        events.append(
            ListingEvent(
                event_id=new_id("evt"),
                vin=scanned.vin,
                target_id=payload.target_id,
                event_type="usage_history_assessed",
                event_at=payload.scanned_at,
                scan_id=payload.scan_id,
                details={
                    "previous_use": current.previous_use,
                    "previous_use_details": current.previous_use_details,
                    "interprovincial_history": current.interprovincial_history,
                    "province_history": current.province_history,
                    "interprovincial_details": current.interprovincial_details,
                },
            )
        )
    return events


def import_scan(root: Path | None, scan_data: dict[str, Any]) -> dict[str, Any]:
    """
    Import a scan JSON payload and update deterministic history.

    Returns summary statistics about the import.
    """
    root_path = project_root(root)
    payload = parse_scan_payload(scan_data)

    save_raw_scan(root_path, payload.scan_id, scan_data)
    logger.info("Archived raw scan %s to local database", payload.scan_id)

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
    events.extend(_build_accident_history_events(payload, previous_inventory, new_inventory))
    events.extend(_build_vehicle_history_report_events(payload, previous_inventory, new_inventory))
    events.extend(_build_maintenance_history_events(payload, previous_inventory, new_inventory))
    events.extend(_build_usage_history_events(payload, previous_inventory, new_inventory))
    append_events(root_path, events)
    save_current_inventory(root_path, new_inventory)

    active_count = sum(1 for v in new_inventory.vehicles if v.status == "active")
    removed_count = sum(1 for v in new_inventory.vehicles if v.status == "removed")
    assessments = [assess_accident_history(vehicle.extra) for vehicle in payload.vehicles]
    reported_accident_count = sum(1 for assessment in assessments if assessment.status == "reported")
    minor_accident_count = sum(
        1 for assessment in assessments if assessment.status == "reported" and assessment.severity == "minor"
    )
    non_recommendable_accident_count = sum(
        1
        for assessment in assessments
        if assessment.status == "reported" and not assessment.recommendation_eligible
    )
    maintenance_assessments = [assess_maintenance_history(vehicle.extra) for vehicle in payload.vehicles]
    reported_maintenance_count = sum(
        1 for assessment in maintenance_assessments if assessment.status != "unknown"
    )
    moderate_maintenance_count = sum(
        1 for assessment in maintenance_assessments if assessment.risk_level == "moderate"
    )
    high_maintenance_count = sum(1 for assessment in maintenance_assessments if assessment.risk_level == "high")
    history_report_assessments = [assess_vehicle_history_report(vehicle.extra) for vehicle in payload.vehicles]
    scanned_history_report_count = sum(
        1 for assessment in history_report_assessments if assessment.status == "scanned"
    )
    failed_history_report_count = sum(
        1 for assessment in history_report_assessments if assessment.status in {"blocked", "error", "unavailable"}
    )
    usage_assessments = [assess_usage_history(vehicle.extra) for vehicle in payload.vehicles]
    commercial_use_count = sum(
        1 for assessment in usage_assessments if assessment.previous_use == "commercial"
    )
    interprovincial_count = sum(
        1 for assessment in usage_assessments if assessment.interprovincial == "yes"
    )

    summary = {
        "target_id": payload.target_id,
        "scan_id": payload.scan_id,
        "scan_complete": payload.scan_complete,
        "vehicles_scanned": len(payload.vehicles),
        "vehicles_with_reported_accident_history": reported_accident_count,
        "vehicles_with_minor_accident_history": minor_accident_count,
        "vehicles_with_non_recommendable_accident_history": non_recommendable_accident_count,
        "vehicles_with_scanned_history_reports": scanned_history_report_count,
        "vehicles_with_failed_history_report_scans": failed_history_report_count,
        "vehicles_with_reported_maintenance_history": reported_maintenance_count,
        "vehicles_with_moderate_maintenance_risk": moderate_maintenance_count,
        "vehicles_with_high_maintenance_risk": high_maintenance_count,
        "vehicles_with_commercial_use": commercial_use_count,
        "vehicles_with_interprovincial_history": interprovincial_count,
        "observations_added": len(observations),
        "events_added": len(events),
        "active_inventory": active_count,
        "removed_inventory": removed_count,
    }
    logger.info("Scan import complete: %s", summary)
    return summary
