"""Tests for the SQLite manifest registry."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from courier_automation.manifest.registry import ManifestRegistry


@pytest.fixture
def registry(tmp_path: Path) -> ManifestRegistry:
    return ManifestRegistry(tmp_path / "manifest.sqlite")


def _register(registry: ManifestRegistry, **overrides):
    payload = dict(
        carrier="seur",
        invoice_number="0289992025D0289264",
        file_hash="a" * 64,
        source_path="/tmp/x.xlsx",
        rows_written=412,
    )
    payload.update(overrides)
    registry.register(**payload)


def test_register_then_has_seen_returns_true(registry):
    _register(registry)
    assert registry.has_seen("seur", "0289992025D0289264", "a" * 64) is True


def test_has_seen_returns_false_for_unknown_invoice(registry):
    assert registry.has_seen("seur", "no-such-invoice", "a" * 64) is False


def test_has_seen_returns_false_for_different_hash(registry):
    _register(registry)
    assert registry.has_seen("seur", "0289992025D0289264", "b" * 64) is False


def test_re_register_same_hash_is_idempotent(registry):
    _register(registry, rows_written=412)
    _register(registry, rows_written=412)  # no-op upsert
    rows = registry.all_for_invoice("seur", "0289992025D0289264")
    assert len(rows) == 1
    assert rows[0]["rows_written"] == 412


def test_supersedes_returns_none_when_no_conflict(registry):
    assert registry.supersedes("seur", "0289992025D0289264", "a" * 64) is None
    _register(registry)
    # Same hash → not a supersession
    assert registry.supersedes("seur", "0289992025D0289264", "a" * 64) is None


def test_supersedes_detects_different_hash_for_same_invoice(registry):
    _register(registry, file_hash="a" * 64)
    prior = registry.supersedes("seur", "0289992025D0289264", "b" * 64)
    assert prior == "a" * 64


def test_register_different_hash_creates_second_row(registry):
    _register(registry, file_hash="a" * 64)
    _register(registry, file_hash="b" * 64)
    rows = registry.all_for_invoice("seur", "0289992025D0289264")
    assert len(rows) == 2


def test_concurrent_register_is_safe(tmp_path):
    db = tmp_path / "manifest.sqlite"

    def worker(i: int) -> None:
        reg = ManifestRegistry(db)
        reg.register(
            carrier="seur",
            invoice_number=f"INV-{i:04d}",
            file_hash=f"{i:064d}",
            source_path=f"/tmp/{i}.xlsx",
            rows_written=i,
        )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    reg = ManifestRegistry(db)
    for i in range(20):
        assert reg.has_seen("seur", f"INV-{i:04d}", f"{i:064d}")


def test_db_path_from_env_var(tmp_path, monkeypatch):
    custom = tmp_path / "custom.sqlite"
    monkeypatch.setenv("COURIER_AUTOMATION_MANIFEST", str(custom))
    reg = ManifestRegistry()
    assert reg.db_path == custom
    _register(reg)
    assert custom.exists()
