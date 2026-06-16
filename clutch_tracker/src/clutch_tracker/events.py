"""Listing lifecycle event generation."""

from __future__ import annotations

import logging

from clutch_tracker.models import InventoryVehicle, ListingEvent, ScanPayload
from clutch_tracker.storage import new_id, normalize_optional_str

logger = logging.getLogger(__name__)


def build_listing_events(
    payload: ScanPayload,
    previous_inventory,
    new_inventory,
    *,
    existing_before: list[InventoryVehicle] | None = None,
) -> list[ListingEvent]:
    """
    Derive listing events from inventory transition.

    Partial scans (scan_complete=False) never emit removal events.
    """
    events: list[ListingEvent] = []

    prev_active: dict[str, InventoryVehicle] = {}
    if previous_inventory:
        prev_active = {v.vin: v for v in previous_inventory.vehicles if v.status == "active"}

    new_active: dict[str, InventoryVehicle] = {
        v.vin: v for v in new_inventory.vehicles if v.status == "active"
    }
    new_removed: dict[str, InventoryVehicle] = {
        v.vin: v for v in new_inventory.vehicles if v.status == "removed"
    }

    for vin, vehicle in new_active.items():
        if vin not in prev_active:
            events.append(
                ListingEvent(
                    event_id=new_id("evt"),
                    vin=vin,
                    target_id=payload.target_id,
                    event_type="listing_appeared",
                    event_at=payload.scanned_at,
                    scan_id=payload.scan_id,
                    details={
                        "listing_id": vehicle.listing_id,
                        "price_cad": vehicle.price_cad,
                    },
                )
            )
            continue

        prev = prev_active[vin]
        prev_price = normalize_optional_str(prev.price_cad)
        new_price = normalize_optional_str(vehicle.price_cad)
        if prev_price is not None and new_price is not None and prev_price != new_price:
            events.append(
                ListingEvent(
                    event_id=new_id("evt"),
                    vin=vin,
                    target_id=payload.target_id,
                    event_type="price_changed",
                    event_at=payload.scanned_at,
                    scan_id=payload.scan_id,
                    details={"old_price_cad": prev_price, "new_price_cad": new_price},
                )
            )

    if payload.scan_complete:
        for vin, vehicle in new_removed.items():
            events.append(
                ListingEvent(
                    event_id=new_id("evt"),
                    vin=vin,
                    target_id=payload.target_id,
                    event_type="listing_removed",
                    event_at=payload.scanned_at,
                    scan_id=payload.scan_id,
                    details={
                        "last_seen_at": vehicle.last_seen_at,
                        "last_price_cad": vehicle.price_cad,
                    },
                )
            )

    logger.debug("Generated %d listing events for scan %s", len(events), payload.scan_id)
    return events
