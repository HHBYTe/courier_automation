# Power BI migration to parquet

How the per-courier Power BI datasets get repointed from the master xlsx
to the parquet substrate produced by `courier_automation` (Phases 0-1-3
of the parquet migration). Power BI repoint was the originally-parked
"Phase 2" — this doc covers it now that the parquet substrate is in
place.

Pilot courier for this work was Seur; the same recipe applies to the
five other shipping datasets unchanged except for paths.

## What the migration changes (and what it doesn't)

- **Changes:** the **Source** step of each query inside the published
  Power BI **dataset** (semantic model). The fact-table query
  (`Datos` / `Data` / `INVOICES` depending on courier) goes from
  `Excel.Workbook(File.Contents("…master.xlsx"))` to a Folder.Files +
  Parquet.Document chain over `data/<carrier>/`. Lookup queries get
  the same treatment against `data/<carrier>/other/` CSVs (static
  lookups) or computed inline from `Datos` (derived lookups).
- **Doesn't change:** measures, relationships, visuals, slicers, the
  thin report .pbix files that consume the dataset. Column names and
  dtypes round-trip identically, so the model rebinds without edits.

## Substrate layout

After running `python scripts/backfill_<carrier>_parquet.py` and one
or more `python -m courier_automation.cli ingest <carrier>` invocations:

```
data/
  <carrier>/
    2024-01.parquet            ← per-month fact rows
    2024-02.parquet
    …
    undated.parquet            ← rows whose date column was empty
    other/                     ← (Seur only so far) lookup tables
      Delegaciones.csv
      Destino.csv
      Origen.csv
      ClaveFactura.csv
      Códigos IC.csv
      SERVICIOS.csv
```

The monthly parquets are written by `export_parquet` in
`courier_automation/store/workbook_appender.py`; the lookup CSVs were
dumped one-off by `scripts/extract_seur_lookups.py`.

## Fact-table repoint (the Datos query)

Replace the existing Source step with:

```m
Source = Table.Combine(
    Table.AddColumn(
        Table.SelectRows(
            Folder.Files("C:\Users\…\Courier Automation\data\seur"),
            each [Extension] = ".parquet"
        ),
        "Data", each Parquet.Document([Content])
    )[Data]
)
```

Then delete every step that followed it in the original query:

- **Navigation** (`= Origen{[Item="Table1",Kind="Table"]}[Data]`) —
  parquet already returns a flat table.
- **Promoted Headers** — parquet ships with headers.
- **Changed Type** — parquet stores dtypes losslessly. Re-typing on
  top corrupts text-stored codes (leading zeros stripped, non-digit
  values nulled) and is the root cause of "Loaded N rows, M errors"
  on first refresh. **Always remove this step on the parquet path.**

If the Datos query had real downstream transformations (filters,
calculated columns) keep those — they don't care where the source came
from.

## Lookup tables — static vs derived

Six sheets live in the Seur master besides Datos:

| Sheet            | Rows  | Nature                | Recipe                       |
|------------------|-------|-----------------------|------------------------------|
| Delegaciones     | 21    | Static lookup         | Load CSV                     |
| Destino          | 103   | Static lookup         | Load CSV                     |
| Origen           | 103   | Static lookup         | Load CSV                     |
| ClaveFactura     | 13    | Static lookup         | Load CSV                     |
| Códigos IC       | 57k   | Derived from Datos    | Compute from `Datos` in PQ   |
| SERVICIOS        | small | Pivot-dump from Datos | Compute from `Datos` in PQ   |

**Static** queries get a Csv.Document Source against
`data/seur/other/<sheet>.csv` with `Encoding=65001` (UTF-8 — required
for `Códigos`, `Cataluña`, etc.). Keep the `Table.TransformColumnTypes`
step on CSV-sourced queries — CSV is untyped so PQ needs to set types.

Example (Delegaciones, which has stray columns in the source sheet
that must be trimmed):

```m
let
    Source   = Csv.Document(File.Contents("…\data\seur\other\Delegaciones.csv"),
                            [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    Trimmed  = Table.SelectColumns(Promoted, {"Código Contable", "Delegación"}),
    Typed    = Table.TransformColumnTypes(Trimmed,
                  {{"Código Contable", Int64.Type}, {"Delegación", type text}})
in
    Typed
```

**Derived** queries should reference `Datos` directly so they
auto-refresh with the parquet (snapshotting them as CSV would freeze
the customer list at extraction time, breaking joins for any new
shipment whose recipient code isn't in the snapshot):

```m
// Códigos IC — distinct customers seen in Datos
let
    Source   = Table.Distinct(
                  Table.SelectColumns(Datos,
                      {"Codigo Cliente Consolidado",
                       "Alias Razon Social CCC Consolidado",
                       "Destinatario"})),
    Renamed  = Table.RenameColumns(Source,
                  {{"Codigo Cliente Consolidado",        "Código IC"},
                   {"Alias Razon Social CCC Consolidado", "Nombre IC"}}),
    Typed    = Table.TransformColumnTypes(Renamed,
                  {{"Código IC", type text}, {"Nombre IC", type text},
                   {"Destinatario", type text}})
in
    Typed
```

```m
// SERVICIOS — group-by reproduction of the original pivot table
let
    Source  = Table.Group(Datos, {"Nombre Completo Servicio"},
                  {{"Count of Nombre Completo Servicio",
                    each Table.RowCount(_), Int64.Type}}),
    Renamed = Table.RenameColumns(Source,
                  {{"Nombre Completo Servicio", "Row Labels"}})
in
    Renamed
```

If a derived query isn't actually referenced by any visual, deleting
the query is cleaner than maintaining it.

## Procedure (per dataset)

1. Open the **dataset** in Power BI Desktop (not the thin report).
   For Service-hosted datasets, `⋯ → Download .pbix` from the
   workspace; requires the workspace to allow downloads.
2. **Home → Transform data** to open Power Query Editor.
3. For each query: select it → **View → Advanced Editor** → replace
   the body with the appropriate snippet from this doc.
4. Verify each query previews correctly (row count, sample values).
5. **Home → Close & Apply.** The first refresh re-materialises the
   model. Expect roughly the same row count as before; "loaded N rows,
   0 errors" is the success criterion. Errors at this stage almost
   always trace back to a leftover Changed Type step.
6. **Model view** → confirm relationships between Datos and the
   lookup tables are still drawn. If any disappeared (sometimes
   happens when a column's type changed during the swap), redraw by
   dragging the key column from one table onto its match in the other:
   - `Datos[Destino]                   ↔ Destino[Código Destino]`
   - `Datos[Origen]                    ↔ Origen[Código Origen]`
   - `Datos[Codigo Cliente Consolidado]↔ Códigos IC[Código IC]`
   - `Datos[Clave Impuesto]            ↔ ClaveFactura[Clave Factura]`
7. Spot-check a visual that uses each relationship.
8. **File → Publish** to overwrite the dataset in the workspace. The
   thin report .pbix files that consume the dataset (the ones in
   `Operations - Couriers/<carrier>/`) keep working unchanged.

## Path and gateway notes

- The Source path inside each query is hardcoded. For a published
  dataset that refreshes on the Power BI Service, that path must
  resolve **on the gateway machine**, not the author's laptop —
  typically a network share or SharePoint/OneDrive path the gateway
  has access to.
- OneDrive paths work for local Power BI Desktop refreshes against
  your own checkout but generally not for scheduled Service refreshes
  without extra setup. Standard practice is to host `data/<carrier>/`
  on a path the on-prem data gateway is configured to read.
- Update the hardcoded base path in every snippet before publishing
  to the workspace. There's no parameterisation in the snippets given
  here — adding a Power Query parameter for the root path is a
  reasonable extension if you want one knob to turn at deploy time.

## Why this layout

- **Folder of monthly parquets, not one master parquet.** New months
  appear next to existing files. Power BI's next refresh picks them
  up automatically — no `.pbix` edit, no master-file rebuild step.
  Each monthly file refreshes independently and reads in parallel.
- **Static lookups as CSV, derived lookups from Datos.** Freezing the
  derived ones (Códigos IC, SERVICIOS) would silently break joins
  whenever a new customer code or service name appears in Datos. The
  cost of computing them in Power Query at refresh time is small
  (Group-By over 287k rows runs in a few seconds).
- **No xlsx-only display hacks on the parquet path.** UPS Version
  loses its locale-formatted comma decimal; UPS Charge Description
  Code becomes uniformly text instead of mixed int+text; Spring/UPS
  dates become true `datetime64` rather than dd/mm/yy strings;
  postcode leading zeros are preserved as text. Power BI sees clean
  typed columns and formats them on the visual side.

## Validation done

- Wwex 2026-01 ingested via `--format both`: parquet and xlsx
  sidecars carry 504 identical rows across 44 columns. Only
  difference is parquet preserving `inf` where openpyxl coerces to
  `NaN` (parquet is the more faithful representation).
- UPS 2025-03 live ingest (from raw CSVs and operator-converted
  xlsx) compared to the same month read out of the master xlsx via
  the backfill: 2,280 rows on both sides, **zero column diffs**
  after the UPS date-format sniffer fix (see `architecture.md`).
- Seur full backfill: 86 monthly parquet files, ~21 MB on disk
  (master xlsx is 113 MB).

## Related code and docs

- `courier_automation/store/workbook_appender.py` — `export_parquet`.
- `scripts/backfill_<carrier>_parquet.py` — per-courier historical
  backfill scripts.
- `scripts/extract_seur_lookups.py` — one-off dump of Seur's lookup
  sheets to `data/seur/other/`.
- `docs/architecture.md` — overall pipeline, parquet substrate, UPS
  date sniffer.
