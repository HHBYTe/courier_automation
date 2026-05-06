"""Throwaway: reverse-engineer the 6 derived Correos columns by looking at
real Datos values vs source PESO KILOS / BULTOS / C. PAIS / F.ADMISION."""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
historical = (
    ROOT / "Operations - Couriers" / "05. Correos Express"
    / "Análisis Envíos Correos Express V2.xlsx"
)

df = pd.read_excel(historical, sheet_name="Datos", engine="openpyxl")
print(f"Datos rows: {len(df)}")

print("\n--- Año dtype + sample ---")
print(df["Año"].dtype, df["Año"].head(3).tolist())

print("\n--- Mes dtype + sample ---")
print(df["Mes"].dtype, df["Mes"].head(3).tolist())

print("\n--- Tipo Bulto distribution ---")
print(df["Tipo Bulto"].value_counts(dropna=False).head(20))

print("\n--- Tipo Exp. distribution ---")
print(df["Tipo Exp."].value_counts(dropna=False))

print("\n--- Q Expediciones distribution ---")
print(df["Q Expediciones"].value_counts(dropna=False))

print("\n--- País distribution ---")
print(df["País"].value_counts(dropna=False).head(20))

print("\n--- Source columns: PESO KILOS, BULTOS, C. PAIS, F.ADMISION ---")
print(df[["PESO KILOS", "BULTOS", "C. PAIS", "F.ADMISION"]].head(5).to_string())

# Cross-check: Tipo Bulto vs PESO KILOS
print("\n--- Tipo Bulto bucket -> PESO KILOS range ---")
for bucket in df["Tipo Bulto"].dropna().unique()[:15]:
    sub = df[df["Tipo Bulto"] == bucket]["PESO KILOS"]
    if len(sub):
        print(f"  {bucket!r:25s}  N={len(sub):5d}  min={sub.min():8.2f}  max={sub.max():8.2f}")

# Cross-check: Tipo Exp. vs BULTOS / PESO
print("\n--- Tipo Exp. -> stats ---")
for exp in df["Tipo Exp."].dropna().unique():
    sub = df[df["Tipo Exp."] == exp]
    print(
        f"  {exp!r:10s}  N={len(sub):5d}  "
        f"PESO_min={sub['PESO KILOS'].min():.2f}  "
        f"PESO_max={sub['PESO KILOS'].max():.2f}  "
        f"BULTOS_unique={sorted(sub['BULTOS'].dropna().unique())[:5]}"
    )

# País -> C. PAIS mapping
print("\n--- País vs C. PAIS ---")
mapping = (
    df[["C. PAIS", "País"]].dropna().drop_duplicates().head(20).to_string(index=False)
)
print(mapping)
