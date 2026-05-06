# Current status — 2026-05-05

## TL;DR

**Six courier parsers in tree:** Seur, Seitrans, Correos Express, UPS (UK),
Wwex (US), Spring (FR). Three have full pipelines (parser → CLI → golden test
against real Datos); three are at varying degrees of partial-implementation.
n8n / Power Automate orchestrator not started; no parser has yet written to a
real production workbook.

- **99 tests passing**, 0 failing, 0 skipped. Ruff clean.

| Carrier | Parser | CLI | Golden | Notes |
|---|---|---|---|---|
| Seur | ✅ | ✅ | ✅ 2,948 rows / 5 fixtures (D/AD/FR) | Pilot. |
| Seitrans | ✅ | ✅ | ✅ 62 rows / 2 fixtures | `Q Expediciones` excluded (operator post-processing). |
| Correos Express | ✅ | ✅ | ✅ 1,364 rows / 1 fixture | Phone columns + `Column58` excluded. |
| UPS (UK) | ✅ CSV, 250 cols | ✅ | ✅ 3 rows / 1 fixture | Place Holder columns excluded. Numeric-string normalisation handles leading-zero/`.0` mismatches. |
| Wwex (US) | ✅ multi-format, 42→44 mapping | ✅ | ✅ 4 stable cols / 425 rows | Heavy operator post-processing on the historical sheet (Ship Date filled from external sources, addresses cleaned, weights overridden) — golden compares only the operator-stable columns: `Source System`, `Account#`, `Tracking#`, `Package Count`. |
| Spring (FR) | ⚠️ raw passthrough | ❌ | ⏸ deferred | 22-col single-sheet parser works against real fixtures; mapping to the historical 24-col `INVOICES` schema and the separate 114-col `REPORT` operations stream are TODO. |

## What's done

### Code

| Module | Status |
|---|---|
| `parsers/base.py` | ✅ Done. Adapter interface, `ParseResult`, `assert_schema`, file-hash helper, Seur invoice-number regex, **shared `to_clean_string`** helper. |
| `parsers/seur.py` | ✅ Done. 68-column passthrough parser; `SEUR_COLUMNS`; dtype groups; public `coerce_seur_dtypes` shared with the golden script. |
| `parsers/seitrans.py` | ✅ Done. 21-col raw → 25-col historical (rename `_` → ` ` except `DOCUMENTO_DATA`; add `Tipo expedición`/`Q Expediciones`/`Año`/`Mes` derived). Public `coerce_seitrans_dtypes`. Invoice number derived from data (filenames are inconsistent), namespaced by year. |
| `parsers/correos.py` | ✅ Done. 51-col raw → 58-col historical. Header band on rows 0-2 (real header is row 2). 6 derived columns (`Año, Mes, Tipo Bulto, Tipo Exp., Q Expediciones, País`) computed from `F.ADMISION` / `PESO KILOS` / `C. PAIS`. Spanish 5-digit postcode normaliser (`_to_postcode`). |
| `parsers/ups.py` | ✅ Done. Headerless 250-column CSVs. Column tuple hard-coded from production `Data` sheet. Auto-detected dtype groups by name keywords (`Date`, `Amount`, `Weight`, …). Each CSV asserts a single Invoice Number. `Invoice Number` coerced to Int64 to match production storage. |
| `parsers/wwex.py` | ✅ Done. Multi-format reader (`.xlsx`, `.xls` via xlrd, `.csv` semicolon-separated). 42-col raw → 44-col historical mapping reverse-engineered: 21 direct renames, derived `Source System` constant + `Domestic/International` from country comparison + `Weight per package = TOTAL_WEIGHT / PACKAGE_COUNT` + `Ship Date` coalesced across SHIPMENT_DATE → ACTUAL_PICKUP_DATE → CREATION_DATE → ACTUAL_DELIVERY_DATE. 20 operator-filled columns emitted as None. |
| `parsers/spring.py` | ⚠️ Partial. 22-col single-sheet parser; sheet name varies per invoice (read by index 0). Mapping to historical 24-col `INVOICES` schema and the 114-col `REPORT` operations stream are TODO. |
| `parsers/plausibility.py` | ✅ Done. Three rule kinds (`no_null`, `min_non_null_rate`, `date_range`), aggregated error message. |
| `manifest/registry.py` | ✅ Done. SQLite + WAL, idempotent `register`, `supersedes` for reissue detection. |
| `store/workbook_appender.py` | ✅ Done. Sidecar lock, working-copy in `%TEMP%`, atomic replace, schema validation against the live workbook before writing. |
| `cli.py` | ✅ Done. `ingest seur`, `ingest seitrans`, `ingest correos`, `ingest ups`, `ingest wwex` — Seur/Seitrans/Correos use `--file/--month/--folder/--dry-run`; UPS and Wwex use `--file/--folder/--dry-run` (their folder layouts have unique conventions). UPS writes to the `Data` sheet (UPS-specific naming), Wwex also writes to `Data`. Spring **not yet wired into the CLI** pending column mapping. Exit codes 0–5. |
| `scripts/extract_seur_golden.py` | ✅ Done. Matches on `(year, trailing-int)`. |
| `scripts/extract_seitrans_golden.py` | ✅ Done. Matches on `(year, DOCUMENTO_NUMERO)`. |
| `scripts/extract_correos_golden.py` | ✅ Done. Matches on the `Nº ENVIO` set. |
| `scripts/extract_ups_golden.py` | ✅ Done. Matches on Invoice Number (normalised to int across both sides). |
| `scripts/extract_wwex_golden.py` | ✅ Done. Matches on the `Tracking#` set. |
| `scripts/_find_golden_candidates.py` | ✅ Done (Seur dev tool). |

### Tests (99 passing)

| Suite | Count | What it covers |
|---|---|---|
| `tests/parsers/test_seur.py` | 14 | Schema, dtypes, leading zeros, schema mismatch (renamed/extra column), invoice-number regex (D/AD/FR), file hash, missing file, missing sheet, real-data parametrized over 5 fixtures. |
| `tests/parsers/test_seur_golden.py` | 1 | Element-wise comparison of parser output to a 2,948-row Datos slice. |
| `tests/parsers/test_seitrans.py` | 9 | Schema, dtypes, `Tipo expedición=Pallet` always, schema-mismatch diff, invoice-number namespacing by year, missing sheet, real-data parametrized over 2 fixtures. |
| `tests/parsers/test_seitrans_golden.py` | 1 | Element-wise comparison of parser output to a 62-row Datos slice (excludes `Q Expediciones` — see "Known gaps"). |
| `tests/parsers/test_correos.py` | 11 | Schema, dtypes, weight-bucket rules (`001 KG` … `MÁS 200 KG`), `Tipo Exp.` 50 kg threshold, `País` lookup, derived columns, schema mismatch, real-data parametrized. |
| `tests/parsers/test_correos_golden.py` | 1 | Element-wise vs 1,364-row Datos slice (excludes phone columns + `Column58` for operator post-processing). |
| `tests/parsers/test_ups.py` | 7 | 250-col schema, dtypes, real-data CSV parses, multi-Invoice-Number rejection. |
| `tests/parsers/test_ups_golden.py` | 1 | Element-wise vs 3-row Data slice (excludes Place Holder columns; numeric-string normalisation handles leading-zero/`.0` mismatches). |
| `tests/parsers/test_wwex.py` | 2 | Raw + historical column tuples; real `.xlsx` smoke test verifies derived columns (Source System constant, DOM/INT). |
| `tests/parsers/test_wwex_golden.py` | 1 | Operator-stable columns only (Source System, Account#, Tracking#, Package Count) against 425-row Data slice. |
| `tests/parsers/test_spring.py` | 3 | Column tuple, real `.XLSX` smoke test (parametrized — case-insensitive glob hits one fixture twice on Windows). |
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

`tests/fixtures/correos/raw/` — 1 real Correos invoice
(`2025_01_31 FAC_UNICO_F2501_14307.xlsx`, 1,433 shipments).
`tests/fixtures/correos/golden/pilot-sample-datos.parquet` — 1,364 rows
(the user filters ~5% of shipments out manually when pasting).

`tests/fixtures/ups/raw/` — 1 real UPS billing CSV
(`Invoice_3961958_012225.csv`, smallest in 2025/01).

`tests/fixtures/wwex/raw/` — 1 real Wwex `.xlsx`
(`2025_05_31 shipment_detail_report.xlsx`).

`tests/fixtures/spring/raw/` — 1 real Spring `.XLSX`
(`E2509827_ES_Details of Invoice_O_110003790_2511251351.XLSX`).

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
| **Spring** parser is raw passthrough, not in CLI. | High before production cutover. | Per-invoice file is 22 cols; historical `INVOICES` sheet is 24 cols (small mapping); separate 114-col `REPORT` operations stream is a second parser entirely. Apply the same playbook as Wwex: pair raw and historical for one shipment, derive the column mapping. |
| **Wwex Ship Date** is operator-derived from external sources. | Low (documented). | Parser coalesces SHIPMENT_DATE → ACTUAL_PICKUP_DATE → CREATION_DATE → ACTUAL_DELIVERY_DATE; ~36% of rows still differ from real Data because the operator pulls dates from the UPS tracking website / shipping label. Excluded from the golden comparison. |
| **Wwex weight/charge values** occasionally edited by operator. | Low. | <1% of rows have manual overrides on `Billed Weight`, `Package Weight`, etc. Wwex golden test checks only 4 operator-stable columns. The parser's correctness on derivable columns is verified by the synthetic test. |
| Seitrans `Q Expediciones` differs by ~8% from real Datos. | Low (documented). | The user marks `1` only on the *first global* occurrence of each `SPEDIZIONE NUMERO` across all of Datos; the parser only sees one file at a time. Per-file dedup is the best we can do without global state. The column is excluded from the Seitrans golden comparison. |
| Correos phone columns + `Column58` differ from real Datos. | Low (documented). | Operator typed-in `+34` prefix on phones, occasional manual overrides on country code. Both excluded from golden comparison. |
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
