"""Normalize UPS (UK) Facturas/<YYYY>/ flat layouts into <YYYY>/<MM> - <Mes>/.

Two passes:
  1. Rename any existing `<YYYY>/MM - <English>` folder to `<YYYY>/MM - <Spanish>`
     (UPS 2024+ was previously organised with English month names).
  2. Move flat `Invoice_<invoicenum>_MMDDYY.<ext>` files into the matching
     Spanish-named month folder.

Dry-run by default. Pass --apply to actually rename / move files.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

from _duplicates import dedupe

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FACTURAS = ROOT / "Operations - Couriers" / "07. UPS (UK)" / "Facturas"

SPANISH_MONTHS: dict[int, str] = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}
ENGLISH_MONTHS: dict[int, str] = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}

_RE_INVOICE = re.compile(r"^Invoice_\d+_(\d{2})(\d{2})(\d{2})$", re.IGNORECASE)
_RE_MONTH_DIR = re.compile(r"^(\d{2})\s*-\s*(.+)$")


def detect_month(filename: str) -> tuple[int, int] | None:
    """Return (year, month) from `Invoice_<num>_MMDDYY.<ext>`, or None."""
    stem = Path(filename).stem
    m = _RE_INVOICE.match(stem)
    if not m:
        return None
    month = int(m.group(1))
    yy = int(m.group(3))
    if not (1 <= month <= 12):
        return None
    return (2000 + yy, month)


def companions_of(path: Path) -> list[Path]:
    return sorted(p for p in path.parent.iterdir()
                  if p.is_file() and p.stem == path.stem)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_FACTURAS,
                        help=f"UPS Facturas root (default: {DEFAULT_FACTURAS}).")
    parser.add_argument("--apply", action="store_true",
                        help="Actually move files. Without this, just print the plan.")
    args = parser.parse_args()

    if not args.root.exists():
        print(f"error: {args.root} does not exist", file=sys.stderr)
        return 1

    moved = 0
    renamed = 0
    skipped = 0
    seen: set[Path] = set()

    # Pass 1: rename English month folders to Spanish.
    for year_dir in sorted(args.root.iterdir()):
        if not (year_dir.is_dir() and year_dir.name.isdigit()):
            continue
        for sub in sorted(year_dir.iterdir()):
            if not sub.is_dir():
                continue
            m = _RE_MONTH_DIR.match(sub.name)
            if not m:
                continue
            month = int(m.group(1))
            if not (1 <= month <= 12):
                continue
            current_label = m.group(2).strip()
            spanish_label = SPANISH_MONTHS[month]
            if current_label.lower() == spanish_label.lower():
                continue
            target = year_dir / f"{month:02d} - {spanish_label}"
            tag = "RENAME" if args.apply else "PLAN-RENAME"
            print(f"  {tag} {year_dir.name}/{sub.name} -> {target.name}")
            if args.apply:
                if target.exists():
                    print(f"  ! target exists, leaving {sub.name} as-is", file=sys.stderr)
                    skipped += 1
                    continue
                sub.rename(target)
            renamed += 1

    # Pass 2: move flat invoice files into Spanish-named month folders.
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

            ym = detect_month(path.name)
            if ym is None:
                print(f"  ! skip (no Invoice_<num>_MMDDYY pattern): {path.name}",
                      file=sys.stderr)
                skipped += 1
                continue

            year, month = ym
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
                print(f"  {tag} {year_dir.name}/{src.name}"
                      f" -> {dest_dir.relative_to(year_dir.parent)}/")
                moved += 1

    print(
        f"\n{'Renamed' if args.apply else 'Would rename'}: {renamed} folders; "
        f"{'moved' if args.apply else 'would move'}: {moved} files; "
        f"skipped: {skipped}"
    )
    dedupe(args.root, apply=args.apply)
    if not args.apply:
        print("(dry-run) re-run with --apply to actually move and dedupe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
