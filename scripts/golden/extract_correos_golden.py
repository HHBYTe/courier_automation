"""One-off: read the production Correos historical workbook and extract
a slice of `Datos` for the fixtures currently in
`tests/fixtures/correos/raw/`. Matches by the `Nº ENVIO` (shipment ID)
set — each shipment is globally unique.

Run from the project root:
  .venv\\Scripts\\python scripts\\extract_correos_golden.py --period pilot-sample

Reads from `Operations - Couriers/05. Correos Express/`. Writes parquet to
`tests/fixtures/correos/golden/`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from courier_automation.parsers.correos import (  # noqa: E402
    CORREOS_COLUMNS,
    coerce_correos_dtypes,
)

DEFAULT_WORKBOOK = (
    ROOT / "Operations - Couriers" / "05. Correos Express"
    / "Análisis Envíos Correos Express V2.xlsx"
)
DEFAULT_RAW_DIR = ROOT / "tests" / "fixtures" / "correos" / "raw"
DEFAULT_GOLDEN_DIR = ROOT / "tests" / "fixtures" / "correos" / "golden"


def _shipment_ids_from_fixtures(raw_dir: Path) -> set[str]:
    """Read each fixture's row 2 → row 3+ to extract its set of Nº ENVIO."""
    ids: set[str] = set()
    for path in sorted(raw_dir.glob("*.xlsx")):
        try:
            df = pd.read_excel(
                path, sheet_name=0, engine="openpyxl", header=2,
                usecols=["Nº ENVIO"],
            )
        except Exception as e:  # noqa: BLE001
            print(f"  (skip {path.name}: {e})", file=sys.stderr)
            continue
        ids.update(str(v).strip() for v in df["Nº ENVIO"].dropna())
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period", required=True)
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN_DIR)
    args = parser.parse_args()

    target_ids = _shipment_ids_from_fixtures(args.raw_dir)
    if not target_ids:
        print(f"no shipment IDs found in fixtures under {args.raw_dir}", file=sys.stderr)
        return 2
    print(f"extracting {len(target_ids)} shipment(s) from {len(list(args.raw_dir.glob('*.xlsx')))} fixture(s)")

    print(f"\nreading {args.workbook.name}...")
    datos = pd.read_excel(args.workbook, sheet_name="Datos", engine="openpyxl")
    print(f"  Datos: {len(datos):,} rows, {len(datos.columns)} columns")
    if tuple(datos.columns) != CORREOS_COLUMNS:
        print(
            "WARNING: Datos columns differ from CORREOS_COLUMNS. Continuing — "
            "the golden test will catch the drift.",
            file=sys.stderr,
        )

    datos_ids = datos["Nº ENVIO"].astype(str).str.strip()
    mask = datos_ids.isin(target_ids)
    matched = mask.sum()
    print(f"  matched: {matched} / {len(target_ids)} fixture shipments")

    if matched == 0:
        print(
            "no shipments found in Datos. Either the fixtures are too recent "
            "to be in the historical workbook, or the fixture's invoice was "
            "never pasted. Pick an older fixture and try again.",
            file=sys.stderr,
        )
        return 3

    slice_df = coerce_correos_dtypes(datos[mask].copy())
    args.golden_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.golden_dir / f"{args.period}-datos.parquet"
    slice_df.to_parquet(out_path, index=False)
    print(f"\nwrote {len(slice_df)} rows -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
