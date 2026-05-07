"""Normalize Correos Express Facturas/<YYYY>/ flat layouts into <YYYY>/<MM> - <Mes>/.

Correos invoices follow several conventions; many files have the month
embedded in the invoice code (`F<YY><MM>...`) rather than at the start of
the filename. Detection is filename-only:

  1. `^YYYY_MM[_ ]…`             e.g. 2024_06_30 FA ... .pdf,
                                      2025_04_30_FAC_UNICO_F2504_13444.xlsx
  2. `^MM[-_ ]…` or `^MM <month> …`
                                  e.g. 11 NOV22 ..., 03 Marzo 2_...XLS
  3. `<spanish-month-abbr><YY>`  e.g. NOV22, DIC22 (no separator)
  4. `F<YY><MM>…` invoice code   e.g. FAC_UNICO_F2210_17420,
                                      A28799120_A58778176_F260113470_2026

Files matching none are left in place with a reason logged.

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
DEFAULT_FACTURAS = (
    ROOT / "Operations - Couriers" / "05. Correos Express" / "Facturas"
)

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
MONTH_BY_NAME: dict[str, int] = {}
for table in (SPANISH_MONTHS, ENGLISH_MONTHS):
    for m, name in table.items():
        MONTH_BY_NAME[name.lower()] = m
        MONTH_BY_NAME[name.lower()[:3]] = m
MONTH_BY_NAME["sept"] = 9

_RE_YYYY_MM = re.compile(r"^(\d{4})_(\d{2})(?=[_ ]|$)")
_RE_MM_PREFIX = re.compile(r"^(\d{2})\s*[-_ ]")
_RE_ABBR_YY = re.compile(
    r"\b(ene|feb|mar|abr|may|jun|jul|ago|sep|sept|oct|nov|dic)(\d{2})\b",
    re.IGNORECASE,
)
# `F<YY><MM>` followed by 4+ digits, with F at a word boundary (start, after
# space or underscore). Avoids false matches inside arbitrary tokens.
_RE_F_YYMM = re.compile(
    r"(?:^|[\s_])F(\d{2})(\d{2})_?\d{4,}",
    re.IGNORECASE,
)


def detect_month(filename: str, folder_year: int) -> tuple[int, int, str] | None:
    stem = Path(filename).stem

    m = _RE_YYYY_MM.match(stem)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            return (year, month, "YYYY_MM")

    m = _RE_MM_PREFIX.match(stem)
    if m:
        month = int(m.group(1))
        if 1 <= month <= 12:
            return (folder_year, month, "MM-prefix")

    m = _RE_ABBR_YY.search(stem)
    if m:
        month = MONTH_BY_NAME.get(m.group(1).lower())
        yy = int(m.group(2))
        if month is not None:
            return (2000 + yy, month, "abbr-YY")

    m = _RE_F_YYMM.search(stem)
    if m:
        yy, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            return (2000 + yy, month, "F-YYMM")

    return None


def companions_of(path: Path) -> list[Path]:
    return sorted(p for p in path.parent.iterdir()
                  if p.is_file() and p.stem == path.stem)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_FACTURAS,
                        help=f"Correos Facturas root (default: {DEFAULT_FACTURAS}).")
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

            detection = detect_month(path.name, folder_year)
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
