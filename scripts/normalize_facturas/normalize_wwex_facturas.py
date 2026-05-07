"""Normalize Wwex (US) /<YYYY>/ flat layouts into <YYYY>/<MM> - <Mes>/ subfolders.

Wwex shipment-detail files live directly under each year folder. Filenames
encode the month in one of two ways:

  1. `^YYYY[_\\- ]+MM…`         e.g. 2024_01 Shipment detail Wwex Report.xls,
                                     2023- 12 shipment_detail_report.xls,
                                     2025_01_31 shipment_detail_report.xls
  2. `…_YYYY-MM-DD_…`           e.g. shipmentDetailsUPS_W130089866_2026-01-01_…xlsx

Files that don't match (e.g. a generic `shipment_detail_report.xls` with no
date in the name) are left in place with a reason logged.

Dry-run by default. Pass --apply to actually move files.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

from _duplicates import dedupe

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FACTURAS = ROOT / "Operations - Couriers" / "11. Wwex (US)"

SPANISH_MONTHS: dict[int, str] = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}

_RE_YYYY_MM = re.compile(r"^(\d{4})[_\- ]+(\d{2})(?=[_\- ]|$)")
_RE_EMBEDDED_DATE = re.compile(r"_(\d{4})-(\d{2})-\d{2}[_-]")


def detect_month(filename: str) -> tuple[int, int, str] | None:
    stem = Path(filename).stem

    m = _RE_YYYY_MM.match(stem)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            return (year, month, "YYYY_MM")

    m = _RE_EMBEDDED_DATE.search(stem)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            return (year, month, "embedded-date")

    return None


def companions_of(path: Path) -> list[Path]:
    return sorted(p for p in path.parent.iterdir()
                  if p.is_file() and p.stem == path.stem)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_FACTURAS,
                        help=f"Wwex root (default: {DEFAULT_FACTURAS}).")
    parser.add_argument("--apply", action="store_true",
                        help="Actually move files. Without this, just print the plan.")
    args = parser.parse_args()

    if not args.root.exists():
        print(f"error: {args.root} does not exist", file=sys.stderr)
        return 1

    moved = 0
    skipped = 0
    seen: set[Path] = set()

    for year_dir in sorted(args.root.iterdir()):
        if not (year_dir.is_dir() and year_dir.name.isdigit()):
            continue
        folder_year = int(year_dir.name)
        flat_files = sorted(p for p in year_dir.iterdir() if p.is_file())
        if not flat_files:
            continue

        print(f"\n[{year_dir.name}] {len(flat_files)} flat files")
        for path in flat_files:
            if path in seen:
                continue

            detection = detect_month(path.name)
            if detection is None:
                print(f"  ! skip (no month pattern): {path.name}", file=sys.stderr)
                skipped += 1
                continue

            year, month, pattern = detection
            if year != folder_year:
                print(f"  ! skip ({path.name}: detected year {year} != folder {folder_year})",
                      file=sys.stderr)
                skipped += 1
                continue

            dest_dir = year_dir / f"{month:02d} - {SPANISH_MONTHS[month]}"
            for src in companions_of(path):
                seen.add(src)
                dst = dest_dir / src.name
                if dst.exists():
                    print(f"  ! collision, skipping: {dst}", file=sys.stderr)
                    skipped += 1
                    continue
                if args.apply:
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(dst))
                tag = "MOVE" if args.apply else "PLAN"
                print(f"  {tag} [{pattern}] {year_dir.name}/{src.name}"
                      f" -> {dest_dir.relative_to(year_dir.parent)}/")
                moved += 1

    print(f"\n{'Moved' if args.apply else 'Would move'}: {moved} files; "
          f"skipped: {skipped}")
    dedupe(args.root, apply=args.apply)
    if not args.apply:
        print("(dry-run) re-run with --apply to actually move and dedupe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
