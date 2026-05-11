"""Throwaway: find Seur invoices that are present in BOTH Datos AND Facturas/,
ranked by row count, so we can pick rich fixtures for the golden test."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
WORKBOOK = (
    ROOT / "Operations - Couriers" / "01. Seur" / "NEW Análisis expediciones SEUR.xlsx"
)
FACTURAS_ROOT = ROOT / "Operations - Couriers" / "01. Seur" / "Facturas"

INVOICE_RE = re.compile(r"\d{10}([A-Z]{1,3})(\d{7})", re.IGNORECASE)


def index_facturas() -> dict[int, list[Path]]:
    """Map trailing-7-digit number → list of file paths (across all years)."""
    idx: dict[int, list[Path]] = {}
    for path in FACTURAS_ROOT.rglob("*.xlsx"):
        m = INVOICE_RE.search(path.stem)
        if not m:
            continue
        trailing = int(m.group(2))
        idx.setdefault(trailing, []).append(path)
    return idx


def main() -> int:
    print(f"loading {WORKBOOK.name}…")
    datos = pd.read_excel(WORKBOOK, sheet_name="Datos", engine="openpyxl")
    print(f"  Datos: {len(datos):,} rows")

    fac = index_facturas()
    print(f"  Facturas/: {sum(len(v) for v in fac.values())} xlsx files indexed")

    counts = (
        datos["Numero Factura"]
        .dropna()
        .astype("int64", errors="ignore")
        .value_counts()
    )

    candidates: list[tuple[int, int, list[Path]]] = []
    for inv, n_rows in counts.items():
        try:
            inv_int = int(inv)
        except (TypeError, ValueError):
            continue
        if inv_int in fac:
            candidates.append((inv_int, int(n_rows), fac[inv_int]))

    print(f"\n{len(candidates)} invoices present in both Datos and Facturas/")
    print("\nTop 15 by row count in Datos:")
    print(f"  {'Numero Factura':>15}  {'rows':>6}  file(s)")
    for inv, n, paths in sorted(candidates, key=lambda t: -t[1])[:15]:
        rels = [str(p.relative_to(ROOT)) for p in paths]
        print(f"  {inv:>15}  {n:>6}  {rels[0]}")
        for r in rels[1:]:
            print(f"  {'':>15}  {'':>6}  {r}")

    print("\nSmallest non-trivial (1-5 rows) — good for fast tests:")
    small = [(i, n, p) for i, n, p in candidates if 1 <= n <= 5]
    for inv, n, paths in sorted(small, key=lambda t: -t[1])[:10]:
        rels = [str(p.relative_to(ROOT)) for p in paths]
        print(f"  {inv:>15}  {n:>6}  {rels[0]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
