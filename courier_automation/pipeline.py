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

import io
import json
import logging
import re
import tempfile
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
from courier_automation.storage import LocalStorage, OpsLocator, Storage
from courier_automation.store.workbook_appender import (
    WorkbookAppender,
    WorkbookLocked,
    export_parquet,
    export_rows,
)

log = logging.getLogger("courier_automation.pipeline")

ROOT = Path(__file__).resolve().parent.parent
UNIFIED_MANIFEST = ROOT / "unified" / "output" / "manifest.json"
UNIFIED_MANIFEST_LOC = OpsLocator("unified/output/manifest.json")

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


def _read_master_readonly(
    storage: Storage, loc: OpsLocator, sheet: str
) -> pd.DataFrame | None:
    """Read the master's data sheet read-only. Returns None if the workbook
    doesn't exist yet (first ingest for the carrier).

    Two-stage read for resilience: first try `open_local_copy` which
    yields a real path (zero-copy on LocalStorage, a tempfile download
    on GraphStorage). If that fails with `PermissionError` (Excel has
    the file open on the operator's PC), fall back to streaming the
    bytes and parsing from a `BytesIO` — bypasses the Windows file lock.
    """
    if not storage.exists(loc):
        return None
    try:
        with storage.open_local_copy(loc) as p:
            return pd.read_excel(p, sheet_name=sheet, engine="openpyxl")
    except (PermissionError, OSError):
        data = storage.read_bytes(loc)
        return pd.read_excel(
            io.BytesIO(data), sheet_name=sheet, engine="openpyxl"
        )


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
    *,
    storage: Storage | None = None,
) -> tuple[bool, str]:
    """Return (is_duplicate, detail). A month is a duplicate when its rows
    already overlap the master beyond `threshold`. Any non-zero partial
    overlap also aborts — half-written months need manual inspection."""
    if storage is None:
        storage = LocalStorage(ops_root=ROOT)
    workbook_loc = OpsLocator(cfg.workbook.as_posix())
    master = _read_master_readonly(storage, workbook_loc, cfg.data_sheet)
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
    parquet_loc: OpsLocator,
    dry_run: bool,
    storage: Storage,
) -> tuple[int, OpsLocator | None]:
    """Append the parsed rows to the master workbook, then write the month
    parquet. Append-first is deliberate: if the parquet write fails, the
    master is updated and the next run's guard (which reads the master)
    still catches it. Returns (rows_written, parquet_loc | None)."""
    combined = pd.concat([p.rows for p in parsed], ignore_index=True)
    if dry_run:
        log.info(
            "%s: would append %d rows to %s and write %s",
            cfg.name, len(combined), cfg.workbook.name, parquet_loc,
        )
        return len(combined), None

    workbook_loc = OpsLocator(cfg.workbook.as_posix())
    appender = WorkbookAppender(sheet_name=cfg.data_sheet, storage=storage)
    rows_written = appender.append(
        workbook_path=_resolve_for_appender(storage, workbook_loc),
        rows=combined,
        expected_columns=parser.expected_columns,
    )
    log.info("%s: appended %d rows to %s", cfg.name, rows_written, cfg.workbook.name)

    # Build the parquet on a local tempfile then publish via Storage.
    # `export_parquet` raises FileExistsError if the target exists, so
    # check existence in Storage first — run_pipeline maps that to
    # EXIT_DUPLICATE.
    if storage.exists(parquet_loc):
        raise FileExistsError(str(parquet_loc))
    with tempfile.TemporaryDirectory() as td:
        tmp_pq = Path(td) / parquet_loc.name
        export_parquet(
            output_path=tmp_pq,
            rows=combined,
            expected_columns=parser.expected_columns,
        )
        storage.write_from_local(parquet_loc, tmp_pq)
    log.info("%s: wrote %d rows -> %s", cfg.name, rows_written, parquet_loc)
    return rows_written, parquet_loc


def _path_to_loc(storage: Storage, path: Path | str) -> OpsLocator:
    """Convert a `Path` (or path-like string) to an `OpsLocator` relative
    to the storage's ops root. Absolute paths under a `LocalStorage`'s
    `ops_root` are made relative; already-relative paths pass through as
    locators. For non-local backends only relative paths make sense."""
    p = Path(path)
    if p.is_absolute():
        if isinstance(storage, LocalStorage):
            return OpsLocator(p.resolve().relative_to(storage.ops_root).as_posix())
        # Non-local backends shouldn't be handed an absolute local Path.
        return OpsLocator(p.name)
    return OpsLocator(p.as_posix())


def _resolve_for_appender(storage: Storage, loc: OpsLocator) -> Path:
    """Resolve `loc` to a real `Path` to hand to `WorkbookAppender.append`.

    `WorkbookAppender.append` still takes `workbook_path: Path` (for
    back-compat with legacy callers) — but when a Storage is injected
    it uses that Storage for the actual transactional write. For
    LocalStorage we resolve via `local_path`; for other backends we
    return a synthetic Path carrying just the locator string (the
    appender will use storage.update_xlsx_atomically with the locator
    derived from this Path's parent relationship to ops_root).
    """
    if isinstance(storage, LocalStorage):
        return storage.local_path(loc)
    # Non-local backend: synthesise a Path. WorkbookAppender.append
    # converts this to an OpsLocator internally using its existing
    # workbook_path.relative_to(storage.ops_root) logic, but with a
    # synthetic Path that lacks an ops_root match we fall back to the
    # locator's name. This branch only matters once GraphStorage is
    # wired up; for PR1 we always pass LocalStorage.
    return Path(str(loc))


# ---------------------------------------------------------------------------
# Royal Mail: full rebuild instead of append


def rebuild_royalmail_master(
    facturas_root: Path,
    workbook: Path,
    *,
    storage: Storage | None = None,
) -> int:
    """Rebuild the Royal Mail master workbook from every invoice CSV under
    the canonical `<YYYY>/<MM> - <Mes>/` tree. Re-runnable: deletes the
    target first. Idempotent by construction — that is why the duplicate
    guard is skipped for Royal Mail. Returns rows written.

    `facturas_root` and `workbook` are accepted as `Path` for backward
    compatibility with the standalone `scripts/build_royalmail_master.py`
    entrypoint; internally we convert to OpsLocators against `storage`.
    """
    if storage is None:
        storage = LocalStorage(ops_root=ROOT)
    parser = RoyalMailParser()
    facturas_loc = _path_to_loc(storage, facturas_root)
    workbook_loc = _path_to_loc(storage, workbook)

    # Discover month-folder CSV files via Storage.
    csv_locs: list[OpsLocator] = []
    if storage.exists(facturas_loc) and storage.is_dir(facturas_loc):
        for year_entry in storage.list_dir(facturas_loc):
            if not (year_entry.is_dir and year_entry.name.isdigit()):
                continue
            year_loc = facturas_loc / year_entry.name
            for month_entry in storage.list_dir(year_loc):
                if not (month_entry.is_dir and _MONTH_DIR_RE.match(month_entry.name)):
                    continue
                month_loc = year_loc / month_entry.name
                for csv_loc in storage.glob_names(month_loc, ("*.csv",)):
                    if "invoice" in csv_loc.name.lower():
                        csv_locs.append(csv_loc)
    csv_locs.sort()
    if not csv_locs:
        raise PipelineError(
            EXIT_USAGE, f"no Royal Mail invoice CSVs found under {facturas_root}"
        )

    frames: list[pd.DataFrame] = []
    for loc in csv_locs:
        try:
            with storage.open_local_copy(loc) as p:
                frames.append(parser.parse(p).rows)
        except (SchemaMismatch, PlausibilityError, ParserError) as e:
            log.warning("  SKIP %s: %s", loc.name, e)
    if not frames:
        raise PipelineError(EXIT_USAGE, "no parseable Royal Mail invoices")

    combined = pd.concat(frames, ignore_index=True).sort_values(
        by=["Invoice Date", "Document Number", "Posting Date", "Docket Number"],
        na_position="last", kind="stable",
    ).reset_index(drop=True)
    storage.delete(workbook_loc, missing_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tmp_wb = Path(td) / workbook_loc.name
        written = export_rows(
            output_path=tmp_wb,
            rows=combined,
            expected_columns=ROYALMAIL_COLUMNS,
            sheet_name="Datos",
            date_formats=parser.export_date_formats,
        )
        storage.write_from_local(workbook_loc, tmp_wb)
    log.info("royalmail: rebuilt master %s with %d rows", workbook_loc.name, written)
    return written


def _rebuild_royalmail(
    cfg: CarrierConfig,
    parsed: list[ParseResult],
    parquet_loc: OpsLocator,
    dry_run: bool,
    storage: Storage,
) -> tuple[int, OpsLocator | None]:
    """Royal Mail path: rebuild the whole master, then write the month
    parquet from the already-parsed month files."""
    combined = pd.concat([p.rows for p in parsed], ignore_index=True)
    if dry_run:
        log.info(
            "royalmail: would rebuild master and write %d rows -> %s",
            len(combined), parquet_loc,
        )
        return len(combined), None

    rebuild_royalmail_master(cfg.facturas_root, cfg.workbook, storage=storage)
    storage.delete(parquet_loc, missing_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tmp_pq = Path(td) / parquet_loc.name
        export_parquet(
            output_path=tmp_pq,
            rows=combined,
            expected_columns=ROYALMAIL_COLUMNS,
        )
        storage.write_from_local(parquet_loc, tmp_pq)
    log.info("royalmail: wrote %d rows -> %s", len(combined), parquet_loc)
    return len(combined), parquet_loc


# ---------------------------------------------------------------------------
# unified rebuild


def _run_unified(storage: Storage) -> dict:
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

    if not storage.exists(UNIFIED_MANIFEST_LOC):
        raise PipelineError(EXIT_UNIFIED, "unified build wrote no manifest.json")
    manifest = json.loads(storage.read_bytes(UNIFIED_MANIFEST_LOC).decode("utf-8"))
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
    storage: Storage | None = None,
) -> int:
    """Run the full pipeline for one carrier. Returns a process exit code
    and emits the PipelineResult (JSON or human) to stdout.

    `storage` defaults to `LocalStorage(ops_root=ROOT)` — the legacy
    behaviour. Callers (the CLI, the collector) pass an explicit
    Storage for tests / cloud runs.
    """
    if storage is None:
        storage = LocalStorage(ops_root=ROOT)
    parquet_loc = _path_to_loc(storage, parquet_path)

    result = PipelineResult(carrier=cfg.name, month=month_label)
    parser = cfg.parser_factory()
    try:
        parsed = _parse_files(parser, files)
        result.files_ingested = len(parsed)

        if cfg.rebuild_mode:
            rows, pq = _rebuild_royalmail(
                cfg, parsed, parquet_loc, dry_run, storage
            )
        else:
            if not force:
                is_dup, detail = _duplicate_guard(
                    cfg, parsed, month_label or "", guard_threshold,
                    storage=storage,
                )
                if is_dup:
                    result.status = "duplicate"
                    result.detail = detail
                    emit_result(result, json_out)
                    return EXIT_DUPLICATE
                log.info("duplicate guard: %s", detail)
            rows, pq = _ingest_master_and_parquet(
                cfg, parser, parsed, parquet_loc, dry_run, storage
            )
        result.rows_appended = rows
        result.parquet_path = str(pq) if pq else None

        if dry_run:
            result.status = "dry-run"
            result.detail = f"parsed {result.files_ingested} files; nothing written"
            emit_result(result, json_out)
            return EXIT_OK

        if not skip_unified:
            result.unified_totals = _run_unified(storage)

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
