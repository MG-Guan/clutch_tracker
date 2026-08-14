"""Tests for the local web UI API."""

from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path

from clutch_tracker.cli import build_parser
from clutch_tracker.web import create_app


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _client(project_copy: Path):
    return create_app(project_copy).test_client()


def _scan_payload() -> dict:
    with (FIXTURES / "scan_complete.json").open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_serve_command_is_registered():
    parser = build_parser()
    args = parser.parse_args(["serve", "--port", "9000"])
    assert args.command == "serve"
    assert args.port == 9000
    assert args.host == "127.0.0.1"


def test_index_serves_html(project_copy: Path):
    client = _client(project_copy)
    response = client.get("/")
    assert response.status_code == 200
    assert b"Clutch Tracker" in response.data
    assert b"btn-restart" in response.data
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_list_targets(project_copy: Path):
    response = _client(project_copy).get("/api/targets")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    ids = [row["target_id"] for row in payload["targets"]]
    assert ids == ["ford-f150-ontario"]


def test_show_unknown_target_returns_404(project_copy: Path):
    response = _client(project_copy).get("/api/targets/does-not-exist")
    assert response.status_code == 404
    assert "Unknown target_id" in response.get_json()["error"]


def test_show_target_returns_search_criteria(project_copy: Path):
    response = _client(project_copy).get("/api/targets/ford-f150-ontario")
    assert response.status_code == 200
    target = response.get_json()["target"]
    assert target["target_id"] == "ford-f150-ontario"
    assert "model_search_terms" in target["criteria"]


def test_import_scan_and_inventory(project_copy: Path):
    client = _client(project_copy)
    scan = _scan_payload()
    imported = client.post("/api/scans", json=scan)
    assert imported.status_code == 200
    summary = imported.get_json()["summary"]
    assert summary["scan_id"] == "scan_test_001"
    assert summary["observations_added"] == 2

    inventory = client.get("/api/targets/ford-f150-ontario/inventory")
    assert inventory.status_code == 200
    vehicles = inventory.get_json()["vehicles"]
    assert len(vehicles) == 2
    assert {v["vin"] for v in vehicles} == {"1FTFW1E50NFA12345", "2C3CCAAG5JH123456"}

    scans = client.get("/api/scans")
    names = [row["name"] for row in scans.get_json()["scans"]]
    assert "scan_test_001.json" in names


def test_import_scan_file_upload(project_copy: Path):
    client = _client(project_copy)
    raw = json.dumps(_scan_payload()).encode("utf-8")
    response = client.post(
        "/api/scans",
        data={"file": (BytesIO(raw), "scan_complete.json")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert response.get_json()["summary"]["vehicles_scanned"] == 2


def test_import_scan_rejects_invalid_json(project_copy: Path):
    response = _client(project_copy).post("/api/scans", json={"scan_id": "missing-fields"})
    assert response.status_code == 400
    assert response.get_json()["ok"] is False


def test_generate_report_and_recommendations(project_copy: Path):
    client = _client(project_copy)
    client.post("/api/scans", json=_scan_payload())

    daily = client.post("/api/actions/generate-report", json={"target_id": "ford-f150-ontario"})
    assert daily.status_code == 200
    daily_body = daily.get_json()
    assert daily_body["name"].endswith(".md")
    assert "Daily Report" in daily_body["content"]

    listed = client.get("/api/targets/ford-f150-ontario/reports/daily")
    assert listed.get_json()["reports"][0]["name"] == daily_body["name"]

    recs = client.post(
        "/api/actions/generate-recommendations",
        json={"target_id": "ford-f150-ontario", "top_n": 2},
    )
    assert recs.status_code == 200
    assert "Recommendations" in recs.get_json()["content"]


def test_report_path_traversal_rejected(project_copy: Path):
    response = _client(project_copy).get("/api/targets/ford-f150-ontario/reports/daily/..secrets.md")
    assert response.status_code == 400


def test_validate_and_initialize_and_doctor(project_copy: Path):
    client = _client(project_copy)
    config = client.post("/api/actions/validate-config")
    assert config.status_code == 200
    assert config.get_json()["ok"] is True

    initialized = client.post("/api/actions/initialize-targets")
    assert initialized.status_code == 200
    assert "ford-f150-ontario" in initialized.get_json()["initialized"]

    repo = client.post("/api/actions/validate-repository")
    assert repo.status_code == 200

    doctor = client.get("/api/doctor")
    assert doctor.status_code == 200
    body = doctor.get_json()
    assert "python" in body
    assert "warnings" in body


def test_overview_totals(project_copy: Path):
    client = _client(project_copy)
    client.post("/api/scans", json=_scan_payload())
    overview = client.get("/api/overview")
    totals = overview.get_json()["totals"]
    assert totals["targets"] == 1
    assert totals["active_listings"] == 2
    assert totals["tracked_vins"] == 2


def test_restart_skipped_outside_live_serve(project_copy: Path):
    response = _client(project_copy).post("/api/actions/restart")
    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["scheduled"] is False


def test_restart_invokes_hook_for_live_serve(project_copy: Path):
    called: list[bool] = []
    app = create_app(project_copy)
    app.config["CLUTCH_ALLOW_RESTART"] = True
    app.config["CLUTCH_RESTART_HOOK"] = lambda: called.append(True)
    response = app.test_client().post("/api/actions/restart")
    assert response.status_code == 200
    assert response.get_json()["scheduled"] is True
    assert called == [True]


def test_restart_argv_keeps_serve_command(monkeypatch):
    from clutch_tracker.web import restart_argv

    monkeypatch.setattr(sys, "argv", ["clutch-tracker", "serve", "--port", "9000"])
    argv = restart_argv("127.0.0.1", 9000)
    assert argv[1:] == ["clutch-tracker", "serve", "--port", "9000"]
    assert "serve" in argv


def test_observations_and_events_pagination(project_copy: Path):
    client = _client(project_copy)
    client.post("/api/scans", json=_scan_payload())
    observations = client.get("/api/targets/ford-f150-ontario/observations?limit=1")
    body = observations.get_json()
    assert body["total"] == 2
    assert len(body["observations"]) == 1

    events = client.get("/api/targets/ford-f150-ontario/events")
    assert events.get_json()["total"] >= 1
