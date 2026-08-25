"""Tests for the local serve-bound scheduler."""

from __future__ import annotations

import json
from pathlib import Path

from clutch_tracker.scheduler import LocalScheduler
from clutch_tracker.web import create_app

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _scan_payload() -> dict:
    with (FIXTURES / "scan_complete.json").open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_schedule_status_inactive_without_scheduler(project_copy: Path):
    client = create_app(project_copy).test_client()
    response = client.get("/api/schedule")
    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["active"] is False


def test_manual_schedule_cycle_generates_recommendations(project_copy: Path):
    scheduler = LocalScheduler(project_copy, interval_seconds=10_000, run_on_startup=False)
    app = create_app(project_copy, scheduler=scheduler)
    client = app.test_client()

    imported = client.post("/api/scans", json=_scan_payload())
    assert imported.status_code == 200

    response = client.post("/api/actions/run-schedule")
    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["finished"] is True
    cycle = body["cycle"]
    assert cycle["reason"] == "manual"
    assert cycle["targets"][0]["target_id"] == "ford-f150-ontario"
    assert cycle["targets"][0]["recommendations_path"]
    assert cycle["targets"][0]["daily_report_path"]
    assert cycle["targets"][0]["scan_requested"] is True

    status = client.get("/api/schedule").get_json()
    assert len(status["pending_scans"]) == 1
    assert status["pending_scans"][0]["target_id"] == "ford-f150-ontario"

    # Re-import clears pending scan for that target.
    payload = _scan_payload()
    payload["scan_id"] = "scan_after_schedule"
    client.post("/api/scans", json=payload)
    status = client.get("/api/schedule").get_json()
    assert status["pending_scans"] == []


def test_run_schedule_rejected_without_live_scheduler(project_copy: Path):
    client = create_app(project_copy).test_client()
    response = client.post("/api/actions/run-schedule")
    assert response.status_code == 400
    assert response.get_json()["ok"] is False


def test_overview_includes_schedule(project_copy: Path):
    scheduler = LocalScheduler(project_copy, interval_seconds=10_000)
    scheduler.start()
    try:
        client = create_app(project_copy, scheduler=scheduler).test_client()
        body = client.get("/api/overview").get_json()
        assert body["schedule"]["active"] is True
        assert body["schedule"]["interval_seconds"] == 10_000
    finally:
        scheduler.stop()


def test_trigger_scan_marks_pending_and_returns_search_url(project_copy: Path, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(
        "clutch_tracker.scheduler.try_open_url",
        lambda url: opened.append(url) or True,
    )
    scheduler = LocalScheduler(project_copy, interval_seconds=10_000)
    client = create_app(project_copy, scheduler=scheduler).test_client()

    response = client.post(
        "/api/actions/trigger-scan",
        json={"target_id": "ford-f150-ontario", "open_browser": True},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["scan"]["target_id"] == "ford-f150-ontario"
    assert body["scan"]["search_url"]
    assert body["scan"]["opened_locally"] is True
    assert opened == [body["scan"]["search_url"]]

    status = client.get("/api/schedule").get_json()
    assert any(s["target_id"] == "ford-f150-ontario" for s in status["pending_scans"])


def test_trigger_scan_works_without_scheduler(project_copy: Path, monkeypatch):
    monkeypatch.setattr("clutch_tracker.web.try_open_url", lambda url: False)
    client = create_app(project_copy).test_client()
    response = client.post(
        "/api/actions/trigger-scan",
        json={"target_id": "ford-f150-ontario", "open_browser": False},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["scan"]["search_url"]
    assert body["scan"]["opened_locally"] is False
