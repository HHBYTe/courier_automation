"""One-off: read the production UPS historical workbook and extract a
slice of `Data` matching the Invoice Number(s) of the fixtures in
`tests/fixtures/ups/raw/`.

Run from the project root:
  .venv\\Scripts\\python scripts\\extract_ups_golden.py --period pilot-sample

Reads from `Operations - Couriers/07. UPS (UK)/UPS Shippings Report.xlsx`.
Writes parquet to `tests/fixtures/ups/golden/`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from courier_automation.parsers.ups import UPS_COLUMNS, coerce_ups_dtypes  # noqa: E402

DEFAULT_WORKBOOK = (
    ROOT / "Operations - Couriers" / "07. UPS (UK)" / "UPS Shippings Report.xlsx"
)
DEFAULT_RAW_DIR = ROOT / "tests" / "fixtures" / "ups" / "raw"
DEFAULT_GOLDEN_DIR = ROOT / "tests" / "fixtures" / "ups" / "golden"


def _invoice_numbers_from_fixtures(raw_dir: Path) -> set[str]:
    """UPS CSVs are headerless; column 6 (zero-indexed 5) is Invoice Number."""
    ids: set[str] = set()
    for path in sorted(raw_dir.glob("*.csv")):
        try:
            df = pd.read_csv(
                path, header=None, dtype=str, encoding="utf-8",
                low_memory=False, usecols=[5],
            )
        except Exception as e:  # noqa: BLE001
            print(f"  (skip {path.name}: {e})", file=sys.stderr)
            continue
        ids.update(str(v).strip() for v in df.iloc[:, 0].dropna())
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period", required=True)
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN_DIR)
    args = parser.parse_args()

    invoice_numbers = _invoice_numbers_from_fixtures(args.raw_dir)
    if not invoice_numbers:
        print(f"no UPS invoice numbers found in {args.raw_dir}", file=sys.stderr)
        return 2
    print(f"extracting invoices: {sorted(invoice_numbers)}")

    print(f"\nreading {args.workbook.name}...")
    datos = pd.read_excel(args.workbook, sheet_name="Data", engine="openpyxl")
    print(f"  Data: {len(datos):,} rows, {len(datos.columns)} columns")
    if tuple(datos.columns) != UPS_COLUMNS:
        print(
            "WARNING: production Data columns differ from UPS_COLUMNS — "
            "the golden test will catch the drift.",
            file=sys.stderr,
        )

    # The fixture CSV has invoice numbers as zero-padded strings
    # (`000003961958`) but Excel stores them as floats (`3961958.0`),
    # dropping the leading zeros. Normalise both sides to int.
    def _to_int(v: object) -> int | None:
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    datos_inv_int = datos["Invoice Number"].apply(_to_int).astype("Int64")
    target_ints = {_to_int(n) for n in invoice_numbers}
    target_ints.discard(None)
    mask = datos_inv_int.isin(target_ints)
    matched_ints = sorted(int(x) for x in datos_inv_int[mask].dropna().unique())
    print(f"\n  matched: {len(matched_ints)} / {len(target_ints)} fixture invoices")
    for n in matched_ints:
        n_rows = int((datos_inv_int == n).sum())
        print(f"    found     {n}  ({n_rows} rows)")
    for n in sorted(target_ints - set(matched_ints)):
        print(f"    NOT found {n}")

    if not matched_ints:
        # Show a sample of recent invoice numbers for the operator.
        sample = datos_inv_int.dropna().drop_duplicates().tail(10).tolist()
        print("\n  (sample of recent invoice numbers in Data:)", file=sys.stderr)
        for s in sample:
            print(f"    {s}", file=sys.stderr)
        return 3

    slice_df = coerce_ups_dtypes(datos[mask].copy())
    args.golden_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.golden_dir / f"{args.period}-data.parquet"
    slice_df.to_parquet(out_path, index=False)
    print(f"\nwrote {len(slice_df)} rows -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
