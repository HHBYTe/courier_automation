// Power Query M source for the Seur dataset, repointed at the parquet
// substrate produced by `python scripts/backfill_seur_parquet.py` and the
// live `python -m courier_automation.cli ingest seur` runs.
//
// HOW TO APPLY (no existing code changes — this is paste-only):
//   1. Open the published Seur dataset in Power BI Desktop
//      (Get data -> Power BI dataset -> select the Seur dataset, OR open
//      the original authoring .pbix if you have it locally).
//   2. Home -> Transform data -> select the `Datos` query (or whatever
//      the master query is named — same one that today reads
//      `NEW Análisis expediciones SEUR.xlsx`).
//   3. View -> Advanced Editor -> replace the entire let/in body with
//      everything below the divider, save, Close & Apply.
//   4. File -> Publish to overwrite the dataset in the workspace.
//
// The visuals, measures, and relationships keep working because the
// output table has the same column names and dtypes the master `Datos`
// sheet emitted. Power BI may flag a couple of column-type changes
// (UPS-style hacks dropped on the parquet path); accept them.
//
// Refresh: each new monthly parquet file appears next to the existing
// ones; Power BI's next refresh picks it up — no .pbix edit needed.
//
// ────────────────────────────────────────────────────────────────────

let
    // Adjust this once for the machine running the refresh. On the Power
    // BI Service this becomes a gateway-mapped path; locally it's the
    // OneDrive checkout of the repo.
    ParquetRoot = "C:\Users\Hermes Barrerio\OneDrive - Artero\Escritorio\Courier Automation\data\seur",

    Source       = Folder.Files(ParquetRoot),
    OnlyParquet  = Table.SelectRows(Source, each [Extension] = ".parquet"),
    LoadedTables = Table.AddColumn(OnlyParquet, "Data", each Parquet.Document([Content])),
    Combined     = Table.Combine(LoadedTables[Data])
in
    Combined
