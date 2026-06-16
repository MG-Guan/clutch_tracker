"""Domain models for clutch_tracker."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TargetCriteria:
    """Search criteria for a tracked target (loaded from config)."""

    body_style: str | None = None
    province: str | None = None
    min_year: int | None = None
    max_year: int | None = None
    make: str | None = None
    model: str | None = None
    model_aliases: tuple[str, ...] = ()
    search_query: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Target:
    """A configured vehicle search target."""

    target_id: str
    label: str
    enabled: bool
    criteria: TargetCriteria


@dataclass(frozen=True)
class VehicleRecord:
    """Permanent vehicle registry entry keyed by VIN."""

    vin: str
    target_id: str
    first_seen_at: str
    last_seen_at: str
    year: str | None = None
    make: str | None = None
    model: str | None = None
    trim: str | None = None
    listing_id: str | None = None
    listing_url: str | None = None


@dataclass(frozen=True)
class Observation:
    """Append-only observation of a vehicle at a point in time."""

    observation_id: str
    vin: str
    target_id: str
    observed_at: str
    scan_id: str
    price_cad: str | None = None
    mileage_km: str | None = None
    listing_url: str | None = None
    listing_id: str | None = None
    year: str | None = None
    make: str | None = None
    model: str | None = None
    trim: str | None = None
    raw_fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ListingEvent:
    """Lifecycle event for a vehicle listing."""

    event_id: str
    vin: str
    target_id: str
    event_type: str
    event_at: str
    scan_id: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScanVehicle:
    """Vehicle payload from a browser agent scan."""

    vin: str
    listing_id: str | None = None
    listing_url: str | None = None
    year: int | str | None = None
    make: str | None = None
    model: str | None = None
    trim: str | None = None
    price_cad: int | float | str | None = None
    mileage_km: int | float | str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScanPayload:
    """Raw scan JSON produced by a browser agent."""

    target_id: str
    scan_id: str
    scanned_at: str
    scan_complete: bool
    vehicles: list[ScanVehicle]
    source: str | None = None
    notes: str | None = None


@dataclass
class InventoryVehicle:
    """Current inventory snapshot entry."""

    vin: str
    last_seen_at: str
    listing_id: str | None = None
    listing_url: str | None = None
    year: str | None = None
    make: str | None = None
    model: str | None = None
    trim: str | None = None
    price_cad: str | None = None
    mileage_km: str | None = None
    status: str = "active"


@dataclass
class CurrentInventory:
    """Current inventory snapshot for a target."""

    target_id: str
    updated_at: str
    scan_id: str | None
    scan_complete: bool
    vehicles: list[InventoryVehicle]
