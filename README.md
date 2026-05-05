# Courier Automation

Internal pipeline that ingests courier invoices into per-courier historical workbooks. Pilot courier: **Seur**.

## Documentation

| Doc | What's in it |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Design, three-layer pipeline, module-by-module walkthrough, tools, key decisions. |
| [docs/workflow.md](docs/workflow.md) | Day-to-day recipes: setup, test, ingest, picking fixtures, refreshing the golden snapshot. |
| [docs/status.md](docs/status.md) | What's done, what's tested, known gaps, roadmap, decision log. |
| [docs/drift_handling.md](docs/drift_handling.md) | Three classes of drift and what each layer catches; the LLM-triage future strategy. |

Reference material: [01_data_exploration.md](01_data_exploration.md) (per-courier raw schemas) and [02_step1_plan.md](02_step1_plan.md) (original Step 1 plan).

## Setup (Windows, PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
```

## Daily commands

```powershell
# Run the test suite
.venv\Scripts\pytest

# Lint + format check
.venv\Scripts\ruff check .
.venv\Scripts\ruff format --check .

# Ingest one Seur invoice into a workbook
.venv\Scripts\python -m courier_automation.cli ingest seur `
    --file "Operations - Couriers\01. Seur\Facturas\2025\04 - Abril\0289992025DXXXXXX.xlsx" `
    --workbook "Operations - Couriers\01. Seur\NEW Análisis expediciones SEUR.xlsx"

# Ingest a whole month's folder
.venv\Scripts\python -m courier_automation.cli ingest seur --month 2025-04
```

## Layout

- `courier_automation/parsers/` — per-courier parsers; `seur.py` is the only one for now.
- `courier_automation/manifest/` — SQLite registry for idempotent ingest.
- `courier_automation/store/` — workbook appender with OneDrive-safe write strategy.
- `courier_automation/cli.py` — Typer entry point.
- `tests/` — one folder per layer; fixtures live under `tests/fixtures/seur/`.
- `scripts/extract_seur_golden.py` — one-off extractor that reads the production `Datos` sheet and saves a parquet golden snapshot for the parser test.
