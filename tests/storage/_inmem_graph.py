"""In-memory fake of the Storage Protocol for backend parity tests.

Models the surface area of `GraphStorage` over a dict-backed tree:

- Locators are nested dict keys, where leaf values are `(bytes, etag, mtime)`
  tuples and intermediate keys are sub-dicts.
- ETags are a process-local monotonic int per write, so the
  `update_xlsx_atomically` retry loop can be exercised deterministically
  (two threads racing → one wins → other sees a stale ETag → retries).

Built specifically for `tests/storage/test_protocol_compliance.py`, which
runs the same test bodies against `LocalStorage(tmp_path)` and
`FakeGraphStorage()` so both backends evolve in lockstep.
"""
from __future__ import annotations

import fnmatch
import hashlib
import itertools
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from courier_automation.storage.base import (
    OpsEntry,
    OpsLocator,
    StorageLocked,
    StorageNotFound,
)

_etag_counter = itertools.count(1)


@dataclass
class _File:
    data: bytes
    etag: str
    mtime: float


class FakeGraphStorage:
    """In-memory Storage that mimics GraphStorage's etag-based concurrency.

    The tree is a single dict mapping ``str(loc)`` → either a `_File`
    (leaf) or the literal sentinel ``"DIR"`` (intermediate folder). A
    single re-entrant lock guards all mutations — concurrency tests
    exercise the retry loop with controlled interleavings.
    """

    def __init__(self) -> None:
        self._tree: dict[str, _File | str] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _key(loc: OpsLocator) -> str:
        return str(loc)

    def _parents_of(self, loc: OpsLocator) -> list[OpsLocator]:
        out: list[OpsLocator] = []
        for i in range(1, len(loc.parts)):
            out.append(OpsLocator(*loc.parts[:i]))
        return out

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def exists(self, loc: OpsLocator) -> bool:
        with self._lock:
            return self._key(loc) in self._tree

    def is_dir(self, loc: OpsLocator) -> bool:
        with self._lock:
            v = self._tree.get(self._key(loc))
            return v == "DIR"

    def list_dir(self, loc: OpsLocator) -> list[OpsEntry]:
        with self._lock:
            if self._key(loc) and self._key(loc) not in self._tree:
                raise StorageNotFound(str(loc))
            prefix = self._key(loc)
            entries: list[OpsEntry] = []
            seen_dirs: set[str] = set()
            for k, v in self._tree.items():
                # Direct child only — skip the entry itself and skip
                # grand-children.
                if k == prefix:
                    continue
                if prefix:
                    if not k.startswith(prefix + "/"):
                        continue
                    rest = k[len(prefix) + 1:]
                else:
                    rest = k
                head, _, tail = rest.partition("/")
                if tail:
                    # grandchild — surface its parent dir once
                    if head not in seen_dirs:
                        seen_dirs.add(head)
                        entries.append(OpsEntry(name=head, is_dir=True))
                    continue
                if isinstance(v, _File):
                    entries.append(
                        OpsEntry(
                            name=head, is_dir=False,
                            size=len(v.data),
                            mtime_iso=datetime.fromtimestamp(
                                v.mtime, tz=timezone.utc
                            ).isoformat(),
                        )
                    )
                else:  # "DIR"
                    if head not in seen_dirs:
                        seen_dirs.add(head)
                        entries.append(OpsEntry(name=head, is_dir=True))
            entries.sort(key=lambda e: e.name)
            return entries

    def glob_names(
        self,
        loc: OpsLocator,
        patterns: tuple[str, ...],
        *,
        case_insensitive: bool = True,
    ) -> list[OpsLocator]:
        try:
            children = self.list_dir(loc)
        except StorageNotFound:
            return []
        out: dict[str, None] = {}
        for child in children:
            if child.is_dir:
                continue
            for pat in patterns:
                hay = child.name.lower() if case_insensitive else child.name
                needle = pat.lower() if case_insensitive else pat
                if fnmatch.fnmatchcase(hay, needle):
                    out[child.name] = None
                    break
        return [loc / name for name in out]

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read_bytes(self, loc: OpsLocator) -> bytes:
        with self._lock:
            v = self._tree.get(self._key(loc))
            if not isinstance(v, _File):
                raise StorageNotFound(str(loc))
            return v.data

    @contextmanager
    def open_local_copy(self, loc: OpsLocator) -> Iterator[Path]:
        data = self.read_bytes(loc)
        with tempfile.NamedTemporaryFile(
            suffix=Path(loc.name).suffix, delete=False
        ) as f:
            f.write(data)
            tmp = Path(f.name)
        try:
            yield tmp
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Write-once
    # ------------------------------------------------------------------

    def write_bytes(
        self, loc: OpsLocator, data: bytes, *, overwrite: bool = False
    ) -> None:
        with self._lock:
            if self._key(loc) in self._tree and not overwrite:
                raise FileExistsError(str(loc))
            self._write_locked(loc, data)

    def write_from_local(
        self, loc: OpsLocator, src: Path, *, overwrite: bool = False
    ) -> None:
        self.write_bytes(loc, Path(src).read_bytes(), overwrite=overwrite)

    def _write_locked(self, loc: OpsLocator, data: bytes) -> None:
        for parent in self._parents_of(loc):
            self._tree.setdefault(self._key(parent), "DIR")
        self._tree[self._key(loc)] = _File(
            data=data,
            etag=f"\"{next(_etag_counter)}\"",
            mtime=time.time(),
        )

    # ------------------------------------------------------------------
    # Placement
    # ------------------------------------------------------------------

    def ensure_dir(self, loc: OpsLocator) -> None:
        with self._lock:
            for parent in self._parents_of(loc):
                self._tree.setdefault(self._key(parent), "DIR")
            self._tree.setdefault(self._key(loc), "DIR")

    def move_in(self, src_local: Path, dest: OpsLocator) -> None:
        with self._lock:
            self._write_locked(dest, Path(src_local).read_bytes())
        try:
            Path(src_local).unlink()
        except OSError:
            pass

    def delete(self, loc: OpsLocator, *, missing_ok: bool = True) -> None:
        with self._lock:
            if self._key(loc) not in self._tree:
                if missing_ok:
                    return
                raise StorageNotFound(str(loc))
            del self._tree[self._key(loc)]

    def rename(self, src: OpsLocator, dest: OpsLocator) -> None:
        with self._lock:
            v = self._tree.get(self._key(src))
            if not isinstance(v, _File):
                raise StorageNotFound(str(src))
            self._write_locked(dest, v.data)
            del self._tree[self._key(src)]

    def file_hash(self, loc: OpsLocator) -> str:
        return hashlib.sha256(self.read_bytes(loc)).hexdigest()

    # ------------------------------------------------------------------
    # Transactional update — mimics If-Match etag concurrency
    # ------------------------------------------------------------------

    def update_xlsx_atomically(
        self,
        loc: OpsLocator,
        mutator: Callable[[Path], int],
        *,
        retries: int = 6,
        retry_seconds: float = 5.0,
    ) -> int:
        for attempt in range(1, retries + 1):
            with self._lock:
                v = self._tree.get(self._key(loc))
                if not isinstance(v, _File):
                    raise StorageNotFound(str(loc))
                seen_etag = v.etag
                snapshot = v.data
            with tempfile.NamedTemporaryFile(
                suffix=Path(loc.name).suffix, delete=False
            ) as f:
                f.write(snapshot)
                tmp = Path(f.name)
            try:
                rows = mutator(tmp)
                new_bytes = tmp.read_bytes()
            finally:
                try:
                    tmp.unlink()
                except OSError:
                    pass
            with self._lock:
                current = self._tree.get(self._key(loc))
                if not isinstance(current, _File) or current.etag != seen_etag:
                    if attempt == retries:
                        raise StorageLocked(
                            f"etag mismatch on {loc} after {retries} retries"
                        )
                    continue
                self._write_locked(loc, new_bytes)
                return rows
            # unreachable, but keep the linter happy
        raise StorageLocked(f"could not publish {loc}")
