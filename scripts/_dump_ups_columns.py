"""Throwaway: dump the 250 UPS Data column names as a Python tuple
literal so we can paste them into parsers/ups.py."""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
hist = ROOT / "Operations - Couriers" / "07. UPS (UK)" / "UPS Shippings Report.xlsx"
df = pd.read_excel(hist, sheet_name="Data", engine="openpyxl", nrows=0)
cols = list(df.columns)
print(f"# {len(cols)} columns")
print("UPS_COLUMNS: tuple[str, ...] = (")
for c in cols:
    print(f"    {c!r},")
print(")")
