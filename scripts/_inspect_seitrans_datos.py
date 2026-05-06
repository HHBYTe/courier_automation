"""Throwaway: print the actual column names of the Seitrans Datos sheet
so we can reconcile them with SEITRANS_COLUMNS in the parser."""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
WB = ROOT / "Operations - Couriers" / "04. Seitrans" / "Análisis envíos Seitrans.xlsx"

df = pd.read_excel(WB, sheet_name="Datos", engine="openpyxl", nrows=2)
print(f"rows={len(df)}, cols={len(df.columns)}")
print("\nactual Datos columns (in order):")
for i, c in enumerate(df.columns, 1):
    print(f"  {i:2d}  {c!r}")
