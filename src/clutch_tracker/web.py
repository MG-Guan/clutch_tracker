"""Local web UI for operating clutch_tracker from a browser."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, current_app, jsonify, request, send_from_directory

from clutch_tracker.comparison import import_scan
from clutch_tracker.config import load_settings, load_targets, project_root
from clutch_tracker.criteria import build_search_criteria, format_criteria_summary
from clutch_tracker.doctor import collect_doctor_report
from clutch_tracker.recommendations import generate_recommendations_report
from clutch_tracker.reporting import generate_daily_report
from clutch_tracker.storage import (
    events_path,
    load_current_inventory,
    load_vehicles,
    observations_path,
    raw_scan_archive_path,
    read_csv_rows,
)
from clutch_tracker.target_manager import initialize_targets
from clutch_tracker.validation import ValidationResult, validate_config, validate_repository

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


class UnknownTargetError(LookupError):
    """Raised when a target_id is not in config."""


def create_app(root: Path | None = None) -> Flask:
    """Create the local operations Flask app bound to a project root."""
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
    app.config["CLUTCH_ROOT"] = project_root(root)
    app.config["CLUTCH_ALLOW_RESTART"] = False

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/api/overview")
    def api_overview():
        return jsonify(_overview(_root()))

    @app.get("/api/targets")
    def api_list_targets():
        return jsonify({"ok": True, "targets": _list_targets(_root())})

    @app.get("/api/targets/<target_id>")
    def api_show_target(target_id: str):
        target = _require_target(_root(), target_id)
        return jsonify({"ok": True, "target": build_search_criteria(target)})

    @app.get("/api/targets/<target_id>/inventory")
    def api_inventory(target_id: str):
        _require_target(_root(), target_id)
        inventory = load_current_inventory(_root(), target_id)
        if inventory is None:
            return jsonify(
                {
                    "ok": True,
                    "target_id": target_id,
                    "updated_at": None,
                    "scan_id": None,
                    "scan_complete": False,
                    "vehicles": [],
                }
            )
        return jsonify(
            {
                "ok": True,
                "target_id": inventory.target_id,
                "updated_at": inventory.updated_at,
                "scan_id": inventory.scan_id,
                "scan_complete": inventory.scan_complete,
                "vehicles": [asdict(v) for v in inventory.vehicles],
            }
        )

    @app.get("/api/targets/<target_id>/vehicles")
    def api_vehicles(target_id: str):
        _require_target(_root(), target_id)
        vehicles = load_vehicles(_root(), target_id)
        rows = [asdict(v) for v in sorted(vehicles.values(), key=lambda x: x.vin)]
        return jsonify({"ok": True, "target_id": target_id, "vehicles": rows})

    @app.get("/api/targets/<target_id>/observations")
    def api_observations(target_id: str):
        _require_target(_root(), target_id)
        rows = read_csv_rows(observations_path(_root(), target_id))
        return jsonify(_paginated("observations", rows))

    @app.get("/api/targets/<target_id>/events")
    def api_events(target_id: str):
        _require_target(_root(), target_id)
        rows = read_csv_rows(events_path(_root(), target_id))
        parsed: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            raw_details = item.get("details_json") or ""
            if raw_details:
                try:
                    item["details"] = json.loads(raw_details)
                except json.JSONDecodeError:
                    item["details"] = raw_details
            else:
                item["details"] = {}
            parsed.append(item)
        return jsonify(_paginated("events", parsed))

    @app.get("/api/targets/<target_id>/reports/<kind>")
    def api_list_reports(target_id: str, kind: str):
        _require_target(_root(), target_id)
        directory = _report_dir(_root(), target_id, kind)
        return jsonify({"ok": True, "target_id": target_id, "kind": kind, "reports": _list_markdown(directory)})

    @app.get("/api/targets/<target_id>/reports/<kind>/<name>")
    def api_read_report(target_id: str, kind: str, name: str):
        _require_target(_root(), target_id)
        path = _report_dir(_root(), target_id, kind) / _safe_filename(name, ".md")
        if not path.is_file():
            return jsonify({"ok": False, "error": f"Report not found: {name}"}), 404
        return jsonify({"ok": True, "name": path.name, "content": path.read_text(encoding="utf-8")})

    @app.get("/api/scans")
    def api_list_scans():
        scan_dir = _root() / "data" / "raw_scans"
        files: list[dict[str, Any]] = []
        if scan_dir.is_dir():
            for path in sorted(scan_dir.glob("*.json"), key=lambda p: p.name, reverse=True):
                files.append(_file_info(path))
        return jsonify({"ok": True, "scans": files})

    @app.get("/api/scans/<scan_id>")
    def api_read_scan(scan_id: str):
        path = raw_scan_archive_path(_root(), _safe_scan_id(scan_id))
        if not path.is_file():
            return jsonify({"ok": False, "error": f"Scan not found: {scan_id}"}), 404
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return jsonify({"ok": True, "scan_id": scan_id, "path": str(path.relative_to(_root())), "payload": payload})

    @app.post("/api/scans")
    def api_import_scan():
        try:
            data = _read_scan_payload()
            summary = import_scan(_root(), data)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "summary": summary})

    @app.post("/api/actions/validate-config")
    def api_validate_config():
        return jsonify(_validation_payload(validate_config(_root())))

    @app.post("/api/actions/validate-repository")
    def api_validate_repository():
        return jsonify(_validation_payload(validate_repository(_root())))

    @app.post("/api/actions/initialize-targets")
    def api_initialize_targets():
        initialized = initialize_targets(_root())
        return jsonify({"ok": True, "initialized": initialized})

    @app.post("/api/actions/generate-report")
    def api_generate_report():
        body = request.get_json(silent=True) or {}
        target_id = body.get("target_id") or request.args.get("target_id")
        if not target_id:
            return jsonify({"ok": False, "error": "target_id is required"}), 400
        _require_target(_root(), str(target_id))
        report_path = generate_daily_report(_root(), str(target_id))
        return jsonify(
            {
                "ok": True,
                "path": str(report_path.relative_to(_root())),
                "name": report_path.name,
                "content": report_path.read_text(encoding="utf-8"),
            }
        )

    @app.post("/api/actions/generate-recommendations")
    def api_generate_recommendations():
        body = request.get_json(silent=True) or {}
        target_id = body.get("target_id") or request.args.get("target_id")
        if not target_id:
            return jsonify({"ok": False, "error": "target_id is required"}), 400
        _require_target(_root(), str(target_id))
        top_n = body.get("top_n", 3)
        try:
            top_n_int = int(top_n)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "top_n must be an integer"}), 400
        if top_n_int < 1:
            return jsonify({"ok": False, "error": "top_n must be >= 1"}), 400
        report_path = generate_recommendations_report(_root(), str(target_id), top_n=top_n_int)
        return jsonify(
            {
                "ok": True,
                "path": str(report_path.relative_to(_root())),
                "name": report_path.name,
                "content": report_path.read_text(encoding="utf-8"),
            }
        )

    @app.get("/api/doctor")
    def api_doctor():
        report = collect_doctor_report()
        return jsonify({"ok": True, **report})

    @app.post("/api/actions/restart")
    def api_restart():
        scheduled = schedule_restart(current_app._get_current_object())
        if not scheduled:
            return jsonify(
                {
                    "ok": True,
                    "scheduled": False,
                    "error": "Not a live serve process; restart is only available from clutch-tracker serve.",
                }
            )
        return jsonify({"ok": True, "scheduled": True})

    @app.errorhandler(UnknownTargetError)
    def handle_unknown_target(exc: UnknownTargetError):
        return jsonify({"ok": False, "error": str(exc)}), 404

    @app.errorhandler(ValueError)
    def handle_value_error(exc: ValueError):
        return jsonify({"ok": False, "error": str(exc)}), 400

    return app


def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, root: Path | None = None) -> None:
    """Start the local web UI (development server, intended for localhost use)."""
    app = create_app(root)
    root_path = project_root(root)
    app.config["CLUTCH_ALLOW_RESTART"] = True
    app.config["CLUTCH_RESTART_ARGV"] = restart_argv(host, port)
    app.config["CLUTCH_RESTART_CWD"] = str(root_path)
    url = f"http://{host}:{port}/"
    logger.info("Starting clutch_tracker web UI at %s", url)
    print(f"Clutch Tracker UI: {url}")
    app.run(host=host, port=port, threaded=True, use_reloader=False)


def restart_argv(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> list[str]:
    """Build argv that re-launches this serve process."""
    if len(sys.argv) >= 2 and "serve" in sys.argv:
        return [sys.executable, *sys.argv]
    return [
        sys.executable,
        "-m",
        "clutch_tracker",
        "serve",
        "--host",
        host,
        "--port",
        str(port),
    ]


def schedule_restart(app: Flask, *, delay_s: float = 0.6) -> bool:
    """Restart the live serve process after the HTTP response is flushed."""
    if not app.config.get("CLUTCH_ALLOW_RESTART"):
        return False
    hook = app.config.get("CLUTCH_RESTART_HOOK")
    if hook is not None:
        hook()
        return True

    argv = list(app.config.get("CLUTCH_RESTART_ARGV") or restart_argv())
    cwd = app.config.get("CLUTCH_RESTART_CWD")

    def _restart() -> None:
        logger.info("Restarting serve process: %s", argv)
        if cwd:
            os.chdir(str(cwd))
        os.execv(argv[0], argv)

    threading.Timer(delay_s, _restart).start()
    return True


def _root() -> Path:
    return Path(current_app.config["CLUTCH_ROOT"])


def _require_target(root: Path, target_id: str):
    targets = {t.target_id: t for t in load_targets(root)}
    if target_id not in targets:
        raise UnknownTargetError(f"Unknown target_id: {target_id}")
    return targets[target_id]


def _list_targets(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for target in load_targets(root):
        inventory = load_current_inventory(root, target.target_id)
        vehicles = load_vehicles(root, target.target_id)
        active = sum(1 for v in (inventory.vehicles if inventory else []) if v.status == "active")
        removed = sum(1 for v in (inventory.vehicles if inventory else []) if v.status == "removed")
        rows.append(
            {
                "target_id": target.target_id,
                "label": target.label,
                "enabled": target.enabled,
                "criteria_summary": format_criteria_summary(target.criteria),
                "active_listings": active,
                "removed_listings": removed,
                "tracked_vins": len(vehicles),
                "last_scan_id": inventory.scan_id if inventory else None,
                "last_scan_complete": inventory.scan_complete if inventory else None,
                "inventory_updated_at": inventory.updated_at if inventory else None,
            }
        )
    return rows


def _overview(root: Path) -> dict[str, Any]:
    targets = _list_targets(root)
    settings = load_settings(root)
    return {
        "ok": True,
        "timezone": settings.get("timezone", "America/Toronto"),
        "totals": {
            "targets": len(targets),
            "enabled": sum(1 for t in targets if t["enabled"]),
            "active_listings": sum(t["active_listings"] for t in targets),
            "removed_listings": sum(t["removed_listings"] for t in targets),
            "tracked_vins": sum(t["tracked_vins"] for t in targets),
        },
        "targets": targets,
    }


def _validation_payload(result: ValidationResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "errors": result.errors,
        "warnings": result.warnings,
        "message": "Configuration is valid." if result.ok and not result.errors else None,
    }


def _paginated(key: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    try:
        limit = int(request.args.get("limit", 200))
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError):
        raise ValueError("limit and offset must be integers") from None
    if limit < 1 or limit > 5000:
        raise ValueError("limit must be between 1 and 5000")
    if offset < 0:
        raise ValueError("offset must be >= 0")
    slice_rows = rows[offset : offset + limit]
    return {"ok": True, "total": total, "limit": limit, "offset": offset, key: slice_rows}


def _report_dir(root: Path, target_id: str, kind: str) -> Path:
    if kind == "daily":
        return root / "reports" / "daily" / target_id
    if kind == "recommendations":
        return root / "reports" / "recommendations" / target_id
    raise ValueError("kind must be 'daily' or 'recommendations'")


def _list_markdown(directory: Path) -> list[dict[str, Any]]:
    if not directory.is_dir():
        return []
    return [_file_info(path) for path in sorted(directory.glob("*.md"), key=lambda p: p.name, reverse=True)]


def _file_info(path: Path) -> dict[str, Any]:
    stat = path.stat()
    modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds")
    return {
        "name": path.name,
        "size_bytes": stat.st_size,
        "modified_at": modified,
    }


def _safe_filename(name: str, suffix: str) -> str:
    if not name or "/" in name or "\\" in name or ".." in name:
        raise ValueError("Invalid file name")
    if not name.endswith(suffix):
        raise ValueError(f"File name must end with {suffix}")
    return name


def _safe_scan_id(scan_id: str) -> str:
    if not scan_id or "/" in scan_id or "\\" in scan_id or ".." in scan_id:
        raise ValueError("Invalid scan_id")
    return scan_id


def _read_scan_payload() -> dict[str, Any]:
    uploaded = request.files.get("file")
    if uploaded is not None:
        raw = uploaded.read()
        return json.loads(raw.decode("utf-8"))
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("Scan payload must be a JSON object or an uploaded JSON file")
    return data
