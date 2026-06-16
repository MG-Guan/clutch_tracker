"""Command-line interface for clutch_tracker."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from clutch_tracker.comparison import import_scan
from clutch_tracker.config import load_settings, load_targets, project_root
from clutch_tracker.criteria import build_search_criteria, format_criteria_summary
from clutch_tracker.doctor import run_doctor
from clutch_tracker.reporting import generate_daily_report
from clutch_tracker.target_manager import initialize_targets
from clutch_tracker.validation import validate_config, validate_repository


def _configure_logging(root: Path) -> None:
    settings = load_settings(root)
    log_cfg = settings.get("logging", {})
    level_name = log_cfg.get("level", "INFO")
    level = getattr(logging, level_name.upper(), logging.INFO)
    fmt = log_cfg.get("format", "%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.basicConfig(level=level, format=fmt)


def cmd_list_targets(root: Path) -> int:
    targets = load_targets(root)
    for target in targets:
        status = "enabled" if target.enabled else "disabled"
        criteria = format_criteria_summary(target.criteria)
        print(f"{target.target_id}\t{target.label}\t{status}\t{criteria}")
    return 0


def cmd_show_target(root: Path, target_id: str) -> int:
    targets = {t.target_id: t for t in load_targets(root)}
    if target_id not in targets:
        print(f"ERROR: Unknown target_id: {target_id}", file=sys.stderr)
        return 1
    print(json.dumps(build_search_criteria(targets[target_id]), indent=2))
    return 0


def cmd_validate_config(root: Path) -> int:
    result = validate_config(root)
    for warning in result.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    if result.errors:
        for error in result.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Configuration is valid.")
    return 0


def cmd_initialize_targets(root: Path) -> int:
    initialized = initialize_targets(root)
    print(f"Initialized {len(initialized)} target(s): {', '.join(initialized)}")
    return 0


def cmd_import_scan(root: Path, scan_path: Path) -> int:
    if not scan_path.exists():
        print(f"ERROR: Scan file not found: {scan_path}", file=sys.stderr)
        return 1
    with scan_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    summary = import_scan(root, data)
    print(json.dumps(summary, indent=2))
    return 0


def cmd_generate_report(root: Path, target_id: str) -> int:
    targets = {t.target_id for t in load_targets(root)}
    if target_id not in targets:
        print(f"ERROR: Unknown target_id: {target_id}", file=sys.stderr)
        return 1
    report_path = generate_daily_report(root, target_id)
    print(f"Report written to {report_path}")
    return 0


def cmd_validate_repository(root: Path) -> int:
    result = validate_repository(root)
    for warning in result.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    if result.errors:
        for error in result.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Repository is valid.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clutch_tracker", description="Clutch.ca vehicle target tracker")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-targets", help="List configured targets with search criteria")
    sub.add_parser("doctor", help="Diagnose Python environment and package install")
    sub.add_parser("validate-config", help="Validate configuration files")
    sub.add_parser("initialize-targets", help="Initialize target data directories")
    sub.add_parser("validate-repository", help="Validate repository data integrity")

    show_parser = sub.add_parser("show-target", help="Show search criteria JSON for a target")
    show_parser.add_argument("target_id", help="Target ID")

    import_parser = sub.add_parser("import-scan", help="Import a browser scan JSON file")
    import_parser.add_argument("scan_json", type=Path, help="Path to scan JSON file")

    report_parser = sub.add_parser("generate-report", help="Generate daily report for a target")
    report_parser.add_argument("--target", required=True, dest="target_id", help="Target ID")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = project_root()

    _configure_logging(root)

    commands = {
        "list-targets": lambda: cmd_list_targets(root),
        "doctor": lambda: run_doctor(),
        "show-target": lambda: cmd_show_target(root, args.target_id),
        "validate-config": lambda: cmd_validate_config(root),
        "initialize-targets": lambda: cmd_initialize_targets(root),
        "import-scan": lambda: cmd_import_scan(root, args.scan_json),
        "generate-report": lambda: cmd_generate_report(root, args.target_id),
        "validate-repository": lambda: cmd_validate_repository(root),
    }
    return commands[args.command]()


if __name__ == "__main__":
    raise SystemExit(main())
