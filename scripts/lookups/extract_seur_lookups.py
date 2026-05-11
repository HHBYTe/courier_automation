"""One-shot: dump every sheet in the Seur master workbook to CSV under
`data/seur/other/`, except the main `Datos` shipment sheet (already
covered by the parquet substrate)."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
WORKBOOK = (
    ROOT / "Operations - Couriers" / "01. Seur" / "NEW Análisis expediciones SEUR.xlsx"
)
OUT_DIR = ROOT / "data" / "seur" / "other"
SKIP = {"Datos"}


def main() -> int:
    print(f"reading {WORKBOOK.name}...")
    xls = pd.ExcelFile(WORKBOOK, engine="openpyxl")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for sheet in xls.sheet_names:
        if sheet in SKIP:
            continue
        df = pd.read_excel(xls, sheet_name=sheet, dtype=object)
        out = OUT_DIR / f"{sheet}.csv"
        df.to_csv(out, index=False, encoding="utf-8")
        print(f"  wrote {len(df):>6} rows x {len(df.columns)} cols -> {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
