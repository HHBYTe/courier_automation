# The pipeline — per-carrier ingest and the unified build

This is the reference for the core data flow: the `pipeline` command
(one carrier, end to end) and `python -m unified.build` (all carriers
combined). For how invoices *arrive*, see [automation.md](automation.md);
for the design rationale, see [architecture.md](architecture.md).

## Two commands, two grains

| Command | Grain | What it does |
|---|---|---|
| `python -m courier_automation.cli pipeline --carrier <name>` | one carrier, one month | parse → duplicate guard → append to master xlsx + write parquet → rebuild unified |
| `python -m unified.build` | all carriers | combine every `data/<carrier>/*.parquet` into one canonical fact table |

`pipeline` calls `unified.build` as its last step, so the day-to-day
command is just `pipeline`. `unified.build` is also runnable standalone
(e.g. after a backfill).

## `pipeline` — the per-carrier orchestrator

```
python -m courier_automation.cli pipeline --carrier <name>
        [--month YYYY-MM] [--dry-run] [--json] [--force]
        [--guard-threshold 0.90] [--skip-unified]
```

| Flag | Effect |
|---|---|
| `--carrier` | Required. One of: seur, seitrans, dachser, correos, ups, wwex, spring, royalmail. |
| `--month YYYY-MM` | The month to ingest. Omitted → auto-discover the latest populated month under the carrier's `Facturas/`. |
| `--dry-run` | Parse + run the duplicate guard only. Writes nothing. |
| `--json` | Emit exactly one JSON result object to stdout (all logs go to stderr). The n8n / automation contract. |
| `--force` | Skip the duplicate guard and ingest anyway. |
| `--guard-threshold` | Overlap fraction at/above which the guard aborts. Default `0.90`. |
| `--skip-unified` | Stop after the per-carrier ingest; don't rebuild the unified table. The collector sweep uses this and rebuilds once at the end. |

### The flow

1. **Resolve files.** From `carriers.CARRIERS[<name>]`: the Facturas
   root, file globs, and name filter. `--month` resolves
   `<root>/<YYYY>/<NN> - <Mes>/`; no `--month` picks the latest such
   folder. No files → exit 1.
2. **Parse.** Every file through the carrier's parser →
   `ParseResult`s. A `SchemaMismatch` → exit 2, `PlausibilityError` →
   exit 5, other `ParserError` → exit 1. Nothing is written on a parse
   failure.
3. **Duplicate guard** (unless `--force` or `rebuild_mode`). See below.
   A duplicate → exit 6, nothing written.
4. **Append + parquet.** Append the parsed rows to the master workbook's
   data sheet via the OneDrive-safe appender, **then** write
   `data/<carrier>/<YYYY>-<MM>.parquet`. Append-first is deliberate: if
   the parquet write fails, the master is still updated and the next
   run's guard (which reads the master) catches it. A workbook lock
   timeout → exit 3; the month parquet already existing → exit 6.
5. **Rebuild unified** (unless `--skip-unified`). Runs `unified.build`
   for *all* carriers so the cross-carrier table stays consistent. A
   schema-validation failure → exit 2, any other failure → exit 7.
6. **Report.** `--json` prints a `PipelineResult`
   (`carrier, month, status, files_ingested, rows_appended,
   parquet_path, unified_totals, detail`); otherwise a human summary.

### The duplicate guard

The SQLite manifest is built but currently disabled, so the guard is the
idempotency net. Before any write, it reads the master's data sheet and
measures how much the incoming month already overlaps it:

- **Invoice-id overlap** (primary) — for carriers with a stable invoice
  column (`CarrierConfig.guard_invoice_column`: seur, seitrans, dachser,
  ups, spring). `overlap = |incoming ids ∩ master ids| / |incoming ids|`.
  `overlap ≥ threshold` → duplicate. `0 < overlap < threshold` → partial
  overlap, also aborts (a half-written month needs a human).
- **Month-row-count overlap** (fallback) — for carriers with no stable
  invoice column (`guard_month_column`: correos, wwex). Counts master
  rows already dated in the target month vs the incoming row count.
- **Skipped** — Royal Mail (`rebuild_mode` — see below), `--force`, or a
  master that doesn't exist yet (first ingest).

A tripped guard is exit 6 — a safe no-op, not an error. This is what
makes the collector's "sweep all eight carriers every run" cheap: an
already-ingested month just trips the guard and moves on.

### Royal Mail is special-cased

Royal Mail has no append-friendly master workbook, so `rebuild_mode` is
set in its `CarrierConfig`. Instead of appending, `pipeline --carrier
royalmail` **rebuilds** `Royal Mail Shipments Report.xlsx` from scratch
by re-parsing every invoice CSV in the Facturas tree
(`rebuild_royalmail_master()` in `pipeline.py`, shared with
`scripts/build_royalmail_master.py`), then writes the month parquet.
This is idempotent by construction, so the duplicate guard is skipped.

## `ingest` vs `pipeline`

`ingest <carrier>` is the manual, single-carrier command — useful for
debugging, backfilling one file, or working on a copy. It has two modes:

| | Master workbook | Monthly parquet | Unified |
|---|---|---|---|
| `ingest` (default, sidecar) | a fresh `.xlsx` *beside* the master | yes (`--format parquet`/`both`) | no |
| `ingest --write-master` | appended in place | no | no |
| `pipeline` | appended in place | yes | yes |

`pipeline` is the only command that does master-append **and** parquet
**and** unified in one run — it's the production path. `ingest` is the
workbench.

## `unified.build` — the combine layer

```
python -m unified.build [--carriers seur seitrans ...]
```

Reads `data/<carrier>/*.parquet` for every carrier (or the subset
given), and for each:

1. **Normalise.** `unified/normalizers/<carrier>.py` maps the carrier's
   parquet columns to the **25-column canonical schema**
   (`unified/schema.py`) plus an internal `_reject_reason`. The
   `service_classifier` maps each carrier's free-text service name to
   `parcel` / `pallet` / `letter` / `freight` / `other` / `None`.
2. **Split** each carrier's rows three ways:
   - **kept** — `_reject_reason` is null. True shipments.
   - **refunds** — rejected, but negative `total_net` with a valid
     `shipment_id` and `posting_date`. Credits, kept on a separate axis
     so net cost = `Σ shipments + Σ refunds`.
   - **rejections** — everything else, carrying its `_reject_reason`
     (non-shipment lines: admin charges, surcharge-only rows, rows that
     failed validation).
3. **Add EUR columns.** Every amount gets a `*_eur` companion converted
   at a single frozen per-currency rate from `unified/fx_rates.py`, so
   carriers in GBP/USD/EUR are comparable on one scale. The native
   columns are kept for audit.
4. **Validate** the kept frame against the canonical schema (dtypes +
   nullability). A failure aborts the build (`pipeline` maps it to exit 2).

Outputs, all under `unified/output/` (git-ignored, regenerated):

| File | Contents |
|---|---|
| `unified_shipments.parquet` / `.csv` | kept rows — the fact table Power BI consumes |
| `refunds.parquet` / `.csv` | refund/credit rows, same schema |
| `rejections.parquet` | dropped rows + `_reject_reason` (the audit trail) |
| `manifest.json` | per-carrier counts, date ranges, the frozen FX rates used |

### Royal Mail in the unified table

Royal Mail bills at **docket** grain (one row = a batch posting), not
one row per shipment. The unified normalizer keeps that grain and maps
the docket's `Quantity` to `bultos_count` — no fake per-shipment rows.
Power BI absorbs the shape difference. Royal Mail's penalty/admin lines
(admin charge, oversize surcharge, etc.) classify to `None` and land in
`rejections.parquet` — visible, not silently dropped.

## Exit codes

The full table is in [architecture.md](architecture.md) (the
`exit_codes.py` section) and [workflow.md](workflow.md). The two codes
that are `pipeline`-specific:

- **6 — duplicate guard tripped.** The month is already in the master.
  Safe no-op; the collector reports it as `duplicate`, not an error.
- **7 — unified build failed (non-schema).** The per-carrier ingest
  succeeded but the combine step didn't.

## How the collector uses it

`scripts/run_collector.py` doesn't reimplement any of this — it sweeps
`pipeline --carrier <name> --skip-unified` for all eight carriers
(affected carriers on their new month, idle carriers auto-discovering
the latest — which trips the guard harmlessly), then runs
`unified.build` once. One carrier crashing is contained, recorded as an
error, and doesn't abort the sweep. See [automation.md](automation.md).
