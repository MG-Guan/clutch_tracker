# Agent Instructions

This document guides AI agents working on `clutch_tracker`.

## Source of Truth

- **Local SQLite database** (`data/clutch_tracker.db`, gitignored) is the authoritative
  historical store for vehicles, observations, events, inventory, and raw scans.
- Never use agent memory as a substitute for local vehicle history.
- Never commit the database or scan history into GitHub.
- All tracked vehicle criteria must come from `config/targets.yaml` (still in git).
- Do not hard-code specific vehicles (make/model/year) in Python code.

## Architecture

```
Browser Agent (raw scan JSON)
        │
        ▼
import-scan CLI  ──►  raw_scans table in data/clutch_tracker.db
        │
        ▼
Deterministic Python (comparison.py, events.py, storage.py / db.py)
        │
        ├── vehicles            (VIN registry)
        ├── observations        (append-only)
        ├── listing_events      (append-only)
        └── current_inventory   (latest snapshot JSON blob)
```

## Key Rules

1. **VIN is the permanent primary key** for every vehicle.
2. **Each target has a unique `target_id`** defined in `config/targets.yaml`.
3. **Observations are append-only** — never overwrite or delete historical rows.
4. **Partial scans** (`scan_complete: false`) must NOT mark missing vehicles as removed.
5. **Unknown values stay unknown** — do not guess or infer missing fields.
6. **Local DB is truth** — content lives in SQLite; config YAML stays in git.
7. **Timestamps** — ISO 8601 in `America/Toronto` timezone.
8. **Reports/snapshots** are generated artifacts under gitignored dirs (regenerable).

## CLI Commands

```bash
python -m clutch_tracker.cli list-targets
python -m clutch_tracker.cli show-target <target_id>
python -m clutch_tracker.cli validate-config
python -m clutch_tracker.cli initialize-targets
python -m clutch_tracker.cli import-scan <scan-json>
python -m clutch_tracker.cli generate-report --target <target_id>
python -m clutch_tracker.cli generate-recommendations --target <target_id>
python -m clutch_tracker.cli validate-repository
python -m clutch_tracker.cli serve
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

## Usage & Registration Disclosure

When a vehicle-history report is scanned, capture two extra disclosure signals so
they can be surfaced in recommendations (these are **disclosure only** and never
change recommendation eligibility):

- **Commercial previous use** — Carfax "Previous Use" values such as
  `Commercial`, `Fleet`, `Rental`, `Taxi`, `Government`, `Police`, etc.
- **Interprovincial history** — registration/renewal records across more than one
  Canadian province (e.g. British Columbia then Ontario).

Provide them inside the existing `vehicle_history_report` object (any of these
shapes is recognized):

```json
"vehicle_history_report": {
  "provider": "carfax",
  "status": "scanned",
  "previous_use": "Commercial",
  "registration_history": [
    { "date": "2022-06-24", "province": "British Columbia", "type": "Canadian Renewal" },
    { "date": "2024-01-11", "province": "Ontario", "type": "Canadian Renewal" }
  ]
}
```

Two-letter province codes (`BC`, `ON`, …) and province names in free-text
summaries are also detected. Unknown values stay `unknown` — never guess.

Resulting inventory fields: `previous_use`, `previous_use_details`,
`interprovincial_history`, `province_history`, `interprovincial_details`. The
recommendations report adds a `Usage/Registration` column plus a
**Commercial Use & Interprovincial Watchlist** section.

## Data Layout

```
data/clutch_tracker.db       # Local SQLite (gitignored) — all vehicle history
config/targets.yaml          # Search criteria (in git)
config/settings.yaml         # Timezone + storage.database_path

reports/daily/<target_id>/           # Generated markdown (gitignored)
reports/recommendations/<target_id>/ # Generated markdown (gitignored)
snapshots/<target_id>/               # Generated inventory snapshots (gitignored)
```

On first open, any leftover legacy CSV/JSON under `data/targets/` or `data/raw_scans/`
is imported into SQLite automatically once.

## What Agents Should NOT Do

- Do not scrape Clutch.ca directly from Python (browser agents collect raw data).
- Do not modify historical observations or events.
- Do not hard-code vehicle criteria in Python.
- Do not treat partial scans as complete inventory snapshots.
