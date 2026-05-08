"""Typer-based CLI for ingesting courier invoices.

After Facturas-tree normalization, every carrier's invoices live under
`<carrier-root>/<YYYY>/<MM> - <Mes>/`. The CLI treats one month as the
unit of work: ingest all parser-eligible files under a chosen month, with
the manifest registry handling per-file idempotency. With no `--file` or
`--month`, each command auto-discovers the latest populated month.

Exit codes:
  0  success
  1  usage error
  2  schema mismatch (loud — the diff is in the message)
  3  workbook lock timeout
  4  manifest conflict (same invoice number, different file hash)
  5  plausibility check failed (silent-drift detector — see docs/drift_handling.md)
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
import typer

from courier_automation.manifest.registry import ManifestRegistry  # noqa: F401
from courier_automation.parsers.base import ParserError, SchemaMismatch
from courier_automation.parsers.plausibility import PlausibilityError
from courier_automation.parsers.correos import CorreosParser
from courier_automation.parsers.seur import SeurParser
from courier_automation.parsers.seitrans import SeitransParser
from courier_automation.parsers.spring import SpringParser
from courier_automation.parsers.ups import UpsParser
from courier_automation.parsers.wwex import WwexParser
from courier_automation.store.workbook_appender import (
    DATOS_SHEET,
    WorkbookAppender,
    WorkbookLocked,
    export_rows,
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
DEFAULT_CORREOS_WORKBOOK = Path(
    "Operations - Couriers/05. Correos Express/Análisis Envíos Correos Express V2.xlsx"
)
DEFAULT_CORREOS_FACTURAS = Path("Operations - Couriers/05. Correos Express/Facturas")
DEFAULT_UPS_WORKBOOK = Path(
    "Operations - Couriers/07. UPS (UK)/UPS Shippings Report.xlsx"
)
DEFAULT_UPS_FACTURAS = Path("Operations - Couriers/07. UPS (UK)/Facturas")
DEFAULT_WWEX_WORKBOOK = Path(
    "Operations - Couriers/11. Wwex (US)/Wwex USA Shippings Report.xlsx"
)
DEFAULT_WWEX_FACTURAS = Path("Operations - Couriers/11. Wwex (US)/Facturas")
DEFAULT_SPRING_WORKBOOK = Path(
    "Operations - Couriers/13. Spring (FR)/Shipment Report.xlsx"
)
DEFAULT_SPRING_FACTURAS = Path("Operations - Couriers/13. Spring (FR)/Facturas")

# Manifest pipeline is disabled while we iterate on parser fixes — see
# docs/architecture.md. The shim below matches the ManifestRegistry surface
# the CLI uses (has_seen / supersedes / register) and turns each into a
# no-op so re-running ingest never short-circuits on "already ingested".
# To re-enable: swap `_NullRegistry()` back to `ManifestRegistry()` in
# `_dispatch_ingest`.
class _NullRegistry:
    db_path: Path | None = None

    def has_seen(self, *_args, **_kwargs) -> bool:
        return False

    def supersedes(self, *_args, **_kwargs) -> str | None:
        return None

    def register(self, *_args, **_kwargs) -> None:
        return None


log = logging.getLogger("courier_automation.cli")

app = typer.Typer(add_completion=False, no_args_is_help=True)
ingest_app = typer.Typer(no_args_is_help=True, help="Ingest invoices.")
app.add_typer(ingest_app, name="ingest")

WRITE_MASTER_OPTION = typer.Option(
    False,
    "--write-master",
    help="Append directly to the master workbook instead of writing a sidecar. "
         "Slower for large workbooks; use only when you actually want the "
         "master updated in-place.",
)


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
        False, "--dry-run",
        help="Parse + manifest-check only; never write anywhere. "
             "Only meaningful with --write-master.",
    ),
    write_master: bool = WRITE_MASTER_OPTION,
) -> None:
    """Ingest one or many Seur invoice files (default: write a sidecar)."""
    _setup_logging()
    files = _resolve_files(
        file=file, month=month, folder=folder,
        default_facturas=DEFAULT_SEUR_FACTURAS,
        file_globs=("*.xlsx",),
    )
    _run_ingest(
        files=files,
        parser=SeurParser(),
        workbook=workbook,
        sheet_name=DATOS_SHEET,
        dry_run=dry_run,
        write_master=write_master,
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
        False, "--dry-run",
        help="Parse + manifest-check only; never write anywhere. "
             "Only meaningful with --write-master.",
    ),
    write_master: bool = WRITE_MASTER_OPTION,
) -> None:
    """Ingest one or many Seitrans invoice files (default: write a sidecar)."""
    _setup_logging()
    files = _resolve_files(
        file=file, month=month, folder=folder,
        default_facturas=DEFAULT_SEITRANS_FACTURAS,
        file_globs=("*.xlsx",),
    )
    _run_ingest(
        files=files,
        parser=SeitransParser(),
        workbook=workbook,
        sheet_name=DATOS_SHEET,
        dry_run=dry_run,
        write_master=write_master,
    )


@ingest_app.command("correos")
def ingest_correos(
    file: Optional[Path] = typer.Option(
        None, "--file", "-f", help="Path to a single raw Correos invoice .xlsx."
    ),
    month: Optional[str] = typer.Option(
        None, "--month", help="Month to ingest in YYYY-MM form."
    ),
    folder: Optional[Path] = typer.Option(
        None, "--folder",
        help=f"Root Facturas folder (default: {DEFAULT_CORREOS_FACTURAS}).",
    ),
    workbook: Path = typer.Option(
        DEFAULT_CORREOS_WORKBOOK, "--workbook", "-w",
        help="Target Correos historical workbook.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Parse + manifest-check only; never write anywhere. "
             "Only meaningful with --write-master.",
    ),
    write_master: bool = WRITE_MASTER_OPTION,
) -> None:
    """Ingest one or many Correos Express invoice files (default: sidecar)."""
    _setup_logging()
    files = _resolve_files(
        file=file, month=month, folder=folder,
        default_facturas=DEFAULT_CORREOS_FACTURAS,
        file_globs=("*.xlsx",),
    )
    _run_ingest(
        files=files,
        parser=CorreosParser(),
        workbook=workbook,
        sheet_name=DATOS_SHEET,
        dry_run=dry_run,
        write_master=write_master,
    )


@ingest_app.command("ups")
def ingest_ups(
    file: Optional[Path] = typer.Option(
        None, "--file", "-f",
        help="Path to a single raw UPS billing CSV.",
    ),
    month: Optional[str] = typer.Option(
        None, "--month",
        help="Month to ingest in YYYY-MM form (scans the folder).",
    ),
    folder: Optional[Path] = typer.Option(
        None, "--folder",
        help=f"Root Facturas folder (default: {DEFAULT_UPS_FACTURAS}).",
    ),
    workbook: Path = typer.Option(
        DEFAULT_UPS_WORKBOOK, "--workbook", "-w",
        help="Target UPS historical workbook.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Parse + manifest-check only; never write anywhere. "
             "Only meaningful with --write-master.",
    ),
    write_master: bool = WRITE_MASTER_OPTION,
) -> None:
    """Ingest one or many UPS billing files (default: sidecar)."""
    _setup_logging()
    # Prefer raw CSVs (the actual UPS Billing Center export). The .xlsx
    # files that sometimes appear are operator-converted; we use them
    # only as a fallback when no CSV exists for the month.
    files = _resolve_files(
        file=file, month=month, folder=folder,
        default_facturas=DEFAULT_UPS_FACTURAS,
        file_globs=("*.csv",),
        fallback_globs=("*.xlsx",),
    )
    _run_ingest(
        files=files,
        parser=UpsParser(),
        workbook=workbook,
        sheet_name="Data",  # UPS uses 'Data' not 'Datos'
        dry_run=dry_run,
        write_master=write_master,
    )


@ingest_app.command("wwex")
def ingest_wwex(
    file: Optional[Path] = typer.Option(
        None, "--file", "-f",
        help="Path to a single raw Wwex shipment-detail file (.xlsx, .xls, .csv).",
    ),
    month: Optional[str] = typer.Option(
        None, "--month",
        help="Month to ingest in YYYY-MM form (scans the folder).",
    ),
    folder: Optional[Path] = typer.Option(
        None, "--folder",
        help=f"Root folder to scan (default: {DEFAULT_WWEX_FACTURAS}).",
    ),
    workbook: Path = typer.Option(
        DEFAULT_WWEX_WORKBOOK, "--workbook", "-w",
        help="Target Wwex historical workbook.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Parse + manifest-check only; never write anywhere. "
             "Only meaningful with --write-master.",
    ),
    write_master: bool = WRITE_MASTER_OPTION,
) -> None:
    """Ingest one or many Wwex shipment-detail files (default: sidecar)."""
    _setup_logging()
    files = _resolve_files(
        file=file, month=month, folder=folder,
        default_facturas=DEFAULT_WWEX_FACTURAS,
        file_globs=("*.xlsx", "*.xls", "*.csv"),
        name_filter=lambda p: "shipment" in p.name.lower(),
    )
    _run_ingest(
        files=files,
        parser=WwexParser(),
        workbook=workbook,
        sheet_name="Data",
        dry_run=dry_run,
        write_master=write_master,
    )


@ingest_app.command("spring")
def ingest_spring(
    file: Optional[Path] = typer.Option(
        None, "--file", "-f",
        help="Path to a single raw Spring invoice .XLSX.",
    ),
    month: Optional[str] = typer.Option(
        None, "--month",
        help="Month to ingest in YYYY-MM form (scans the folder).",
    ),
    folder: Optional[Path] = typer.Option(
        None, "--folder",
        help=f"Root folder to scan (default: {DEFAULT_SPRING_FACTURAS}).",
    ),
    workbook: Path = typer.Option(
        DEFAULT_SPRING_WORKBOOK, "--workbook", "-w",
        help="Target Spring historical workbook.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Parse + manifest-check only; never write anywhere. "
             "Only meaningful with --write-master.",
    ),
    write_master: bool = WRITE_MASTER_OPTION,
) -> None:
    """Ingest one or many Spring invoice files (default: sidecar)."""
    _setup_logging()
    files = _resolve_files(
        file=file, month=month, folder=folder,
        default_facturas=DEFAULT_SPRING_FACTURAS,
        file_globs=("*.xlsx", "*.XLSX"),
        name_filter=lambda p: "details of invoice" in p.name.lower(),
    )
    _run_ingest(
        files=files,
        parser=SpringParser(),
        workbook=workbook,
        sheet_name="INVOICES",
        dry_run=dry_run,
        write_master=write_master,
    )


def _run_ingest(
    *,
    files: list[Path],
    parser,
    workbook: Path,
    sheet_name: str,
    dry_run: bool,
    write_master: bool,
) -> None:
    """Dispatch one ingest run to either the master-workbook appender or the
    sidecar exporter, based on `write_master`."""
    registry = _NullRegistry()

    if not write_master:
        if dry_run:
            typer.echo(
                "error: --dry-run is only meaningful with --write-master "
                "(sidecar mode never touches the master)",
                err=True,
            )
            raise typer.Exit(code=EXIT_USAGE)
        export_path = _export_sidecar_path(workbook, files)
        _ingest_export(
            files=files,
            parser=parser,
            registry=registry,
            export_path=export_path,
            sheet_name=sheet_name,
        )
        return

    appender = WorkbookAppender(sheet_name=sheet_name)
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


def _ingest_export(
    *,
    files: list[Path],
    parser,
    registry: ManifestRegistry,
    export_path: Path,
    sheet_name: str = DATOS_SHEET,
) -> None:
    """Parse files, register in manifest, and write rows to a sidecar .xlsx
    instead of appending to the master workbook."""
    frames: list[pd.DataFrame] = []
    appended = 0
    skipped = 0

    for path in files:
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
            skipped += 1
            continue

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

        frames.append(parsed.rows)
        registry.register(
            carrier=parsed.carrier,
            invoice_number=parsed.invoice_number,
            file_hash=parsed.file_hash,
            source_path=parsed.source_path,
            rows_written=len(parsed.rows),
        )
        typer.echo(
            f"{path.name}: queued {len(parsed.rows)} rows for export "
            f"(invoice {parsed.invoice_number})"
        )
        appended += 1

    if not frames:
        typer.echo(f"done: {appended} ingested, {skipped} skipped, 0 rows exported")
        return

    combined = pd.concat(frames, ignore_index=True)
    written = export_rows(
        output_path=export_path,
        rows=combined,
        expected_columns=parser.expected_columns,
        sheet_name=sheet_name,
        numeric_columns=getattr(parser, "export_numeric_columns", ()),
        date_formats=getattr(parser, "export_date_formats", None),
        number_formats=getattr(parser, "export_number_formats", None),
    )
    typer.echo(f"exported {written} rows to {export_path}")
    typer.echo(
        f"done: {appended} ingested, {skipped} skipped, {written} rows exported"
    )


def _export_sidecar_path(workbook: Path, files: list[Path]) -> Path:
    """Place the sidecar next to the workbook, stamped with the source month
    when all files share one, otherwise a timestamp."""
    parent_names = {f.parent.name for f in files}
    stamp: str | None = None
    if len(parent_names) == 1:
        only = next(iter(parent_names))
        m = _MONTH_DIR_RE.match(only)
        if m:
            year_dir = next(iter(files)).parent.parent
            if year_dir.name.isdigit():
                stamp = f"{year_dir.name}-{int(m.group(1)):02d}"
    if stamp is None:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return workbook.parent / f"{workbook.stem} - append {stamp}.xlsx"


_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
_MONTH_DIR_RE = re.compile(r"^(\d{2})\s*-\s*")


def _parse_month_arg(month: str) -> tuple[int, int]:
    m = _MONTH_RE.match(month)
    if not m:
        typer.echo(f"--month must be YYYY-MM, got {month!r}", err=True)
        raise typer.Exit(code=EXIT_USAGE)
    return int(m.group(1)), int(m.group(2))


def _discover_month_folder(root: Path, year: int, month: int) -> Path | None:
    year_dir = root / str(year)
    if not year_dir.is_dir():
        return None
    for sub in sorted(year_dir.iterdir()):
        if not sub.is_dir():
            continue
        m = _MONTH_DIR_RE.match(sub.name)
        if m and int(m.group(1)) == month:
            return sub
    return None


def _discover_latest_month(root: Path) -> tuple[int, int, Path] | None:
    """Return (year, month, folder) for the most recent populated month, or
    None if `root` has no valid `<YYYY>/<MM> - <Mes>/` folders."""
    if not root.is_dir():
        return None
    best: tuple[int, int, Path] | None = None
    for year_dir in root.iterdir():
        if not (year_dir.is_dir() and year_dir.name.isdigit()):
            continue
        year = int(year_dir.name)
        for sub in year_dir.iterdir():
            if not sub.is_dir():
                continue
            m = _MONTH_DIR_RE.match(sub.name)
            if not m:
                continue
            month = int(m.group(1))
            if not (1 <= month <= 12):
                continue
            if best is None or (year, month) > (best[0], best[1]):
                best = (year, month, sub)
    return best


def _resolve_files(
    *,
    file: Optional[Path],
    month: Optional[str],
    folder: Optional[Path],
    default_facturas: Path,
    file_globs: tuple[str, ...] = ("*.xlsx",),
    fallback_globs: tuple[str, ...] | None = None,
    name_filter: Optional[Callable[[Path], bool]] = None,
) -> list[Path]:
    """Resolve which files to ingest from one of:
      --file PATH           : single file
      --month YYYY-MM       : that month under `folder` (or default_facturas)
      (none of the above)   : the latest populated month under same root

    `fallback_globs` are only tried when `file_globs` produces no matches —
    used by UPS to prefer raw CSVs but fall back to operator-converted
    XLSX when CSVs aren't available for a month.

    Raises typer.Exit(EXIT_USAGE) if both --file and --month given, or if
    the chosen path yields no files.
    """
    if file is not None and month is not None:
        typer.echo("error: pass at most one of --file or --month", err=True)
        raise typer.Exit(code=EXIT_USAGE)

    if file is not None:
        return [file]

    root = folder if folder is not None else default_facturas
    if month is not None:
        year, mm = _parse_month_arg(month)
        month_folder = _discover_month_folder(root, year, mm)
        descriptor = f"{month} under {root}"
    else:
        latest = _discover_latest_month(root)
        month_folder = latest[2] if latest else None
        descriptor = f"latest month under {root}"

    if month_folder is None:
        typer.echo(f"no invoices found for {descriptor}", err=True)
        raise typer.Exit(code=EXIT_USAGE)

    def _glob_in(globs: tuple[str, ...]) -> list[Path]:
        seen: set[Path] = set()
        out: list[Path] = []
        for g in globs:
            for p in month_folder.glob(g):
                # Windows globs are case-insensitive, so `*.xlsx` and
                # `*.XLSX` match the same files. Dedupe before downstream.
                if p in seen:
                    continue
                seen.add(p)
                out.append(p)
        if name_filter is not None:
            out = [f for f in out if name_filter(f)]
        return sorted(out)

    files = _glob_in(file_globs)
    if not files and fallback_globs:
        files = _glob_in(fallback_globs)
        if files:
            typer.echo(
                f"note: no {file_globs} files in {month_folder.name}; "
                f"falling back to {fallback_globs}"
            )

    if not files:
        typer.echo(f"no invoices found for {descriptor}", err=True)
        raise typer.Exit(code=EXIT_USAGE)

    typer.echo(f"resolved {len(files)} files from {month_folder}")
    return files


if __name__ == "__main__":
    app()
