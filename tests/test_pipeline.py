"""Tests for the pipeline orchestrator: carrier registry, duplicate guard,
and the unified-build no-parquet path.

The duplicate guard is the idempotency safety net (the manifest registry
is disabled project-wide), so its verdicts are load-bearing.
"""
from __future__ import annotations

import types

import pandas as pd

import unified.build as unified_build
from courier_automation.carriers import CARRIERS
from courier_automation.pipeline import _clean_id_set, _duplicate_guard


# ---------------------------------------------------------------------------
# carrier registry


def test_registry_has_all_eight_carriers():
    assert set(CARRIERS) == {
        "seur", "seitrans", "dachser", "correos",
        "ups", "wwex", "spring", "royalmail",
    }


def test_registry_entries_are_well_formed():
    for name, cfg in CARRIERS.items():
        assert cfg.name == name
        assert callable(cfg.parser_factory)
        # parser_factory builds a parser whose carrier id matches the key.
        assert cfg.parser_factory().carrier == name
        assert cfg.data_sheet  # non-empty sheet name
        assert cfg.file_globs  # at least one glob
        # exactly one guard strategy, except royalmail (rebuild — no guard).
        has_guard = bool(cfg.guard_invoice_column or cfg.guard_month_column)
        assert has_guard or cfg.rebuild_mode


def test_registry_classification_fields():
    # Exactly seitrans + dachser use the parser-sniff probe — they have no
    # reliable filename signature. Every other carrier has a filename regex.
    probe = {n for n, c in CARRIERS.items() if c.classify_probe}
    assert probe == {"seitrans", "dachser"}
    for name, cfg in CARRIERS.items():
        if name in probe:
            assert cfg.classify_patterns == ()
        else:
            assert cfg.classify_patterns, f"{name} has no classify_patterns"


def test_only_royalmail_is_rebuild_mode():
    rebuild = {n for n, c in CARRIERS.items() if c.rebuild_mode}
    assert rebuild == {"royalmail"}


# ---------------------------------------------------------------------------
# _clean_id_set


def test_clean_id_set_strips_dot_zero_and_nulls():
    s = pd.Series(["685", "685.0", 685.0, None, "", "  ABC  "])
    assert _clean_id_set(s) == {"685", "ABC"}


# ---------------------------------------------------------------------------
# duplicate guard — invoice-overlap heuristic (seitrans)


def _parsed(df: pd.DataFrame):
    """A minimal stand-in for ParseResult — the guard only reads `.rows`."""
    return [types.SimpleNamespace(rows=df)]


def _seitrans_rows(ids: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"DOCUMENTO NUMERO": ids})


def test_guard_master_missing_is_not_duplicate(monkeypatch):
    monkeypatch.setattr(
        "courier_automation.pipeline._read_master_readonly", lambda *a, **k: None
    )
    is_dup, detail = _duplicate_guard(
        CARRIERS["seitrans"], _parsed(_seitrans_rows(["1", "2"])), "2026-04", 0.90
    )
    assert is_dup is False
    assert "first ingest" in detail


def test_guard_no_overlap_is_not_duplicate(monkeypatch):
    monkeypatch.setattr(
        "courier_automation.pipeline._read_master_readonly",
        lambda *a, **k: _seitrans_rows(["100", "101", "102"]),
    )
    is_dup, detail = _duplicate_guard(
        CARRIERS["seitrans"], _parsed(_seitrans_rows(["1", "2", "3"])), "2026-04", 0.90
    )
    assert is_dup is False
    assert "0%" in detail


def test_guard_full_overlap_is_duplicate(monkeypatch):
    monkeypatch.setattr(
        "courier_automation.pipeline._read_master_readonly",
        lambda *a, **k: _seitrans_rows(["1", "2", "3", "99"]),
    )
    is_dup, detail = _duplicate_guard(
        CARRIERS["seitrans"], _parsed(_seitrans_rows(["1", "2", "3"])), "2026-04", 0.90
    )
    assert is_dup is True
    assert "already ingested" in detail


def test_guard_partial_overlap_aborts(monkeypatch):
    # 2 of 4 incoming ids already in master = 50% — below threshold but
    # non-zero, so the guard still aborts and asks for manual inspection.
    monkeypatch.setattr(
        "courier_automation.pipeline._read_master_readonly",
        lambda *a, **k: _seitrans_rows(["1", "2", "500"]),
    )
    is_dup, detail = _duplicate_guard(
        CARRIERS["seitrans"],
        _parsed(_seitrans_rows(["1", "2", "3", "4"])),
        "2026-04", 0.90,
    )
    assert is_dup is True
    assert "partial overlap" in detail


def test_guard_missing_column_skips_gracefully(monkeypatch):
    monkeypatch.setattr(
        "courier_automation.pipeline._read_master_readonly",
        lambda *a, **k: pd.DataFrame({"SOMETHING ELSE": [1, 2]}),
    )
    is_dup, detail = _duplicate_guard(
        CARRIERS["seitrans"], _parsed(_seitrans_rows(["1"])), "2026-04", 0.90
    )
    assert is_dup is False
    assert "guard skipped" in detail


# ---------------------------------------------------------------------------
# duplicate guard — month-count fallback (wwex: synthetic invoice number)


def test_guard_month_fallback_detects_duplicate(monkeypatch):
    master = pd.DataFrame({
        "Ship Date": pd.to_datetime(
            ["2026-04-01"] * 9 + ["2026-03-15"] * 5
        )
    })
    monkeypatch.setattr(
        "courier_automation.pipeline._read_master_readonly", lambda *a, **k: master
    )
    incoming = pd.DataFrame({"Ship Date": pd.to_datetime(["2026-04-02"] * 10)})
    is_dup, detail = _duplicate_guard(
        CARRIERS["wwex"], _parsed(incoming), "2026-04", 0.90
    )
    # master already has 9 of ~10 rows for 2026-04 -> duplicate.
    assert is_dup is True
    assert "2026-04" in detail


def test_guard_month_fallback_clears_when_month_empty(monkeypatch):
    master = pd.DataFrame({"Ship Date": pd.to_datetime(["2026-03-01"] * 20)})
    monkeypatch.setattr(
        "courier_automation.pipeline._read_master_readonly", lambda *a, **k: master
    )
    incoming = pd.DataFrame({"Ship Date": pd.to_datetime(["2026-04-02"] * 10)})
    is_dup, _ = _duplicate_guard(CARRIERS["wwex"], _parsed(incoming), "2026-04", 0.90)
    assert is_dup is False


# ---------------------------------------------------------------------------
# unified.build no-parquet path (the 3-tuple/4-tuple bug fix)


def test_normalize_carrier_no_parquets_returns_4tuple(monkeypatch):
    monkeypatch.setattr(unified_build, "_discover", lambda carrier: [])
    result = unified_build._normalize_carrier("seur")
    assert len(result) == 4  # (kept, refunds, rejected, stats)
    kept, refunds, rejected, stats = result
    assert kept.empty and refunds.empty and rejected.empty
    assert stats["carrier"] == "seur"
