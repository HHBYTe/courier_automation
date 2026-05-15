"""Tests for the LocalStorage backend.

Covers every Storage Protocol method against `tmp_path`. The
transactional `update_xlsx_atomically` tests include lock-contention
scenarios that exercise the O_EXCL sidecar lock — the same semantics
the legacy WorkbookAppender's lock-contention tests cover, just
routed through the new abstraction.
"""
from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

import pytest

from courier_automation.storage import (
    LocalStorage,
    OpsLocator,
    StorageLocked,
    StorageNotFound,
)
from courier_automation.storage.local import LOCK_SUFFIX


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(
        ops_root=tmp_path / "ops",
        working_dir=tmp_path / "work",
    )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def test_exists_and_is_dir(storage: LocalStorage) -> None:
    assert not storage.exists(OpsLocator("missing.txt"))
    storage.write_bytes(OpsLocator("a/b.txt"), b"hello")
    assert storage.exists(OpsLocator("a/b.txt"))
    assert storage.is_dir(OpsLocator("a"))
    assert not storage.is_dir(OpsLocator("a/b.txt"))


def test_list_dir(storage: LocalStorage) -> None:
    storage.write_bytes(OpsLocator("dir/one.xlsx"), b"x")
    storage.write_bytes(OpsLocator("dir/two.csv"), b"yy")
    storage.ensure_dir(OpsLocator("dir/sub"))
    entries = storage.list_dir(OpsLocator("dir"))
    by_name = {e.name: e for e in entries}
    assert set(by_name) == {"one.xlsx", "two.csv", "sub"}
    assert by_name["sub"].is_dir is True
    assert by_name["one.xlsx"].is_dir is False
    assert by_name["one.xlsx"].size == 1
    assert by_name["one.xlsx"].mtime_iso is not None


def test_list_dir_missing_raises(storage: LocalStorage) -> None:
    with pytest.raises(StorageNotFound):
        storage.list_dir(OpsLocator("nope"))


def test_glob_names_case_insensitive_dedupes(storage: LocalStorage) -> None:
    storage.write_bytes(OpsLocator("d/file.xlsx"), b"x")
    storage.write_bytes(OpsLocator("d/OTHER.XLSX"), b"x")
    storage.write_bytes(OpsLocator("d/note.txt"), b"x")
    # Both *.xlsx and *.XLSX patterns should collapse to the same set
    # under case-insensitive matching (mirrors Windows glob behaviour).
    matches = storage.glob_names(
        OpsLocator("d"), ("*.xlsx", "*.XLSX")
    )
    names = sorted(p.name for p in matches)
    assert names == ["OTHER.XLSX", "file.xlsx"]


# Note: case-sensitive glob behaviour is covered by the Graph backend's
# in-memory fake (PR2), where it actually differs from case-insensitive.
# On Windows the filesystem is case-insensitive so the two modes
# collapse — exercising it here would mean writing platform-specific
# tests against no real underlying behaviour.


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def test_read_bytes_and_missing(storage: LocalStorage) -> None:
    storage.write_bytes(OpsLocator("payload.bin"), b"abc123")
    assert storage.read_bytes(OpsLocator("payload.bin")) == b"abc123"
    with pytest.raises(StorageNotFound):
        storage.read_bytes(OpsLocator("nope.bin"))


def test_open_local_copy_yields_path(storage: LocalStorage) -> None:
    storage.write_bytes(OpsLocator("a/b.txt"), b"contents")
    with storage.open_local_copy(OpsLocator("a/b.txt")) as p:
        assert p.read_bytes() == b"contents"
        assert p.exists()
    # LocalStorage yields the real file (no temp); it persists after.
    assert p.exists()


def test_open_local_copy_missing(storage: LocalStorage) -> None:
    with pytest.raises(StorageNotFound):
        with storage.open_local_copy(OpsLocator("nope")):
            pass


# ---------------------------------------------------------------------------
# Write-once
# ---------------------------------------------------------------------------

def test_write_bytes_refuses_overwrite_by_default(storage: LocalStorage) -> None:
    loc = OpsLocator("x.bin")
    storage.write_bytes(loc, b"first")
    with pytest.raises(FileExistsError):
        storage.write_bytes(loc, b"second")
    storage.write_bytes(loc, b"second", overwrite=True)
    assert storage.read_bytes(loc) == b"second"


def test_write_from_local(storage: LocalStorage, tmp_path: Path) -> None:
    src = tmp_path / "src.bin"
    src.write_bytes(b"payload")
    storage.write_from_local(OpsLocator("dst/file.bin"), src)
    assert storage.read_bytes(OpsLocator("dst/file.bin")) == b"payload"
    # Source unchanged (write_from_local copies, not moves).
    assert src.exists()


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------

def test_move_in_consumes_source(storage: LocalStorage, tmp_path: Path) -> None:
    src = tmp_path / "incoming.txt"
    src.write_bytes(b"hello")
    storage.move_in(src, OpsLocator("inbox/incoming.txt"))
    assert not src.exists()
    assert storage.read_bytes(OpsLocator("inbox/incoming.txt")) == b"hello"


def test_delete_missing_ok(storage: LocalStorage) -> None:
    storage.delete(OpsLocator("nope"))  # no error by default
    with pytest.raises(StorageNotFound):
        storage.delete(OpsLocator("nope"), missing_ok=False)


def test_delete_existing(storage: LocalStorage) -> None:
    storage.write_bytes(OpsLocator("a.bin"), b"x")
    storage.delete(OpsLocator("a.bin"))
    assert not storage.exists(OpsLocator("a.bin"))


def test_rename(storage: LocalStorage) -> None:
    storage.write_bytes(OpsLocator("old/a.bin"), b"x")
    storage.rename(OpsLocator("old/a.bin"), OpsLocator("new/b.bin"))
    assert not storage.exists(OpsLocator("old/a.bin"))
    assert storage.read_bytes(OpsLocator("new/b.bin")) == b"x"


def test_file_hash_matches_sha256(storage: LocalStorage) -> None:
    payload = b"the quick brown fox" * 1024
    storage.write_bytes(OpsLocator("f.bin"), payload)
    assert storage.file_hash(OpsLocator("f.bin")) == hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Transactional update
# ---------------------------------------------------------------------------

def test_update_xlsx_atomically_runs_mutator_and_publishes(
    storage: LocalStorage,
) -> None:
    loc = OpsLocator("master.xlsx")
    storage.write_bytes(loc, b"original-bytes")

    seen: list[Path] = []

    def mutator(working_path: Path) -> int:
        seen.append(working_path)
        # The mutator operates on a working copy off the ops tree.
        assert working_path.read_bytes() == b"original-bytes"
        assert storage.ops_root not in working_path.parents
        working_path.write_bytes(b"mutated-bytes")
        return 7

    rows = storage.update_xlsx_atomically(loc, mutator)
    assert rows == 7
    assert storage.read_bytes(loc) == b"mutated-bytes"
    # Lock sidecar cleaned up.
    assert not (storage.ops_root / "master.xlsx").with_suffix(
        ".xlsx" + LOCK_SUFFIX
    ).exists()


def test_update_xlsx_atomically_missing(storage: LocalStorage) -> None:
    with pytest.raises(StorageNotFound):
        storage.update_xlsx_atomically(
            OpsLocator("nope.xlsx"), lambda p: 0
        )


def test_update_xlsx_atomically_exhausts_retries_when_locked(
    storage: LocalStorage,
) -> None:
    loc = OpsLocator("master.xlsx")
    storage.write_bytes(loc, b"x")
    # Pre-create the sidecar lock to simulate a held lock.
    lock_path = (storage.ops_root / "master.xlsx").with_suffix(
        ".xlsx" + LOCK_SUFFIX
    )
    lock_path.write_text("pid=99999 ts=0\n")

    with pytest.raises(StorageLocked):
        storage.update_xlsx_atomically(
            loc, lambda p: 0, retries=2, retry_seconds=0.01
        )
    # Lock left intact for the real holder; we did not clobber it.
    assert lock_path.exists()


def test_update_xlsx_atomically_serialises_concurrent_writers(
    storage: LocalStorage,
) -> None:
    """Two threads racing on the same workbook serialise via O_EXCL —
    both succeed, no torn write."""
    loc = OpsLocator("master.xlsx")
    storage.write_bytes(loc, b"0")
    results: list[int] = []
    errors: list[BaseException] = []

    def worker(stamp: int) -> None:
        try:
            def mutate(p: Path) -> int:
                # Hold the lock long enough to make the other thread
                # actually queue.
                time.sleep(0.05)
                p.write_bytes(str(stamp).encode())
                return stamp
            results.append(
                storage.update_xlsx_atomically(
                    loc, mutate, retries=20, retry_seconds=0.05
                )
            )
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors
    assert sorted(results) == [1, 2]
    # Final bytes are whichever thread won the second slot — but the
    # file is not corrupted: it contains a clean stamp from one of them.
    final = storage.read_bytes(loc).decode()
    assert final in {"1", "2"}


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def test_factory_returns_local_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COURIER_BACKEND", raising=False)
    monkeypatch.setenv("COURIER_OPS_ROOT", str(tmp_path / "ops"))
    from courier_automation.storage import get_storage

    s = get_storage()
    assert isinstance(s, LocalStorage)
    assert s.ops_root == (tmp_path / "ops").resolve()


def test_factory_rejects_unknown_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COURIER_BACKEND", "wat")
    from courier_automation.storage import get_storage

    with pytest.raises(ValueError):
        get_storage()