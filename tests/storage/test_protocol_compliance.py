"""Parameterized Storage Protocol compliance tests.

Runs the same set of tests against `LocalStorage(tmp_path)` and
`FakeGraphStorage()` to keep the two backends behaviour-compatible.
The real `GraphStorage` is exercised separately by an opt-in
integration test (`tests/integration/test_graph_e2e.py`, gated on
`GRAPH_INTEGRATION=1`) — see docs/graph_backend.md.

The in-memory fake is canonical for unit tests because:
- It matches the existing "no mocks, real fixtures" style of the suite.
- The ETag retry path is deterministic — no flakiness.
- No HTTP, no network, no credentials.
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
    Storage,
    StorageNotFound,
)
from tests.storage._inmem_graph import FakeGraphStorage


@pytest.fixture(params=["local", "fake_graph"])
def storage(request: pytest.FixtureRequest, tmp_path: Path) -> Storage:
    if request.param == "local":
        return LocalStorage(
            ops_root=tmp_path / "ops", working_dir=tmp_path / "work"
        )
    return FakeGraphStorage()


# ---------------------------------------------------------------------------
# Basics — read / write / exists
# ---------------------------------------------------------------------------

def test_write_then_read_roundtrip(storage: Storage) -> None:
    loc = OpsLocator("dir/file.bin")
    storage.write_bytes(loc, b"hello")
    assert storage.read_bytes(loc) == b"hello"
    assert storage.exists(loc)


def test_read_missing_raises(storage: Storage) -> None:
    with pytest.raises(StorageNotFound):
        storage.read_bytes(OpsLocator("nope"))


def test_overwrite_default_refused(storage: Storage) -> None:
    loc = OpsLocator("f.bin")
    storage.write_bytes(loc, b"first")
    with pytest.raises(FileExistsError):
        storage.write_bytes(loc, b"second")
    storage.write_bytes(loc, b"second", overwrite=True)
    assert storage.read_bytes(loc) == b"second"


# ---------------------------------------------------------------------------
# Directory listing
# ---------------------------------------------------------------------------

def test_list_dir(storage: Storage) -> None:
    storage.write_bytes(OpsLocator("d/one.xlsx"), b"x")
    storage.write_bytes(OpsLocator("d/two.csv"), b"yy")
    storage.ensure_dir(OpsLocator("d/sub"))
    names = sorted(e.name for e in storage.list_dir(OpsLocator("d")))
    assert names == ["one.xlsx", "sub", "two.csv"]


def test_glob_case_insensitive(storage: Storage) -> None:
    # Use case-different basenames so the test makes sense on
    # case-insensitive filesystems too (Windows).
    storage.write_bytes(OpsLocator("d/alpha.xlsx"), b"x")
    storage.write_bytes(OpsLocator("d/BETA.XLSX"), b"x")
    storage.write_bytes(OpsLocator("d/note.txt"), b"x")
    matches = sorted(
        p.name for p in storage.glob_names(OpsLocator("d"), ("*.xlsx",))
    )
    assert matches == ["BETA.XLSX", "alpha.xlsx"]


# ---------------------------------------------------------------------------
# open_local_copy hands the caller a real Path
# ---------------------------------------------------------------------------

def test_open_local_copy_yields_readable_path(storage: Storage) -> None:
    storage.write_bytes(OpsLocator("a/b.txt"), b"contents")
    with storage.open_local_copy(OpsLocator("a/b.txt")) as p:
        assert isinstance(p, Path)
        assert p.read_bytes() == b"contents"


# ---------------------------------------------------------------------------
# Placement: move / rename / delete
# ---------------------------------------------------------------------------

def test_move_in_uploads_and_deletes_source(
    storage: Storage, tmp_path: Path
) -> None:
    src = tmp_path / "incoming.txt"
    src.write_bytes(b"payload")
    storage.move_in(src, OpsLocator("inbox/landed.txt"))
    assert not src.exists()
    assert storage.read_bytes(OpsLocator("inbox/landed.txt")) == b"payload"


def test_delete_missing_ok(storage: Storage) -> None:
    storage.delete(OpsLocator("nope"))
    with pytest.raises(StorageNotFound):
        storage.delete(OpsLocator("nope"), missing_ok=False)


def test_rename(storage: Storage) -> None:
    storage.write_bytes(OpsLocator("old/a.bin"), b"x")
    storage.rename(OpsLocator("old/a.bin"), OpsLocator("new/b.bin"))
    assert not storage.exists(OpsLocator("old/a.bin"))
    assert storage.read_bytes(OpsLocator("new/b.bin")) == b"x"


def test_file_hash_is_sha256(storage: Storage) -> None:
    payload = b"hello world" * 100
    storage.write_bytes(OpsLocator("f.bin"), payload)
    assert (
        storage.file_hash(OpsLocator("f.bin"))
        == hashlib.sha256(payload).hexdigest()
    )


# ---------------------------------------------------------------------------
# Transactional update — both backends serialise concurrent writers
# ---------------------------------------------------------------------------

def test_update_runs_mutator_and_publishes(storage: Storage) -> None:
    loc = OpsLocator("master.xlsx")
    storage.write_bytes(loc, b"original")

    def mutator(p: Path) -> int:
        assert p.read_bytes() == b"original"
        p.write_bytes(b"mutated")
        return 42

    rows = storage.update_xlsx_atomically(loc, mutator)
    assert rows == 42
    assert storage.read_bytes(loc) == b"mutated"


def test_update_missing_raises(storage: Storage) -> None:
    with pytest.raises(StorageNotFound):
        storage.update_xlsx_atomically(
            OpsLocator("nope.xlsx"), lambda p: 0
        )


def test_concurrent_updates_serialise(storage: Storage) -> None:
    """Two threads racing on the same locator. LocalStorage serialises
    via O_EXCL; FakeGraphStorage serialises via etag retry. Both should
    succeed; the file ends in a clean stamp from one of the two."""
    loc = OpsLocator("master.xlsx")
    storage.write_bytes(loc, b"0")
    results: list[int] = []
    errors: list[BaseException] = []

    def worker(stamp: int) -> None:
        try:
            def mutate(p: Path) -> int:
                time.sleep(0.02)
                p.write_bytes(str(stamp).encode())
                return stamp
            results.append(
                storage.update_xlsx_atomically(
                    loc, mutate, retries=30, retry_seconds=0.02
                )
            )
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors, errors
    assert sorted(results) == [1, 2]
    final = storage.read_bytes(loc).decode()
    assert final in {"1", "2"}
