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

Always operate on a copy of the workbook for now — the production file
is off-limits until end-to-end validation is signed off.

```powershell
# Copy the production workbook to a scratch path
Copy-Item "Operations - Couriers\01. Seur\NEW Análisis expediciones SEUR.xlsx" `
          "$env:TEMP\seur-smoke.xlsx"

# Dry run first — parses + manifest-checks but doesn't write
.\.venv\Scripts\python -m courier_automation.cli ingest seur `
    --file "Operations - Couriers\01. Seur\Facturas\2025\0289992025D0235697.xlsx" `
    --workbook "$env:TEMP\seur-smoke.xlsx" `
    --dry-run

# Real run
.\.venv\Scripts\python -m courier_automation.cli ingest seur `
    --file "Operations - Couriers\01. Seur\Facturas\2025\0289992025D0235697.xlsx" `
    --workbook "$env:TEMP\seur-smoke.xlsx"

# Re-run — should report "already ingested"
.\.venv\Scripts\python -m courier_automation.cli ingest seur `
    --file "Operations - Couriers\01. Seur\Facturas\2025\0289992025D0235697.xlsx" `
    --workbook "$env:TEMP\seur-smoke.xlsx"
```

Open `seur-smoke.xlsx` in Excel afterwards and confirm the new rows
appear in `Datos`. Open `Expediciones SEUR.pbix` (also pointed at the
scratch path) and confirm refresh still works.

## CLI exit codes

| Code | Meaning | What to do |
|---|---|---|
| 0 | Success | Nothing. |
| 1 | Usage error | Read the message; fix the args. |
| 2 | Schema mismatch | The carrier changed columns. Update `SEUR_COLUMNS` in `parsers/seur.py`. The diff in the message tells you what changed. |
| 3 | Workbook lock timeout | Another ingest run, or someone has the lock file. Wait or remove the stale `.courier-automation.lock`. |
| 4 | Manifest conflict | Carrier reissued the same invoice with new content. Inspect both files; if the new one is canonical, delete the old manifest row and re-ingest. |
| 5 | Plausibility check failed | Either the data really is bad, or rules are too tight. The message lists every offending column. See [drift_handling.md](drift_handling.md). |

## Adding new fixtures (for the parser test)

```powershell
# Pick a small invoice from any year folder
Copy-Item "Operations - Couriers\01. Seur\Facturas\2025\0289992025D0177161.xlsx" `
          tests\fixtures\seur\raw\

# Re-run the parser tests — the parametrized real-data test will pick it up automatically
.\.venv\Scripts\pytest tests\parsers\test_seur.py -v
```

The fixture set deliberately includes one of each filename prefix (D,
AD, FR) so the regex stays exercised against real data. Don't remove
those.

## Refreshing the golden snapshot

The golden test compares parser output to a parquet slice of `Datos`.
The slice is keyed on the fixtures currently in `tests/fixtures/seur/raw/`,
so any change to that set requires regenerating the parquet:

```powershell
.\.venv\Scripts\python scripts\extract_seur_golden.py --period pilot-sample

.\.venv\Scripts\pytest tests\parsers\test_seur_golden.py -v
```

Reading the 113 MB workbook takes 1–2 minutes. The `--period` arg is
just a tag for the output filename; the test globs `*-datos.parquet`
and uses the most recent one.

If the golden test fails after a parser change, the assertion message
points at the column and first differing row. Common causes:

- A code field is stored as numeric in one source but text in the other
  (handled by `_to_clean_string`).
- A new STRING_COLUMN needs to be added because Excel coerced its values.
- The Datos sheet itself drifted — verify against the user.

## Picking a candidate fixture

```powershell
.\.venv\Scripts\python scripts\_find_golden_candidates.py
```

Prints the top invoices that exist in *both* `Datos` and `Facturas/`,
ranked by row count. Useful when picking a richer fixture for the golden
test or replacing a fixture that fell out of `Datos`.

## Adding a new courier

Roughly the parser pattern from Seur, repeated. The Step 1 plan
(`02_step1_plan.md` §3) lists the recommended order: Seitrans, Correos
Express, VASP, etc. For each:

1. Survey one real invoice (or read the relevant section in
   `01_data_exploration.md`).
2. Write `courier_automation/parsers/<carrier>.py` with `CARRIER_COLUMNS`,
   dtype groups, plausibility rules, and a `<Carrier>Parser` class.
3. Add a `<carrier>_invoice_factory` fixture in `tests/conftest.py`.
4. Mirror `tests/parsers/test_seur.py` for the new carrier.
5. Once a few real invoices land in fixtures, copy `extract_seur_golden.py`
   to `extract_<carrier>_golden.py` and adjust the matching strategy
   (some carriers use different invoice-number formats).
6. Add the new parser to the CLI under `ingest <carrier>`.

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

1. n8n watches the corporate inbox label `Couriers/Seur`.
2. New invoice email → download attachment → POST to a Python ingest
   endpoint (or `python -m courier_automation.cli ingest seur --file ...`
   if the runner is co-located).
3. On exit code 0 → mark email as ingested, send "rows appended" notification.
4. On exit code 2 / 4 / 5 → send loud alert with the error message; do
   not retry automatically.
5. On exit code 3 → retry after 5 minutes (lock contention).

The Python pipeline doesn't care which orchestrator drives it —
n8n, Power Automate, Windows Task Scheduler, or `cron` all work the
same way.
