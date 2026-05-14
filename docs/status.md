# Current status — 2026-05-14

## TL;DR

**Eight courier parsers, all CLI-wired, all in the unified build.** The
per-carrier `pipeline` command (parse → duplicate guard → master append +
parquet → unified rebuild) is built; the cross-courier `unified` package
combines every carrier into one canonical fact table; the **collector**
(n8n Cloud + a local Task Scheduler runner) feeds invoices in
automatically for the five email carriers. UPS / WWEX / Royal Mail are
still fetched by hand into the same inbox.

- **145 tests passing.** 5 pre-existing failures (see "Known gaps"): 2
  CLI tests that assert manifest behaviour (the manifest is disabled),
  3 golden/schema-drift parser tests. 1 concurrency test is flaky under
  load (passes in isolation). Ruff clean on all package + script files.

| Carrier | Parser | `ingest` | `pipeline` | Golden | Unified | Notes |
|---|---|---|---|---|---|---|
| Seur | ✅ | ✅ | ✅ | ✅ 2,948 rows / 5 fixtures | ✅ | Pilot. `destination_country` now ~100% via plaza-code mapping. |
| Seitrans | ✅ | ✅ | ✅ | ✅ 62 rows / 2 fixtures | ✅ | `Q Expediciones` excluded (operator post-processing). Header `sniff()` for intake. |
| Dachser | ✅ | ✅ | ✅ | ⏸ deferred | ✅ | Two raw layouts. Header `sniff()` for intake. **Live schema drift** in the 2026-02 ES invoice — see Known gaps. |
| Correos Express | ✅ | ✅ | ✅ | ✅ 1,364 rows / 1 fixture | ✅ | Phone columns + `Column58` excluded. |
| UPS (UK) | ✅ | ✅ | ✅ | ✅ 3 rows / 1 fixture | ✅ | 250-col CSV. Unified normalizer aggregates charge lines → shipments. |
| Wwex (US) | ✅ | ✅ | ✅ | ✅ 4 stable cols / 425 rows | ✅ | Heavy operator post-processing on the historical sheet. |
| Spring (FR) | ⚠️ raw passthrough | ✅ | ✅ | ⏸ deferred | ✅ | 22-col parser works against real fixtures; full historical mapping still TODO. |
| Royal Mail (UK) | ✅ | ✅ | ✅ rebuild-mode | ⏸ deferred | ✅ | Pipe-CSV, docket grain. `pipeline` rebuilds the master from scratch each run; unified normalizer keeps docket grain. |

## What's done

### Code

| Module | Status |
|---|---|
| `parsers/base.py` | ✅ Adapter interface, `ParseResult`, `assert_schema`, file-hash helper, Seur invoice-number regex, shared `to_clean_string`. |
| `parsers/{seur,seitrans,dachser,correos,ups,wwex,spring,royalmail}.py` | ✅ Eight carrier parsers. `seitrans` + `dachser` also expose a header-only `sniff()` for intake classification. (Spring's full historical mapping is still partial — see Known gaps.) |
| `parsers/plausibility.py` | ✅ Three rule kinds (`no_null`, `min_non_null_rate`, `date_range`), aggregated error message. |
| `carriers.py` | ✅ `CARRIERS` registry — one `CarrierConfig` per carrier; single source of truth for paths, sheet names, file globs, guard keys, classification rules. |
| `exit_codes.py` | ✅ Shared process exit codes 0–7. |
| `manifest/registry.py` | ✅ Built and tested (SQLite + WAL, `register` / `has_seen` / `supersedes`). **Currently disabled** via a `_NullRegistry` shim — the `pipeline` duplicate guard covers idempotency in the meantime. |
| `store/workbook_appender.py` | ✅ OneDrive-safe append (sidecar lock, working copy, atomic replace); `export_rows` / `export_parquet`. |
| `intake.py` | ✅ `classify_invoice_file` (filename regex + parser `sniff()` probe) and `place_invoice_file` (file into `Facturas/<YYYY>/<NN> - <Mes>/`, hash-dedup, conflict quarantine). |
| `pipeline.py` | ✅ `run_pipeline` — the per-carrier orchestrator: parse → duplicate guard → master append + parquet → unified rebuild. Royal Mail rebuild path. |
| `cli.py` | ✅ Eight `ingest <carrier>` subcommands + the `pipeline` command. Thin shell over `carriers` + `pipeline`. |
| `unified/` | ✅ `schema.py` (25-col canonical, EUR columns), `normalizers/` (8 carriers), `service_classifier.py`, `fx_rates.py`, `build.py` (kept / refunds / rejections split + `manifest.json`). |
| `scripts/run_collector.py` | ✅ Scheduled local runner — scan inbox, classify + file, sweep `pipeline` for all 8, rebuild unified, log + SMTP summary. |
| `scripts/build_royalmail_master.py` | ✅ Royal Mail master rebuild (delegates to `pipeline.rebuild_royalmail_master`). |
| `scripts/backfill/backfill_*_parquet.py` | ✅ Per-carrier historical parquet backfill from the master sheets; `backfill_all_parquet.py` chains them. |
| `scripts/golden/extract_*_golden.py` | ✅ Golden-snapshot extractors for seur, seitrans, correos, ups, wwex. |
| `n8n/courier-collector.workflow.json` | ✅ Importable n8n Cloud workflow (Outlook → OneDrive `_inbox/`). |

### Tests (145 passing)

Per-parser unit + golden suites (`tests/parsers/`), the manifest suite
(`tests/manifest/`), the store suite (`tests/store/`), the CLI suite
(`tests/cli/`), and:

| Suite | Covers |
|---|---|
| `tests/test_pipeline.py` | Carrier-registry completeness, the duplicate guard (no/partial/full overlap, month fallback, missing-column skip), `_normalize_carrier`'s no-parquet path. |
| `tests/test_royalmail_normalizer.py` | The Royal Mail unified normalizer — canonical shape, docket grain (`Quantity` → `bultos_count`), GBP/GB constants, penalty-line rejection, negative `Net Value` not silently kept. |
| `tests/test_intake.py` | `classify_invoice_file` per carrier (filename patterns + the seitrans/dachser sniff probe + cross-probe negatives + unknown → None); `place_invoice_file` month-from-content, re-drop idempotency, conflict quarantine; `quarantine_file` name collisions. |
| `tests/test_run_collector.py` | `CarrierRun` status mapping, `RunReport.errors` / `nothing_happened`, `_is_ready` inbox-scan skip rules, the 8-carrier sweep (incl. one carrier crashing without aborting the sweep), unified-rebuild gating. |

### Documentation

- [docs/architecture.md](architecture.md) ✅ — design, the layered pipeline, module walkthrough, tools, key decisions.
- [docs/pipeline.md](pipeline.md) ✅ — the `pipeline` command + the unified build, end to end.
- [docs/automation.md](automation.md) ✅ — the collector: n8n Cloud workflow, the local runner, setup, operator workflow.
- [docs/workflow.md](workflow.md) ✅ — daily recipes: setup, test, ingest, pipeline, collector, adding a courier.
- [docs/drift_handling.md](drift_handling.md) ✅ — drift classes, the detection layers, the future LLM-triage strategy.
- [docs/power_bi.md](power_bi.md) ✅ — repointing the per-courier Power BI datasets to the parquet substrate.
- [docs/status.md](status.md) ✅ — this file.

## What's untested against production

| Item | Risk | Mitigation today |
|---|---|---|
| The `pipeline` master-append path has not yet done a real net-new append to a production master. | openpyxl load+save on the 113 MB Seur workbook; `.pbix` refresh after a programmatic write. | The append path is unit-tested; the Royal Mail *rebuild* path has run against the real master; appends in this session tripped the duplicate guard (already-ingested months) so didn't write. Needs a genuine new-month run. |
| The n8n Cloud workflow has not run end-to-end. | OneDrive OAuth, attachment encoding, folder IDs. | The Python half (`intake` + `run_collector`) is verified end-to-end against a temp inbox. n8n needs an Azure AD app registration first. |
| The collector SMTP email has not sent. | M365 SMTP AUTH is off by default. | The runner degrades gracefully (does the ingest, logs, exits non-zero) when SMTP is unset or fails. |

## Known gaps and follow-ups

| Gap | Severity | Notes |
|---|---|---|
| **Dachser 2026-02 ES invoice schema drift.** | Medium. | `2026_02_ES_112271952.xlsx` has the raw header `Nº de pedido`, which `_RENAME_CLEAN` doesn't map (a `º` character-variant mismatch) → the `Pedido` column goes missing. The parser now raises a clean `SchemaMismatch` (exit 2) instead of a bare `KeyError`, and the collector surfaces it as `error(2)` instead of crashing the sweep — but `pipeline --carrier dachser` fails until the rename map is fixed. |
| **Manifest disabled.** | Low (covered). | The `pipeline` duplicate guard is the idempotency net. It's heuristic (invoice-id or month-row overlap), not a hash-exact record. Re-enable the `ManifestRegistry` alongside it once the parsers stabilise. |
| **n8n Cloud not yet wired.** | Medium (blocks full automation). | The workflow JSON is ready to import, but n8n Cloud → Outlook/OneDrive needs an Azure AD app registration in the artero.com tenant — likely an IT request. |
| **FX rates are placeholders.** | Medium. | `unified/fx_rates.py` ships approximate early-2026 GBP/USD rates marked `>>> REVIEW THESE RATES <<<`. The `*_eur` columns aren't trustworthy until the business sets the frozen rates. |
| **UPS / WWEX / Royal Mail fetched manually.** | Low (by decision). | No portal automation — the operator downloads and drops files into `_inbox/`. Phasing portal scrapers is future work. |
| **5 pre-existing test failures.** | Low. | `test_ingest_is_idempotent` + `test_manifest_conflict_exits_4` assert manifest behaviour that the `_NullRegistry` shim no-ops; `test_correos_golden`, `test_ups_golden`, `test_ups::test_rejects_csv_with_wrong_column_count` are golden/schema drift. All predate the pipeline + collector work. |
| **Spring** full historical mapping incomplete. | Medium. | The 22-col parser works; mapping to the 24-col `INVOICES` schema and the separate 114-col `REPORT` stream are still TODO. |
| Seitrans `Q Expediciones`, Correos phone columns, Wwex Ship Date | Low (all documented). | Operator post-processing the parser can't reproduce; excluded from the respective golden comparisons. |
| Seur `--month YYYY-MM` vs the flat 2025+ folder layout. | Low. | The collector sidesteps this — `intake.place_invoice_file` always files into `<YYYY>/<NN> - <Mes>/` subfolders, which the pipeline's discovery resolves. |

## Roadmap

### Immediate

1. **Fix the Dachser 2026-02 rename-map drift** so `pipeline --carrier dachser` runs again.
2. **Set the real FX rates** in `unified/fx_rates.py` and rebuild unified.
3. **Wire n8n Cloud** — Azure AD app registration, import the workflow, create the Outlook + OneDrive credentials, set the folder IDs.
4. **Enable M365 SMTP AUTH** on a sending mailbox for the collector summary email.
5. **First real production run** — a genuine new-month `pipeline` append to a master, in shadow mode (a copy) then promoted.

### Next

6. **Re-enable the manifest** alongside the duplicate guard once the parsers are stable.
7. **Power BI repoint** — point the per-courier datasets at `data/<carrier>/` parquet (see [power_bi.md](power_bi.md)).
8. **Portal automation** for UPS / WWEX / Royal Mail (currently manual).
9. **Spring** — finish the historical-schema mapping.

### Later

10. Option A → Option B — per-courier DuckDB with the `.xlsx` regenerated from queries.
11. LLM-assisted drift triage, out of the runtime path (see [drift_handling.md](drift_handling.md)).

## Decision log

| Date | Decision | Where it lives |
|---|---|---|
| 2026-05-05 | Pilot Seur, Option A storage, n8n preferred. venv + pip. LLM out of the runtime path. | [architecture.md](architecture.md), [drift_handling.md](drift_handling.md). |
| 2026-05-14 | `carriers.CARRIERS` registry — one source of truth for per-carrier config. | [carriers.py](../courier_automation/carriers.py), [architecture.md](architecture.md) §10. |
| 2026-05-14 | The `pipeline` command is the per-carrier production path: master append **and** parquet **and** unified in one run. | [pipeline.md](pipeline.md). |
| 2026-05-14 | Duplicate guard (heuristic, reads the master) is the idempotency net while the manifest stays disabled. | [pipeline.py](../courier_automation/pipeline.py), [architecture.md](architecture.md) §9. |
| 2026-05-14 | Royal Mail stays at docket grain in the fact table; the `pipeline` rebuilds its master from scratch each run. | [pipeline.md](pipeline.md), [architecture.md](architecture.md) §11. |
| 2026-05-14 | Unified schema carries both native-currency and frozen-rate EUR columns. | [unified/schema.py](../unified/schema.py), [unified/fx_rates.py](../unified/fx_rates.py). |
| 2026-05-14 | Collector split: n8n Cloud fetches (dumb pipe), a local Task Scheduler runner does classification + ingest. UPS/WWEX/Royal Mail stay manual. | [automation.md](automation.md), [architecture.md](architecture.md) §8. |
| 2026-05-14 | `Operations - Couriers/` is written only via the OneDrive-safe appender and `intake.place_invoice_file`. | [architecture.md](architecture.md) §7. |
