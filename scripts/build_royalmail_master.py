"""One-shot: build the Royal Mail historical workbook from per-invoice
CSVs.

Royal Mail has no master workbook yet — invoices arrive as one pipe-
separated `.csv` per week in `Operations - Couriers/12. Royal Mail (UK)/
Facturas/<YYYY>/<MM> - <Mes>/`. This script walks that tree, parses each
file via `RoyalMailParser`, concatenates the rows, sorts them, and writes
`Royal Mail Shipments Report.xlsx` (sheet `Datos`) using the same
`export_rows` helper the sidecar path uses.

Re-runnable: deletes the target xlsx first. Only scans the canonical
`<YYYY>/<MM> - <Mes>/` layout — the `other/` folder is intentionally
skipped (those loose CSVs can be added by hand or via `ingest royalmail
--file`).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from courier_automation.parsers.royalmail import (  # noqa: E402
    ROYALMAIL_COLUMNS,
    RoyalMailParser,
)
from courier_automation.store.workbook_appender import (  # noqa: E402
    DATOS_SHEET,
    export_rows,
)

FACTURAS = ROOT / "Operations - Couriers" / "12. Royal Mail (UK)" / "Facturas"
OUT_XLSX = (
    ROOT / "Operations - Couriers" / "12. Royal Mail (UK)"
    / "Royal Mail Shipments Report.xlsx"
)

_MONTH_DIR_RE = re.compile(r"^(\d{2})\s*-\s*", re.IGNORECASE)


def _discover_invoices() -> list[Path]:
    """All `*Invoice*.csv` files under the canonical YYYY/MM layout."""
    files: list[Path] = []
    if not FACTURAS.is_dir():
        return files
    for year_dir in sorted(FACTURAS.iterdir()):
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        for month_dir in sorted(year_dir.iterdir()):
            if not month_dir.is_dir() or not _MONTH_DIR_RE.match(month_dir.name):
                continue
            files.extend(
                sorted(p for p in month_dir.glob("*.csv") if "invoice" in p.name.lower())
            )
    return files


def main() -> int:
    files = _discover_invoices()
    if not files:
        print(f"no invoice CSVs found under {FACTURAS}")
        return 1
    print(f"discovered {len(files)} invoice CSV(s)")

    parser = RoyalMailParser()
    frames: list[pd.DataFrame] = []
    for path in files:
        try:
            result = parser.parse(path)
        except Exception as e:  # noqa: BLE001
            print(f"  SKIP {path.name}: {e}")
            continue
        print(
            f"  {path.parent.name}/{path.name}: "
            f"{result.row_count} rows (invoice {result.invoice_number})"
        )
        frames.append(result.rows)

    if not frames:
        print("no parseable invoices; nothing to write")
        return 1

    combined = pd.concat(frames, ignore_index=True)
    # Stable, human-friendly ordering: invoice date, then by docket within
    # the invoice. Sort with NaT/NaN last so any malformed rows surface at
    # the bottom rather than scattered through the sheet.
    combined = combined.sort_values(
        by=["Invoice Date", "Document Number", "Posting Date", "Docket Number"],
        na_position="last",
        kind="stable",
    ).reset_index(drop=True)

    if OUT_XLSX.exists():
        print(f"removing existing {OUT_XLSX.name}")
        OUT_XLSX.unlink()

    written = export_rows(
        output_path=OUT_XLSX,
        rows=combined,
        expected_columns=ROYALMAIL_COLUMNS,
        sheet_name=DATOS_SHEET,
        date_formats=parser.export_date_formats,
    )
    print(f"\nwrote {written} rows -> {OUT_XLSX.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
