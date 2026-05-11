"""One-off: read the production Seur historical workbook and extract a slice
of the Datos sheet for a specific period, save it as parquet for the golden
parser test.

Run from the project root, e.g.:
  .venv\\Scripts\\python scripts\\extract_seur_golden.py --period 2025-04

The output (`tests/fixtures/seur/golden/<period>-datos.parquet`) is what
`tests/parsers/test_seur_golden.py` compares the parser output against.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from courier_automation.parsers.base import (  # noqa: E402
    ParserError,
    extract_seur_invoice_number,
)
from courier_automation.parsers.seur import (  # noqa: E402
    SEUR_COLUMNS,
    coerce_seur_dtypes,
)

# The Numero Factura column in Datos stores only the trailing 7-digit number
# (e.g. 235697), not the full filename invoice number (0289992025D0235697).
# Trailing numbers can REPEAT across years/prefixes, so we always pair with
# the year extracted from the filename and filter Datos by Fecha Factura.year.
_FULL_RE = re.compile(r"\d{6}(\d{4})[A-Z]{1,3}(\d{7})", re.IGNORECASE)


def _year_and_trailing(invoice_number: str) -> tuple[int, int] | None:
    m = _FULL_RE.match(invoice_number)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))

DEFAULT_WORKBOOK = (
    ROOT / "Operations - Couriers" / "01. Seur" / "NEW Análisis expediciones SEUR.xlsx"
)
DEFAULT_RAW_DIR = ROOT / "tests" / "fixtures" / "seur" / "raw"
DEFAULT_GOLDEN_DIR = ROOT / "tests" / "fixtures" / "seur" / "golden"


def _invoice_numbers_for_period(raw_dir: Path) -> list[str]:
    """Use the same regex SeurParser uses, so it stays in sync."""
    numbers = set()
    for path in raw_dir.glob("*.xlsx"):
        try:
            numbers.add(extract_seur_invoice_number(path.name))
        except ParserError:
            print(f"  (skip non-Seur filename: {path.name})", file=sys.stderr)
    return sorted(numbers)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--period",
        required=True,
        help="Tag for the output filename (e.g. '2025-04').",
    )
    parser.add_argument(
        "--workbook",
        type=Path,
        default=DEFAULT_WORKBOOK,
        help=f"Production Seur workbook (default: {DEFAULT_WORKBOOK}).",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=DEFAULT_RAW_DIR,
        help="Directory of raw invoices that define which rows to extract.",
    )
    parser.add_argument(
        "--golden-dir",
        type=Path,
        default=DEFAULT_GOLDEN_DIR,
        help="Output directory for the golden parquet.",
    )
    args = parser.parse_args()

    invoice_numbers = _invoice_numbers_for_period(args.raw_dir)
    if not invoice_numbers:
        print(
            f"no raw invoices in {args.raw_dir} — copy real Seur invoices "
            "there before running this script.",
            file=sys.stderr,
        )
        return 2
    print(
        f"extracting {len(invoice_numbers)} invoice(s): "
        f"{', '.join(invoice_numbers[:3])}{'…' if len(invoice_numbers) > 3 else ''}"
    )

    print(f"reading {args.workbook} (this can take a minute on big workbooks)...")
    datos = pd.read_excel(
        args.workbook, sheet_name="Datos", engine="openpyxl"
    )
    print(f"  Datos: {len(datos):,} rows, {len(datos.columns)} columns")
    if tuple(datos.columns) != SEUR_COLUMNS:
        print(
            "WARNING: Datos columns differ from SEUR_COLUMNS. Continuing — "
            "the golden test will catch the drift.",
            file=sys.stderr,
        )

    # Match on (year, trailing-int). Trailing alone is NOT unique across
    # years/prefixes, so we always pair it with the year from the filename
    # and filter Datos by Fecha Factura.year.
    targets: set[tuple[int, int]] = set()
    target_to_full: dict[tuple[int, int], str] = {}
    for n in invoice_numbers:
        pair = _year_and_trailing(n)
        if pair is None:
            print(f"  (skip -- can't parse year/trailing from {n})", file=sys.stderr)
            continue
        targets.add(pair)
        target_to_full[pair] = n

    datos_inv = pd.to_numeric(datos["Numero Factura"], errors="coerce").astype("Int64")
    datos_year = (
        pd.to_datetime(datos["Fecha Factura"], errors="coerce").dt.year.astype("Int64")
    )

    mask = pd.Series(False, index=datos.index)
    found_pairs: list[tuple[int, int]] = []
    for y, t in sorted(targets):
        m = (datos_year == y) & (datos_inv == t)
        if m.any():
            mask |= m
            found_pairs.append((y, t))
    missing = sorted(targets - set(found_pairs))
    found_mask = mask

    print(f"  matched: {len(found_pairs)} / {len(targets)} fixture invoices")
    for y, t in found_pairs:
        n_rows = int(((datos_year == y) & (datos_inv == t)).sum())
        full = target_to_full.get((y, t), f"{y}/{t}")
        print(f"    found     {full}  -> year={y} trailing={t}  ({n_rows} rows)")
    for y, t in missing:
        full = target_to_full.get((y, t), f"{y}/{t}")
        print(f"    NOT found {full}  -> year={y} trailing={t}")

    if missing:
        # Show a sample of recent Numero Factura values so the user can pick a
        # known-good fixture next time.
        sample = datos_inv.dropna().drop_duplicates().tail(10).tolist()
        print("  (sample of recent invoice numbers in Datos:)", file=sys.stderr)
        for s in sample:
            print(f"    {s}", file=sys.stderr)

    if not found_pairs:
        print(
            "\nno fixture invoices found in Datos. Pick one from the sample "
            "above, copy that invoice file from "
            "Operations - Couriers/01. Seur/Facturas/<year>/ "
            "into tests/fixtures/seur/raw/, then re-run.",
            file=sys.stderr,
        )
        return 3

    # Apply the same dtype coercion the parser uses, so the parquet snapshot
    # matches parser output exactly.
    slice_df = coerce_seur_dtypes(datos[found_mask].copy())
    args.golden_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.golden_dir / f"{args.period}-datos.parquet"
    slice_df.to_parquet(out_path, index=False)
    print(f"\nwrote {len(slice_df)} rows from {len(found_pairs)} invoice(s) -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
