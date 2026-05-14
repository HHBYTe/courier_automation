# Workflow

Day-to-day recipes. All commands assume PowerShell from the project root.

## First-time setup

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt
```

That's it. No `pip install -e`, no environment-variable dance. The
project root is added to `sys.path` automatically by `pytest.ini`'s
`pythonpath = .`.

## Running the test suite

```powershell
# Full suite
.\.venv\Scripts\pytest

# Verbose, see each test name
.\.venv\Scripts\pytest -v

# See `print()` and log output from passing tests too
.\.venv\Scripts\pytest -s -v

# Just one test by name substring
.\.venv\Scripts\pytest -s -v -k golden

# Stop at first failure
.\.venv\Scripts\pytest -x

# Re-run only what failed last time
.\.venv\Scripts\pytest --lf
```

See [the pytest section in architecture.md](architecture.md) for what each
test layer covers. Test artifacts land in
`%TEMP%\pytest-of-<user>\pytest-current\`. Pass `--basetemp=.\test-tmp`
to send them to a project-relative folder you can browse.

## Linting

```powershell
.\.venv\Scripts\ruff check .
.\.venv\Scripts\ruff check . --fix      # auto-fix what's auto-fixable
.\.venv\Scripts\ruff format .
```

## Running the pipeline (one carrier, end to end)

`pipeline` is the production command: parse → duplicate guard → append to
the master + write the parquet → rebuild unified. It writes to the real
master workbook (via the OneDrive-safe appender), so there's no copy
dance — the duplicate guard makes re-runs safe. Full reference:
[pipeline.md](pipeline.md).

```powershell
# Dry run — parse + duplicate guard only, writes nothing
.\.venv\Scripts\python -m courier_automation.cli pipeline `
    --carrier seitrans --dry-run

# Ingest the latest populated month
.\.venv\Scripts\python -m courier_automation.cli pipeline --carrier seitrans

# A specific month, machine-readable output (the n8n / automation contract)
.\.venv\Scripts\python -m courier_automation.cli pipeline `
    --carrier seur --month 2026-04 --json

# Re-run the same month — the guard trips, exit 6, nothing written
```

## Manual ingest (development / debugging)

`ingest <carrier>` is the workbench command — one carrier, one file or
month, no unified rebuild. Default mode writes a *sidecar* `.xlsx` next
to the master (plus a parquet) and never touches the master; use it on a
copy when iterating on a parser:

```powershell
Copy-Item "Operations - Couriers\04. Seitrans\Análisis envíos Seitrans.xlsx" `
          "$env:TEMP\seitrans-smoke.xlsx"

# Sidecar mode (default) — parses, writes a fresh xlsx + parquet beside the master
.\.venv\Scripts\python -m courier_automation.cli ingest seitrans `
    --file "Operations - Couriers\04. Seitrans\Facturas\2025\2025_06_30_24633.xlsx"

# Append to a workbook in place (here, the scratch copy)
.\.venv\Scripts\python -m courier_automation.cli ingest seitrans `
    --file "...\2025_06_30_24633.xlsx" `
    --workbook "$env:TEMP\seitrans-smoke.xlsx" --write-master --dry-run
```

All eight carriers are wired: `seur`, `seitrans`, `dachser`, `correos`,
`ups`, `wwex`, `royalmail`, `spring`. See [pipeline.md](pipeline.md) for
`ingest` vs `pipeline`.

## Running the collector (the full scheduled job)

`scripts/run_collector.py` is what Windows Task Scheduler runs: scan the
OneDrive `_inbox/`, classify + file each invoice, sweep `pipeline` for
all eight carriers, rebuild unified, log, email a summary. Setup,
env vars, and the operator workflow are in [automation.md](automation.md).

```powershell
# Run it by hand (point COURIER_INBOX at a scratch dir to test without the real inbox)
$env:COURIER_INBOX = "$env:TEMP\collector-test\_inbox"
.\.venv\Scripts\python scripts\run_collector.py
```

## CLI exit codes

| Code | Meaning | What to do |
|---|---|---|
| 0 | Success | Nothing. |
| 1 | Usage error | Read the message; fix the args. |
| 2 | Schema mismatch | The carrier changed columns. Update `<CARRIER>_RAW_COLUMNS` (or the equivalent constant) in `parsers/<carrier>.py`. The diff in the message tells you what changed. |
| 3 | Workbook lock timeout | Another ingest run, or someone has the lock file. Wait or remove the stale `.courier-automation.lock`. |
| 4 | Manifest conflict | Carrier reissued the same invoice with new content. (Unused while the manifest is disabled.) |
| 5 | Plausibility check failed | Either the data really is bad, or rules are too tight. The message lists every offending column. See [drift_handling.md](drift_handling.md). |
| 6 | Duplicate guard tripped | `pipeline` only — the month is already in the master. A safe no-op; nothing was written. Use `--force` to override. |
| 7 | Unified build failed | `pipeline` only — the per-carrier ingest succeeded but the combine step didn't. Check the message; run `python -m unified.build` to reproduce. |

## Adding new fixtures (for the parser test)

```powershell
# Seur: pick from any year folder; keep at least one of each prefix (D/AD/FR)
Copy-Item "Operations - Couriers\01. Seur\Facturas\2025\0289992025D0177161.xlsx" `
          tests\fixtures\seur\raw\

# Seitrans: any file works; the parametrized test picks it up
Copy-Item "Operations - Couriers\04. Seitrans\Facturas\2025\2025_03_31 11848.xlsx" `
          tests\fixtures\seitrans\raw\

# Re-run the parser tests — the parametrized real-data test runs once per fixture
.\.venv\Scripts\pytest tests\parsers -v
```

The Seur fixture set deliberately keeps one of each filename prefix
(D / AD / FR) so the regex stays exercised against real data. Don't
remove those.

## Refreshing a golden snapshot

The golden test compares parser output to a parquet slice of `Datos`.
The slice is keyed on the fixtures currently in
`tests/fixtures/<carrier>/raw/`, so any fixture change requires
regenerating the parquet:

```powershell
# Seur (~1-2 minutes; 113 MB workbook)
.\.venv\Scripts\python scripts\golden\extract_seur_golden.py --period pilot-sample
.\.venv\Scripts\pytest tests\parsers\test_seur_golden.py -v

# Seitrans (under 5 seconds; 569 KB workbook)
.\.venv\Scripts\python scripts\golden\extract_seitrans_golden.py --period pilot-sample
.\.venv\Scripts\pytest tests\parsers\test_seitrans_golden.py -v
```

The golden extractors live under `scripts/golden/` — one per carrier
(`extract_<carrier>_golden.py`).

The `--period` arg is just a tag for the output filename; each golden
test globs `*-datos.parquet` under its courier's golden dir and uses the
most recent file.

If a golden test fails after a parser change, the assertion message
points at the column and first differing row. Common causes:

- A code field is stored as numeric in one source but text in the other
  (handled by `to_clean_string` in `parsers/base.py`).
- A new column needs to move into the typed groups because Excel coerced
  its values (e.g. Seitrans `Mes` was an int and turned out to be a
  date in production).
- An assumption about a derived column was wrong (e.g. Seitrans
  `Tipo expedición` is always `"Pallet"`).
- The Datos sheet itself drifted — verify against the user.

## Picking a candidate fixture (Seur only, currently)

```powershell
.\.venv\Scripts\python scripts\golden\_find_golden_candidates.py
```

Prints the top Seur invoices that exist in *both* `Datos` and
`Facturas/`, ranked by row count. Useful when picking a richer fixture
or replacing one that fell out of `Datos`. (Seitrans doesn't yet have
an equivalent — its workbook is small enough to inspect directly.)

## Adding a new courier

Both `parsers/seur.py` and `parsers/seitrans.py` follow the same shape
and serve as templates. Pick whichever is closer to the new courier's
file format:

- **Seur-shaped** (raw schema = historical schema, no derived columns):
  copy seur.py.
- **Seitrans-shaped** (raw schema differs from historical: column
  renames + derived columns added): copy seitrans.py and adapt the
  `_rename_and_derive` step.

Eight carriers are wired already; the remaining ones (VASP, Lynda's,
Express Catalan, DPD France) follow the same recipe. For each:

1. Survey one real invoice — open it, note the sheet name, the header
   row, the column names and dtypes.
2. Write `courier_automation/parsers/<carrier>.py` with
   `<CARRIER>_RAW_COLUMNS`, the dtype groups, plausibility rules, and a
   `<Carrier>Parser` class.
3. Add a `<carrier>_invoice_factory` + `default_<carrier>_row` fixture in
   `tests/conftest.py`. Make `default_<carrier>_row` schema-asserted
   against `<CARRIER>_RAW_COLUMNS` so synthetic rows can never drift
   from the parser's expectations.
4. Mirror `tests/parsers/test_<carrier>.py` and the conftest's
   `real_<carrier>_invoice` parametrized fixture.
5. Once one or two real invoices land in fixtures, write
   `scripts/golden/extract_<carrier>_golden.py` (mirror the Seur or Seitrans
   one) and run it against the historical workbook. **Expect surprises**
   — every drift the Seur and Seitrans golden tests caught will probably
   recur in some shape.
6. Add a `CarrierConfig` entry to `courier_automation/carriers.py`:
   parser class, workbook + Facturas paths, data-sheet name, file globs,
   the duplicate-guard key (`guard_invoice_column` or `guard_month_column`),
   and the intake classification rule (`classify_patterns` if the
   filename is distinctive, else `classify_probe=True` plus a header
   `sniff()` on the parser). Add an `ingest <carrier>` subcommand in
   `cli.py` — it's ~15 lines that read the new registry entry.
7. Add a normalizer at `unified/normalizers/<carrier>.py` mapping the
   carrier's parquet to the canonical schema, register it in
   `unified/normalizers/__init__.py`, add the carrier to
   `unified/schema.py`'s `CARRIERS`, and add a branch to
   `unified/service_classifier.py`. Use `seitrans.py` as the template.
8. Backfill the parquet substrate:
   `scripts/backfill/backfill_<carrier>_parquet.py` (copy an existing one).

## Memory / state on disk

- `~/.courier_automation/manifest.sqlite` — the manifest of ingested
  files. Survives reboots. Override with
  `$env:COURIER_AUTOMATION_MANIFEST = "C:\path\to\manifest.sqlite"`.
- `%TEMP%\courier_automation_work\` — the writer's working dir.
  Always cleaned up by the `_working_copy` `finally` block. Non-empty
  between runs only on a crash mid-write.
- `%TEMP%\pytest-of-<user>\` — pytest's scratch from `tmp_path`.
  Last 3 runs kept; rotates automatically.

## Operator workflow (production)

The collector is built. n8n Cloud watches one Outlook folder and drops
invoice attachments into the OneDrive `_inbox/`; a Windows Task
Scheduler job runs `scripts/run_collector.py`, which classifies and
files each invoice, sweeps the `pipeline` for all eight carriers,
rebuilds the unified table, and emails a summary. UPS / WWEX / Royal
Mail are still fetched by hand — the operator drops their downloads into
the same `_inbox/`.

The full operator runbook — Outlook + n8n setup, SMTP config, Task
Scheduler, and the `_unclassified/` / `_conflicts/` triage folders —
lives in [automation.md](automation.md). Day to day, the operator just
reads the summary email and acts on its "ATTENTION NEEDED" section.
