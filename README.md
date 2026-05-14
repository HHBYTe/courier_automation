# Courier Automation

Internal pipeline that ingests courier invoices into per-courier
historical workbooks and combines them into one cross-courier dataset.

**Eight carriers wired** — Seur, Seitrans, Dachser, Correos Express, UPS
(UK), WWEX (US), Spring (FR), Royal Mail (UK). Five (Seur, Seitrans,
Correos, UPS, WWEX) are golden-tested against real production rows.

The flow: a **collector** (n8n Cloud + a local runner) drops invoices
into place → the **`pipeline`** command parses each, guards against
duplicates, and appends to the master workbook + a parquet → the
**`unified`** build combines every carrier into one canonical fact table.

## Documentation

| Doc | What's in it |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Design, the layered pipeline, module-by-module walkthrough, tools, key decisions. |
| [docs/pipeline.md](docs/pipeline.md) | The `pipeline` command and the unified build, end to end — the duplicate guard, exit codes, kept/refunds/rejections. |
| [docs/automation.md](docs/automation.md) | The collector: the n8n Cloud workflow, the local Task Scheduler runner, setup, the operator workflow. |
| [docs/workflow.md](docs/workflow.md) | Day-to-day recipes: setup, test, ingest, pipeline, collector, fixtures, adding a courier. |
| [docs/status.md](docs/status.md) | What's done, what's tested, known gaps, roadmap, decision log. |
| [docs/drift_handling.md](docs/drift_handling.md) | The classes of drift and what each layer catches; the LLM-triage future strategy. |
| [docs/power_bi.md](docs/power_bi.md) | Repointing the per-courier Power BI datasets to the parquet substrate. |

## Setup (Windows, PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
```

## Daily commands

```powershell
# Run the test suite + lint
.venv\Scripts\pytest
.venv\Scripts\ruff check .

# Pipeline: ingest one carrier's latest month end to end
# (parse -> duplicate guard -> master + parquet -> rebuild unified)
.venv\Scripts\python -m courier_automation.cli pipeline --carrier seitrans

# ...a specific month, machine-readable output (the automation contract)
.venv\Scripts\python -m courier_automation.cli pipeline --carrier seur --month 2026-04 --json

# Manual single-carrier ingest (workbench; default = sidecar, doesn't touch the master)
.venv\Scripts\python -m courier_automation.cli ingest seur `
    --file "Operations - Couriers\01. Seur\Facturas\2026\04 - Abril\0289992026DXXXXXX.xlsx"

# Rebuild the unified cross-courier table from all carriers' parquet
.venv\Scripts\python -m unified.build

# The full scheduled collector (scan inbox -> classify + file -> sweep pipeline -> unified)
.venv\Scripts\python scripts\run_collector.py
```

Exit codes (shared by `ingest` and `pipeline`): `0` ok · `1` usage ·
`2` schema mismatch · `3` workbook lock · `4` manifest conflict ·
`5` plausibility · `6` duplicate guard · `7` unified build failed.

## Layout

- `courier_automation/parsers/` — eight per-courier parsers plus shared helpers in `base.py` and the `plausibility.py` detector.
- `courier_automation/carriers.py` — the `CARRIERS` registry: per-carrier paths, sheets, globs, guard + classification rules.
- `courier_automation/pipeline.py` — the per-carrier orchestrator (`run_pipeline`) behind `cli pipeline`.
- `courier_automation/intake.py` — classify a dropped invoice to a carrier and file it into `Facturas/`.
- `courier_automation/store/` — the OneDrive-safe workbook appender + parquet writer.
- `courier_automation/manifest/` — SQLite registry for idempotent ingest (built; currently disabled).
- `courier_automation/{cli,exit_codes}.py` — the Typer CLI and the shared exit codes.
- `unified/` — the cross-courier combine layer: canonical schema, per-carrier normalizers, FX rates, `build.py`.
- `scripts/` — `run_collector.py` (the scheduled runner), `backfill/`, `golden/`, `build_royalmail_master.py`.
- `n8n/` — the importable n8n Cloud collector workflow.
- `tests/` — one folder per layer; fixtures under `tests/fixtures/<carrier>/`.
- `data/<carrier>/`, `unified/output/` — generated parquet/CSV artifacts (git-ignored).
