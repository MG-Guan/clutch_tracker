# clutch_tracker

Production-oriented Python project for tracking multiple vehicle search targets on [Clutch.ca](https://www.clutch.ca). Browser agents collect raw scan data; deterministic Python code maintains append-only history keyed by VIN.

## Architecture

```mermaid
flowchart TD
    YAML["config/targets.yaml\n(search criteria)"]
    Agent["Browser Agent\n(raw scan JSON)"]
    CLI["cli.py import-scan"]
    Archive["data/raw_scans/"]
    Engine["comparison.py + events.py"]
    Registry["vehicles.csv\n(VIN registry)"]
    Obs["observations.csv\n(append-only)"]
    Events["listing_events.csv\n(append-only)"]
    Inv["current_inventory.json\n(latest snapshot)"]
    Report["reporting.py"]
    Daily["reports/daily/&lt;target_id&gt;/"]
    Snap["snapshots/&lt;target_id&gt;/"]

    YAML --> Engine
    Agent --> CLI
    CLI --> Archive
    CLI --> Engine
    Engine --> Registry
    Engine --> Obs
    Engine --> Events
    Engine --> Inv
    Inv --> Report
    Report --> Daily
    Report --> Snap
```

### Design principles

| Rule | Implementation |
|------|----------------|
| No hard-coded vehicles | All criteria live in `config/targets.yaml` |
| Unique targets | Each entry has a `target_id`; duplicates rejected at validation |
| VIN as primary key | `vehicles.csv` registry keyed by VIN across all history |
| Append-only history | `observations.csv` and `listing_events.csv` are never overwritten |
| Partial scan safety | `scan_complete: false` never marks missing vehicles as removed |
| Unknown stays unknown | Missing fields remain empty/null; no inference |
| Repository is truth | Git-tracked files are the historical database, not agent memory |
| Atomic writes | Temp file + `os.replace()` for all structured data updates |
| Timestamps | ISO 8601 in `America/Toronto` (`config/settings.yaml`) |

### Data layout

Each configured target gets isolated storage:

```
data/targets/<target_id>/
├── vehicles.csv           # Permanent VIN registry
├── observations.csv       # Append-only scan observations
├── listing_events.csv     # Appeared / removed / price_changed events
└── current_inventory.json # Latest inventory snapshot

reports/daily/<target_id>/   # Generated markdown reports
snapshots/<target_id>/       # Point-in-time inventory snapshots
data/raw_scans/              # Archived browser scan JSON
data/registry/targets.csv    # Target metadata registry
```

### Scan import pipeline

1. Browser agent writes a scan JSON file with `target_id`, `scan_id`, `scanned_at`, `scan_complete`, and `vehicles[]`.
2. `import-scan` archives the raw JSON to `data/raw_scans/<scan_id>.json`.
3. Deterministic Python code:
   - Appends one observation row per scanned vehicle.
   - Updates the VIN registry (`first_seen_at` / `last_seen_at`).
   - Emits listing events (appeared, removed, price_changed).
   - Rewrites `current_inventory.json` (active + removed statuses).
4. Removals are emitted **only** when `scan_complete: true` and a previously active VIN is absent.

## Installation

From the repository root (where `pyproject.toml` lives):

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Verify the install:

```bash
python -m clutch_tracker.cli list-targets
# or
clutch-tracker list-targets
```

### Troubleshooting

**`ModuleNotFoundError: No module named 'clutch_tracker'`**

The package is not installed in your active environment. Activate your venv and run `pip install -e ".[dev]"` from the repo root. Creating a venv alone is not enough — editable install registers the `src/clutch_tracker` package.

**`province=True` in list-targets output**

YAML parses unquoted `ON` / `OFF` as booleans. Always quote province codes in `config/targets.yaml`:

```yaml
province: "ON"   # correct
province: ON     # wrong — becomes true
```

## CLI usage

```bash
# List configured targets (includes make/model summary)
python -m clutch_tracker.cli list-targets

# Show full search criteria JSON for a target (for browser agents)
python -m clutch_tracker.cli show-target ford-f150-ontario

# Validate configuration
python -m clutch_tracker.cli validate-config

# Create on-disk directories and empty data files
python -m clutch_tracker.cli initialize-targets

# Import a browser scan
python -m clutch_tracker.cli import-scan tests/fixtures/scan_complete.json

# Generate a daily report
python -m clutch_tracker.cli generate-report --target ford-f150-ontario

# Validate repository integrity
python -m clutch_tracker.cli validate-repository
```

## Configuration

### `config/targets.yaml`

Define search targets with unique `target_id` values and criteria. Vehicle makes and models appear here only — never in Python source.

**Model-specific search** (e.g. Ford F-150):

```yaml
- target_id: ford-f150-ontario
  label: "Ontario Ford F-150"
  enabled: true
  criteria:
    make: Ford
    model: F-150
    model_aliases:       # alternate spellings (F150, F 150, …)
      - F150
      - F 150
    province: "ON"
    min_year: 2019
    max_year: 2024
    search_query: "Ford F-150"   # optional Clutch.ca search box text
```

Browser agents should call `show-target <target_id>` to retrieve the full search payload including `model_search_terms`.

### `config/settings.yaml`

Global settings: timezone, logging, reporting fields, and scan behavior.

## Development

```bash
pytest
```

See [AGENTS.md](AGENTS.md) for AI agent operating instructions.

## Project structure

```
├── AGENTS.md
├── README.md
├── pyproject.toml
├── config/
│   ├── targets.yaml
│   └── settings.yaml
├── data/
│   ├── registry/
│   ├── raw_scans/
│   └── targets/
├── reports/
│   ├── daily/
│   └── summary/
├── snapshots/
├── src/clutch_tracker/
│   ├── cli.py
│   ├── config.py
│   ├── models.py
│   ├── validation.py
│   ├── storage.py
│   ├── comparison.py
│   ├── events.py
│   ├── reporting.py
│   └── target_manager.py
└── tests/
```
