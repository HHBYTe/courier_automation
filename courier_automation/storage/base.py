"""Storage Protocol — the seam between pipeline code and where bytes live.

The pipeline never reads or writes `Operations - Couriers/` directly; it
goes through a `Storage` instance. Two backends implement the Protocol:

- `LocalStorage` — wraps the local filesystem (today's behaviour).
- `GraphStorage` — talks to Microsoft Graph (Level 3).

Locators are `pathlib.PurePosixPath` values relative to the ops root,
e.g. `OpsLocator("01. Seur/NEW Análisis expediciones SEUR.xlsx")`.
They are pure strings — never carry a drive letter, never inspect the
filesystem on their own.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Protocol, runtime_checkable

OpsLocator = PurePosixPath


@dataclass(frozen=True)
class OpsEntry:
    """One child of a directory listing — what `Path.iterdir()` would
    have given us, normalised across backends."""

    name: str
    is_dir: bool
    size: int | None = None        # None for directories
    mtime_iso: str | None = None   # ISO-8601 UTC; None for directories


class StorageLocked(RuntimeError):
    """Could not publish a workbook update within the retry budget.

    LocalStorage raises this when the sidecar lock cannot be acquired;
    GraphStorage raises it after the etag-mismatch retry loop is
    exhausted. Re-exported as `WorkbookLocked` by
    `courier_automation.store.workbook_appender` for backward compat.
    """


class StorageNotFound(FileNotFoundError):
    """Backend-agnostic 'no such file' error."""


@runtime_checkable
class Storage(Protocol):
    """The single I/O contract the pipeline depends on.

    The transactional `update_xlsx_atomically` method is the linchpin:
    the master-workbook append is one atomic operation, never decomposed
    into read+modify+write at call sites. That's the only way local
    lock-file semantics and Graph etag semantics can both stay hidden
    from `pipeline.py`.
    """

    # ---- discovery ----
    def exists(self, loc: OpsLocator) -> bool: ...
    def is_dir(self, loc: OpsLocator) -> bool: ...
    def list_dir(self, loc: OpsLocator) -> list[OpsEntry]: ...
    def glob_names(
        self,
        loc: OpsLocator,
        patterns: tuple[str, ...],
        *,
        case_insensitive: bool = True,
    ) -> list[OpsLocator]:
        """Non-recursive name-glob inside `loc`.

        Defaults to case-insensitive so `*.xlsx` and `*.XLSX` collapse
        the same way on Graph as they do on Windows.
        """
        ...

    # ---- read-only bytes ----
    def read_bytes(self, loc: OpsLocator) -> bytes: ...

    def open_local_copy(
        self, loc: OpsLocator
    ) -> AbstractContextManager[Path]:
        """Yield a real local `Path` to the file's contents.

        Use this when handing a file to openpyxl, pandas, or pyarrow —
        anything that wants a path-on-disk rather than bytes-in-memory.
        On LocalStorage the yielded Path *is* the file (no copy). On
        GraphStorage it's a temp file downloaded for the duration of
        the context.
        """
        ...

    # ---- write-once ----
    def write_bytes(
        self,
        loc: OpsLocator,
        data: bytes,
        *,
        overwrite: bool = False,
    ) -> None: ...

    def write_from_local(
        self,
        loc: OpsLocator,
        src: Path,
        *,
        overwrite: bool = False,
    ) -> None:
        """Publish a file built locally (export_rows / export_parquet /
        manifest.json) to ops storage."""
        ...

    # ---- placement ----
    def ensure_dir(self, loc: OpsLocator) -> None: ...

    def move_in(self, src_local: Path, dest: OpsLocator) -> None:
        """Move a local file into ops storage. After return, `src_local`
        no longer exists and `dest` contains the bytes."""
        ...

    def delete(self, loc: OpsLocator, *, missing_ok: bool = True) -> None: ...

    def rename(self, src: OpsLocator, dest: OpsLocator) -> None: ...

    def file_hash(self, loc: OpsLocator) -> str:
        """SHA-256 hex of the file at `loc`. Used by intake's
        identical-redrop check."""
        ...

    # ---- the transactional method ----
    def update_xlsx_atomically(
        self,
        loc: OpsLocator,
        mutator: Callable[[Path], int],
        *,
        retries: int = 6,
        retry_seconds: float = 5.0,
    ) -> int:
        """Open `loc` for read+mutate+publish as one atomic transaction.

        Contract:
          1. The backend hands `mutator` a local `Path` containing the
             current bytes of `loc`.
          2. `mutator` edits that local file in place and returns the
             number of rows written (or any int the caller wants).
          3. The backend publishes the edited bytes back to `loc`
             atomically.
          4. If a concurrent writer modified `loc` between (1) and (3),
             the whole transaction restarts — `mutator` is called again
             on fresh bytes — until `retries` is exhausted, after which
             `StorageLocked` is raised.

        Local backend: `O_EXCL` sidecar lock → working copy off the
        OneDrive tree → `mutator` → `os.replace()`. Identical to the
        legacy `WorkbookAppender._lock` / `_working_copy` /
        `_atomic_replace` sequence.

        Graph backend: GET item (etag) → download bytes → `mutator` on
        a tempfile → PUT `/content` with `If-Match: <etag>` → on HTTP
        412 (precondition failed) or 423 (Excel Online session active)
        retry the whole loop.
        """
        ...


__all__ = [
    "OpsEntry",
    "OpsLocator",
    "Storage",
    "StorageLocked",
    "StorageNotFound",
]
