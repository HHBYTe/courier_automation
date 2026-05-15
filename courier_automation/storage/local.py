"""Local-filesystem implementation of the Storage Protocol.

Wraps the existing OneDrive-folder filesystem layout: `ops_root` points
at the local mount of `Operations - Couriers/` (the OneDrive sync
client provides the cloud bridge). All locators are resolved by joining
`ops_root` with the locator's POSIX string.

The transactional `update_xlsx_atomically` method mirrors the legacy
`WorkbookAppender._lock` / `_working_copy` / `_atomic_replace` sequence
1:1 — same `O_EXCL` sidecar lock, same off-tree working copy, same
`os.replace()` publish. Step 2 of the refactor wires `WorkbookAppender`
to delegate here and removes the duplicate code from the old module.
"""
from __future__ import annotations

import fnmatch
import logging
import os
import shutil
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from courier_automation.parsers.base import compute_file_hash
from courier_automation.storage.base import (
    OpsEntry,
    OpsLocator,
    StorageLocked,
    StorageNotFound,
)

log = logging.getLogger(__name__)

LOCK_SUFFIX = ".courier-automation.lock"


class LocalStorage:
    """Storage backed by the local filesystem under `ops_root`.

    The working directory for the atomic-update dance lives outside the
    ops tree on purpose — OneDrive's sync client must not see the
    intermediate edits, only the final atomic publish.
    """

    def __init__(
        self,
        ops_root: Path,
        *,
        working_dir: Path | None = None,
    ) -> None:
        self.ops_root = Path(ops_root).resolve()
        self._working_dir = (
            working_dir
            if working_dir is not None
            else Path(tempfile.gettempdir()) / "courier_automation_work"
        )

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    def _resolve(self, loc: OpsLocator) -> Path:
        return self.ops_root.joinpath(*loc.parts)

    def local_path(self, loc: OpsLocator) -> Path:
        """Return the resolved local Path for `loc`.

        Local-backend-specific. Callers that need a `pathlib.Path` (for
        e.g. logging the on-disk location, or passing to a non-Storage
        API) can use this when they know the backend is local. The
        Protocol does not expose this — the equivalent for GraphStorage
        is `open_local_copy()` (download to a temp Path).
        """
        return self._resolve(loc)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def exists(self, loc: OpsLocator) -> bool:
        return self._resolve(loc).exists()

    def is_dir(self, loc: OpsLocator) -> bool:
        return self._resolve(loc).is_dir()

    def list_dir(self, loc: OpsLocator) -> list[OpsEntry]:
        path = self._resolve(loc)
        if not path.is_dir():
            raise StorageNotFound(f"not a directory: {loc}")
        entries: list[OpsEntry] = []
        for child in sorted(path.iterdir()):
            if child.is_dir():
                entries.append(OpsEntry(name=child.name, is_dir=True))
            else:
                stat = child.stat()
                entries.append(
                    OpsEntry(
                        name=child.name,
                        is_dir=False,
                        size=stat.st_size,
                        mtime_iso=datetime.fromtimestamp(
                            stat.st_mtime, tz=timezone.utc
                        ).isoformat(),
                    )
                )
        return entries

    def glob_names(
        self,
        loc: OpsLocator,
        patterns: tuple[str, ...],
        *,
        case_insensitive: bool = True,
    ) -> list[OpsLocator]:
        path = self._resolve(loc)
        if not path.is_dir():
            return []
        # Case-insensitive matching collapses *.xlsx / *.XLSX duplicates
        # the same way Windows globs already do, so behaviour is uniform
        # across backends.
        matcher = fnmatch.fnmatchcase
        names: dict[str, None] = {}  # preserve insertion order, dedupe
        for child in sorted(path.iterdir()):
            if child.is_dir():
                continue
            for pat in patterns:
                hay = child.name.lower() if case_insensitive else child.name
                needle = pat.lower() if case_insensitive else pat
                if matcher(hay, needle):
                    names[child.name] = None
                    break
        return [loc / name for name in names]

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read_bytes(self, loc: OpsLocator) -> bytes:
        path = self._resolve(loc)
        if not path.exists():
            raise StorageNotFound(str(loc))
        return path.read_bytes()

    @contextmanager
    def open_local_copy(self, loc: OpsLocator) -> Iterator[Path]:
        """Yield the resolved local Path — no copy, no temp."""
        path = self._resolve(loc)
        if not path.exists():
            raise StorageNotFound(str(loc))
        yield path

    # ------------------------------------------------------------------
    # Write-once
    # ------------------------------------------------------------------

    def write_bytes(
        self, loc: OpsLocator, data: bytes, *, overwrite: bool = False
    ) -> None:
        path = self._resolve(loc)
        if path.exists() and not overwrite:
            raise FileExistsError(str(loc))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def write_from_local(
        self, loc: OpsLocator, src: Path, *, overwrite: bool = False
    ) -> None:
        path = self._resolve(loc)
        if path.exists() and not overwrite:
            raise FileExistsError(str(loc))
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, path)

    # ------------------------------------------------------------------
    # Placement
    # ------------------------------------------------------------------

    def ensure_dir(self, loc: OpsLocator) -> None:
        self._resolve(loc).mkdir(parents=True, exist_ok=True)

    def move_in(self, src_local: Path, dest: OpsLocator) -> None:
        path = self._resolve(dest)
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_local), str(path))

    def delete(self, loc: OpsLocator, *, missing_ok: bool = True) -> None:
        path = self._resolve(loc)
        if not path.exists():
            if missing_ok:
                return
            raise StorageNotFound(str(loc))
        path.unlink()

    def rename(self, src: OpsLocator, dest: OpsLocator) -> None:
        src_path = self._resolve(src)
        dest_path = self._resolve(dest)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_path), str(dest_path))

    def file_hash(self, loc: OpsLocator) -> str:
        path = self._resolve(loc)
        if not path.exists():
            raise StorageNotFound(str(loc))
        return compute_file_hash(path)

    # ------------------------------------------------------------------
    # Transactional update
    # ------------------------------------------------------------------

    def update_xlsx_atomically(
        self,
        loc: OpsLocator,
        mutator: Callable[[Path], int],
        *,
        retries: int = 6,
        retry_seconds: float = 5.0,
    ) -> int:
        path = self._resolve(loc)
        if not path.exists():
            raise StorageNotFound(str(loc))
        with self._lock(path, retries=retries, retry_seconds=retry_seconds):
            with self._working_copy(path) as working_copy:
                rows = mutator(working_copy)
                self._atomic_replace(working_copy, path)
        return rows

    @contextmanager
    def _lock(
        self,
        workbook_path: Path,
        *,
        retries: int,
        retry_seconds: float,
    ) -> Iterator[None]:
        lock_path = workbook_path.with_suffix(workbook_path.suffix + LOCK_SUFFIX)
        for attempt in range(1, retries + 1):
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if attempt == retries:
                    raise StorageLocked(
                        f"could not acquire {lock_path.name} after "
                        f"{retries} attempts (held by another ingest run, "
                        f"or stale)"
                    )
                log.info(
                    "lock held on %s, retry %d/%d in %.1fs",
                    lock_path.name,
                    attempt,
                    retries,
                    retry_seconds,
                )
                time.sleep(retry_seconds)
                continue
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(f"pid={os.getpid()} ts={time.time()}\n")
                break
            except Exception:
                lock_path.unlink(missing_ok=True)
                raise
        try:
            yield
        finally:
            lock_path.unlink(missing_ok=True)

    @contextmanager
    def _working_copy(self, workbook_path: Path) -> Iterator[Path]:
        base = self._working_dir
        base.mkdir(parents=True, exist_ok=True)
        run_dir = base / f"run-{uuid.uuid4().hex[:12]}"
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            working_copy = run_dir / workbook_path.name
            shutil.copy2(workbook_path, working_copy)
            yield working_copy
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    @staticmethod
    def _atomic_replace(source: Path, target: Path) -> None:
        """Stage to <target>.tmp on the target's volume, then os.replace.
        os.replace is atomic on Windows when source and dest share a
        volume."""
        staging = target.with_suffix(target.suffix + ".tmp")
        if staging.exists():
            staging.unlink()
        shutil.copy2(source, staging)
        os.replace(staging, target)


__all__ = ["LocalStorage", "LOCK_SUFFIX"]
