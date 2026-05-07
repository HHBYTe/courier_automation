"""Audit whether courier invoices in Facturas folders appear in historical workbooks.

For each carrier we walk the Facturas (or Invoices) folder, compute each file's
SHA-256 hash, and look it up in the manifest registry. A file whose hash is not
present in the registry has not been ingested into the historical workbook.

Writes a plain text report to `missing_invoices.txt` in the repository root.
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from courier_automation.manifest.registry import ManifestRegistry  # noqa: E402

DEFAULT_ROOT = ROOT / "Operations - Couriers"
DEFAULT_OUTPUT = ROOT / "missing_invoices.txt"


# (label, carrier_name_in_registry, folder_relative_to_root, invoice_subdir, file_globs)
# `invoice_subdir` is None when invoices live directly under the carrier folder.
CARRIERS: tuple[tuple[str, str, str, str | None, tuple[str, ...]], ...] = (
    ("Seur",            "seur",     "01. Seur",            "Facturas", ("*.xlsx",)),
    ("Seitrans",        "seitrans", "04. Seitrans",        "Facturas", ("*.xlsx",)),
    ("Correos Express", "correos",  "05. Correos Express", "Facturas", ("*.xlsx",)),
    ("UPS (UK)",        "ups",      "07. UPS (UK)",        "Invoices", ("*.csv",)),
    ("Wwex (US)",       "wwex",     "11. Wwex (US)",       None,       ("*.xlsx", "*.xls", "*.csv")),
    ("Spring (FR)",     "spring",   "13.Spring (FR)",      None,       ("*.xlsx",)),
)


def _compute_file_hash(path: Path, *, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def _load_known_hashes(registry: ManifestRegistry, carrier: str) -> set[str]:
    """All file_hashes the registry has seen for this carrier."""
    with sqlite3.connect(registry.db_path) as conn:
        rows = conn.execute(
            "SELECT file_hash FROM files WHERE carrier=?", (carrier,)
        ).fetchall()
    return {row[0] for row in rows}


def _discover_invoice_files(carrier_root: Path, globs: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for pattern in globs:
        files.extend(carrier_root.rglob(pattern))
    if globs == ("*.xlsx", "*.xls", "*.csv"):
        # Wwex: only shipment_detail_report files are ingestable invoices.
        files = [p for p in files if "shipment_detail_report" in p.name.lower()]
    # Skip Excel lock/temp files (`~$...`) and our own sidecar lock files.
    return sorted(
        p for p in files
        if not p.name.startswith("~$") and ".courier-automation.lock" not in p.name
    )


def audit_carrier(
    *,
    label: str,
    carrier: str,
    carrier_root: Path,
    invoice_subdir: str | None,
    globs: tuple[str, ...],
    registry: ManifestRegistry,
) -> tuple[list[str], list[Path]]:
    """Return (report lines, missing files) for one carrier."""
    if not carrier_root.exists():
        return ([f"{label}: carrier folder not found ({carrier_root})"], [])

    scan_root = carrier_root / invoice_subdir if invoice_subdir else carrier_root
    if not scan_root.exists():
        return ([f"{label}: invoice folder not found ({scan_root})"], [])

    files = _discover_invoice_files(scan_root, globs)
    if not files:
        return ([f"{label}: 0 invoice files found under {scan_root}"], [])

    known_hashes = _load_known_hashes(registry, carrier)

    missing: list[Path] = []
    for path in files:
        try:
            digest = _compute_file_hash(path)
        except OSError:
            missing.append(path)
            continue
        if digest not in known_hashes:
            missing.append(path)

    lines = [
        f"{label}: {len(files)} invoice files, {len(missing)} not in workbook"
    ]
    return lines, missing


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit courier Facturas invoices.")
    parser.add_argument(
        "--root", type=Path, default=DEFAULT_ROOT,
        help="Operations - Couriers root folder.",
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT,
        help="Text report file path.",
    )
    parser.add_argument(
        "--manifest", type=Path, default=None,
        help="Override path to the manifest SQLite db.",
    )
    args = parser.parse_args()

    registry = ManifestRegistry(db_path=args.manifest)

    lines: list[str] = [
        "Invoice Audit Report",
        f"Timestamp: {datetime.now().isoformat(sep=' ', timespec='seconds')}",
        f"Root: {args.root}",
        f"Manifest: {registry.db_path}",
        "",
        "Summary",
        "-------",
    ]
    detail_blocks: list[str] = []
    grand_total_missing = 0

    for label, carrier, subdir, invoice_subdir, globs in CARRIERS:
        carrier_root = args.root / subdir
        summary_lines, missing = audit_carrier(
            label=label,
            carrier=carrier,
            carrier_root=carrier_root,
            invoice_subdir=invoice_subdir,
            globs=globs,
            registry=registry,
        )
        lines.extend(summary_lines)

        # Pull the file count out of the summary line for the grand total.
        # We have it locally though — recompute cheaply.
        if missing:
            block = [f"\n{label} — missing invoices:"]
            block.extend(f"  {p}" for p in missing)
            detail_blocks.append("\n".join(block))
            grand_total_missing += len(missing)

    lines.append("")
    lines.append(f"Total missing invoices: {grand_total_missing}")
    if detail_blocks:
        lines.append("")
        lines.append("Details")
        lines.append("-------")
        lines.extend(detail_blocks)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report written to {args.output} ({grand_total_missing} missing)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
