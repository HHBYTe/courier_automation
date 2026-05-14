"""Tests for scripts/run_collector.py — the scheduled collector runner.

The sweep / unified steps are exercised with `_run_pipeline` and
`unified.build.main` monkeypatched; the rest is pure logic.
"""
from __future__ import annotations

import datetime as dt
import os
import time
from pathlib import Path

import scripts.run_collector as rc
from courier_automation.exit_codes import (
    EXIT_DUPLICATE,
    EXIT_OK,
    EXIT_SCHEMA,
    EXIT_USAGE,
)


def _report() -> rc.RunReport:
    return rc.RunReport(started=dt.datetime.now(), log_path=Path("x.log"))


# ---------------------------------------------------------------------------
# CarrierRun status mapping


def test_carrier_run_status_mapping():
    assert rc.CarrierRun("seur", "2026-05", EXIT_OK).status == "ok"
    assert rc.CarrierRun("seur", "2026-05", EXIT_DUPLICATE).status == "duplicate"
    assert rc.CarrierRun("seur", None, EXIT_USAGE).status == "no-files"
    err = rc.CarrierRun("ups", "2026-05", EXIT_SCHEMA)
    assert err.status == "error(2)"
    assert err.is_error
    assert not rc.CarrierRun("seur", "2026-05", EXIT_DUPLICATE).is_error
    assert not rc.CarrierRun("seur", None, EXIT_USAGE).is_error


# ---------------------------------------------------------------------------
# RunReport.errors / nothing_happened


def test_run_report_nothing_happened():
    r = _report()
    assert r.nothing_happened
    r.carrier_runs.append(rc.CarrierRun("seur", None, EXIT_DUPLICATE))
    r.carrier_runs.append(rc.CarrierRun("ups", None, EXIT_USAGE))
    assert r.nothing_happened  # all clean no-ops
    assert not r.errors


def test_run_report_carrier_error_surfaces():
    r = _report()
    r.carrier_runs.append(rc.CarrierRun("ups", "2026-05", EXIT_SCHEMA))
    assert not r.nothing_happened
    assert any("ups" in e for e in r.errors)


def test_run_report_unclassified_is_an_error():
    r = _report()
    r.collected.append(
        rc.CollectedFile("weird.txt", None, quarantine="unclassified",
                         detail="no carrier matched")
    )
    assert r.errors
    assert not r.nothing_happened


def test_run_report_conflict_is_an_error():
    r = _report()
    r.collected.append(
        rc.CollectedFile("seit.xlsx", "seitrans", quarantine="conflict",
                         detail="differs from ...")
    )
    assert any("conflict" in e for e in r.errors)


# ---------------------------------------------------------------------------
# _is_ready — inbox scan skip rules


def test_is_ready_accepts_a_settled_file(tmp_path):
    f = tmp_path / "invoice.xlsx"
    f.write_text("x")
    old = time.time() - 3600
    os.utime(f, (old, old))
    assert rc._is_ready(f)


def test_is_ready_skips_junk_and_fresh_files(tmp_path):
    old = time.time() - 3600
    for bad in (".hidden.xlsx", "~$invoice.xlsx", "invoice.tmp"):
        p = tmp_path / bad
        p.write_text("x")
        os.utime(p, (old, old))
        assert not rc._is_ready(p), bad
    # a file written just now may still be syncing
    fresh = tmp_path / "fresh.xlsx"
    fresh.write_text("x")
    assert not rc._is_ready(fresh)
    # directories (e.g. _unclassified/) are not files
    d = tmp_path / "subdir"
    d.mkdir()
    assert not rc._is_ready(d)


# ---------------------------------------------------------------------------
# _sweep — every carrier runs; affected carriers on their month


def test_sweep_covers_all_carriers(monkeypatch):
    calls: list[tuple[str, str | None]] = []

    def fake_run_pipeline(carrier: str, month: str | None) -> tuple[int, str]:
        calls.append((carrier, month))
        return (EXIT_OK, "") if carrier == "seitrans" else (EXIT_DUPLICATE, "")

    monkeypatch.setattr(rc, "_run_pipeline", fake_run_pipeline)
    report = _report()
    rc._sweep(report, affected={"seitrans": {"2026-05"}})

    assert {c for c, _ in calls} == set(rc.CARRIERS)  # all 8 swept
    assert ("seitrans", "2026-05") in calls           # affected: with its month
    assert ("ups", None) in calls                     # idle: auto-discover
    assert len(report.carrier_runs) == 8


def test_sweep_contains_a_carrier_crash(monkeypatch):
    # An uncaught exception in one carrier must be contained — recorded as
    # an error, the rest of the sweep still runs.
    def fake_run_pipeline(carrier: str, month: str | None) -> tuple[int, str]:
        if carrier == "dachser":
            return EXIT_SCHEMA, "KeyError: drift"
        return EXIT_DUPLICATE, ""

    monkeypatch.setattr(rc, "_run_pipeline", fake_run_pipeline)
    report = _report()
    rc._sweep(report, affected={})
    assert len(report.carrier_runs) == 8  # crash didn't abort the sweep
    dach = next(r for r in report.carrier_runs if r.carrier == "dachser")
    assert dach.is_error and "drift" in dach.detail


# ---------------------------------------------------------------------------
# _rebuild_unified — runs once iff a carrier appended rows


def test_rebuild_unified_skipped_when_nothing_appended(monkeypatch):
    ran: list = []
    monkeypatch.setattr(rc.unified_build, "main", lambda argv: ran.append(argv) or 0)
    report = _report()
    report.carrier_runs = [rc.CarrierRun("seur", None, EXIT_DUPLICATE)]
    rc._rebuild_unified(report)
    assert not ran
    assert report.unified_status == "skipped"


def test_rebuild_unified_runs_when_a_carrier_appended(monkeypatch):
    ran: list = []
    monkeypatch.setattr(rc.unified_build, "main", lambda argv: ran.append(argv) or 0)
    report = _report()
    report.carrier_runs = [rc.CarrierRun("seitrans", "2026-05", EXIT_OK)]
    rc._rebuild_unified(report)
    assert ran == [[]]
    assert report.unified_status == "rebuilt"
