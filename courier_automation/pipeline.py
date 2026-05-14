"""End-to-end per-carrier pipeline.

For ONE carrier, in order:
  1. parse the new month's invoices,
  2. a duplicate guard (the manifest registry is disabled project-wide,
     so this is the idempotency safety net — it reads the master sheet
     and refuses to append a month that's already there),
  3. append the formatted rows to the carrier's master Excel workbook
     (no sidecar) AND write `data/<carrier>/<YYYY>-<MM>.parquet`,
  4. rebuild the unified cross-carrier table.

One carrier per run — designed for n8n to fan out (one Execute Command
node per carrier). `run_pipeline` returns a process exit code and, in
`--json` mode, emits exactly one result object to stdout.

Royal Mail is special-cased: it has no append-friendly master, so its
master is rebuilt from scratch each run (idempotent by construction)
and the duplicate guard is skipped.

This module imports `carriers`, `parsers`, `store`, and `unified` — but
never `cli`, which imports it (`run_pipeline`). File discovery lives in
`cli.py`; this module receives already-resolved inputs.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

from courier_automation.carriers import CarrierConfig
from courier_automation.exit_codes import (
    EXIT_DUPLICATE,
    EXIT_LOCK,
    EXIT_OK,
    EXIT_PLAUSIBILITY,
    EXIT_SCHEMA,
    EXIT_UNIFIED,
    EXIT_USAGE,
)
from courier_automation.parsers.base import (
    CourierParser,
    ParseResult,
    ParserError,
    SchemaMismatch,
    to_clean_string,
)
from courier_automation.parsers.plausibility import PlausibilityError
from courier_automation.parsers.royalmail import ROYALMAIL_COLUMNS, RoyalMailParser
from courier_automation.store.workbook_appender import (
    WorkbookAppender,
    WorkbookLocked,
    export_parquet,
    export_rows,
)

log = logging.getLogger("courier_automation.pipeline")

ROOT = Path(__file__).resolve().parent.parent
UNIFIED_MANIFEST = ROOT / "unified" / "output" / "manifest.json"

DEFAULT_GUARD_THRESHOLD = 0.90

_MONTH_DIR_RE = re.compile(r"^(\d{2})\s*-\s*", re.IGNORECASE)


class PipelineError(Exception):
    """A pipeline step failed. Carries the process exit code to return."""

    def __init__(self, exit_code: int, detail: str) -> None:
        super().__init__(detail)
        self.exit_code = exit_code
        self.detail = detail


@dataclass
class PipelineResult:
    carrier: str
    month: str | None
    status: str = "error"  # ok | duplicate | dry-run | error
    files_ingested: int = 0
    rows_appended: int = 0
    parquet_path: str | None = None
    unified_totals: dict = field(default_factory=dict)
    detail: str = ""


def emit_result(result: PipelineResult, json_out: bool) -> None:
    """Emit the result to stdout. In JSON mode this is the ONLY thing the
    process writes to stdout - everything else goes through `logging`
    (stderr), so n8n can parse stdout as a single JSON object.

    JSON is emitted ASCII-only (`ensure_ascii=True`) so it survives a
    cp1252 Windows console without mangling; detail strings are kept
    ASCII too so the human-readable branch is equally safe."""
    if json_out:
        print(json.dumps(asdict(result)))
        return
    print(
        f"[{result.carrier}] {result.status}: {result.detail}\n"
        f"  files_ingested={result.files_ingested} "
        f"rows_appended={result.rows_appended} "
        f"parquet={result.parquet_path}\n"
        f"  unified_totals={result.unified_totals or '(skipped)'}"
    )


# ---------------------------------------------------------------------------
# parsing


def _parse_files(parser: CourierParser, files: list[Path]) -> list[ParseResult]:
    """Parse every file, mapping parser exceptions to PipelineError exit codes."""
    parsed: list[ParseResult] = []
    for path in files:
        try:
            parsed.append(parser.parse(path))
        except SchemaMismatch as e:
            raise PipelineError(EXIT_SCHEMA, f"{path.name}: schema mismatch - {e}")
        except PlausibilityError as e:
            raise PipelineError(
                EXIT_PLAUSIBILITY, f"{path.name}: plausibility check failed - {e}"
            )
        except ParserError as e:
            raise PipelineError(EXIT_USAGE, f"{path.name}: parser error - {e}")
    if not parsed:
        raise PipelineError(EXIT_USAGE, "no files to parse")
    return parsed


# ---------------------------------------------------------------------------
# duplicate guard


def _read_master_readonly(path: Path, sheet: str) -> pd.DataFrame | None:
    """Read the master's data sheet read-only. Returns None if the workbook
    doesn't exist yet (first ingest for the carrier). Mirrors the
    OneDrive-locked-file fallback the backfill scripts use."""
    if not path.exists():
        return None
    try:
        return pd.read_excel(path, sheet_name=sheet, engine="openpyxl")
    except PermissionError:
        tmp = Path(tempfile.gettempdir()) / f"guard-{uuid.uuid4().hex[:8]}-{path.name}"
        shutil.copy2(path, tmp)
        try:
            return pd.read_excel(tmp, sheet_name=sheet, engine="openpyxl")
        finally:
            tmp.unlink(missing_ok=True)


def _clean_id_set(series: pd.Series) -> set[str]:
    """Normalise an id column to a set of clean strings (drops the spurious
    `.0` Excel adds, drops nulls/blanks) so raw-vs-master compares cleanly."""
    return {
        s for s in (to_clean_string(v) for v in series) if s is not None and s != ""
    }


def _duplicate_guard(
    cfg: CarrierConfig,
    parsed: list[ParseResult],
    month_label: str,
    threshold: float,
) -> tuple[bool, str]:
    """Return (is_duplicate, detail). A month is a duplicate when its rows
    already overlap the master beyond `threshold`. Any non-zero partial
    overlap also aborts — half-written months need manual inspection."""
    master = _read_master_readonly(cfg.workbook, cfg.data_sheet)
    if master is None:
        return False, "master workbook not found - treating as first ingest"

    # Primary heuristic: invoice-id overlap.
    if cfg.guard_invoice_column:
        col = cfg.guard_invoice_column
        if col not in master.columns:
            log.warning(
                "guard column %r missing from %s master - guard skipped",
                col, cfg.name,
            )
            return False, f"guard column {col!r} missing from master - guard skipped"
        new_ids: set[str] = set()
        for p in parsed:
            if col in p.rows.columns:
                new_ids |= _clean_id_set(p.rows[col])
        if not new_ids:
            return False, f"no {col!r} values in incoming rows - guard skipped"
        master_ids = _clean_id_set(master[col])
        overlap = len(new_ids & master_ids) / len(new_ids)
        if overlap >= threshold:
            return True, (
                f"{overlap:.0%} of incoming invoice ids already in master "
                f"(>= {threshold:.0%}) - month already ingested"
            )
        if overlap > 0:
            return True, (
                f"partial overlap {overlap:.0%} of incoming invoice ids already "
                f"in master - manual inspection required (use --force to override)"
            )
        return False, f"0% invoice-id overlap with master ({len(new_ids)} new ids)"

    # Fallback heuristic: month-row-count overlap.
    if cfg.guard_month_column:
        col = cfg.guard_month_column
        if col not in master.columns:
            log.warning(
                "guard month column %r missing from %s master - guard skipped",
                col, cfg.name,
            )
            return False, f"guard column {col!r} missing from master - guard skipped"
        n_new = sum(len(p.rows) for p in parsed)
        if n_new == 0:
            return False, "no incoming rows - guard skipped"
        dates = pd.to_datetime(master[col], errors="coerce")
        n_master = int((dates.dt.strftime("%Y-%m") == month_label).sum())
        if n_master >= threshold * n_new:
            return True, (
                f"master already has {n_master} rows for {month_label} "
                f"vs {n_new} incoming (>= {threshold:.0%}) - month already ingested"
            )
        return False, (
            f"master has {n_master} rows for {month_label} vs {n_new} incoming"
        )

    return False, "no duplicate-guard column configured for this carrier"


# ---------------------------------------------------------------------------
# ingest: master append + monthly parquet


def _ingest_master_and_parquet(
    cfg: CarrierConfig,
    parser: CourierParser,
    parsed: list[ParseResult],
    parquet_path: Path,
    dry_run: bool,
) -> tuple[int, Path | None]:
    """Append the parsed rows to the master workbook, then write the month
    parquet. Append-first is deliberate: if the parquet write fails, the
    master is updated and the next run's guard (which reads the master)
    still catches it. Returns (rows_written, parquet_path | None)."""
    combined = pd.concat([p.rows for p in parsed], ignore_index=True)
    if dry_run:
        log.info(
            "%s: would append %d rows to %s and write %s",
            cfg.name, len(combined), cfg.workbook.name, parquet_path,
        )
        return len(combined), None

    appender = WorkbookAppender(sheet_name=cfg.data_sheet)
    rows_written = appender.append(
        workbook_path=cfg.workbook,
        rows=combined,
        expected_columns=parser.expected_columns,
    )
    log.info("%s: appended %d rows to %s", cfg.name, rows_written, cfg.workbook.name)

    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    # export_parquet raises FileExistsError if the month parquet exists —
    # run_pipeline maps that to EXIT_DUPLICATE.
    export_parquet(
        output_path=parquet_path,
        rows=combined,
        expected_columns=parser.expected_columns,
    )
    log.info("%s: wrote %d rows -> %s", cfg.name, rows_written, parquet_path)
    return rows_written, parquet_path


# ---------------------------------------------------------------------------
# Royal Mail: full rebuild instead of append


def rebuild_royalmail_master(facturas_root: Path, workbook: Path) -> int:
    """Rebuild the Royal Mail master workbook from every invoice CSV under
    the canonical `<YYYY>/<MM> - <Mes>/` tree. Re-runnable: deletes the
    target first. Idempotent by construction — that is why the duplicate
    guard is skipped for Royal Mail. Returns rows written."""
    parser = RoyalMailParser()
    files: list[Path] = []
    if facturas_root.is_dir():
        for year_dir in sorted(facturas_root.iterdir()):
            if not (year_dir.is_dir() and year_dir.name.isdigit()):
                continue
            for month_dir in sorted(year_dir.iterdir()):
                if not (month_dir.is_dir() and _MONTH_DIR_RE.match(month_dir.name)):
                    continue
                files.extend(
                    sorted(
                        p for p in month_dir.glob("*.csv")
                        if "invoice" in p.name.lower()
                    )
                )
    if not files:
        raise PipelineError(
            EXIT_USAGE, f"no Royal Mail invoice CSVs found under {facturas_root}"
        )

    frames: list[pd.DataFrame] = []
    for path in files:
        try:
            frames.append(parser.parse(path).rows)
        except (SchemaMismatch, PlausibilityError, ParserError) as e:
            log.warning("  SKIP %s: %s", path.name, e)
    if not frames:
        raise PipelineError(EXIT_USAGE, "no parseable Royal Mail invoices")

    combined = pd.concat(frames, ignore_index=True).sort_values(
        by=["Invoice Date", "Document Number", "Posting Date", "Docket Number"],
        na_position="last", kind="stable",
    ).reset_index(drop=True)
    workbook.unlink(missing_ok=True)
    written = export_rows(
        output_path=workbook,
        rows=combined,
        expected_columns=ROYALMAIL_COLUMNS,
        sheet_name="Datos",
        date_formats=parser.export_date_formats,
    )
    log.info("royalmail: rebuilt master %s with %d rows", workbook.name, written)
    return written


def _rebuild_royalmail(
    cfg: CarrierConfig,
    parsed: list[ParseResult],
    parquet_path: Path,
    dry_run: bool,
) -> tuple[int, Path | None]:
    """Royal Mail path: rebuild the whole master, then write the month
    parquet from the already-parsed month files."""
    combined = pd.concat([p.rows for p in parsed], ignore_index=True)
    if dry_run:
        log.info(
            "royalmail: would rebuild master and write %d rows -> %s",
            len(combined), parquet_path,
        )
        return len(combined), None

    rebuild_royalmail_master(cfg.facturas_root, cfg.workbook)
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    parquet_path.unlink(missing_ok=True)  # rebuild model: overwrite freely
    export_parquet(
        output_path=parquet_path,
        rows=combined,
        expected_columns=ROYALMAIL_COLUMNS,
    )
    log.info("royalmail: wrote %d rows -> %s", len(combined), parquet_path)
    return len(combined), parquet_path


# ---------------------------------------------------------------------------
# unified rebuild


def _run_unified() -> dict:
    """Rebuild the unified cross-carrier table in-process. Returns the
    headline totals from the freshly-written manifest.json."""
    import unified.build as unified_build  # local import: heavy, optional path

    try:
        rc = unified_build.main([])
    except SystemExit as e:
        # _write_outputs / _add_eur_columns raise SystemExit(2) on schema or
        # FX failure; anything else is a generic unified failure.
        code = e.code if isinstance(e.code, int) else 1
        raise PipelineError(
            EXIT_SCHEMA if code == 2 else EXIT_UNIFIED,
            f"unified build failed (SystemExit {code})",
        )
    if rc != 0:
        raise PipelineError(EXIT_UNIFIED, f"unified build returned {rc}")

    if not UNIFIED_MANIFEST.exists():
        raise PipelineError(EXIT_UNIFIED, "unified build wrote no manifest.json")
    manifest = json.loads(UNIFIED_MANIFEST.read_text(encoding="utf-8"))
    return {
        "total_kept": manifest.get("total_kept"),
        "total_refunds": manifest.get("total_refunds"),
        "total_rejected": manifest.get("total_rejected"),
    }


# ---------------------------------------------------------------------------
# orchestrator


def run_pipeline(
    *,
    cfg: CarrierConfig,
    files: list[Path],
    parquet_path: Path,
    month_label: str | None,
    dry_run: bool,
    json_out: bool,
    force: bool,
    guard_threshold: float,
    skip_unified: bool,
) -> int:
    """Run the full pipeline for one carrier. Returns a process exit code
    and emits the PipelineResult (JSON or human) to stdout."""
    result = PipelineResult(carrier=cfg.name, month=month_label)
    parser = cfg.parser_factory()
    try:
        parsed = _parse_files(parser, files)
        result.files_ingested = len(parsed)

        if cfg.rebuild_mode:
            rows, pq = _rebuild_royalmail(cfg, parsed, parquet_path, dry_run)
        else:
            if not force:
                is_dup, detail = _duplicate_guard(
                    cfg, parsed, month_label or "", guard_threshold
                )
                if is_dup:
                    result.status = "duplicate"
                    result.detail = detail
                    emit_result(result, json_out)
                    return EXIT_DUPLICATE
                log.info("duplicate guard: %s", detail)
            rows, pq = _ingest_master_and_parquet(
                cfg, parser, parsed, parquet_path, dry_run
            )
        result.rows_appended = rows
        result.parquet_path = str(pq) if pq else None

        if dry_run:
            result.status = "dry-run"
            result.detail = f"parsed {result.files_ingested} files; nothing written"
            emit_result(result, json_out)
            return EXIT_OK

        if not skip_unified:
            result.unified_totals = _run_unified()

        result.status = "ok"
        result.detail = (
            f"appended {rows} rows; "
            + ("unified rebuilt" if not skip_unified else "unified skipped")
        )
        emit_result(result, json_out)
        return EXIT_OK

    except PipelineError as e:
        result.status = "error"
        result.detail = e.detail
        emit_result(result, json_out)
        return e.exit_code
    except WorkbookLocked as e:
        result.status = "error"
        result.detail = f"workbook locked: {e}"
        emit_result(result, json_out)
        return EXIT_LOCK
    except SchemaMismatch as e:
        result.status = "error"
        result.detail = f"workbook schema mismatch: {e}"
        emit_result(result, json_out)
        return EXIT_SCHEMA
    except FileExistsError as e:
        result.status = "duplicate"
        result.detail = (
            f"month parquet already exists ({e}); delete it to re-ingest"
        )
        emit_result(result, json_out)
        return EXIT_DUPLICATE
