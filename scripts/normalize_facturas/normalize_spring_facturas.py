"""Normalize Spring (FR) /<YYYY>/ flat layouts into <YYYY>/<MM> - <Mes>/ subfolders.

Spring filenames embed the invoice issue datetime as `_YYMMDDHHMM` at the
end of the stem. Both the `.XLSX` (Details of Invoice) and the `.PDF.pdf`
companion follow this convention; the latter has a `.PDF` token before the
true extension, which we accommodate.

Examples:
  E2509827_ES_Details of Invoice_O_110003790_2511251351.XLSX  -> 2025-11
  E2509827_ES_Invoice_O_110003790_2511251351.PDF.pdf          -> 2025-11
  E2603429_ES_Details of Invoice_O_110003790_2604141531.XLSX  -> 2026-04

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
DEFAULT_FACTURAS = ROOT / "Operations - Couriers" / "13. Spring (FR)"

SPANISH_MONTHS: dict[int, str] = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}

# Trailing `_YYMMDDHHMM`, optionally followed by `.PDF` (Spring PDFs are
# `<stem>.PDF.pdf`, so the .PDF survives Path.stem).
_RE_TRAILING_DATE = re.compile(r"_(\d{2})(\d{2})\d{6}(?:\.PDF)?$", re.IGNORECASE)


def detect_month(filename: str) -> tuple[int, int] | None:
    stem = Path(filename).stem
    m = _RE_TRAILING_DATE.search(stem)
    if not m:
        return None
    yy, month = int(m.group(1)), int(m.group(2))
    if not (1 <= month <= 12):
        return None
    return (2000 + yy, month)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_FACTURAS,
                        help=f"Spring root (default: {DEFAULT_FACTURAS}).")
    parser.add_argument("--apply", action="store_true",
                        help="Actually move files. Without this, just print the plan.")
    args = parser.parse_args()

    if not args.root.exists():
        print(f"error: {args.root} does not exist", file=sys.stderr)
        return 1

    moved = 0
    skipped = 0

    for year_dir in sorted(args.root.iterdir()):
        if not (year_dir.is_dir() and year_dir.name.isdigit()):
            continue
        folder_year = int(year_dir.name)
        flat_files = sorted(p for p in year_dir.iterdir() if p.is_file())
        if not flat_files:
            continue

        print(f"\n[{year_dir.name}] {len(flat_files)} flat files")
        for path in flat_files:
            ym = detect_month(path.name)
            if ym is None:
                print(f"  ! skip (no trailing-date pattern): {path.name}", file=sys.stderr)
                skipped += 1
                continue

            year, month = ym
            if year != folder_year:
                print(f"  ! skip ({path.name}: detected year {year} != folder {folder_year})",
                      file=sys.stderr)
                skipped += 1
                continue

            dest_dir = year_dir / f"{month:02d} - {SPANISH_MONTHS[month]}"
            dst = dest_dir / path.name
            if dst.exists():
                print(f"  ! collision, skipping: {dst}", file=sys.stderr)
                skipped += 1
                continue
            if args.apply:
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(dst))
            tag = "MOVE" if args.apply else "PLAN"
            print(f"  {tag} {year_dir.name}/{path.name}"
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
