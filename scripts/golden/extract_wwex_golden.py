"""One-off: read the production Wwex historical workbook and extract a
slice of `Data` matching the Tracking# values in
`tests/fixtures/wwex/raw/`.

Run from the project root:
  .venv\\Scripts\\python scripts\\extract_wwex_golden.py --period pilot-sample

Reads from `Operations - Couriers/11. Wwex (US)/Wwex USA Shippings Report.xlsx`.
Writes parquet to `tests/fixtures/wwex/golden/`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from courier_automation.parsers.wwex import (  # noqa: E402
    WWEX_COLUMNS,
    coerce_wwex_dtypes,
)

DEFAULT_WORKBOOK = (
    ROOT / "Operations - Couriers" / "11. Wwex (US)" / "Wwex USA Shippings Report.xlsx"
)
DEFAULT_RAW_DIR = ROOT / "tests" / "fixtures" / "wwex" / "raw"
DEFAULT_GOLDEN_DIR = ROOT / "tests" / "fixtures" / "wwex" / "golden"


def _tracking_numbers_from_fixtures(raw_dir: Path) -> set[str]:
    """Read each fixture, pull TRACKING_NO."""
    out: set[str] = set()
    for path in sorted(raw_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(
                path, sheet_name=0, engine="openpyxl",
                dtype=str, usecols=["TRACKING_NO"],
            )
        except Exception as e:  # noqa: BLE001
            print(f"  (skip {path.name}: {e})", file=sys.stderr)
            continue
        out.update(str(v).strip() for v in df["TRACKING_NO"].dropna())
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period", required=True)
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN_DIR)
    args = parser.parse_args()

    targets = _tracking_numbers_from_fixtures(args.raw_dir)
    if not targets:
        print(f"no Tracking# values found in {args.raw_dir}", file=sys.stderr)
        return 2
    print(f"extracting {len(targets)} Tracking# value(s) from fixtures")

    print(f"\nreading {args.workbook.name}...")
    datos = pd.read_excel(args.workbook, sheet_name="Data", engine="openpyxl")
    print(f"  Data: {len(datos):,} rows, {len(datos.columns)} columns")

    if tuple(datos.columns) != WWEX_COLUMNS:
        print(
            "WARNING: production Data columns differ from WWEX_COLUMNS — "
            "the golden test will catch the drift.",
            file=sys.stderr,
        )

    datos_track = datos["Tracking#"].astype(str).str.strip()
    mask = datos_track.isin(targets)
    matched = mask.sum()
    print(f"\n  matched: {matched} / {len(targets)} Tracking#")

    if matched == 0:
        print(
            "no shipments matched. Fixtures may be too recent. Pick an "
            "older fixture or wait for the operator to paste this month.",
            file=sys.stderr,
        )
        return 3

    slice_df = coerce_wwex_dtypes(datos[mask].copy())
    args.golden_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.golden_dir / f"{args.period}-data.parquet"
    slice_df.to_parquet(out_path, index=False)
    print(f"\nwrote {len(slice_df)} rows -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
