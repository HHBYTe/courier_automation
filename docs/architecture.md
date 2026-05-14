# Architecture

## Project context

Artero processes invoices from ~11 different couriers, each with its own file
format, delivery channel, and cadence. The status quo is manual: someone
opens each invoice, copies rows into a per-courier "Análisis expediciones"
workbook, and a Power BI report consumes that workbook. The cost is human
time and silent errors when copy-paste goes wrong.

This project automates that ingest. It was planned in three phases, and
as of mid-2026 all three have a working implementation in this codebase:

1. **Step 1 — automated ingestion.** Get invoices off email and courier
   portals, parse them, append to the per-courier historical workbook.
   Eight carrier parsers; the `pipeline` command chains parse → guard →
   store; the **collector** (n8n Cloud + a local runner) feeds invoices
   in automatically. **Pilot courier: Seur.**
2. **Step 2 — per-courier Power BI.** The existing `.pbix` files keep
   reading their workbook; a parquet substrate (`data/<carrier>/`) is
   written alongside for the eventual repoint. See [power_bi.md](power_bi.md).
3. **Step 3 — unified cross-courier dataset.** The `unified` package
   combines every carrier's parquet into one canonical fact table for
   global reporting. See [pipeline.md](pipeline.md).

This document is the design-and-rationale overview. For the end-to-end
data flow of the `pipeline` command and the unified build, see
[pipeline.md](pipeline.md); for the automated collector, see
[automation.md](automation.md).

## The pipeline, layer by layer

```
  COLLECTOR                INTAKE              PIPELINE (per carrier)         COMBINE
  ─────────                ──────              ──────────────────────         ───────
  n8n Cloud watches    →   classify each   →   parse → duplicate guard →  →   unified build:
  one Outlook folder,      file to a           append to master xlsx          all carriers'
  drops attachments        carrier, file       + write monthly parquet        parquet → one
  into OneDrive            into Facturas/                                     canonical table
  _inbox/                  <YYYY>/<Mes>/
       │                        │                      │                          │
       └──── scripts/run_collector.py orchestrates intake → pipeline sweep → combine ┘
                                                        │                          │
                                                        ▼                          ▼
                                              Power BI (per-courier)          Big Power BI
```

Each layer is loosely coupled and independently testable. The `pipeline`
command is the per-carrier core (parse → guard → store); the collector
and the unified build sit on either side of it.

- **Collector** — n8n Cloud watches one Outlook folder and uploads every
  invoice attachment into a single OneDrive inbox folder
  (`Operations - Couriers/_inbox/`). It has *no carrier logic* — it's a
  dumb pipe. n8n Cloud can't see local disk or run Python, so this is the
  only part that runs off-machine. UPS / WWEX / Royal Mail are still
  fetched manually (their files are dropped into the same inbox). Full
  design in [automation.md](automation.md).
- **Intake** — `courier_automation/intake.py`. Classifies each inbox file
  to a carrier (filename regex, then a parser header-`sniff()` probe for
  the two carriers with no filename signature) and moves it into the
  carrier's `Facturas/<YYYY>/<NN> - <Mes>/` folder, deriving the month
  from the invoice's own content. Unrecognised files quarantine to
  `_inbox/_unclassified/`.
- **Parser** — pure Python, one adapter per carrier. Loads the raw file,
  validates its schema, coerces dtypes to a canonical form, runs a
  plausibility check, and emits a `ParseResult` with rows ready to append.
- **Store** — appends `ParseResult.rows` to the data sheet of the
  carrier's historical workbook in an OneDrive-safe way (lock, working
  copy, atomic replace), and writes a per-month parquet to
  `data/<carrier>/`. Idempotency is enforced by the pipeline's duplicate
  guard (the SQLite manifest is built but currently disabled — see below).
- **Combine** — `python -m unified.build` reads every carrier's parquet,
  normalises each to a 25-column canonical schema, adds frozen-rate EUR
  columns, splits rows into kept / refunds / rejections, and writes
  `unified/output/unified_shipments.{parquet,csv}`.

The per-carrier Power BI reports are **untouched** — they read from the
same workbook they already do.

The `scripts/run_collector.py` runner ties it together on a schedule:
scan the inbox, classify + file each invoice, run the `pipeline` for all
eight carriers, rebuild the unified table once, and email a summary.

## Storage: Option A (workbook) vs Option B (DuckDB)

The original Step 1 plan defined two storage options. The pilot uses
**Option A**: keep the existing
`NEW Análisis expediciones SEUR.xlsx` (113 MB) as the source of truth and
append rows to its `Datos` sheet. Pros: zero workflow change for the user,
the existing `.pbix` keeps working. Cons: workbook is huge and write-fragile.

**Option B** (DuckDB per courier with the workbook regenerated from
queries) is the destination once three couriers are running cleanly.
Choosing Option A first lets us prove the parser end-to-end on the cheapest
possible substrate.

### UPS date-format sniffer (2026-05-08)

UPS ships dates in three observed formats: ISO `YYYY-MM-DD HH:MM:SS` in
the comma-separated billing CSVs, `DD/MM/YYYY` in the semicolon
GB-locale variant, and `MM/DD/YY` in operator-converted xlsx (Excel
on a US-locale machine renders the cell that way when the operator
converts a CSV by hand). Two pandas 2.2.3 quirks force a per-file
sniffer in [parsers/ups.py](../courier_automation/parsers/ups.py):

1. `pd.to_datetime(s, dayfirst=True)` flips month/day even on
   unambiguous ISO strings — `"2025-03-04 00:00:00"` parses to
   `2025-04-03`. So `dayfirst=True` cannot be the default.
2. With `dayfirst=False`, real `DD/MM/YYYY` values like `"13/03/25"`
   silently NaT (no 13th month). So the semicolon CSV path still
   needs `dayfirst=True`.

`_sniff_dayfirst` reads up to 500 string values out of any date
column, looks for slash-separated dates, and returns `True` only when
a first-slot value > 12 proves DD/MM ordering. Everything else (ISO,
MM/DD, all-ambiguous) gets `dayfirst=False`, which parses ISO and
MM/DD correctly. The backfill path is unaffected — the master xlsx
stores dates as native datetime cells, so the sniffer skips them.
Validated by ingesting UPS 2025-03 from raw xlsx and confirming
zero per-column differences against the master-derived parquet.

### Parquet substrate (2026-05)

Every carrier has a per-month parquet file at
`data/<carrier>/<YYYY>-<MM>.parquet` (snappy, no index). The master xlsx
remains authoritative; parquet is the staging layer for the eventual
DuckDB cutover *and* the input to the unified build. Parquet is written:

- by the `pipeline` command, alongside the master-workbook append;
- by the `ingest` command's sidecar path (`--format parquet` / `both`);
- in bulk by `scripts/backfill/backfill_<carrier>_parquet.py`, which seeds
  historical months from the master sheet (re-applying the parser's
  `coerce_<carrier>_dtypes` and partitioning by date).

Power BI repoint and master decommission stay parked — see
[power_bi.md](power_bi.md).

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
- `to_clean_string(value)` — shared helper that converts numbers / NaN /
  strings to a normalised `string` dtype, dropping the spurious `.0`
  Excel introduces when it auto-stores a code field as a number
  (`685` / `685.0` / `"685.0"` all → `"685"`). Every per-courier coerce
  function uses it.

### `courier_automation/carriers.py`

The carrier registry — one `CARRIERS: dict[str, CarrierConfig]` that is
the single source of truth for everything carrier-specific the CLI,
pipeline, and intake need. Before it existed, the per-carrier workbook
paths, Facturas roots, data-sheet names, and file globs were scattered
across eight near-identical CLI subcommands.

`CarrierConfig` (frozen dataclass) holds, per carrier: `parser_factory`
(the parser class), `workbook`, `facturas_root`, `data_sheet`
(`"Datos"` / `"Data"` / `"New Datos"` / `"INVOICES"` — they differ),
`file_globs` / `fallback_globs` / `name_filter` (file discovery),
`guard_invoice_column` / `guard_month_column` (the duplicate guard's key
— see [pipeline.md](pipeline.md)), `rebuild_mode` (Royal Mail only), and
`classify_patterns` / `classify_probe` (intake classification — see
[automation.md](automation.md)). Imports parser classes and stdlib only,
so it can be imported by `cli.py`, `pipeline.py`, and `intake.py` without
a cycle.

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

Public function:

- `coerce_seur_dtypes(df)` — dtype canonicalisation, used by both the
  parser and the golden-extraction script so the comparison is sound.

### `courier_automation/parsers/seitrans.py`

The Seitrans parser. Raw invoices are 21-column Italian-named files
(sheet `Risultato`); the historical workbook has 25 columns — the same
21 with `_` → ` ` renamed (except `DOCUMENTO_DATA` which kept its
underscore in production), plus 4 derived columns prepended:
`Tipo expedición`, `Q Expediciones`, `Año`, `Mes`.

Key constants (all use the *historical-Datos* column names — the
schema after the rename step):

- `SEITRANS_RAW_COLUMNS` (21) — raw underscore-named columns; what
  `assert_schema` validates against right after `read_excel`.
- `DERIVED_COLUMNS` (4) — `Tipo expedición`, `Q Expediciones`, `Año`,
  `Mes`.
- `SEITRANS_COLUMNS` (25) — derived + renamed raw; the schema the
  writer expects on the Datos sheet.
- `_KEEP_UNDERSCORE = {"DOCUMENTO_DATA"}` — the one column not renamed
  in production. Caught by the golden test, fixed in the rename map.
- `DATE_COLUMNS / INT_COLUMNS / FLOAT_COLUMNS` — note `Mes` is a *date*
  (first day of month), not int; `DOCUMENTO_NUMERO`, `Año`, `IMBALLI`,
  `Q Expediciones` are Int64.

Parse flow:

1. Read raw 21-col xlsx.
2. Validate raw schema with `assert_schema`.
3. `_rename_and_derive(df)` — rename `_` → ` ` (except `DOCUMENTO_DATA`)
   and add the 4 derived columns. Output is shaped like historical Datos.
4. `coerce_seitrans_dtypes(df)` — dtype canonicalisation, identical to
   what the golden script applies to Datos.
5. Plausibility checks on the historical schema.
6. Derive `invoice_number` from data: `f"{year}-{DOCUMENTO_NUMERO}"`.
   Filenames are unreliable (observed: `2025_01_31 3065.xlsx`,
   `2024_12_31 Factura 48172.xlsx`, `2025_06_30_24633.xlsx`).

Two derived-column nuances worth knowing:

- **`Tipo expedición` is always `Pallet`.** Confirmed across all 3,464
  rows of historical Datos. The previous "IMBALLI > 1 → Pallet" guess
  was wrong.
- **`Q Expediciones` is per-file dedup** (`1` on the first row for each
  `SPEDIZIONE NUMERO` within the file, `0` afterwards). The user's
  actual rule is "1 on the first global occurrence across all of
  Datos", which the parser can't replicate without global state.
  Excluded from the golden comparison; ~8% divergence on the fixtures.

### The other six parsers

`seur.py` and `seitrans.py` are the two templates — raw-schema-equals-
historical-schema (Seur) and raw-differs-needs-rename-and-derive
(Seitrans). The other six follow one of those shapes:

| Parser | Raw format | Notes |
|---|---|---|
| `correos.py` | 51-col xlsx, header band on rows 0-2 | 58-col output; 6 derived columns; Spanish-postcode normaliser. |
| `dachser.py` | xlsx, two layouts ("bracketed" / "clean") | 55-col canonical → 60-col output; `_read_raw` sniffs the layout, `_RENAME_*` maps each variant to canonical names. |
| `ups.py` | headerless 250-col CSV | per-file `dayfirst` date sniffer (see above); each CSV asserts a single Invoice Number. |
| `wwex.py` | multi-format (`.xlsx` / `.xls` / `.csv`) | 42-col raw → 44-col output; handles two `Custom-N` packaging-label variants. |
| `spring.py` | 22-col xlsx, sheet read by index | tolerant of portal-inserted metadata columns. |
| `royalmail.py` | pipe-separated cp1252 CSV, three row kinds | keeps docket rows only, propagates invoice fields; 34-col raw → 21-col output. No append master — see the rebuild path in [pipeline.md](pipeline.md). |

Every parser exposes the same `parse(path) -> ParseResult` contract and a
`carrier` / `expected_columns` classvar. `seitrans.py` and `dachser.py`
additionally expose `sniff(path) -> bool` — a header-only schema check
the intake classifier uses to tell those two apart (neither has a
distinctive filename). Per-carrier status is in [status.md](status.md).

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

> **Manifest pipeline temporarily disabled (2026-05-08).** While we
> iterate on per-courier parser fixes (wwex `<NA>` → blank, etc.) and
> regenerate sidecars repeatedly, the CLI uses a `_NullRegistry` shim
> in [cli.py](../courier_automation/cli.py) that no-ops `has_seen` /
> `supersedes` / `register`. The `ManifestRegistry` class, its SQLite
> file, and the existing manifest rows are left untouched — re-enabling
> is a one-line swap (`_NullRegistry()` → `ManifestRegistry()` in
> `_dispatch_ingest`). Re-enable once the parsers stabilise and we
> resume single-pass ingest. Sidecar collisions are still guarded by
> `export_rows`'s `FileExistsError` check, so accidental
> double-exports remain loud.

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

Typer entry point with two command groups:

- **`ingest <carrier>`** — eight subcommands (`seur`, `seitrans`,
  `dachser`, `correos`, `ups`, `wwex`, `royalmail`, `spring`). Each
  accepts `--file / --month YYYY-MM / --folder / --workbook / --dry-run /
  --write-master / --format`. The default is *sidecar* mode (write a
  fresh `.xlsx` next to the master + a parquet, never touch the master);
  `--write-master` appends to the master in place. Used for manual,
  single-carrier ingest and debugging.
- **`pipeline --carrier <name>`** — the end-to-end per-carrier
  orchestrator: parse the month → duplicate guard → append to the master
  *and* write the parquet → rebuild the unified table. `--json` emits one
  result object for n8n; `--dry-run`, `--force`, `--guard-threshold`,
  `--skip-unified` round it out. This is the production path; full flow
  in [pipeline.md](pipeline.md).

With no `--file`/`--month`, file discovery auto-selects the latest
populated month under the carrier's Facturas folder. The CLI is a thin
shell: per-carrier config comes from `carriers.CARRIERS`, the pipeline
logic from `pipeline.py`.

### `courier_automation/exit_codes.py`

The process exit codes, in one module so `cli.py` and `pipeline.py`
share one definition (`cli.py` imports `pipeline.py`, so the codes can't
live in either):

| Code | Meaning | Disposition |
|---|---|---|
| 0 | Success | — |
| 1 | Usage error | Fix the args. |
| 2 | Schema mismatch | The diff is in the message; update the parser. |
| 3 | Workbook lock timeout | Another run holds the lock; retry. |
| 4 | Manifest conflict | Reissued invoice — currently unused (manifest disabled). |
| 5 | Plausibility check failed | Silent-drift detector tripped. |
| 6 | Duplicate guard tripped | The month is already in the master (`pipeline` only). |
| 7 | Unified build failed (non-schema) | `pipeline` only. |

Each code maps cleanly to an n8n / Task Scheduler branch.

### `courier_automation/intake.py`

The carrier-classification and file-filing half of the collector.
`classify_invoice_file(path)` returns a `Classification` — a tier-A
filename-regex match (`CarrierConfig.classify_patterns`), then a tier-B
parser header-`sniff()` probe for `seitrans` / `dachser`, then `None`
(quarantine). `place_invoice_file(path, carrier)` parses the file, takes
the month from `ParseResult.invoice_date`, and moves it into
`Facturas/<YYYY>/<NN> - <Mes>/`; identical re-drops are de-duplicated by
hash, content collisions raise `IntakeConflict`. Imports `carriers` and
`parsers` only. Detail in [automation.md](automation.md).

### `courier_automation/pipeline.py`

The per-carrier orchestrator behind `cli pipeline`. `run_pipeline(...)`
parses the month's files, runs the **duplicate guard** (reads the master
sheet, measures invoice-id or month-row overlap — the idempotency safety
net while the manifest is disabled), appends to the master workbook
*and* writes the month parquet, then rebuilds the unified table. Royal
Mail is special-cased (`rebuild_mode`): its master is rebuilt from
scratch each run rather than appended. Returns an exit code; `--json`
emits a `PipelineResult`. Also home to `rebuild_royalmail_master()`,
shared with `scripts/build_royalmail_master.py`. Full flow in
[pipeline.md](pipeline.md).

### `scripts/run_collector.py`

The scheduled local runner — the on-machine half of the collector.
Scans the OneDrive `_inbox/`, classifies + files each invoice via
`intake.py`, sweeps `run_pipeline` for all eight carriers (one carrier's
crash can't abort the sweep), rebuilds the unified table once, writes a
timestamped log, and emails a summary over SMTP. Run by Windows Task
Scheduler. Setup and the operator workflow are in [automation.md](automation.md).

### The `unified/` package

The cross-courier combine layer (`python -m unified.build`).

- `schema.py` — the 25-column canonical fact schema (`UNIFIED_COLUMNS`),
  strict dtypes, per-column nullability, and `coerce` / `validate`.
- `normalizers/` — one `normalize(df, source_file) -> df` per carrier,
  mapping that carrier's parquet to the canonical schema plus a
  `_reject_reason`. Registered in `normalizers/REGISTRY`.
- `service_classifier.py` — maps each carrier's free-text service name
  to `parcel` / `pallet` / `letter` / `freight` / `other` / `None`
  (`None` = not a shipment → rejected).
- `fx_rates.py` — frozen per-currency EUR rates. `build.py` adds
  `*_eur` companion columns so every carrier is comparable on one scale.
- `build.py` — reads `data/<carrier>/*.parquet`, normalises, splits each
  carrier's rows into **kept** (true shipments), **refunds** (negative
  billing with a valid shipment id), and **rejections** (everything
  else, with `_reject_reason`), and writes
  `unified/output/{unified_shipments,refunds,rejections}.*` + a
  `manifest.json`. Detail in [pipeline.md](pipeline.md).

### `scripts/golden/extract_seur_golden.py` and `scripts/golden/extract_seitrans_golden.py`

One-off scripts (run by the operator, not in the runtime). Each reads
`Datos` from the courier's production workbook, slices to rows whose
identity tuple matches a fixture in `tests/fixtures/<carrier>/raw/`,
applies the same `coerce_<carrier>_dtypes` the parser uses, and writes a
parquet to `tests/fixtures/<carrier>/golden/<period>-datos.parquet`. The
golden test then compares parser output to that parquet.

Identity tuples differ per courier:

- **Seur**: `(year, trailing-7-digit-number)`. Datos stores only the
  trailing int; year disambiguates.
- **Seitrans**: `(year, DOCUMENTO_NUMERO)`. Year is parsed from the
  filename's `YYYY_MM_DD` prefix; trailing number from the filename's
  trailing digits group. Datos stores `DOCUMENTO_NUMERO` directly.

Both scripts read from `Operations - Couriers/` but **only write** to
`tests/fixtures/.../golden/` — the production folder is read-only.

### `scripts/golden/_find_golden_candidates.py`

Dev-only tool (`_`-prefixed; ruff-excluded). Cross-references invoice
numbers in Seur `Datos` against files in
`Operations - Couriers/01. Seur/Facturas/` so picking fresh fixtures
for the golden test is one command.

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
| n8n Cloud | Email collector | Visible to ops, OAuth2 Outlook/OneDrive nodes, schedule + alerting. Kept a dumb pipe — the heavy logic stays in Python. See [automation.md](automation.md). |
| Windows Task Scheduler | Local runner host | Runs `scripts/run_collector.py` on a schedule; no extra service to keep alive. |
| `smtplib` (stdlib) | Collector summary email | No new dependency; the runner emails its run summary directly. |

Notably **not** chosen:

- **xlwings / Excel COM** — would tie us to Excel being installed on the
  host. Pure-Python openpyxl is portable to any machine the runner lives on.
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

The pipeline classifies outcomes into distinct exit codes for a reason:

- Schema mismatch (2) → operator must update the parser's column tuple.
- Lock timeout (3) → another run is in progress, retry later.
- Plausibility failed (5) → either the data really is bad, or our rules
  are too tight; tune.
- Duplicate guard (6) → the month is already ingested; a safe no-op.
- Unified failed (7) → the per-carrier ingest succeeded but the combine
  step didn't.

Each maps cleanly to an n8n / Task Scheduler branch — and now does: the
collector's runner reports per-carrier exit codes in its summary email.
See `exit_codes.py` above for the full table.

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

### 7. Writes to `Operations - Couriers/` go through controlled paths only

Originally the production folder was strictly read-only — the pipeline
was in development and only the golden-extraction scripts touched it
(read-only). Now the production path *does* write there, but only via
two controlled routes: the OneDrive-safe appender (master workbooks —
lock + working copy + atomic replace) and `intake.place_invoice_file`
(moving invoices into `Facturas/`). Everything else — the golden
scripts, the backfill scripts, dev smoke tests — still treats the folder
read-only and works on `Copy-Item` copies. Ad-hoc writes are not allowed.

### 8. The collector is split: n8n Cloud fetches, a local runner ingests

n8n Cloud has no local filesystem and can't run Python against the
113 MB master workbooks, so the collector is two decoupled halves joined
by the OneDrive `_inbox/` folder: n8n Cloud fetches email attachments
and drops them in OneDrive (a dumb pipe, no carrier logic); a local
Windows Task Scheduler job (`scripts/run_collector.py`) does the
classification, filing, and pipeline run. They never talk directly — the
duplicate guard makes the local sweep safe to run on any cadence. See
[automation.md](automation.md).

### 9. Duplicate guard instead of the manifest (for now)

The SQLite manifest is built and tested but disabled (see the note under
`registry.py`). The `pipeline` command's **duplicate guard** is the
idempotency net in the meantime: it reads the master sheet and aborts
(exit 6) if the incoming month already overlaps it. It's a heuristic,
not a hash-exact record, but it's enough to make scheduled re-runs
safe — and it needs no database. The manifest can be re-enabled
alongside it later.

### 10. Carrier-specific config lives in one registry

`carriers.CARRIERS` is the single source of truth for per-carrier paths,
sheet names, file globs, guard keys, and classification rules. The eight
`ingest` subcommands and the `pipeline` command all read from it, so
adding or retargeting a carrier is a one-place change.

### 11. Royal Mail keeps docket grain; the unified schema absorbs it

Royal Mail invoices bill at *docket* granularity (a batch posting), not
one row per shipment like every other carrier. Rather than synthesise
fake per-shipment rows, the fact table stays at docket grain and the
`Quantity` column carries the shipment count; the unified normalizer
maps it to `bultos_count` and Power BI absorbs the shape difference.
Royal Mail also has no append-friendly master, so the `pipeline`
*rebuilds* its workbook from scratch each run instead of appending.

## Where things live on disk

| Path | Contents |
|---|---|
| `courier_automation/` | The Python package (parsers, store, manifest, carriers, intake, pipeline, cli). |
| `unified/` | The cross-courier combine layer — schema, normalizers, fx_rates, `build.py`. |
| `unified/output/` | Build artifacts: `unified_shipments.{parquet,csv}`, `refunds.*`, `rejections.parquet`, `manifest.json`. Git-ignored, regenerated. |
| `data/<carrier>/` | Per-month parquet substrate (`<YYYY>-<MM>.parquet`). Git-ignored. |
| `scripts/` | Operator tools: `run_collector.py`, `backfill/`, `golden/`, `build_royalmail_master.py`. |
| `n8n/` | The importable n8n Cloud collector workflow JSON. |
| `tests/` | Test suite. `tests/fixtures/<carrier>/raw/` holds real invoices; `…/golden/` holds the parquet snapshots. |
| `docs/` | This documentation. |
| `logs/collector/` | Timestamped collector-run logs + the run lock. Git-ignored. |
| `Operations - Couriers/` | Production data: workbooks, raw invoices, PDFs, `.pbix`. Written **only** via the OneDrive-safe appender and `intake.place_invoice_file` (see decision 7). `_inbox/` is the collector drop folder. |
| `~/.courier_automation/manifest.sqlite` | Manifest (override with `$env:COURIER_AUTOMATION_MANIFEST`). Currently disabled. |
| `%TEMP%\courier_automation_work\` | Writer's working dir. Always cleaned up; non-empty between runs only on crash. |
| `%TEMP%\pytest-of-<user>\` | pytest scratch (last 3 runs kept). |
