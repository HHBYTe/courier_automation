"""One-shot: build the Royal Mail historical workbook from per-invoice
CSVs.

Royal Mail has no append-friendly master — invoices arrive as one pipe-
separated `.csv` per week in `Operations - Couriers/12. Royal Mail (UK)/
Facturas/<YYYY>/<MM> - <Mes>/`. The actual rebuild logic now lives in
`courier_automation.pipeline.rebuild_royalmail_master` so the `pipeline
--carrier royalmail` command and this script share one implementation.

Re-runnable: deletes the target xlsx first. Only scans the canonical
`<YYYY>/<MM> - <Mes>/` layout — the `other/` folder is intentionally
skipped (those loose CSVs can be added via `ingest royalmail --file`).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from courier_automation.pipeline import (  # noqa: E402
    PipelineError,
    rebuild_royalmail_master,
)

FACTURAS = ROOT / "Operations - Couriers" / "12. Royal Mail (UK)" / "Facturas"
OUT_XLSX = (
    ROOT / "Operations - Couriers" / "12. Royal Mail (UK)"
    / "Royal Mail Shipments Report.xlsx"
)


def main() -> int:
    try:
        written = rebuild_royalmail_master(FACTURAS, OUT_XLSX)
    except PipelineError as e:
        print(e.detail)
        return 1
    print(f"wrote {written} rows -> {OUT_XLSX.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
