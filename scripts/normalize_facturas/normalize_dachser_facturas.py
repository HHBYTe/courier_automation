"""Normalize Dachser Facturas/<YYYY>/ flat layouts into <YYYY>/<MM> - <Mes>/ subfolders.

Unlike Seur, Dachser invoices have no shared schema (different per-year layouts,
PDFs with no useful metadata, ZIPs, manual reports). Month detection is purely
filename-based. Files whose filename doesn't clearly encode a month are skipped
with a reason and left in place.

Recognised patterns (tried in order):
  1. `YYYY_MM_…`              e.g. 2026_01_ES_112261586.xlsx
  2. `^MM[-_ ]…`              e.g. 01-2025 IN 112100582.xlsx, 04 Abril 2024.xlsx
  3. `<spanish-month>YYYY` or `<spanish-month> YYYY` (case-insensitive,
     full or 3-letter abbreviation)
                              e.g. artero enero2023.xlsx, artero nov2022.XLS

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
DEFAULT_FACTURAS = ROOT / "Operations - Couriers" / "03. Dachser" / "Facturas"

SPANISH_MONTHS: dict[int, str] = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}
MONTH_BY_NAME: dict[str, int] = {}
for m, name in SPANISH_MONTHS.items():
    MONTH_BY_NAME[name.lower()] = m
    MONTH_BY_NAME[name.lower()[:3]] = m
# Spanish-specific 3-letter forms that don't match the first 3 of the full name.
MONTH_BY_NAME["sept"] = 9

_RE_YYYY_MM = re.compile(r"^(\d{4})[_\- ](\d{2})(?=[_\- ]|$)")
_RE_MM_PREFIX = re.compile(r"^(\d{2})\s*[-_ ]")
_RE_MONTH_NAME_YEAR = re.compile(
    r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|sept|"
    r"octubre|noviembre|diciembre|"
    r"ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)"
    r"\s*(\d{4})",
    re.IGNORECASE,
)


def detect_month(filename: str, folder_year: int) -> tuple[int, int, str] | None:
    """Return (year, month, matched_pattern) or None if no clear signal."""
    stem = Path(filename).stem

    # Pattern 1: 2026_01_… or 2026-01-…
    m = _RE_YYYY_MM.match(stem)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            return (year, month, "YYYY_MM")

    # Pattern 2: leading 2-digit month with a separator. Use folder year,
    # since this pattern doesn't carry a year.
    m = _RE_MM_PREFIX.match(stem)
    if m:
        month = int(m.group(1))
        if 1 <= month <= 12:
            return (folder_year, month, "MM-prefix")

    # Pattern 3: spanish month name + 4-digit year somewhere in the name
    m = _RE_MONTH_NAME_YEAR.search(stem)
    if m:
        name = m.group(1).lower()
        year = int(m.group(2))
        month = MONTH_BY_NAME.get(name)
        if month is not None:
            return (year, month, "month-name")

    return None


def companions_of(path: Path) -> list[Path]:
    """All files in the same dir whose stem matches `path.stem`."""
    return sorted(p for p in path.parent.iterdir()
                  if p.is_file() and p.stem == path.stem)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_FACTURAS,
                        help=f"Dachser Facturas root (default: {DEFAULT_FACTURAS}).")
    parser.add_argument("--apply", action="store_true",
                        help="Actually move files. Without this, just print the plan.")
    args = parser.parse_args()

    if not args.root.exists():
        print(f"error: {args.root} does not exist", file=sys.stderr)
        return 1

    moved = 0
    skipped = 0
    seen_stems: set[Path] = set()

    for year_dir in sorted(args.root.iterdir()):
        if not (year_dir.is_dir() and year_dir.name.isdigit()):
            continue
        folder_year = int(year_dir.name)
        flat_files = sorted(p for p in year_dir.iterdir() if p.is_file())
        if not flat_files:
            continue

        print(f"\n[{year_dir.name}] {len(flat_files)} flat files")
        for path in flat_files:
            if path in seen_stems:
                continue

            detection = detect_month(path.name, folder_year)
            if detection is None:
                print(f"  ! skip (no month pattern): {path.name}", file=sys.stderr)
                skipped += 1
                continue

            year, month, pattern = detection
            if year != folder_year:
                print(f"  ! skip ({path.name}: filename year {year} != folder {folder_year})",
                      file=sys.stderr)
                skipped += 1
                continue

            dest_dir = year_dir / f"{month:02d} - {SPANISH_MONTHS[month]}"
            companions = companions_of(path)
            for src in companions:
                seen_stems.add(src)
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
