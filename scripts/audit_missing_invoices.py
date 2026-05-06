"""Audit invoice file pairs under Operations - Couriers.

Reports PDFs or spreadsheets that do not have a matching partner.
Writes a plain text report to the repository root by default.
"""

from __future__ import annotations

from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCAN_ROOT = ROOT / "Operations - Couriers"
DEFAULT_OUTPUT = ROOT / "missing_invoices.txt"

ALLOWED_EXTENSIONS = {".pdf", ".xlsx", ".xls"}


def scan_missing_pairs(scan_root: Path) -> list[str]:
    pairs: dict[Path, dict[str, bool]] = {}
    for path in sorted(scan_root.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            continue

        key = path.with_suffix("")
        record = pairs.setdefault(key, {"pdf": False, "sheet": False})
        if suffix == ".pdf":
            record["pdf"] = True
        else:
            record["sheet"] = True

    missing = []
    for key, record in sorted(pairs.items()):
        if record["pdf"] and not record["sheet"]:
            missing.append(f"MISSING spreadsheet for {key}")
        elif record["sheet"] and not record["pdf"]:
            missing.append(f"MISSING pdf for {key}")
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find invoice PDFs or spreadsheets under Operations - Couriers that lack a matching partner."
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_SCAN_ROOT, help="Folder to scan.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Text file to write.")
    args = parser.parse_args()

    if not args.root.exists():
        print(f"Scan root does not exist: {args.root}", file=sys.stderr)
        return 1

    missing = scan_missing_pairs(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    with args.output.open("w", encoding="utf-8") as out_file:
        out_file.write(f"missing invoice audit\n")
        out_file.write(f"scan root: {args.root}\n")
        out_file.write(f"found {len(missing)} missing pair(s)\n\n")
        for line in missing:
            out_file.write(f"{line}\n")

    print(f"Wrote report to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
