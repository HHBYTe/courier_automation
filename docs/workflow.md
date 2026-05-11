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

## Ingesting an invoice (development)

Always operate on a copy of the workbook for now — production files are
off-limits until end-to-end validation is signed off.

### Seur

```powershell
Copy-Item "Operations - Couriers\01. Seur\NEW Análisis expediciones SEUR.xlsx" `
          "$env:TEMP\seur-smoke.xlsx"

# Dry run — parses + manifest-checks but doesn't write
.\.venv\Scripts\python -m courier_automation.cli ingest seur `
    --file "Operations - Couriers\01. Seur\Facturas\2025\0289992025D0235697.xlsx" `
    --workbook "$env:TEMP\seur-smoke.xlsx" --dry-run

# Real run, then re-run — second run reports "already ingested"
.\.venv\Scripts\python -m courier_automation.cli ingest seur `
    --file "Operations - Couriers\01. Seur\Facturas\2025\0289992025D0235697.xlsx" `
    --workbook "$env:TEMP\seur-smoke.xlsx"
```

### Seitrans

```powershell
Copy-Item "Operations - Couriers\04. Seitrans\Análisis envíos Seitrans.xlsx" `
          "$env:TEMP\seitrans-smoke.xlsx"

# Single file
.\.venv\Scripts\python -m courier_automation.cli ingest seitrans `
    --file "Operations - Couriers\04. Seitrans\Facturas\2025\2025_06_30_24633.xlsx" `
    --workbook "$env:TEMP\seitrans-smoke.xlsx" --dry-run

# Auto-discover the latest month under the default Facturas folder
.\.venv\Scripts\python -m courier_automation.cli ingest seitrans `
    --workbook "$env:TEMP\seitrans-smoke.xlsx"
```

Open the smoke file in Excel afterwards, verify the new rows in `Datos`,
and confirm the matching `.pbix` (`Expediciones SEUR.pbix` or
`Expediciones Seitrans.pbix`) refreshes against the scratch path.

## CLI exit codes

| Code | Meaning | What to do |
|---|---|---|
| 0 | Success | Nothing. |
| 1 | Usage error | Read the message; fix the args. |
| 2 | Schema mismatch | The carrier changed columns. Update `<CARRIER>_RAW_COLUMNS` (or the equivalent constant) in `parsers/<carrier>.py`. The diff in the message tells you what changed. |
| 3 | Workbook lock timeout | Another ingest run, or someone has the lock file. Wait or remove the stale `.courier-automation.lock`. |
| 4 | Manifest conflict | Carrier reissued the same invoice with new content. Inspect both files; if the new one is canonical, delete the old manifest row and re-ingest. |
| 5 | Plausibility check failed | Either the data really is bad, or rules are too tight. The message lists every offending column. See [drift_handling.md](drift_handling.md). |

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
.\.venv\Scripts\python scripts\extract_seur_golden.py --period pilot-sample
.\.venv\Scripts\pytest tests\parsers\test_seur_golden.py -v

# Seitrans (under 5 seconds; 569 KB workbook)
.\.venv\Scripts\python scripts\extract_seitrans_golden.py --period pilot-sample
.\.venv\Scripts\pytest tests\parsers\test_seitrans_golden.py -v
```

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
.\.venv\Scripts\python scripts\_find_golden_candidates.py
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

Step 1 plan (`02_step1_plan.md` §3) lists the recommended order:
Correos Express → VASP → Lynda's → etc. For each carrier:

1. Survey one real invoice (or read the relevant section in
   `01_data_exploration.md`).
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
6. Add the new parser to the CLI under `ingest <carrier>`. Reuse
   `_ingest_one`; only the parser instance and default paths differ.

## Memory / state on disk

- `~/.courier_automation/manifest.sqlite` — the manifest of ingested
  files. Survives reboots. Override with
  `$env:COURIER_AUTOMATION_MANIFEST = "C:\path\to\manifest.sqlite"`.
- `%TEMP%\courier_automation_work\` — the writer's working dir.
  Always cleaned up by the `_working_copy` `finally` block. Non-empty
  between runs only on a crash mid-write.
- `%TEMP%\pytest-of-<user>\` — pytest's scratch from `tmp_path`.
  Last 3 runs kept; rotates automatically.

## Operator workflow (once n8n is wired)

This is what production will look like. Not built yet.

1. n8n watches the corporate inbox labels `Couriers/Seur` and
   `Couriers/Seitrans` (and a label per active courier as we add them).
2. New invoice email → download attachment → POST to a Python ingest
   endpoint (or run `python -m courier_automation.cli ingest <carrier>
   --file ...` if the runner is co-located).
3. On exit code 0 → mark email as ingested, send "rows appended" notification.
4. On exit code 2 / 4 / 5 → send loud alert with the error message; do
   not retry automatically.
5. On exit code 3 → retry after 5 minutes (lock contention).

The Python pipeline doesn't care which orchestrator drives it —
n8n, Power Automate, Windows Task Scheduler, or `cron` all work the
same way.
