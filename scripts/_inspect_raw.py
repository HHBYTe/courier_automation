"""Throwaway: inspect Numero Factura values in raw Seur fixtures."""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
for path in sorted((ROOT / "tests" / "fixtures" / "seur" / "raw").glob("*.xlsx")):
    print(f"\n--- {path.name} ---")
    df = pd.read_excel(path, sheet_name="Sheet1", engine="openpyxl")
    print(f"  rows: {len(df)}")
    print(f"  Numero Factura unique: {df['Numero Factura'].unique()[:5]}")
    print(f"  Numero Factura dtype:  {df['Numero Factura'].dtype}")
    print(f"  Numero Linea sample:   {df['Numero Linea'].head(3).tolist()}")
    print(f"  Fecha Factura sample:  {df['Fecha Factura'].head(2).tolist()}")
