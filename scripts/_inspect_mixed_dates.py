"""Throwaway: trace what coerce does on a slice with mixed datetime+string
DOCUMENTO_DATA values."""
from pathlib import Path
import pandas as pd
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from courier_automation.parsers.seitrans import coerce_seitrans_dtypes

df = pd.read_excel(
    ROOT / "Operations - Couriers" / "04. Seitrans" / "Análisis envíos Seitrans.xlsx",
    sheet_name="Datos",
    engine="openpyxl",
)
slice_ = df[df["DOCUMENTO NUMERO"].isin([3065, 24633])].copy()
print(f"slice rows: {len(slice_)}")
print("per-invoice DOCUMENTO_DATA value-types:")
for dn in (3065, 24633):
    sub = slice_[slice_["DOCUMENTO NUMERO"] == dn]
    types = sorted({type(v).__name__ for v in sub["DOCUMENTO_DATA"]})
    print(f"  {dn}: types = {types}, sample = {sub['DOCUMENTO_DATA'].iloc[0]!r}")

c = coerce_seitrans_dtypes(slice_)
print("\nafter coerce_seitrans_dtypes, DOCUMENTO_DATA non-null counts:")
print(c.groupby("DOCUMENTO NUMERO")["DOCUMENTO_DATA"].count().to_string())

# Try to_datetime directly on the same slice
print("\nDirect pd.to_datetime(errors='coerce', dayfirst=True) non-null counts:")
direct = pd.to_datetime(slice_["DOCUMENTO_DATA"], errors="coerce", dayfirst=True)
direct_with_nm = pd.DataFrame({"dn": slice_["DOCUMENTO NUMERO"], "v": direct})
print(direct_with_nm.groupby("dn")["v"].count().to_string())
