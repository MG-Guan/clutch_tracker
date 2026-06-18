"""Tests for storage and atomic writes."""

from __future__ import annotations

from pathlib import Path

from clutch_tracker.models import CurrentInventory, InventoryVehicle, Observation, VehicleRecord
from clutch_tracker.storage import (
    append_observations,
    atomic_write,
    load_current_inventory,
    load_vehicles,
    now_iso,
    observations_path,
    read_csv_rows,
    save_current_inventory,
    save_vehicles,
    vehicles_path,
)


def test_atomic_write_creates_file(tmp_path: Path):
    target = tmp_path / "nested" / "file.txt"
    atomic_write(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"


def test_now_iso_contains_timezone_offset(project_copy: Path):
    ts = now_iso(project_copy)
    assert "T" in ts
    assert "+" in ts or ts.endswith("Z") or "-" in ts[10:]


def test_append_observations_is_append_only(project_copy: Path):
    target_id = "ford-f150-ontario"
    obs1 = Observation(
        observation_id="obs_1",
        vin="VIN001",
        target_id=target_id,
        observed_at="2026-06-16T10:00:00-04:00",
        scan_id="scan_a",
    )
    obs2 = Observation(
        observation_id="obs_2",
        vin="VIN002",
        target_id=target_id,
        observed_at="2026-06-16T11:00:00-04:00",
        scan_id="scan_b",
    )
    append_observations(project_copy, [obs1])
    append_observations(project_copy, [obs2])

    rows = read_csv_rows(observations_path(project_copy, target_id))
    assert len(rows) == 2
    assert rows[0]["observation_id"] == "obs_1"
    assert rows[1]["observation_id"] == "obs_2"


def test_save_and_load_vehicles(project_copy: Path):
    target_id = "ford-f150-ontario"
    vehicles = {
        "VIN123": VehicleRecord(
            vin="VIN123",
            target_id=target_id,
            first_seen_at="2026-06-16T10:00:00-04:00",
            last_seen_at="2026-06-16T10:00:00-04:00",
            make="TestMake",
        )
    }
    save_vehicles(project_copy, target_id, vehicles)
    loaded = load_vehicles(project_copy, target_id)
    assert loaded["VIN123"].make == "TestMake"
    assert vehicles_path(project_copy, target_id).exists()


def test_save_and_load_inventory_accident_metadata(project_copy: Path):
    target_id = "ford-f150-ontario"
    inventory = CurrentInventory(
        target_id=target_id,
        updated_at="2026-06-16T10:00:00-04:00",
        scan_id="scan_accident_metadata",
        scan_complete=True,
        vehicles=[
            InventoryVehicle(
                vin="VINACCIDENT123",
                last_seen_at="2026-06-16T10:00:00-04:00",
                accident_history_status="reported",
                accident_severity="minor",
                accident_details="minor bumper repair",
                recommendation_eligible=True,
            )
        ],
    )

    save_current_inventory(project_copy, inventory)
    loaded = load_current_inventory(project_copy, target_id)

    assert loaded is not None
    vehicle = loaded.vehicles[0]
    assert vehicle.accident_history_status == "reported"
    assert vehicle.accident_severity == "minor"
    assert vehicle.accident_details == "minor bumper repair"
    assert vehicle.recommendation_eligible is True
