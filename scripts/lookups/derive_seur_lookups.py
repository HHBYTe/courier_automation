"""Re-derive Seur's non-static lookup CSVs from the parquet substrate.

Two lookup tables in `Operations - Couriers/01. Seur/NEW Análisis
expediciones SEUR.xlsx` are not static reference data — they're
computed from the shipment rows in `Datos`:

  - `Códigos IC` : distinct (Cliente Consolidado code, alias, recipient)
                   triples seen across all shipments.
  - `SERVICIOS`  : count of rows per `Nombre Completo Servicio`.

Power BI can compute these inline from the Datos query (see
`docs/power_bi.md`), but we also maintain CSV snapshots under
`data/seur/other/` so Power BI can load them from a stable file
instead of materialising the derivation per refresh. Run this script
after every Datos ingest to keep those CSVs current.

Output (overwritten in place):
  data/seur/other/Códigos IC.csv
  data/seur/other/SERVICIOS.csv

Independent of the backfill scripts and the live ingest pipeline —
reads only the parquet directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
PARQUET_DIR = ROOT / "data" / "seur"
OUT_DIR = PARQUET_DIR / "other"

CODIGOS_IC_SRC = ("Codigo Cliente Consolidado", "Alias Razon Social CCC Consolidado", "Destinatario")
CODIGOS_IC_OUT = ("Código IC", "Nombre IC", "Destinatario")
SERVICIOS_SRC = "Nombre Completo Servicio"


def _load_datos(parquet_dir: Path) -> pd.DataFrame:
    files = sorted(parquet_dir.glob("*.parquet"))
    if not files:
        raise SystemExit(f"no parquet files found under {parquet_dir}")
    frames = [pd.read_parquet(f, columns=list(set(CODIGOS_IC_SRC) | {SERVICIOS_SRC})) for f in files]
    return pd.concat(frames, ignore_index=True)


def _derive_codigos_ic(df: pd.DataFrame) -> pd.DataFrame:
    out = df.loc[:, list(CODIGOS_IC_SRC)].drop_duplicates().reset_index(drop=True)
    out.columns = list(CODIGOS_IC_OUT)
    return out


def _derive_servicios(df: pd.DataFrame) -> pd.DataFrame:
    counts = (
        df[SERVICIOS_SRC]
        .value_counts(dropna=False)
        .rename_axis("Row Labels")
        .reset_index(name="Count of Nombre Completo Servicio")
    )
    return counts


def main() -> int:
    print(f"reading parquet from {PARQUET_DIR.relative_to(ROOT)}...")
    df = _load_datos(PARQUET_DIR)
    print(f"  {len(df):,} shipment rows loaded")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    codigos = _derive_codigos_ic(df)
    codigos_path = OUT_DIR / "Códigos IC.csv"
    codigos.to_csv(codigos_path, index=False, encoding="utf-8")
    print(f"  wrote {len(codigos):>6} rows -> {codigos_path.relative_to(ROOT)}")

    servicios = _derive_servicios(df)
    servicios_path = OUT_DIR / "SERVICIOS.csv"
    servicios.to_csv(servicios_path, index=False, encoding="utf-8")
    print(f"  wrote {len(servicios):>6} rows -> {servicios_path.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
