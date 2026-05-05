"""Append parser rows to a courier's historical workbook.

OneDrive-safe write strategy:
  1. Acquire a sidecar lock file (atomic O_CREAT+O_EXCL).
  2. Copy the workbook into a non-OneDrive working dir.
  3. Edit the working copy with openpyxl.
  4. Stage to <target>.tmp on the same volume as the target.
  5. os.replace() — atomic on Windows when source and dest share a volume.
  6. Release the lock.

This avoids OneDrive sync conflict files and protects against partial writes
when Excel is open or the process dies mid-save.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pandas as pd
from openpyxl import load_workbook

from courier_automation.parsers.base import SchemaMismatch, assert_schema

log = logging.getLogger(__name__)

DATOS_SHEET = "Datos"
LOCK_SUFFIX = ".courier-automation.lock"


class WorkbookLocked(RuntimeError):
    """Could not acquire the sidecar lock within the configured retry budget."""


class WorkbookAppender:
    def __init__(
        self,
        *,
        sheet_name: str = DATOS_SHEET,
        lock_retries: int = 6,
        lock_retry_seconds: float = 5.0,
        working_dir: Path | None = None,
    ) -> None:
        self.sheet_name = sheet_name
        self.lock_retries = lock_retries
        self.lock_retry_seconds = lock_retry_seconds
        self.working_dir = working_dir

    def append(
        self,
        *,
        workbook_path: Path,
        rows: pd.DataFrame,
        expected_columns: tuple[str, ...],
    ) -> int:
        """Append `rows` to the workbook's data sheet. Returns rows written."""
        workbook_path = Path(workbook_path)
        if not workbook_path.exists():
            raise FileNotFoundError(f"workbook not found: {workbook_path}")

        with self._lock(workbook_path):
            with self._working_copy(workbook_path) as working_copy:
                written = self._append_to_workbook(
                    working_copy, rows, expected_columns
                )
                self._atomic_replace(working_copy, workbook_path)
        log.info(
            "workbook append: %s += %d rows (sheet=%s)",
            workbook_path.name,
            written,
            self.sheet_name,
        )
        return written

    def _append_to_workbook(
        self,
        path: Path,
        rows: pd.DataFrame,
        expected_columns: tuple[str, ...],
    ) -> int:
        wb = load_workbook(path, keep_vba=True)
        try:
            if self.sheet_name not in wb.sheetnames:
                raise SchemaMismatch(
                    f"workbook is missing the {self.sheet_name!r} sheet "
                    f"(found: {wb.sheetnames})"
                )
            ws = wb[self.sheet_name]
            actual_headers = tuple(
                cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))
            )
            assert_schema(
                pd.DataFrame(columns=list(actual_headers)), expected_columns
            )

            written = 0
            for record in rows.to_dict(orient="records"):
                ws.append(
                    [_to_excel_value(record[col]) for col in expected_columns]
                )
                written += 1
            wb.save(path)
            return written
        finally:
            wb.close()

    @contextmanager
    def _lock(self, workbook_path: Path) -> Iterator[None]:
        lock_path = workbook_path.with_suffix(workbook_path.suffix + LOCK_SUFFIX)
        for attempt in range(1, self.lock_retries + 1):
            try:
                fd = os.open(
                    lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                )
            except FileExistsError:
                if attempt == self.lock_retries:
                    raise WorkbookLocked(
                        f"could not acquire {lock_path.name} after "
                        f"{self.lock_retries} attempts (held by another "
                        f"ingest run, or stale)"
                    )
                log.info(
                    "lock held on %s, retry %d/%d in %.1fs",
                    lock_path.name,
                    attempt,
                    self.lock_retries,
                    self.lock_retry_seconds,
                )
                time.sleep(self.lock_retry_seconds)
                continue
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(f"pid={os.getpid()} ts={time.time()}\n")
                break
            except Exception:
                # If we can't write metadata, surrender the lock and re-raise.
                lock_path.unlink(missing_ok=True)
                raise
        try:
            yield
        finally:
            lock_path.unlink(missing_ok=True)

    @contextmanager
    def _working_copy(self, workbook_path: Path) -> Iterator[Path]:
        base = self.working_dir or Path(tempfile.gettempdir()) / "courier_automation_work"
        base.mkdir(parents=True, exist_ok=True)
        run_dir = base / f"run-{uuid.uuid4().hex[:12]}"
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            working_copy = run_dir / workbook_path.name
            shutil.copy2(workbook_path, working_copy)
            yield working_copy
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    def _atomic_replace(self, source: Path, target: Path) -> None:
        """Stage to <target>.tmp on the target's volume, then os.replace.
        os.replace is atomic on Windows when source and dest share a volume."""
        staging = target.with_suffix(target.suffix + ".tmp")
        if staging.exists():
            staging.unlink()
        shutil.copy2(source, staging)
        os.replace(staging, target)


def _to_excel_value(val: object) -> object:
    """Convert a pandas/numpy value into something openpyxl writes cleanly.

    NaN/NaT/<NA> → None; pandas Timestamp → datetime; pandas Int64/Float64 NA → None.
    """
    if val is None:
        return None
    if isinstance(val, pd.Timestamp):
        if pd.isna(val):
            return None
        return val.to_pydatetime()
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        # pd.isna can choke on some array-like values; we'll just pass through.
        pass
    return val
