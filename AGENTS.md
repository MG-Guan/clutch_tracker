# Agent Instructions

This document guides AI agents working on `clutch_tracker`.

## Source of Truth

- **GitHub repository files** are the authoritative historical database.
- Never use agent memory as a substitute for on-disk vehicle history.
- All tracked vehicle criteria must come from `config/targets.yaml`.
- Do not hard-code specific vehicles (make/model/year) in Python code.

## Architecture

```
Browser Agent (raw scan JSON)
        │
        ▼
import-scan CLI  ──►  data/raw_scans/<scan_id>.json  (archive)
        │
        ▼
Deterministic Python (comparison.py, events.py, storage.py)
        │
        ├── data/targets/<target_id>/vehicles.csv       (VIN registry)
        ├── data/targets/<target_id>/observations.csv   (append-only)
        ├── data/targets/<target_id>/listing_events.csv (append-only)
        └── data/targets/<target_id>/current_inventory.json
```

## Key Rules

1. **VIN is the permanent primary key** for every vehicle.
2. **Each target has a unique `target_id`** defined in `config/targets.yaml`.
3. **Observations are append-only** — never overwrite or delete historical rows.
4. **Partial scans** (`scan_complete: false`) must NOT mark missing vehicles as removed.
5. **Unknown values stay unknown** — do not guess or infer missing fields.
6. **Atomic file writes** — all persistence uses temp-file + rename.
7. **Timestamps** — ISO 8601 in `America/Toronto` timezone.

## CLI Commands

```bash
python -m clutch_tracker.cli list-targets
python -m clutch_tracker.cli show-target <target_id>
python -m clutch_tracker.cli validate-config
python -m clutch_tracker.cli initialize-targets
python -m clutch_tracker.cli import-scan <scan-json>
python -m clutch_tracker.cli generate-report --target <target_id>
python -m clutch_tracker.cli validate-repository
```

## Model search in targets.yaml

Use `make`, `model`, and optional `model_aliases` to track a specific vehicle line (e.g. F-150):

```yaml
criteria:
  make: Ford
  model: F-150
  model_aliases:
    - F150
    - F 150
  search_query: "Ford F-150"
```

`show-target` returns `model_search_terms` — the deduplicated list of model strings agents should match.

## Scan JSON Format

```json
{
  "target_id": "ford-f150-ontario",
  "scan_id": "scan_20260616_001",
  "scanned_at": "2026-06-16T10:00:00-04:00",
  "scan_complete": true,
  "vehicles": [
    {
      "vin": "1FTFW1E50NFA12345",
      "listing_id": "clutch-123",
      "year": 2022,
      "make": "Ford",
      "model": "F-150",
      "price_cad": 45000,
      "mileage_km": 32000
    }
  ]
}
```

## Data Layout

```
data/targets/<target_id>/
├── vehicles.csv
├── observations.csv
├── listing_events.csv
└── current_inventory.json

reports/daily/<target_id>/
snapshots/<target_id>/
```

## What Agents Should NOT Do

- Do not scrape Clutch.ca directly from Python (browser agents collect raw data).
- Do not modify historical observations or events.
- Do not hard-code vehicle criteria in Python.
- Do not treat partial scans as complete inventory snapshots.
