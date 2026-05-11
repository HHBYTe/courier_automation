"""One-shot: backfill historical UPS months from the master workbook into
`data/ups/<YYYY>-<MM>.parquet`. Rows with no Invoice Date land in
`data/ups/undated.parquet`.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from courier_automation.parsers.ups import (  # noqa: E402
    UPS_COLUMNS,
    coerce_ups_dtypes,
)
from courier_automation.store.workbook_appender import export_parquet  # noqa: E402

CARRIER = "ups"
DATE_COLUMN = "Invoice Date"
SHEET = "Data"
WORKBOOK = (
    ROOT / "Operations - Couriers" / "07. UPS (UK)" / "UPS Shippings Report.xlsx"
)
OUT_DIR = ROOT / "data" / CARRIER


def _read_master_readonly(path: Path, sheet: str) -> pd.DataFrame:
    try:
        return pd.read_excel(path, sheet_name=sheet, engine="openpyxl")
    except PermissionError:
        tmp = Path(tempfile.gettempdir()) / f"backfill-{uuid.uuid4().hex[:8]}-{path.name}"
        shutil.copy2(path, tmp)
        try:
            return pd.read_excel(tmp, sheet_name=sheet, engine="openpyxl")
        finally:
            tmp.unlink(missing_ok=True)


def _write_partition(df: pd.DataFrame, out_path: Path, columns: tuple[str, ...]) -> int:
    out_path.unlink(missing_ok=True)
    return export_parquet(output_path=out_path, rows=df, expected_columns=columns)


def main() -> int:
    print(f"reading {WORKBOOK.name} (sheet={SHEET})...")
    df = _read_master_readonly(WORKBOOK, SHEET)
    print(f"  {len(df):,} rows, {len(df.columns)} columns")
    df = coerce_ups_dtypes(df[list(UPS_COLUMNS)].copy())

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dates = pd.to_datetime(df[DATE_COLUMN], errors="coerce")
    undated = df[dates.isna()]
    dated = df[dates.notna()]

    written = 0
    months = 0
    for (year, month), part in dated.groupby([dates.dt.year, dates.dt.month]):
        out_path = OUT_DIR / f"{int(year):04d}-{int(month):02d}.parquet"
        n = _write_partition(part, out_path, UPS_COLUMNS)
        print(f"  wrote {n:>6} rows -> {out_path.relative_to(ROOT)}")
        written += n
        months += 1

    if not undated.empty:
        n = _write_partition(undated, OUT_DIR / "undated.parquet", UPS_COLUMNS)
        print(f"  wrote {n:>6} rows -> data/{CARRIER}/undated.parquet (no {DATE_COLUMN})")
        written += n

    print(f"\ntotal: {written} rows written across {months} months")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
