"""Throwaway: extract the complete C. PAIS → País mapping from Datos."""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
historical = (
    ROOT / "Operations - Couriers" / "05. Correos Express"
    / "Análisis Envíos Correos Express V2.xlsx"
)
df = pd.read_excel(historical, sheet_name="Datos", engine="openpyxl",
                   usecols=["C. PAIS", "País"])
print("Full unique (C. PAIS -> País) mapping:")
mapping = df.dropna().drop_duplicates().sort_values("C. PAIS")
for _, row in mapping.iterrows():
    print(f"  {row['C. PAIS']!r:8s} -> {row['País']!r}")
print(f"\nTotal unique pairs: {len(mapping)}")
print(f"Rows with C. PAIS but NaN País: {df[df['C. PAIS'].notna() & df['País'].isna()].shape[0]}")
print(f"Rows with NaN C. PAIS but value País: {df[df['C. PAIS'].isna() & df['País'].notna()].shape[0]}")
