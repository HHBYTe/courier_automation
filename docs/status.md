# Current status — 2026-05-05

## TL;DR

**Seur and Seitrans pipelines are both code-complete and golden-tested
against real production data.** Neither has yet written to the real
production workbook. n8n / Power Automate orchestrator and the remaining
9 couriers not started.

- **73 tests passing**, 0 failing, 0 skipped.
- Seur golden test passes against **2,948 real `Datos` rows** from 5 real
  Seur fixtures covering all three filename prefixes (D, AD, FR).
- Seitrans golden test passes against **62 real `Datos` rows** from 2 real
  Seitrans fixtures spanning both observed filename variants
  (space-separated and underscore-separated).
- Ruff clean.

## What's done

### Code

| Module | Status |
|---|---|
| `parsers/base.py` | ✅ Done. Adapter interface, `ParseResult`, `assert_schema`, file-hash helper, Seur invoice-number regex, **shared `to_clean_string`** helper. |
| `parsers/seur.py` | ✅ Done. 68-column passthrough parser; `SEUR_COLUMNS`; dtype groups; public `coerce_seur_dtypes` shared with the golden script. |
| `parsers/seitrans.py` | ✅ Done. 21-col raw → 25-col historical (rename `_` → ` ` except `DOCUMENTO_DATA`; add `Tipo expedición`/`Q Expediciones`/`Año`/`Mes` derived). Public `coerce_seitrans_dtypes`. Invoice number derived from data (filenames are inconsistent), namespaced by year. |
| `parsers/plausibility.py` | ✅ Done. Three rule kinds (`no_null`, `min_non_null_rate`, `date_range`), aggregated error message. |
| `manifest/registry.py` | ✅ Done. SQLite + WAL, idempotent `register`, `supersedes` for reissue detection. |
| `store/workbook_appender.py` | ✅ Done. Sidecar lock, working-copy in `%TEMP%`, atomic replace, schema validation against the live workbook before writing. |
| `cli.py` | ✅ Done. `ingest seur` and `ingest seitrans`, each with `--file/--month/--folder/--dry-run`, exit codes 0–5. |
| `scripts/extract_seur_golden.py` | ✅ Done. Reads from `Operations - Couriers/`, writes to `tests/fixtures/seur/golden/`. Matches on `(year, trailing-int)`. |
| `scripts/extract_seitrans_golden.py` | ✅ Done. Same shape; matches on `(year, DOCUMENTO_NUMERO)` extracted from filename. |
| `scripts/_find_golden_candidates.py` | ✅ Done (Seur dev tool). |

### Tests (73 passing)

| Suite | Count | What it covers |
|---|---|---|
| `tests/parsers/test_seur.py` | 14 | Schema, dtypes, leading zeros, schema mismatch (renamed/extra column), invoice-number regex (D/AD/FR), file hash, missing file, missing sheet, real-data parametrized over 5 fixtures. |
| `tests/parsers/test_seur_golden.py` | 1 | Element-wise comparison of parser output to a 2,948-row Datos slice. |
| `tests/parsers/test_seitrans.py` | 9 | Schema, dtypes, `Tipo expedición=Pallet` always, schema-mismatch diff, invoice-number namespacing by year, missing sheet, real-data parametrized over 2 fixtures. |
| `tests/parsers/test_seitrans_golden.py` | 1 | Element-wise comparison of parser output to a 62-row Datos slice (excludes `Q Expediciones` — see "Known gaps"). |
| `tests/parsers/test_plausibility.py` | 13 | Each rule kind in isolation; Seur integration; aggregate failure messages. |
| `tests/manifest/test_registry.py` | 9 | Register, has_seen, idempotent re-register, supersedes, env-var override, concurrent register (20 threads). |
| `tests/store/test_workbook_appender.py` | 8 | Append, preserve, header validation, missing sheet, lock retry success, lock timeout, lock release, value round-trip. |
| `tests/cli/test_ingest_cli.py` | 14 | Single file, idempotent, dry-run, month batch, auto-discovery of latest month, usage errors, schema/lock/manifest/plausibility exit codes — Seur **and** Seitrans variants. |
| `tests/parsers/test_plausibility.py` integration | 4 | Seur parser end-to-end with planted plausibility violations. |

### Fixtures

`tests/fixtures/seur/raw/` — 5 real Seur invoices:

- `0289992025AD0001394.xlsx` — Andorra prefix, 1 row, in Datos ✓
- `0289992025D0136751.xlsx` — domestic, 5 rows, in Datos ✓
- `0289992025D0179257.xlsx` — domestic, 2,940 rows, in Datos ✓
- `0289992025D0235697.xlsx` — domestic, 1 row, in Datos ✓
- `0289992025FR0020268.xlsx` — France prefix, 1 row, in Datos ✓

`tests/fixtures/seur/golden/pilot-sample-datos.parquet` — 2,948 rows / 280 KB.

`tests/fixtures/seitrans/raw/` — 2 real Seitrans invoices spanning both
filename variants:

- `2025_01_31 3065.xlsx` — space-separated, 8 rows, in Datos ✓
- `2025_06_30_24633.xlsx` — underscore-separated, 54 rows, in Datos ✓

`tests/fixtures/seitrans/golden/pilot-sample-datos.parquet` — 62 rows.

### Documentation

- [docs/architecture.md](architecture.md) ✅ — design, modules, tools, decisions.
- [docs/workflow.md](workflow.md) ✅ — daily recipes for both couriers.
- [docs/status.md](status.md) ✅ — this file.
- [docs/drift_handling.md](drift_handling.md) ✅ — drift detection layers + future LLM-triage.

## What's untested against production

| Item | Risk | Mitigation today |
|---|---|---|
| Writer has never touched the 113 MB Seur workbook (or the 569 KB Seitrans workbook). | Performance, VBA / named ranges / table refs, `.pbix` refresh after programmatic write. | Smoke test on a `Copy-Item` of each workbook before pointing the writer at the real one. |
| End-to-end ingest of a freshly-emailed invoice (collector → parser → store). | Email-to-disk path, attachment naming, encoding. | n8n / Power Automate workflow not built yet — collector layer is mocked by `--file <path>`. |

## Known gaps and follow-ups

| Gap | Severity | Notes |
|---|---|---|
| Seitrans `Q Expediciones` differs by ~8% from real Datos. | Low (documented). | The user marks `1` only on the *first global* occurrence of each `SPEDIZIONE NUMERO` across all of Datos; the parser only sees one file at a time. Per-file dedup is the best we can do without global state. The column is excluded from the Seitrans golden comparison. |
| Seur `--month YYYY-MM` doesn't match the flat 2025+ folder layout. | Medium. Pilot can use `--file` or auto-discovery for now. | The 2022/2023 Seur Facturas have `MM - <Mes>/` subfolders; 2025/2026 are flat (one xlsx per invoice in the year folder). The Seitrans CLI handles flat layouts because Seitrans Facturas are always flat. Either add a `--year` mode for Seur or parse `Fecha Factura` to filter. |
| Plausibility rule for `Fecha Servicio` (Seur) is 95% non-null. | Low. | Empirically passes for current fixtures but might be too strict for years with more cancelled-shipment rows. Tune from a longer baseline. |
| No mechanism for "this invoice was reissued and the new file IS canonical". | Low. | CLI exit 4 forces human review. The user manually deletes the manifest row to proceed. Could add `--accept-supersedes` later. |
| Single-machine deployment assumed. | Low. | The lock file works on a single host. Cross-host coordination would need a real lock service — out of scope for the pilot. |

## Roadmap

### Immediate (next 1-2 weeks)

1. **Smoke-test the writer against copies of the real workbooks** (Seur 113 MB, Seitrans 569 KB). Confirm openpyxl load+save times, `.pbix` refresh.
2. **Decide on the orchestrator.** n8n if Artero IT runs one; Power Automate otherwise. Wire trigger → fetch → POST-to-Python.
3. **Production cutover for Seur and Seitrans.** First fully-automatic monthly ingest in shadow mode (writes to a copy; user diffs against the real workbook), then promote to real.

### Step 1 expansion (weeks 2–6)

4. **Next parser: Correos Express** (lowest volume, simplest format per `01_data_exploration.md`). Establish the pattern of reusing the rename-derive-coerce flow.
5. **Fix Seur `--month` for flat year folders** — add `--year YYYY` mode, or parse `Fecha Factura` per file.
6. **Decision: Option A → Option B.** Once three couriers run cleanly, evaluate moving to per-courier DuckDB with the `.xlsx` regenerated from queries.

### Step 1 long tail

7. Remaining 8 couriers in the order specified by `02_step1_plan.md`: VASP, Lynda's, Express Catalan, Dachser, DPD France, UPS (UK), Wwex, Spring.
8. Backfill historical invoices that aren't yet in `Datos` (opt-in `cli.py backfill --courier <c> --from 2019`).

### Step 2 / 3 (out of scope here)

9. Per-courier Power BI continues to read from the workbook (no UX change for the user).
10. Unified cross-courier dataset, global summary report.

## Decision log

| Date | Decision | Where it lives |
|---|---|---|
| 2026-05-05 | Pilot Seur, Option A storage, no Amazon, n8n preferred. | [02_step1_plan.md](../02_step1_plan.md) §8 + [architecture.md](architecture.md). |
| 2026-05-05 | venv + pip + pinned reqs (no uv/Poetry). Internal tool, real-data fixtures (no anonymisation). | [architecture.md](architecture.md) tools table. |
| 2026-05-05 | LLM out of the runtime path; reserved for offline drift-triage. | [drift_handling.md](drift_handling.md). |
| 2026-05-05 | `Operations - Couriers/` is read-only. | Top-level constraint, enforced by tests + CLI defaults. |
| 2026-05-05 | Seur golden test passes (2,948 rows, 5 fixtures). | This document. |
| 2026-05-05 | Seitrans golden test passes (62 rows, 2 fixtures); `Q Expediciones` excluded from comparison because it's operator-post-processed cross-file. | [test_seitrans_golden.py](../tests/parsers/test_seitrans_golden.py). |
| 2026-05-05 | Seitrans invoice number derived from data (`f"{year}-{DOCUMENTO_NUMERO}"`), not filename. Filenames are inconsistent. | [seitrans.py](../courier_automation/parsers/seitrans.py). |
| 2026-05-05 | `_*` filename prefix marks user-scratch / dev tools — excluded from ruff via `ruff.toml`. | [ruff.toml](../ruff.toml). |
