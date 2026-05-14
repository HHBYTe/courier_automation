# Drift handling — current detectors and the future strategy

Couriers ship invoice files in formats they own. Sooner or later one of them
will rename a column, switch from dot to comma decimals, move the header row,
or reissue an invoice. This doc is the playbook for what happens when they do.

## Three classes of drift

| Class | Example | What we want |
|---|---|---|
| **Structural** | Column renamed, added, removed, or reordered. Sheet renamed. | Loud abort. The pipeline is wrong about the world; refuse to write garbage. |
| **Bulk value-level** | All `Peso` values become NaN because the courier switched to comma decimals. | Loud abort. The schema is fine but the data isn't. |
| **Subtle / partial** | A handful of rows have a malformed date; postcodes lose leading zeros at the source. | Best-effort detection. Some classes (lost-zero postcodes) are unrecoverable; we live with them. |

## What we catch today (deterministic, in the runtime path)

Every layer pushes back on something different.

### 1. Structural drift — `assert_schema`

`courier_automation/parsers/base.py` compares the parsed DataFrame's columns
to the parser's hard-coded `expected_columns` tuple. Any difference (missing,
added, reordered) raises `SchemaMismatch` with a clean diff. The CLI maps that
to **exit code 2** and the workbook is never touched.

The same check runs again on the live workbook in
`store/workbook_appender.py` before any append, so a Datos sheet that drifted
out from under us also fails loud.

### 2. Sheet/file-level drift

`SeurParser.parse()` catches `ValueError` from `pd.read_excel` and re-raises as
`ParserError` with the sheet name in the message. CLI exit **1**.

### 3. Reissued invoice — manifest `supersedes`

`manifest/registry.py` tracks `(carrier, invoice_number, file_hash)`. If the
same invoice number appears with a different hash, `supersedes()` returns the
prior hash. The CLI exits **4** instead of double-ingesting. Human decides
whether the new file replaces or augments.

### 4. Bulk value-level drift — `assert_plausible`

`courier_automation/parsers/plausibility.py` runs after dtype coercion with
three deterministic checks:

- **`no_null`** — set of columns where any null is unacceptable (primary-key-
  like fields: invoice number, line number, invoice date). Rejects rows where
  a critical field failed to coerce.
- **`min_non_null_rate`** — per-column floors (defaults to 0.95). A sudden
  drop in non-null rate is the fingerprint of silent NaN coercion. This is
  the main detector for "Seur switched to comma decimals" and "the date format
  changed and half the rows lost their `Fecha Servicio`".
- **`date_range`** — every parsed date must fall in `[2018, 2035]` (Seur).
  Catches mis-parsed dates that landed at 1970-epoch or year 9999.

Failures aggregate into one error message listing every offending column. CLI
exit **5**.

The Seur rule set lives at the bottom of `parsers/seur.py` (`PLAUSIBILITY_*`
constants) so it's reviewable in one place and tunable per courier.

### 5. Golden test — periodic ground-truth check

The golden tests (`tests/parsers/test_<carrier>_golden.py` — seur,
seitrans, correos, ups, wwex) parse real invoices and compare them
row-for-row to parquet snapshots extracted from the production `Datos`
sheets. Any per-cell drift the deterministic checks miss shows up here.
Run them after every parser change.

The Seitrans golden test confirmed the value of this layer in practice:
five distinct schema-level findings (filename inconsistency,
`DOCUMENTO_DATA` not renamed, `Tipo expedición` actually constant, `Mes`
stored as a date not int, mixed date formats causing silent NaT) all
surfaced through golden-test failures during development — none of which
the unit tests or plausibility checks would have caught alone.

### 6. Collector intake — quarantine over guessing

The collector classifies each dropped invoice file to a carrier
(`intake.classify_invoice_file` — filename regex, then a parser
header-`sniff()`). A file that matches no carrier is **quarantined** to
`_inbox/_unclassified/`, never guessed at; a file that classifies but
fails to parse goes to the same place. A name collision with different
content goes to `_inbox/_conflicts/`. The collector's runner reports
all three in its summary email's "ATTENTION NEEDED" section. This is
drift-handling at the *file* level — the runner refuses to mis-file or
mis-parse, and surfaces it for a human, rather than failing silently or
guessing.

### A worked example — Dachser, 2026-02

The live `2026_02_ES_112271952.xlsx` invoice renamed a raw header to
`Nº de pedido`, which the Dachser parser's `_RENAME_CLEAN` map doesn't
cover (a `º` character-variant mismatch), so the `Pedido` column goes
missing. This is textbook **structural drift**: the parser now raises a
clean `SchemaMismatch` (exit 2) with `missing=['Pedido']` instead of a
bare `KeyError`, the collector's sweep records it as `error(2)` and
keeps going, and the summary email flags it. The fix is a one-line
addition to the rename map — exactly the kind of triage the LLM tool
below would eventually automate.

## What we still don't catch

- **Postcode leading zeros** lost upstream by Excel when a cell was stored as
  a number (e.g. `08001` → `8001`). The bytes don't reach us; can't recover.
- **Subtle value drift** within plausibility tolerances — e.g. an off-by-one
  in dates that still parses, a currency conversion that halves all amounts.
  The golden test catches these for one period but they can creep in between
  golden refreshes.
- **Operator post-processing in Datos** — Seitrans's `Q Expediciones` is
  marked `1` only on the first global occurrence of each `SPEDIZIONE_NUMERO`
  across all of Datos; the parser only sees one file at a time. Per-file
  dedup matches ~92% of real values; the remaining ~8% is operator-level
  cross-file dedup we can't replicate. The column is excluded from the
  Seitrans golden comparison with a clear comment.
- **Header row moving down** (a new preamble like VASP has). `assert_schema`
  fires — loud — but the diff is hard to read because row 1 is now data, not
  headers. Triage will be slow until the operator notices the pattern.

## Future strategy — LLM-assisted drift triage (not built)

When the deterministic detectors *fail*, the operator currently has to read
the diff and patch the parser by hand. That triage step is a real candidate
for an LLM, **out of the runtime path**, where non-determinism is harmless.

### Workflow we'd build

```
  $ python -m courier_automation.triage <bad-invoice.xlsx>
  ─────────────────────────────────────────────────────────
   Schema mismatch: missing=['Bultos'], added=['Numero Bultos']

   LLM proposal (model: claude-sonnet-4-6, cached):
   • Likely cause: courier renamed 'Bultos' → 'Numero Bultos' (no
     semantic change; same dtype distribution in sample rows).
   • Suggested fix:
       --- a/courier_automation/parsers/seur.py
       +++ b/courier_automation/parsers/seur.py
       @@ -68,7 +68,7 @@
       -    "Bultos",
       +    "Numero Bultos",
   • Apply with: --apply  (dry run shown above)
```

The triage tool reads the invoice, the parser's expected schema, the prior
golden snapshot, and recent successful ingests. It proposes the patch as a
unified diff and only applies it when the operator passes `--apply`. The
runtime parser stays fully deterministic.

### Why an LLM is *not* in the runtime

- **Determinism.** Tests, the golden snapshot, and idempotency guarantees all
  rely on the parser producing the same output for the same input every run.
- **Cost & latency.** ~120 invoices/year for Seur is fine, but multiplied
  across 11 couriers and weekly portal ingests it's wasted spend on a problem
  that 30 lines of statistical assertions handle.
- **Privacy.** Invoice files contain customer names, addresses, amounts.
  Sending them to a third-party API for every ingest is a policy decision the
  business hasn't made; sending them only after a failure (rare, opt-in via
  the triage command) is a much smaller ask.

### When to actually build the triage tool

Build it after the **second** real schema-drift incident hits a courier in
production. One incident is anecdote; two means the manual triage cost is
worth offsetting. Until then, a `SchemaMismatch` diff plus `git blame` on the
column tuple is fast enough.

### Adjacent uses for an LLM (also out of the hot path)

- **New-courier parser bootstrap.** Hand a sample VASP / Dachser / Wwex file
  to the LLM and have it draft the parser skeleton + column tuple + dtype
  groups. One-shot per courier, big time saver.
- **Anomaly summarization.** A weekly cron that reads the manifest and the
  ingestion logs, summarizes "this week we ingested X invoices, Y failed
  plausibility, Z were deferred". Useful operations report; not load-bearing.

## Decision log

- **2026-05-05** — chose deterministic plausibility checks over LLM in the
  runtime path. LLM filed for triage / new-courier bootstrap only.
- **2026-05-05** — Seitrans `Q Expediciones` excluded from the golden
  comparison. The user marks `1` only on the *first global* occurrence of
  each `SPEDIZIONE_NUMERO` across all of Datos; per-file dedup is the most
  the parser can produce without global state. ~8% divergence on real
  fixtures; documented in the test docstring.
