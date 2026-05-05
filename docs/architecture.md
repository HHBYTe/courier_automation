# Architecture

## Project context

Artero processes invoices from ~11 different couriers, each with its own file
format, delivery channel, and cadence. The status quo is manual: someone
opens each invoice, copies rows into a per-courier "Análisis expediciones"
workbook, and a Power BI report consumes that workbook. The cost is human
time and silent errors when copy-paste goes wrong.

This project automates that ingest. It's planned in three phases:

1. **Step 1 — automated ingestion** (this codebase). Get invoices off email
   and courier portals, parse them, append to the per-courier historical
   workbook. **Pilot courier: Seur.**
2. **Step 2 — per-courier Power BI** (no UX change for the user — the
   existing `.pbix` files keep working).
3. **Step 3 — unified cross-courier dataset** for global reporting.

This document describes Step 1 as built today.

## The three-layer pipeline

```
   ┌─────────────────────┐    ┌─────────────────────┐   ┌──────────────────────────┐
   │  COLLECTOR          │ -> │   PARSER            │ ->│   STORE                  │
   │  (n8n / Power       │    │   (per-courier      │   │   (workbook appender,    │
   │   Automate)         │    │    Python adapter)  │   │    SQLite manifest)      │
   └─────────────────────┘    └─────────────────────┘   └──────────────────────────┘
                                                              │
                                                              ▼
                                                       Power BI (per-courier)
```

Each layer is loosely coupled and independently testable.

- **Collector** — watches an inbox label / runs a portal scraper, drops
  attachments into a folder. **Not yet built**; for now the CLI accepts
  a local file path so the rest of the pipeline can be developed and
  validated independently. Planned host: an n8n workflow (preferred) or
  Power Automate (fallback if Artero IT doesn't run n8n).
- **Parser** — pure Python. Loads the raw xlsx, validates its schema,
  coerces dtypes to a canonical form, runs a plausibility check, and emits
  a `ParseResult` with rows ready to append.
- **Store** — appends `ParseResult.rows` to the `Datos` sheet of the
  courier's historical workbook in an OneDrive-safe way (lock, working
  copy, atomic replace). Idempotency is enforced by a SQLite manifest.

The Power BI report is **untouched** — it reads from the same workbook it
already does.

## Storage: Option A (workbook) vs Option B (DuckDB)

The Step 1 plan ([02_step1_plan.md](../02_step1_plan.md)) defined two
options. The pilot uses **Option A**: keep the existing
`NEW Análisis expediciones SEUR.xlsx` (113 MB) as the source of truth and
append rows to its `Datos` sheet. Pros: zero workflow change for the user,
the existing `.pbix` keeps working. Cons: workbook is huge and write-fragile.

**Option B** (DuckDB per courier with the workbook regenerated from
queries) is the destination once three couriers are running cleanly.
Choosing Option A first lets us prove the parser end-to-end on the cheapest
possible substrate.

## Module walkthrough

### `courier_automation/parsers/base.py`

Adapter interface and shared helpers, courier-agnostic.

- `CourierParser` (Protocol) — every parser declares `carrier` and
  `expected_columns` and implements `parse(path) -> ParseResult`.
- `ParseResult` (frozen dataclass) — `(carrier, invoice_number,
  invoice_date, rows, source_path, file_hash)`. The parser's contract.
- `assert_schema(df, expected)` — raises `SchemaMismatch` with a clean
  diff (missing/added/reordered columns). The structural-drift gate.
- `compute_file_hash(path)` — SHA-256, used as part of the manifest
  primary key.
- `extract_seur_invoice_number(filename)` — Seur-specific filename regex
  `\d{10}[A-Z]{1,3}\d{7}` that handles all observed prefixes (D, AD, FR).

### `courier_automation/parsers/seur.py`

The Seur parser. Raw schema is identical to the historical `Datos` schema
(both are 68 columns), so the parser is essentially a passthrough — it
loads `Sheet1`, validates the schema, coerces dtypes, runs plausibility
checks, and returns the rows.

Key constants:

- `SEUR_COLUMNS` (68 strings) — the canonical column tuple.
- `DATE_COLUMNS`, `INT_COLUMNS`, `FLOAT_COLUMNS`, `STRING_COLUMNS` — the
  dtype coercion groups. `STRING_COLUMNS` is read-time-only (`dtype=str`
  hint) to preserve leading zeros in postcodes and code fields.
- `PLAUSIBILITY_NO_NULL`, `PLAUSIBILITY_MIN_NON_NULL_RATE`,
  `PLAUSIBILITY_DATE_RANGE` — the value-level drift rules.

Public functions:

- `coerce_seur_dtypes(df)` — dtype canonicalisation, used by both the
  parser and the golden-extraction script so the comparison is sound.
- `_to_clean_string(value)` — normalises numeric-as-text values across
  raw vs Datos (685 / 685.0 / "685.0" all → "685"). Necessary because
  Excel auto-stores code columns as numbers in some files but not others.

### `courier_automation/parsers/plausibility.py`

Deterministic detector for **value-level drift** that survives a clean
schema check. Three rule kinds:

- `no_null` — columns where any null is unacceptable (primary-key-like
  fields).
- `min_non_null_rate` — per-column floor (e.g. 0.95). The main detector
  of silent NaN coercion (e.g. Seur switches to comma decimals →
  `pd.to_numeric` returns NaN).
- `date_range` — every parsed date must fall in `[lo, hi]`. Catches
  mis-parsed dates landing at 1970-epoch or year 9999.

Failures aggregate into one `PlausibilityError` listing every offending
column. CLI exit code **5**. See [drift_handling.md](drift_handling.md) for
the broader strategy.

### `courier_automation/manifest/registry.py`

SQLite-backed registry of ingested files at
`~/.courier_automation/manifest.sqlite` (overridable via
`COURIER_AUTOMATION_MANIFEST` env var).

- Schema: `files(carrier, invoice_number, file_hash, source_path,
  ingested_at, rows_written)` with PK `(carrier, invoice_number,
  file_hash)`.
- WAL mode for safe concurrent writes.
- API: `has_seen()`, `register()`, `supersedes()`, `all_for_invoice()`.
- `supersedes(carrier, invoice_number, new_hash)` returns the prior hash
  if the same invoice was previously ingested with a *different* hash —
  the "carrier reissued the file" signal. CLI exits **4** on this.

### `courier_automation/store/workbook_appender.py`

OneDrive-safe writer for the historical workbook.

Algorithm:

1. Acquire a sidecar lock (`<workbook>.courier-automation.lock`) via
   `O_CREAT | O_EXCL` — atomic, no race.
2. Copy the workbook to `%TEMP%\courier_automation_work\run-<hex>\` (a
   non-OneDrive working dir).
3. `openpyxl.load_workbook` the working copy, validate `Datos` headers
   match `expected_columns`.
4. Append rows via `ws.append(...)` (streaming).
5. `save()` the working copy.
6. Stage to `<original>.tmp` on the target volume, `os.replace` (atomic).
7. Release the lock; clean up the working dir.

Custom errors: `WorkbookLocked` (lock timeout, exit 3), reuses
`SchemaMismatch` (live workbook headers drifted, exit 2).

### `courier_automation/cli.py`

Typer entry point. Single command tree: `ingest seur --file ... | --month
YYYY-MM`. Exit codes:

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Usage error (e.g. missing arguments) |
| 2 | Schema mismatch (the diff is in the message) |
| 3 | Workbook lock timeout |
| 4 | Manifest conflict (same invoice, different hash) |
| 5 | Plausibility check failed (silent-drift detector) |

`--dry-run` parses and manifest-checks without writing.

### `scripts/extract_seur_golden.py`

One-off script (run by the operator, not in the runtime). Reads `Datos`
from the production workbook, slices to rows whose `(year, trailing-int)`
matches a fixture in `tests/fixtures/seur/raw/`, applies
`coerce_seur_dtypes`, and writes a parquet to
`tests/fixtures/seur/golden/<period>-datos.parquet`. The golden test then
compares parser output to that parquet.

The script reads from `Operations - Couriers/` but **only writes** to
`tests/fixtures/seur/golden/` — the production folder is read-only.

### `scripts/_find_golden_candidates.py`

Dev-only tool (`_`-prefixed). Cross-references invoice numbers in `Datos`
against files in `Operations - Couriers/01. Seur/Facturas/` so picking
fresh fixtures for the golden test is one command.

## Tools and why each was picked

| Tool | Role | Rationale |
|---|---|---|
| Python 3.11+ | Language | Stdlib `sqlite3`, `pathlib`, `dataclasses`. Type hints make the contracts readable. |
| `venv` + `pip` + pinned `requirements.txt` | Dependency management | User chose simplicity over `uv`/`poetry`. Two-line install on a fresh machine, no extra tooling. |
| `pandas` | DataFrame manipulation, Excel I/O | Industry standard for tabular data; great Excel/parquet integration. |
| `openpyxl` | Read + write `.xlsx` | Pure Python, no Excel install needed. The streaming `ws.append()` keeps memory bounded on big workbooks. |
| `pyarrow` | Parquet | Used for the golden snapshot. Lossless dtype round-trip. Will also be the bridge to DuckDB in Step 1.5. |
| `typer` | CLI | Annotation-driven, gets `--help` for free, `CliRunner` for tests. |
| `sqlite3` (stdlib) | Manifest | ACID, no external service, tiny footprint, great enough for ~120 invoices/year. WAL mode for safe concurrency. |
| `pytest` + `pytest-cov` | Testing | Fixtures, parametrisation, the standard. `tmp_path` keeps tests hermetic. |
| `ruff` | Lint + format | Single fast tool, single config file, replaces both `flake8` and `black`. |
| n8n / Power Automate | Orchestrator (not yet built) | Visible to ops, schedule + Gmail/Outlook triggers + alerting; the heavy parsing stays in Python. |

Notably **not** chosen:

- **xlwings / Excel COM** — would tie us to Excel being installed on the
  host. Pure-Python openpyxl is portable to any machine the n8n /
  Power Automate runner lives on.
- **DuckDB** — out of scope for the pilot (Option A). Will revisit when
  we move to Option B.
- **`uv` / `poetry`** — explicit user preference for plain venv + pinned
  requirements.

## Key technical decisions (with rationale)

### 1. Determinism in the runtime path; LLMs only out of band

Every step inside `parser → manifest → writer` is deterministic. Tests,
the manifest's idempotency guarantees, and the golden snapshot all rely
on this. LLMs are filed as a future drift-triage tool that runs *after*
a deterministic detector fires — see [drift_handling.md](drift_handling.md).

### 2. Loud-fail on structural drift; defer-fail on lock contention; idempotent on duplicate

The pipeline classifies failures into four exit codes for a reason:

- Schema mismatch (2) → operator must update the parser's column tuple.
- Lock timeout (3) → another run is in progress, retry later.
- Manifest conflict (4) → courier reissued the same invoice number;
  operator must decide which file is canonical.
- Plausibility failed (5) → either the data really is bad, or our rules
  are too tight; tune.

Each maps cleanly to an n8n branch later.

### 3. OneDrive-safe write strategy

Writing in place to the OneDrive workbook risks (a) sync conflict files,
(b) corruption if Excel is open, (c) partial state on crash. The
working-copy + atomic-replace pattern eliminates all three.

### 4. Manifest keyed by file hash, not just invoice number

A courier can reissue an invoice (same number, different content). The
hash detects it; `supersedes()` flags it for human review instead of
silently overwriting.

### 5. Golden test as the load-bearing trust signal

Schema validation + plausibility checks catch *categories* of drift; the
golden test catches *all* drift in one shot, by comparing parser output
to the rows the user actually pastes today. The pilot will not be
declared production-ready until the golden test passes for at least one
real period (✅ as of 2026-05-05, against 2948 real Datos rows from five
fixtures).

### 6. Filename invoice number ≠ data invoice number

The filename has the full string (`0289992025D0235697`); the `Numero
Factura` column inside the file (and in Datos) has just the trailing
7-digit int (`235697`). The `ParseResult.invoice_number` (and manifest
key) uses the filename string for global uniqueness across years and
prefixes. The data column is left as int for type-correct golden
comparison.

### 7. Read-only production folder

`Operations - Couriers/` is treated as read-only. The pipeline reads
from it but never writes. Smoke-tests against the real workbook always
go through a `Copy-Item` to `%TEMP%` first. No code that writes back
into that folder is allowed.

## Where things live on disk

| Path | Contents |
|---|---|
| `courier_automation/` | The Python package. |
| `tests/` | Test suite + fixtures. |
| `tests/fixtures/seur/raw/` | Real Seur invoices used as fixtures (not anonymised — internal tool). |
| `tests/fixtures/seur/golden/` | Parquet snapshots from Datos for the golden test. |
| `scripts/` | One-off operator tools (golden extractor, fixture finder). |
| `docs/` | This documentation. |
| `Operations - Couriers/` | **Read-only.** Production data: workbooks, raw invoices, PDFs, `.pbix` files. |
| `~/.courier_automation/manifest.sqlite` | Manifest (override with `$env:COURIER_AUTOMATION_MANIFEST`). |
| `%TEMP%\courier_automation_work\` | Writer's working dir. Always cleaned up; non-empty between runs only on crash. |
| `%TEMP%\pytest-of-<user>\` | pytest scratch (last 3 runs kept). |
