"""Throwaway: figure out the Q Expediciones rule."""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
WB = ROOT / "Operations - Couriers" / "04. Seitrans" / "Análisis envíos Seitrans.xlsx"

df = pd.read_excel(WB, sheet_name="Datos", engine="openpyxl")
print(f"Datos: {len(df)} rows")
print(f"\nQ Expediciones distribution: {df['Q Expediciones'].value_counts().to_dict()}")
print(f"unique SPEDIZIONE NUMERO: {df['SPEDIZIONE NUMERO'].nunique()}")
print(f"unique DOCUMENTO NUMERO: {df['DOCUMENTO NUMERO'].nunique()}")
print(f"sum of Q Expediciones: {df['Q Expediciones'].sum()}")
print()

# Hypothesis 1: Q Expediciones = 1 on the first occurrence of each
# SPEDIZIONE NUMERO (within the whole sheet)
first_occ = ~df.duplicated(subset=["SPEDIZIONE NUMERO"], keep="first")
match_h1 = (df["Q Expediciones"] == first_occ.astype(int)).sum()
print(f"H1 (first occurrence of SPEDIZIONE NUMERO globally): {match_h1}/{len(df)} match")

# Hypothesis 2: Q Expediciones = 1 on first occurrence within the same DOCUMENTO
first_occ_doc = ~df.duplicated(subset=["DOCUMENTO NUMERO", "SPEDIZIONE NUMERO"], keep="first")
match_h2 = (df["Q Expediciones"] == first_occ_doc.astype(int)).sum()
print(f"H2 (first occurrence of SPEDIZIONE NUMERO within DOCUMENTO): {match_h2}/{len(df)} match")

# Look at a small sample where Q Expediciones varies
sample = df[df["DOCUMENTO NUMERO"] == 24633][
    ["DOCUMENTO NUMERO", "SPEDIZIONE NUMERO", "Q Expediciones"]
].head(15)
print("\nsample for DOCUMENTO 24633 (first 15 rows):")
print(sample.to_string(index=False))
