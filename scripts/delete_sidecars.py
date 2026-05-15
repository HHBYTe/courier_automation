"""Delete carrier sidecar workbooks (`<stem> - append <stamp>.xlsx`).

A sidecar is what the ingest CLI writes when `--write-master` is off: an
.xlsx placed beside the master workbook, stamped with the source month
(`YYYY-MM`) or a timestamp (`YYYYMMDD-HHMMSS`). See
`courier_automation.cli._export_sidecar_path`.

Usage:
    python scripts/delete_sidecars.py                 # dry-run, lists matches
    python scripts/delete_sidecars.py --delete        # actually removes them
    python scripts/delete_sidecars.py --root <path>   # scan a custom root
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCAN_ROOT = REPO_ROOT / "Operations - Couriers"

# Match the stamp pattern produced by _export_sidecar_path:
#   YYYY-MM       (canonical month folder)
#   YYYYMMDD-HHMMSS (timestamp fallback)
STAMP_RE = re.compile(r"^\d{4}-\d{2}$|^\d{8}-\d{6}$")
SIDECAR_SUFFIX_RE = re.compile(r" - append (?P<stamp>.+)$")


def find_sidecars(root: Path) -> list[Path]:
    matches: list[Path] = []
    for path in root.rglob("* - append *.xlsx"):
        if not path.is_file():
            continue
        m = SIDECAR_SUFFIX_RE.search(path.stem)
        if m and STAMP_RE.match(m.group("stamp")):
            matches.append(path)
    return sorted(matches)


def human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_SCAN_ROOT,
        help=f"Scan root (default: {DEFAULT_SCAN_ROOT})",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Actually delete. Without this flag, only lists matches.",
    )
    args = parser.parse_args()

    if not args.root.exists():
        print(f"Scan root does not exist: {args.root}", file=sys.stderr)
        return 2

    sidecars = find_sidecars(args.root)
    if not sidecars:
        print(f"No sidecars found under {args.root}")
        return 0

    total = sum(p.stat().st_size for p in sidecars)
    verb = "Deleting" if args.delete else "Would delete"
    print(f"{verb} {len(sidecars)} sidecar(s) ({human_bytes(total)}):")
    for p in sidecars:
        print(f"  {p.relative_to(args.root)}")

    if not args.delete:
        print("\nDry-run. Re-run with --delete to remove these files.")
        return 0

    failed: list[tuple[Path, OSError]] = []
    for p in sidecars:
        try:
            p.unlink()
        except OSError as exc:
            failed.append((p, exc))

    if failed:
        print(f"\n{len(failed)} file(s) could not be deleted:", file=sys.stderr)
        for path, exc in failed:
            print(f"  {path}: {exc}", file=sys.stderr)
        return 1

    print(f"\nDeleted {len(sidecars)} sidecar(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
