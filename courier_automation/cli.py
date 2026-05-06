"""Typer-based CLI for ingesting courier invoices.

Exit codes:
  0  success
  1  usage error
  2  schema mismatch (loud — the diff is in the message)
  3  workbook lock timeout
  4  manifest conflict (same invoice number, different file hash)
  5  plausibility check failed (silent-drift detector — see docs/drift_handling.md)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import typer

from courier_automation.manifest.registry import ManifestRegistry
from courier_automation.parsers.base import ParserError, SchemaMismatch
from courier_automation.parsers.plausibility import PlausibilityError
from courier_automation.parsers.seur import SeurParser
from courier_automation.parsers.seitrans import SeitransParser
from courier_automation.store.workbook_appender import (
    WorkbookAppender,
    WorkbookLocked,
)

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_SCHEMA = 2
EXIT_LOCK = 3
EXIT_MANIFEST_CONFLICT = 4
EXIT_PLAUSIBILITY = 5

DEFAULT_SEUR_WORKBOOK = Path(
    "Operations - Couriers/01. Seur/NEW Análisis expediciones SEUR.xlsx"
)
DEFAULT_SEUR_FACTURAS = Path("Operations - Couriers/01. Seur/Facturas")
DEFAULT_SEITRANS_WORKBOOK = Path(
    "Operations - Couriers/04. Seitrans/Análisis envíos Seitrans.xlsx"
)
DEFAULT_SEITRANS_FACTURAS = Path("Operations - Couriers/04. Seitrans/Facturas")

log = logging.getLogger("courier_automation.cli")

app = typer.Typer(add_completion=False, no_args_is_help=True)
ingest_app = typer.Typer(no_args_is_help=True, help="Ingest invoices.")
app.add_typer(ingest_app, name="ingest")


def _setup_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        )


@ingest_app.command("seur")
def ingest_seur(
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        "-f",
        help="Path to a single raw Seur invoice .xlsx.",
    ),
    month: Optional[str] = typer.Option(
        None,
        "--month",
        help="Month to ingest in YYYY-MM form (scans the folder).",
    ),
    folder: Optional[Path] = typer.Option(
        None,
        "--folder",
        help=f"Root Facturas folder (default: {DEFAULT_SEUR_FACTURAS}).",
    ),
    workbook: Path = typer.Option(
        DEFAULT_SEUR_WORKBOOK,
        "--workbook",
        "-w",
        help="Target Seur historical workbook.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Parse + manifest-check only; never write to the workbook.",
    ),
) -> None:
    """Ingest one or many Seur invoice files into the Datos sheet."""
    _setup_logging()

    if file is not None and month is not None:
        typer.echo("error: pass at most one of --file or --month", err=True)
        raise typer.Exit(code=EXIT_USAGE)

    if file is not None:
        files = [file]
    elif month is not None:
        files = _discover_month_files(month, folder or DEFAULT_SEUR_FACTURAS)
        if not files:
            typer.echo(
                f"no .xlsx invoices found for month {month} under {folder or DEFAULT_SEUR_FACTURAS}",
                err=True,
            )
            raise typer.Exit(code=EXIT_USAGE)
    else:
        files = _discover_latest_month_files(folder or DEFAULT_SEUR_FACTURAS)
        if not files:
            typer.echo(
                f"no .xlsx invoices found under {folder or DEFAULT_SEUR_FACTURAS}",
                err=True,
            )
            raise typer.Exit(code=EXIT_USAGE)

    parser = SeurParser()
    registry = ManifestRegistry()
    appender = WorkbookAppender()

    summary = {"appended": 0, "skipped": 0, "rows_written": 0}
    for path in files:
        result = _ingest_one(
            path,
            parser=parser,
            registry=registry,
            appender=appender,
            workbook=workbook,
            dry_run=dry_run,
        )
        summary["appended"] += int(result["appended"])
        summary["skipped"] += int(result["skipped"])
        summary["rows_written"] += int(result["rows_written"])

    typer.echo(
        f"done: {summary['appended']} ingested, {summary['skipped']} skipped, "
        f"{summary['rows_written']} rows written"
        + (" (dry-run)" if dry_run else "")
    )


@ingest_app.command("seitrans")
def ingest_seitrans(
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        "-f",
        help="Path to a single raw Seitrans invoice .xlsx.",
    ),
    month: Optional[str] = typer.Option(
        None,
        "--month",
        help="Month to ingest in YYYY-MM form (scans the folder).",
    ),
    folder: Optional[Path] = typer.Option(
        None,
        "--folder",
        help=f"Root Facturas folder (default: {DEFAULT_SEITRANS_FACTURAS}).",
    ),
    workbook: Path = typer.Option(
        DEFAULT_SEITRANS_WORKBOOK,
        "--workbook",
        "-w",
        help="Target Seitrans historical workbook.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Parse + manifest-check only; never write to the workbook.",
    ),
) -> None:
    """Ingest one or many Seitrans invoice files into the Datos sheet."""
    _setup_logging()

    if file is not None and month is not None:
        typer.echo("error: pass at most one of --file or --month", err=True)
        raise typer.Exit(code=EXIT_USAGE)

    if file is not None:
        files = [file]
    elif month is not None:
        files = _discover_seitrans_month_files(
            month, folder or DEFAULT_SEITRANS_FACTURAS
        )
        if not files:
            typer.echo(
                f"no .xlsx invoices found for month {month} under {folder or DEFAULT_SEITRANS_FACTURAS}",
                err=True,
            )
            raise typer.Exit(code=EXIT_USAGE)
    else:
        files = _discover_latest_seitrans_month_files(
            folder or DEFAULT_SEITRANS_FACTURAS
        )
        if not files:
            typer.echo(
                f"no .xlsx invoices found under {folder or DEFAULT_SEITRANS_FACTURAS}",
                err=True,
            )
            raise typer.Exit(code=EXIT_USAGE)

    parser = SeitransParser()
    registry = ManifestRegistry()
    appender = WorkbookAppender()

    summary = {"appended": 0, "skipped": 0, "rows_written": 0}
    for path in files:
        result = _ingest_one(
            path,
            parser=parser,
            registry=registry,
            appender=appender,
            workbook=workbook,
            dry_run=dry_run,
        )
        summary["appended"] += int(result["appended"])
        summary["skipped"] += int(result["skipped"])
        summary["rows_written"] += int(result["rows_written"])

    typer.echo(
        f"done: {summary['appended']} ingested, {summary['skipped']} skipped, "
        f"{summary['rows_written']} rows written"
        + (" (dry-run)" if dry_run else "")
    )


def _ingest_one(
    path: Path,
    *,
    parser: SeurParser,
    registry: ManifestRegistry,
    appender: WorkbookAppender,
    workbook: Path,
    dry_run: bool,
) -> dict[str, int]:
    try:
        parsed = parser.parse(path)
    except SchemaMismatch as e:
        typer.echo(f"{path.name}: schema mismatch — {e}", err=True)
        raise typer.Exit(code=EXIT_SCHEMA) from e
    except PlausibilityError as e:
        typer.echo(f"{path.name}: plausibility check failed — {e}", err=True)
        raise typer.Exit(code=EXIT_PLAUSIBILITY) from e
    except ParserError as e:
        typer.echo(f"{path.name}: parser error — {e}", err=True)
        raise typer.Exit(code=EXIT_USAGE) from e

    if registry.has_seen(parsed.carrier, parsed.invoice_number, parsed.file_hash):
        typer.echo(f"{path.name}: already ingested (skip)")
        return {"appended": 0, "skipped": 1, "rows_written": 0}

    prior_hash = registry.supersedes(
        parsed.carrier, parsed.invoice_number, parsed.file_hash
    )
    if prior_hash is not None:
        typer.echo(
            f"{path.name}: invoice {parsed.invoice_number} was previously ingested "
            f"with a different hash ({prior_hash[:12]}…). Refusing to ingest twice. "
            f"Inspect manually and remove the prior manifest row to proceed.",
            err=True,
        )
        raise typer.Exit(code=EXIT_MANIFEST_CONFLICT)

    if dry_run:
        typer.echo(
            f"{path.name}: would append {parsed.row_count} rows "
            f"(invoice {parsed.invoice_number}, hash {parsed.file_hash[:12]}…)"
        )
        return {"appended": 0, "skipped": 0, "rows_written": 0}

    try:
        rows_written = appender.append(
            workbook_path=workbook,
            rows=parsed.rows,
            expected_columns=parser.expected_columns,
        )
    except SchemaMismatch as e:
        typer.echo(f"{path.name}: workbook schema mismatch — {e}", err=True)
        raise typer.Exit(code=EXIT_SCHEMA) from e
    except WorkbookLocked as e:
        typer.echo(f"{path.name}: workbook locked — {e}", err=True)
        raise typer.Exit(code=EXIT_LOCK) from e

    registry.register(
        carrier=parsed.carrier,
        invoice_number=parsed.invoice_number,
        file_hash=parsed.file_hash,
        source_path=parsed.source_path,
        rows_written=rows_written,
    )
    typer.echo(
        f"{path.name}: appended {rows_written} rows "
        f"(invoice {parsed.invoice_number})"
    )
    return {"appended": 1, "skipped": 0, "rows_written": rows_written}


_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


def _discover_month_files(month: str, root: Path) -> list[Path]:
    """Find raw Seur invoices for a month under `Facturas/<YYYY>/<MM> - <Mes>/`."""
    m = _MONTH_RE.match(month)
    if not m:
        typer.echo(f"--month must be YYYY-MM, got {month!r}", err=True)
        raise typer.Exit(code=EXIT_USAGE)
    year, mm = m.group(1), m.group(2)

    year_dir = root / year
    if not year_dir.exists():
        return []
    candidates: list[Path] = []
    for sub in year_dir.glob(f"{mm} - *"):
        if sub.is_dir():
            candidates.extend(sub.glob("*.xlsx"))
    return sorted(candidates)


def _discover_latest_month_files(root: Path) -> list[Path]:
    """Find raw Seur invoices for the latest available month under `Facturas/`.

    This scans `Facturas/<YYYY>/<MM> - <Mes>/` and chooses the newest
    year/month that contains `.xlsx` invoices.
    """
    if not root.exists():
        return []

    latest_candidates: dict[tuple[int, int], list[Path]] = {}
    for year_dir in sorted(root.iterdir()):
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        year = int(year_dir.name)
        for month_dir in sorted(year_dir.iterdir()):
            if not month_dir.is_dir() or len(month_dir.name) < 2:
                continue
            month_prefix = month_dir.name[:2]
            if not month_prefix.isdigit():
                continue
            month = int(month_prefix)
            if month < 1 or month > 12:
                continue
            files = sorted(month_dir.glob("*.xlsx"))
            if files:
                latest_candidates.setdefault((year, month), []).extend(files)

    if not latest_candidates:
        return []

    latest_year_month = max(latest_candidates)
    return sorted(latest_candidates[latest_year_month])


_SEITRANS_FILE_RE = re.compile(r"^(\d{4})_(\d{2})_(\d{2})_.+$")


def _discover_seitrans_month_files(month: str, root: Path) -> list[Path]:
    """Find raw Seitrans invoices for a month under `Facturas/<YYYY>/`."""
    m = _MONTH_RE.match(month)
    if not m:
        typer.echo(f"--month must be YYYY-MM, got {month!r}", err=True)
        raise typer.Exit(code=EXIT_USAGE)
    year, mm = m.group(1), m.group(2)

    if not root.exists():
        return []

    candidates: list[Path] = []
    year_dir = root / year
    if not year_dir.exists():
        return []

    for path in sorted(year_dir.glob("*.xlsx")):
        stem = path.stem
        match = _SEITRANS_FILE_RE.match(stem)
        if not match:
            continue
        if match.group(1) == year and match.group(2) == mm:
            candidates.append(path)
    return sorted(candidates)


def _discover_latest_seitrans_month_files(root: Path) -> list[Path]:
    """Find raw Seitrans invoices for the latest available month under `Facturas/`."""
    if not root.exists():
        return []

    latest_candidates: dict[tuple[int, int], list[Path]] = {}
    for year_dir in sorted(root.iterdir()):
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        for path in sorted(year_dir.glob("*.xlsx")):
            stem = path.stem
            match = _SEITRANS_FILE_RE.match(stem)
            if not match:
                continue
            year = int(match.group(1))
            month = int(match.group(2))
            if month < 1 or month > 12:
                continue
            latest_candidates.setdefault((year, month), []).append(path)

    if not latest_candidates:
        return []

    latest_year_month = max(latest_candidates)
    return sorted(latest_candidates[latest_year_month])


if __name__ == "__main__":
    app()
