"""One-off: read the production Seitrans historical workbook and extract a
slice of the Datos sheet for a specific period, save it as parquet for the
golden parser test.

Run from the project root:
  .venv\\Scripts\\python scripts\\extract_seitrans_golden.py --period pilot-sample

Reads from `Operations - Couriers/04. Seitrans/Análisis envíos Seitrans.xlsx`.
Writes to `tests/fixtures/seitrans/golden/<period>-datos.parquet`. The
production folder is read-only.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from courier_automation.parsers.seitrans import (  # noqa: E402
    SEITRANS_COLUMNS,
    coerce_seitrans_dtypes,
)

DEFAULT_WORKBOOK = (
    ROOT / "Operations - Couriers" / "04. Seitrans" / "Análisis envíos Seitrans.xlsx"
)
DEFAULT_RAW_DIR = ROOT / "tests" / "fixtures" / "seitrans" / "raw"
DEFAULT_GOLDEN_DIR = ROOT / "tests" / "fixtures" / "seitrans" / "golden"

# Seitrans filenames begin with the invoice's date as YYYY_MM_DD, then a
# free-form label, then the invoice number somewhere. We pull both and use
# the date to decide the year (avoiding trailing-number collisions across
# years, same defensive move as the Seur extractor).
_FILENAME_RE = re.compile(r"^(\d{4})_(\d{2})_(\d{2})\D+(\d+)\b")


def _year_and_documento_from_filename(stem: str) -> tuple[int, int] | None:
    m = _FILENAME_RE.match(stem)
    if not m:
        return None
    return int(m.group(1)), int(m.group(4))


def _scan_fixture_invoices(raw_dir: Path) -> list[tuple[int, int, str]]:
    """Return (year, documento_numero, filename) for every fixture xlsx."""
    out: list[tuple[int, int, str]] = []
    for path in raw_dir.glob("*.xlsx"):
        pair = _year_and_documento_from_filename(path.stem)
        if pair is None:
            print(
                f"  (skip -- can't parse year/documento from {path.name})",
                file=sys.stderr,
            )
            continue
        out.append((*pair, path.name))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period", required=True, help="Output filename tag (e.g. 'pilot-sample').")
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN_DIR)
    args = parser.parse_args()

    fixtures = _scan_fixture_invoices(args.raw_dir)
    if not fixtures:
        print(f"no parseable raw fixtures in {args.raw_dir}", file=sys.stderr)
        return 2
    print(f"extracting {len(fixtures)} invoice(s):")
    for y, d, name in fixtures:
        print(f"  {name}  -> year={y} DOCUMENTO_NUMERO={d}")

    print(f"\nreading {args.workbook.name}...")
    datos = pd.read_excel(args.workbook, sheet_name="Datos", engine="openpyxl")
    print(f"  Datos: {len(datos):,} rows, {len(datos.columns)} columns")
    if tuple(datos.columns) != SEITRANS_COLUMNS:
        print(
            "WARNING: Datos columns differ from SEITRANS_COLUMNS. Continuing — "
            "the golden test will catch the drift.",
            file=sys.stderr,
        )

    datos_doc = pd.to_numeric(datos["DOCUMENTO NUMERO"], errors="coerce").astype("Int64")
    datos_year = (
        pd.to_datetime(
            datos["DOCUMENTO_DATA"], errors="coerce", format="mixed", dayfirst=True
        )
        .dt.year.astype("Int64")
    )

    targets = {(y, d): name for y, d, name in fixtures}
    mask = pd.Series(False, index=datos.index)
    found: list[tuple[int, int]] = []
    for (y, d), name in sorted(targets.items()):
        m = (datos_year == y) & (datos_doc == d)
        if m.any():
            mask |= m
            found.append((y, d))
    missing = sorted(set(targets) - set(found))

    print(f"\n  matched: {len(found)} / {len(targets)} fixture invoices")
    for y, d in found:
        n = int(((datos_year == y) & (datos_doc == d)).sum())
        print(f"    found     {targets[(y, d)]}  -> year={y} doc={d}  ({n} rows)")
    for y, d in missing:
        print(f"    NOT found {targets[(y, d)]}  -> year={y} doc={d}")

    if missing:
        # Help the operator find a known-good fixture next time.
        recent = (
            pd.DataFrame({"y": datos_year, "d": datos_doc})
            .dropna()
            .drop_duplicates()
            .tail(15)
        )
        print("\n  (sample of recent (year, DOCUMENTO_NUMERO) in Datos:)", file=sys.stderr)
        for _, r in recent.iterrows():
            print(f"    {int(r['y'])}-{int(r['d'])}", file=sys.stderr)

    if not found:
        print(
            "\nno fixture invoices found in Datos. Pick one from the sample "
            "above and copy that file into tests/fixtures/seitrans/raw/, "
            "then re-run.",
            file=sys.stderr,
        )
        return 3

    slice_df = coerce_seitrans_dtypes(datos[mask].copy())
    args.golden_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.golden_dir / f"{args.period}-datos.parquet"
    slice_df.to_parquet(out_path, index=False)
    print(f"\nwrote {len(slice_df)} rows from {len(found)} invoice(s) -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
