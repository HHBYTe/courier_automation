# Current status — 2026-05-05

## TL;DR

Seur Python pipeline is **code-complete and golden-tested against real
production data**. Has not yet written to the real production workbook.
n8n / Power Automate orchestrator and other couriers not started.

- **60 tests passing**, 0 failing, 0 skipped.
- Golden test passes against **2948 real `Datos` rows** from 5 real Seur
  fixtures covering all three filename prefixes (D, AD, FR).
- Ruff clean.

## What's done

### Code

| Module | Status |
|---|---|
| `parsers/base.py` | ✅ Done. Adapter interface, `ParseResult`, `assert_schema`, file-hash helper, Seur invoice-number regex. |
| `parsers/seur.py` | ✅ Done. 68-column passthrough parser; `SEUR_COLUMNS`; dtype groups; `coerce_seur_dtypes` (public, shared with the golden script); `_to_clean_string` for code-vs-text normalisation. |
| `parsers/plausibility.py` | ✅ Done. Three rule kinds (`no_null`, `min_non_null_rate`, `date_range`), aggregated error message. |
| `manifest/registry.py` | ✅ Done. SQLite + WAL, idempotent `register`, `supersedes` for reissue detection. |
| `store/workbook_appender.py` | ✅ Done. Sidecar lock, working-copy in `%TEMP%`, atomic replace, schema validation against the live workbook before writing. |
| `cli.py` | ✅ Done. `ingest seur --file/--month/--dry-run`, exit codes 0–5. |
| `scripts/extract_seur_golden.py` | ✅ Done. Reads from `Operations - Couriers/`, writes to `tests/fixtures/seur/golden/`. Matches on `(year, trailing-int)`. |
| `scripts/_find_golden_candidates.py` | ✅ Done (dev tool). |

### Tests (60 passing)

| Suite | Count | What it covers |
|---|---|---|
| `tests/parsers/test_seur.py` | 14 | Schema, dtypes, leading zeros, schema mismatch (renamed/extra column), invoice-number regex (D/AD/FR), file hash, missing file, missing sheet, real-data parametrized. |
| `tests/parsers/test_seur_golden.py` | 1 | Element-wise comparison of parser output to a 2948-row Datos slice. |
| `tests/parsers/test_plausibility.py` | 13 | Each rule kind in isolation; Seur integration; aggregate failure messages. |
| `tests/manifest/test_registry.py` | 9 | Register, has_seen, idempotent re-register, supersedes, env-var override, concurrent register (20 threads). |
| `tests/store/test_workbook_appender.py` | 8 | Append, preserve, header validation, missing sheet, lock retry success, lock timeout, lock release, value round-trip. |
| `tests/cli/test_ingest_cli.py` | 11 | Single file, idempotent, dry-run, month batch, usage errors, schema/lock/manifest/plausibility exit codes. |
| `tests/parsers/test_plausibility.py` integration | 4 | Seur parser end-to-end with planted plausibility violations. |

### Fixtures

`tests/fixtures/seur/raw/` (5 files, all real Seur invoices):

- `0289992025AD0001394.xlsx` — Andorra prefix, 1 row, in Datos ✓
- `0289992025D0136751.xlsx` — domestic, 5 rows, in Datos ✓
- `0289992025D0179257.xlsx` — domestic, 2940 rows, in Datos ✓
- `0289992025D0235697.xlsx` — domestic, 1 row, in Datos ✓
- `0289992025FR0020268.xlsx` — France prefix, 1 row, in Datos ✓

`tests/fixtures/seur/golden/`:

- `pilot-sample-datos.parquet` — 2948 rows, 280 KB, dtype-coerced slice
  of Datos for the five fixtures above.

### Documentation

- [docs/architecture.md](architecture.md) ✅
- [docs/workflow.md](workflow.md) ✅
- [docs/status.md](status.md) ✅ (this file)
- [docs/drift_handling.md](drift_handling.md) ✅

## What's untested against production

| Item | Risk | Mitigation today |
|---|---|---|
| Writer has never touched the 113 MB production workbook. | Performance (openpyxl on 113 MB), VBA / named ranges / table refs, `.pbix` refresh after programmatic write. | Smoke test on a `Copy-Item` of the workbook before pointing the writer at the real one. |
| End-to-end ingest of a freshly-emailed invoice (collector → parser → store). | Email-to-disk path, attachment naming, encoding. | n8n / Power Automate workflow not built yet — collector layer is mocked by `--file <path>`. |

## Known gaps and follow-ups

| Gap | Severity | Notes |
|---|---|---|
| `--month YYYY-MM` doesn't match the flat 2025+ folder layout. | Medium. Pilot can use `--file` per invoice for now. | The 2022/2023 folders have `MM - <Mes>/` subfolders; 2025/2026 are flat. Either add a `--year` mode (simple) or open every file and filter by `Fecha Factura` (robust). |
| Plausibility rule for `Fecha Servicio` is 95% non-null. | Low. | Empirically passes for current fixtures but might be too strict for years with more cancelled-shipment rows. Tune from a longer baseline once we have one. |
| No mechanism for "this invoice was reissued and the new file IS canonical". | Low. | CLI exit 4 forces human review. The user manually deletes the manifest row to proceed. Could add `--accept-supersedes` later. |
| Single-machine deployment assumed. | Low. | The lock file works on a single host. Cross-host coordination would need a real lock service — out of scope for the pilot. |

## Roadmap

### Immediate (next 1-2 weeks)

1. **Smoke-test the writer against a copy of the real workbook.** Confirm
   openpyxl handles 113 MB load+save in acceptable time, confirm `.pbix`
   refresh still works.
2. **Decide on the orchestrator.** n8n if Artero IT runs one; Power
   Automate otherwise. Wire the trigger → fetch → POST-to-Python loop.
3. **Production cutover for Seur.** First fully-automatic monthly ingest
   in shadow mode (writes to a copy; user diffs against the real
   workbook), then promote to real.

### Step 1 expansion (weeks/months 2–6)

4. **Add the next two parsers** following the plan's sequencing:
   Seitrans, Correos Express. Each gets its own parser, plausibility
   rule set, fixtures, and golden test.
5. **Fix the `--month` mode** for flat year folders (probably add `--year`
   alongside).
6. **Decision: Option A → Option B.** Once 3 couriers run cleanly,
   evaluate moving to per-courier DuckDB with the `.xlsx` regenerated
   from queries.

### Step 1 long tail

7. Remaining 8 couriers in the order specified by `02_step1_plan.md`.
8. Backfill historical invoices that aren't yet in `Datos` (opt-in
   `cli.py backfill --courier seur --from 2019`).

### Step 2 / 3 (out of scope here)

9. Per-courier Power BI continues to read from the workbook (no UX
   change for the user).
10. Unified cross-courier dataset, global summary report.

## Decision log

| Date | Decision | Where it lives |
|---|---|---|
| 2026-05-05 | Pilot Seur, Option A storage, no Amazon, n8n preferred. | [02_step1_plan.md](../02_step1_plan.md) §8 + [architecture.md](architecture.md). |
| 2026-05-05 | venv + pip + pinned reqs (no uv/Poetry). Internal tool, real-data fixtures (no anonymisation). | [architecture.md](architecture.md) tools table. |
| 2026-05-05 | LLM out of the runtime path; reserved for offline drift-triage. | [drift_handling.md](drift_handling.md). |
| 2026-05-05 | `Operations - Couriers/` is read-only. | Top-level constraint, enforced by tests + CLI defaults. |
| 2026-05-05 | Golden test passes (2948 rows, 5 fixtures). | This document. |
