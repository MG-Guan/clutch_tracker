"""In-process schedule that runs while `clutch-tracker serve` is alive."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from clutch_tracker.config import get_timezone_name, load_settings, load_targets, project_root
from clutch_tracker.criteria import build_search_criteria
from clutch_tracker.recommendations import generate_recommendations_report
from clutch_tracker.reporting import generate_daily_report
from clutch_tracker.storage import now_iso

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_HOURS = 3


@dataclass
class TargetCycleResult:
    target_id: str
    recommendations_path: str | None = None
    daily_report_path: str | None = None
    scan_requested: bool = False
    search_url: str | None = None
    error: str | None = None


@dataclass
class CycleResult:
    started_at: str
    finished_at: str
    reason: str
    targets: list[TargetCycleResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(t.error is None for t in self.targets)


class LocalScheduler:
    """Background scheduler bound to the serve process lifetime."""

    def __init__(
        self,
        root: Path,
        *,
        interval_seconds: float | None = None,
        run_on_startup: bool | None = None,
    ) -> None:
        self.root = project_root(root)
        settings = load_settings(self.root)
        schedule = settings.get("schedule") or {}
        if not isinstance(schedule, dict):
            schedule = {}

        hours = schedule.get("interval_hours", DEFAULT_INTERVAL_HOURS)
        try:
            hours_f = float(hours)
        except (TypeError, ValueError):
            hours_f = float(DEFAULT_INTERVAL_HOURS)
        if hours_f <= 0:
            hours_f = float(DEFAULT_INTERVAL_HOURS)

        self.interval_seconds = (
            float(interval_seconds) if interval_seconds is not None else hours_f * 3600.0
        )
        self.enabled = bool(schedule.get("enabled", True))
        self.generate_recommendations = bool(schedule.get("generate_recommendations", True))
        self.generate_daily_report = bool(schedule.get("generate_daily_report", True))
        self.request_browser_scan = bool(schedule.get("request_browser_scan", True))
        self.open_search_url = bool(schedule.get("open_search_url", False))
        if run_on_startup is None:
            self.run_on_startup = bool(schedule.get("run_on_startup", False))
        else:
            self.run_on_startup = bool(run_on_startup)

        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running_cycle = False
        self.started_at: str | None = None
        self.next_run_at: str | None = None
        self.last_cycle: CycleResult | None = None
        self.pending_scans: dict[str, dict[str, Any]] = {}

    def start(self) -> None:
        if not self.enabled:
            logger.info("Local scheduler disabled in settings")
            return
        if self._thread and self._thread.is_alive():
            return
        self.started_at = now_iso(self.root)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="clutch-local-scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Local scheduler started (interval=%.0fs, run_on_startup=%s)",
            self.interval_seconds,
            self.run_on_startup,
        )

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict[str, Any]:
        with self._lock:
            last = None
            if self.last_cycle is not None:
                last = {
                    "started_at": self.last_cycle.started_at,
                    "finished_at": self.last_cycle.finished_at,
                    "reason": self.last_cycle.reason,
                    "ok": self.last_cycle.ok,
                    "targets": [
                        {
                            "target_id": t.target_id,
                            "recommendations_path": t.recommendations_path,
                            "daily_report_path": t.daily_report_path,
                            "scan_requested": t.scan_requested,
                            "search_url": t.search_url,
                            "error": t.error,
                        }
                        for t in self.last_cycle.targets
                    ],
                }
            return {
                "enabled": self.enabled,
                "active": bool(self._thread and self._thread.is_alive() and not self._stop.is_set()),
                "interval_seconds": self.interval_seconds,
                "interval_hours": self.interval_seconds / 3600.0,
                "run_on_startup": self.run_on_startup,
                "generate_recommendations": self.generate_recommendations,
                "generate_daily_report": self.generate_daily_report,
                "request_browser_scan": self.request_browser_scan,
                "open_search_url": self.open_search_url,
                "started_at": self.started_at,
                "next_run_at": self.next_run_at,
                "running_cycle": self._running_cycle,
                "pending_scans": list(self.pending_scans.values()),
                "last_cycle": last,
            }

    def note_scan_imported(self, target_id: str) -> None:
        with self._lock:
            self.pending_scans.pop(target_id, None)

    def request_scan(
        self,
        target_id: str,
        *,
        reason: str = "manual",
        open_browser: bool = True,
    ) -> dict[str, Any]:
        """Mark a browser scan as due and optionally open the Clutch search URL."""
        target = next((t for t in load_targets(self.root) if t.target_id == target_id), None)
        if target is None:
            raise LookupError(f"Unknown target_id: {target_id}")

        criteria = build_search_criteria(target)
        search_url = criteria.get("search_url")
        if not isinstance(search_url, str) or not search_url.strip():
            search_url = None

        requested_at = now_iso(self.root)
        with self._lock:
            self.pending_scans[target_id] = {
                "target_id": target_id,
                "label": target.label,
                "requested_at": requested_at,
                "reason": reason,
                "search_url": search_url,
                "note": "Browser agent must collect scan JSON; Python does not scrape Clutch.ca.",
            }

        opened = False
        if open_browser and search_url:
            opened = try_open_url(search_url)

        return {
            "target_id": target_id,
            "label": target.label,
            "requested_at": requested_at,
            "reason": reason,
            "search_url": search_url,
            "opened_locally": opened,
            "scan_requirements": criteria.get("scan_requirements"),
            "criteria": criteria.get("criteria"),
        }

    def run_cycle(self, *, reason: str = "scheduled") -> CycleResult:
        with self._lock:
            if self._running_cycle:
                raise RuntimeError("A schedule cycle is already running")
            self._running_cycle = True
        started = now_iso(self.root)
        results: list[TargetCycleResult] = []
        try:
            targets = [t for t in load_targets(self.root) if t.enabled]
            for target in targets:
                results.append(self._run_target(target.target_id, reason=reason))
            finished = now_iso(self.root)
            cycle = CycleResult(
                started_at=started,
                finished_at=finished,
                reason=reason,
                targets=results,
            )
            with self._lock:
                self.last_cycle = cycle
            logger.info(
                "Schedule cycle finished reason=%s targets=%d ok=%s",
                reason,
                len(results),
                cycle.ok,
            )
            return cycle
        finally:
            with self._lock:
                self._running_cycle = False

    def _run_target(self, target_id: str, *, reason: str) -> TargetCycleResult:
        result = TargetCycleResult(target_id=target_id)
        try:
            target = next(t for t in load_targets(self.root) if t.target_id == target_id)
            criteria = build_search_criteria(target)
            search_url = criteria.get("search_url")
            if isinstance(search_url, str) and search_url.strip():
                result.search_url = search_url

            if self.request_browser_scan:
                result.scan_requested = True
                with self._lock:
                    self.pending_scans[target_id] = {
                        "target_id": target_id,
                        "label": target.label,
                        "requested_at": now_iso(self.root),
                        "reason": reason,
                        "search_url": result.search_url,
                        "note": "Browser agent must collect scan JSON; Python does not scrape Clutch.ca.",
                    }
                if self.open_search_url and result.search_url:
                    try_open_url(result.search_url)

            if self.generate_recommendations:
                path = generate_recommendations_report(self.root, target_id)
                result.recommendations_path = str(path.relative_to(self.root))

            if self.generate_daily_report:
                path = generate_daily_report(self.root, target_id)
                result.daily_report_path = str(path.relative_to(self.root))
        except Exception as exc:  # noqa: BLE001 - surface per-target failure in status
            logger.exception("Schedule cycle failed for target %s", target_id)
            result.error = str(exc)
        return result

    def _loop(self) -> None:
        if self.run_on_startup:
            try:
                self.run_cycle(reason="startup")
            except Exception:  # noqa: BLE001
                logger.exception("Startup schedule cycle failed")

        while not self._stop.is_set():
            next_dt = _now_tz(self.root) + timedelta(seconds=self.interval_seconds)
            self.next_run_at = next_dt.isoformat(timespec="seconds")
            if self._stop.wait(self.interval_seconds):
                break
            try:
                self.run_cycle(reason="scheduled")
            except Exception:  # noqa: BLE001
                logger.exception("Scheduled cycle failed")


def _now_tz(root: Path) -> datetime:
    settings = load_settings(root)
    tz = ZoneInfo(get_timezone_name(settings))
    return datetime.now(tz)


def try_open_url(url: str) -> bool:
    """Best-effort open of a search URL on macOS/Linux (optional nudge)."""
    import shutil
    import subprocess
    import sys

    try:
        if sys.platform == "darwin" and shutil.which("open"):
            subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        if shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    except OSError as exc:
        logger.warning("Could not open search URL: %s", exc)
    return False
