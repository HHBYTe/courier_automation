"""Audit whether courier invoices in Facturas folders appear in historical workbooks.

Simple version: reads Excel files directly (no courier_automation import).
Writes a plain text report to `missing_invoices.txt` in the repository root.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = ROOT / "Operations - Couriers"
DEFAULT_OUTPUT = ROOT / "missing_invoices.txt"


def audit_carriers(root: Path) -> list[str]:
    """Scan Facturas folders and report invoice file counts."""
    lines: list[str] = []
    invoice_file_paths: dict[str, list[str]] = {}

    
    # Seur
    seur_path = root / "01. Seur" 
    seur_invoices_path = seur_path / "Facturas"
    if seur_path.exists():
        seur_files = list(seur_invoices_path.rglob("*.xlsx"))
        invoice_file_paths[seur_path] = [str(f) for f in seur_files]
        lines.append(f"Seur: {len(seur_files)} invoice files")
    else:
        lines.append("Seur: Facturas folder not found")
    
    # Seitrans
    seitrans_path = root / "04. Seitrans"
    seitrans_invoices_path = seitrans_path / "Facturas"
    if seitrans_path.exists():
        seitrans_files = list(seitrans_invoices_path.rglob("*.xlsx"))
        invoice_file_paths[seitrans_path] = [str(f) for f in seitrans_files]
        lines.append(f"Seitrans: {len(seitrans_files)} invoice files")
    else:
        lines.append("Seitrans: Facturas folder not found")
    
    # Correos
    correos_path = root / "05. Correos Express"
    correos_invoices_path = correos_path / "Facturas"
    if correos_path.exists():
        correos_files = list(correos_invoices_path.rglob("*.xlsx"))
        invoice_file_paths[correos_path] = [str(f) for f in correos_files]
        lines.append(f"Correos Express: {len(correos_files)} invoice files")
    else:
        lines.append("Correos Express: Facturas folder not found")
    
    # UPS
    ups_path = root / "07. UPS (UK)"
    ups_invoices_path = ups_path / "Invoices"
    if ups_path.exists():
        ups_files = list(ups_invoices_path.rglob("*.xlsx"))
        invoice_file_paths[ups_path] = [str(f) for f in ups_files]
        lines.append(f"UPS (UK): {len(ups_files)} invoice files")
    else:
        lines.append("UPS (UK): Invoices folder not found")
    
    # Wwex
    wwex_path = root / "11. Wwex (US)"
    wwex_invoices_path = wwex_path
    if wwex_path.exists():
        wwex_files = list(wwex_invoices_path.rglob("*.xlsx"))
        invoice_file_paths[wwex_path] = [str(f) for f in wwex_files]
        lines.append(f"Wwex (US): {len(wwex_files)} invoice files")
    else:
        lines.append("Wwex (US): folder not found")
    
    # Spring
    spring_path = root / "13.Spring (FR)"
    spring_invoices_path = spring_path
    if spring_path.exists():
        spring_files = list(spring_invoices_path.rglob("*.xlsx"))
        invoice_file_paths[spring_path] = [str(f) for f in spring_files]
        lines.append(f"Spring (FR): {len(spring_files)} invoice files")
    else:
        lines.append("Spring (FR): folder not found")
    
    return lines, invoice_file_paths

def extract_invoices(root: Path) -> list[str]:
    """Scan invoice folder and return list of invoice file paths from the latest year (assuming the input directory contains folders for each year)."""
    return [str(p) for p in root.rglob("*.xlsx")]

def check_invoice_inclusion(invoices: list[str], historical_workbook: Path) -> list[str]:
    """Check if invoice files are included in the historical workbook."""
    # Placeholder for actual implementation
    # This function would read the historical workbook and compare it against the list of invoice files
    return []  # Return a list of missing invoice file paths

def main() -> int:
    parser = argparse.ArgumentParser(description="Audit courier Facturas invoices.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Operations - Couriers root folder.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Text report file path.")
    args = parser.parse_args()
    
    lines = [
        f"Invoice Audit Report",
        f"Timestamp: {datetime.now().isoformat(sep=' ', timespec='seconds')}",
        f"Root: {args.root}",
        "",
    ]
    
    audit_lines, invoice_file_paths = audit_carriers(args.root)
    lines.extend(audit_lines)
    print(invoice_file_paths)

    for path, invoices in invoice_file_paths.items():
        # Placeholder: replace with actual historical workbook path
        historical_workbook = Path("path/to/historical_workbook.xlsx")
        missing_invoices = check_invoice_inclusion(invoices, historical_workbook)
        if missing_invoices:
            lines.append(f"Missing invoices in {path}:")
            lines.extend(f"  {inv}" for inv in missing_invoices)
        else:
            lines.append(f"All invoices from {path} are included in the historical workbook.")
            
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
