# Project Plan — Step 1: Automated Ingestion of Courier Invoices

> Companion to `01_data_exploration.md`.
> Owner: Hermes Barreiro · Drafted: 2026-05-05

---

## 1. Scope of "Step 1"

The user has framed three phases:

1. **Now (this plan)** — automate **getting the data** off email / courier portals, parsing it into a clean per-courier table, and appending it to the per-courier "Análisis expediciones" workbook (or its successor).
2. **Next** — keep generating the per-courier Power BI views, no UX change for the user.
3. **Later** — build a unified cross-courier dataset for the global summary.

This document covers **Step 1 only**, plus the parts of the data model that step 2/3 require so we don't paint ourselves into a corner.

**Step 1 success criterion** — when a new monthly invoice email arrives, no manual copy-paste happens: within X minutes the courier's historical file (or its database replacement) shows the new month's rows, with the same shape the user is used to, and the .pbix refreshes successfully.

---

## 2. Recommended architecture

Three loosely coupled layers. Each can be built and tested independently.

```
   ┌─────────────────────┐    ┌─────────────────────┐   ┌──────────────────────────┐
   │  COLLECTOR          │    │   PARSER            │   │   STORE                  │
   │  (email + portal)   │ -> │   (per-courier)     │ ->│   (raw + normalized)     │
   └─────────────────────┘    └─────────────────────┘   └──────────────────────────┘
                                                              │
                                                              ▼
                                                       Power BI (per courier)
```

### 2.1 Collector
- **Gmail / Outlook** label `Couriers/<carrier>` → an automated job downloads new attachments to `Facturas/<carrier>/<YYYY>/raw/`.
- For carriers that publish on a portal (UPS, Wwex, possibly Spring), a small **Playwright** script per portal logs in and downloads the same files into the same destination.
- Each downloaded file is **registered in a manifest** (`manifest.parquet` or SQLite) keyed by `(carrier, invoice_number, file_hash)` so the rest of the pipeline is idempotent.

### 2.2 Parser
- **One adapter per courier** (`parsers/seur.py`, `parsers/vasp.py`, …). Each adapter takes a path and returns a normalized DataFrame for that courier.
- Adapters share a common interface; their job is the messy part: finding the real header row, decoding cp1252, mapping legacy → current schema (Dachser, Wwex), parsing postcodes out of free text (Lynda's), splitting per-charge accessorials.
- **Schema-per-courier** (`Datos`-level) is preserved — the goal of the parser is just to turn the carrier's file into a tidy table identical to what the user pastes into the historical workbook today.
- A second pass adds the **derived columns** the user adds today: `Año`, `Mes`, `Tipo Bulto`, `Tipo Exp.`, `Q Expediciones = 1`, country lookups.

### 2.3 Store
The user's current "store" is the workbook itself. Two viable targets:

**Option A — Keep the workbook as the source of truth.**
The pipeline appends rows to the `Datos` sheet of `<courier>/Análisis ….xlsx` using `openpyxl` in append mode. Pros: zero workflow change, .pbix works untouched. Cons: workbooks become gigantic, append-locking is fragile, hard to back-fill / replay.

**Option B — Store data in a database, project workbooks out of it.**
A small **DuckDB** file (or SQLite) per courier — line-level rows are the truth; the .xlsx is regenerated from a query. The Power BI report is repointed at the DB (Power BI has a native DuckDB connector). Pros: clean idempotency, schema versioning, easy backfill, ready for the global roll-up. Cons: one-time cost to redirect each .pbix.

> **Recommendation: Option B**, because it removes the same fragility that motivates this project in the first place. But staging it inside Option A for the first courier (Seur) is cheap and proves the parser end-to-end before committing to the migration.

### 2.4 Where the code lives
The user already has a `Courier Automation` folder on the Desktop and a `Web Scraper` folder beside it. Suggest:

```
Courier Automation/
  collectors/
    gmail.py              # OAuth + label-based download
    outlook.py            # if corporate uses Outlook 365
    portals/
      ups.py              # Playwright login + download
      wwex.py
      spring.py
  parsers/
    base.py               # Adapter interface, common helpers
    seur.py
    vasp.py
    dachser.py
    seitrans.py
    correos.py
    lyndas.py
    dpd_fr.py
    expresscatalan.py
    wwex.py
    spring.py
  store/
    duckdb_writer.py
    workbook_appender.py  # Option-A fallback
    schema/               # SQL/parquet schemas + reference tables
  manifest/
    files.parquet         # registry of every downloaded raw file
  cli.py                  # `python cli.py ingest seur 2025-04`
  tests/
    fixtures/<courier>/...# anonymised sample files
```

A single Windows Task Scheduler job (or `schtasks` / GitHub Actions on a self-hosted runner) runs `python cli.py ingest --all` daily.

---

## 3. Sequencing — what to build first

The carriers are **not equally hard**. Build in this order so you ship value early and learn the parser pattern on the cleanest case:

| Order | Courier | Why first | Parser difficulty |
|---|---|---|---|
| 1 | **Seur** | High volume, raw schema is identical to historical schema → trivial mapping. Every win compounds. | Easy |
| 2 | **Seitrans** | Cleanest non-Seur source (21 named cols, one sheet). | Easy |
| 3 | **Correos Express** | Low volume (1/month), only quirk is "promote row 1 to header". | Easy-Medium |
| 4 | **VASP** | Multi-sheet but `Detalhe` is regular once header row is located. PT encoding gotcha. | Medium |
| 5 | **Lynda's** | 8 columns, but address parsing into postcode requires a regex + UK-postcode validation. | Medium |
| 6 | **Express Catalan** | `.xls` binary, free-form cover sheet — needs `xlrd` + offset detection. | Medium |
| 7 | **Dachser** | Old/New schema bifurcation; needs the Old↔New mapping table the user already has on the `New & old Fields` sheet. | Medium-Hard |
| 8 | **DPD France** | No header row in raw file; must hard-code positional → named mapping. | Hard |
| 9 | **UPS (UK)** | 250-col standard layout; high cardinality of charge codes; weekly cadence pushes more on the collector. | Medium (parser) / Hard (collector — portal) |
| 10 | **Wwex (US)** | Schema drift over time, file extension drift. Needs schema detector. | Hard |
| 11 | **Spring** | Two streams (operations + invoices), 114-col raw, weekly. Save for last when patterns are settled. | Hard |

Defer for v2: Royal Mail (not started yet), Amazon (likely out of scope), the Outvio/Adyen/Tickelia siblings.

---

## 4. Concrete first-week milestones

Aimed at the next ~5 working days. Each milestone produces something tangible.

1. **D1 — Confirm scope and credentials with the user.** Required answers:
   - Which mailbox receives invoices (`marc.montull@artero.com` vs a shared inbox)? Are auto-forwarding rules / labels acceptable?
   - For each portal carrier (UPS, Wwex, Spring) — is there a service account we can use, or only the user's personal credentials?
   - Where should `<courier>/Análisis ….xlsx` live going forward — in the OneDrive folder, on a fileshare, or be replaced by a DuckDB file?
   - Is "06. Amazon" actually a courier-cost workstream or an unrelated VAT report?
2. **D2 — Repository skeleton.** Create `Courier Automation/` repo (suggest: a real git repo, not just a folder), wire `parsers/base.py`, write a `cli.py` stub, set up `pytest` with a `fixtures/` tree of anonymised sample files copied from each courier's most recent invoice. Add `.gitignore` for any file with PII or amounts.
3. **D3 — Seur end-to-end.** Implement `parsers/seur.py`, golden-test it against the existing historical file (parser output of all 2025 monthlies should match what the user already pasted into `NEW Análisis expediciones SEUR.xlsx`, sheet `Datos`, modulo row order). Build `store/workbook_appender.py` (Option A path) and prove the .pbix still refreshes.
4. **D4 — Seitrans + Correos Express.** Same pattern. Now we have three couriers and the parser interface is settled.
5. **D5 — Decide on Option A vs B.** With three working parsers, the cost of the DuckDB switch is concrete. Pick a path with the user, then build the collector for whichever email account holds Seur invoices and run the first **fully automatic** monthly ingest in shadow mode (writes to a copy of the workbook, user diffs vs his own).

After this first week, the rest of the carriers are largely a matter of writing a parser per week and adding the corresponding portal scraper for the three portal-only ones.

---

## 5. Normalized schema (used in step 3, sketched now)

Even though the global roll-up is "later", **picking the target schema now** is what allows the per-courier parsers to write into both their historical workbook *and* the eventual unified store from day one. Proposed minimum:

| Field | Type | Notes |
|---|---|---|
| `carrier` | string | `seur, vasp, dachser, …` (controlled vocab) |
| `invoice_number` | string | as-printed |
| `invoice_date` | date | end-of-month for most, exact for UPS |
| `invoice_currency` | string | EUR / GBP / USD |
| `shipment_id` | string | carrier's tracking / expedition number |
| `shipment_date` | date | |
| `delivery_date` | date | nullable |
| `service` | string | carrier's service code, raw |
| `service_normalized` | enum | `domestic, eu, export, return` (cheap to derive from origin/destination country) |
| `origin_country` | iso2 | |
| `origin_postal_code` | string | |
| `dest_country` | iso2 | |
| `dest_postal_code` | string | |
| `packages` | int | |
| `weight_kg` | decimal | use `Peso` or equivalent |
| `volumetric_weight_kg` | decimal | nullable |
| `length_cm`, `width_cm`, `height_cm` | decimal | nullable |
| `freight_amount` | decimal | base transport (`Portes`, `Item Charge`, `Transport Cost (Base)`, …) |
| `fuel_amount` | decimal | (`Cargo Combustible`, `Taxe gasoil`, `Gasoil Cost`, `Tx Combustivel`) |
| `surcharges_amount` | decimal | catch-all for all other accessorials (sum) |
| `surcharges_breakdown` | json | per-charge dictionary so we don't lose detail |
| `tax_amount` | decimal | VAT / TVA |
| `total_amount` | decimal | line-level total inc. tax |
| `raw_row` | json | the full original row preserved verbatim |

`raw_row` is the escape hatch — anything courier-specific the user later wants for a per-courier .pbix is still queryable.

This schema is small enough to fit as a single DuckDB table `shipments`. The reference dimensions (`countries`, `regions`, `services_<carrier>`) live in side tables.

---

## 6. Risks & mitigations specific to step 1

| Risk | Mitigation |
|---|---|
| User's company blocks IMAP / OAuth on the Gmail/Outlook account | Fall back to a manual "drop folder" — collector watches a folder the user drags emails into. Lossy but unblocks the parser work. |
| Credentials for portal logins are personal (cannot live in a service) | Run the collector locally on the user's machine via Task Scheduler, not in the cloud. |
| Schema drift from carriers (esp. Wwex) | Every parser asserts an expected column set; mismatches abort the run and emit a diff. The user gets one alert email and can fix the mapping; no silent corruption. |
| `.pbix` references hard-coded sheet ranges | Parser writes into the **same sheet name and same column order** as the user has today. .pbix doesn't notice. |
| Excel file locks on append (someone has it open) | Writer takes a lock file; if the historical workbook is open the run defers and retries — never overwrites. |
| OneDrive sync conflicts (`-LAPTOP-J0QI6SK1` is already visible in the slide deck filename → known sync conflict pattern) | Pipeline writes to a non-OneDrive working dir and only copies the final file into OneDrive at the end. |

---

## 7. What we are NOT doing in step 1

- No new Power BI authoring. The existing `.pbix` files are reused with their existing source.
- No global roll-up dashboard yet. The normalized schema is being defined, but no consumer exists for it.
- No re-parsing of historical data older than 2025. Backfill is a separate, opt-in command (`cli.py backfill --courier seur --from 2019`) once the parser is trusted.
- No replacement of the user's manual workflow on the file-cleaning side until at least three couriers are reliably automated end-to-end.

---

## 8. Decisions needed from the user before coding starts

1. **Source of truth** — Option A (workbook) or Option B (DuckDB) for the historical store?
2. **Account access** — Which mailbox/inbox? Service account or personal? Permission to set up an automation rule on it?
3. **Portal credentials** — UPS Billing Center, Wwex/SpeedShip, Spring/XBSBack — willing to set up scripted login (with the security implications)?
4. **"06. Amazon"** — in scope or out?
5. **Hosting** — runs on the user's laptop on a schedule, or on a small VM the company can provide?
6. **First courier to deliver** — confirm Seur as the pilot, or does another carrier have higher operational pain?

Answer these six and the build can start the same day.
