"""Tests for courier_automation.intake — classifying dropped invoice
files to a carrier and filing them into the Facturas tree.

`place_invoice_file` is always called with `base_dir=tmp_path` so tests
never touch the real `Operations - Couriers/` tree.
"""
from __future__ import annotations

import shutil

import pytest
from openpyxl import Workbook

from courier_automation.intake import (
    IntakeConflict,
    classify_invoice_file,
    place_invoice_file,
    quarantine_file,
)
from tests.conftest import make_dachser_invoice, make_seitrans_invoice


# ---------------------------------------------------------------------------
# Tier A — filename fast path


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("0289992025D0289264.xlsx", "seur"),
        ("0289992025AD0001394.xlsx", "seur"),
        ("20260511_9075855419_Invoice_0751634000.csv", "royalmail"),
        ("Invoice_59586753_010224.csv", "ups"),
        ("Invoice_56093066_010323.xlsx", "ups"),
        ("FAC_UNICO_F2506_14077.xlsx", "correos"),
        ("2025_01_31 FAC_UNICO_F2501_14307.xlsx", "correos"),
        ("2025_05_31 shipment_detail_report.xlsx", "wwex"),
        ("shipmentDetailsUPS_W130089866_2026-01-01_2026-01-31.xlsx", "wwex"),
        ("E2509827_ES_Details of Invoice_O_110003790.XLSX", "spring"),
    ],
)
def test_classify_filename_patterns(tmp_path, filename, expected):
    cls = classify_invoice_file(tmp_path / filename)
    assert cls.carrier == expected
    assert cls.parse_result is None  # tier A never parses


def test_classify_unknown_returns_none(tmp_path):
    assert classify_invoice_file(tmp_path / "random.txt").carrier is None
    assert classify_invoice_file(tmp_path / "meeting notes.docx").carrier is None


# ---------------------------------------------------------------------------
# Tier B — parser header sniff (seitrans / dachser have no filename signature)


def test_classify_seitrans_by_sniff(tmp_path):
    # A date-prefixed name matches no tier-A pattern, so it falls to the probe.
    f = make_seitrans_invoice(tmp_path / "2026_01_FA2997.xlsx")
    assert classify_invoice_file(f).carrier == "seitrans"


def test_classify_dachser_by_sniff(tmp_path):
    f = make_dachser_invoice(tmp_path / "01-2025 IN 112100582.xlsx")
    assert classify_invoice_file(f).carrier == "dachser"


def test_probe_does_not_cross_classify(tmp_path):
    seit = make_seitrans_invoice(tmp_path / "seit.xlsx")
    dach = make_dachser_invoice(tmp_path / "dach.xlsx")
    assert classify_invoice_file(seit).carrier == "seitrans"
    assert classify_invoice_file(dach).carrier == "dachser"


def test_classify_garbage_xlsx_returns_none(tmp_path):
    f = tmp_path / "mystery.xlsx"
    wb = Workbook()
    wb.active.append(["col_a", "col_b", "col_c"])
    wb.save(f)
    assert classify_invoice_file(f).carrier is None


# ---------------------------------------------------------------------------
# place_invoice_file


def test_place_derives_month_from_content(tmp_path):
    f = make_seitrans_invoice(tmp_path / "inbox" / "seit.xlsx")
    dest, parsed = place_invoice_file(f, "seitrans", base_dir=tmp_path)
    # the default synthetic seitrans row is dated 2025-04-30
    assert dest.parent.name == "04 - Abril"
    assert dest.parent.parent.name == "2025"
    assert dest.exists()
    assert not f.exists()  # moved out of the inbox
    assert parsed.carrier == "seitrans"


def test_place_uses_invoice_date_not_file_mtime(tmp_path):
    import os
    import time

    f = make_seitrans_invoice(tmp_path / "inbox" / "seit.xlsx")
    # OS mtime far from the invoice date — placement must ignore it.
    os.utime(f, (time.time(), time.time()))
    dest, _ = place_invoice_file(f, "seitrans", base_dir=tmp_path)
    assert dest.parent.name == "04 - Abril"  # from content, not "now"


def test_place_redrop_is_idempotent(tmp_path):
    f1 = make_seitrans_invoice(tmp_path / "in1" / "seit.xlsx")
    f2 = tmp_path / "in2" / "seit.xlsx"
    f2.parent.mkdir(parents=True)
    shutil.copy(f1, f2)  # byte-identical re-drop
    dest, _ = place_invoice_file(f1, "seitrans", base_dir=tmp_path)
    dest2, _ = place_invoice_file(f2, "seitrans", base_dir=tmp_path)
    assert dest2 == dest
    assert not f2.exists()  # identical re-drop removed from the inbox


def test_place_conflict_raises_and_leaves_file(tmp_path, default_seitrans_row):
    f1 = make_seitrans_invoice(tmp_path / "in1" / "seit.xlsx")
    place_invoice_file(f1, "seitrans", base_dir=tmp_path)
    # a DIFFERENT file with the same name, same month -> name collision
    other = default_seitrans_row()
    other["DOCUMENTO_NUMERO"] = 999999
    f2 = make_seitrans_invoice(tmp_path / "in2" / "seit.xlsx", rows=[other])
    with pytest.raises(IntakeConflict):
        place_invoice_file(f2, "seitrans", base_dir=tmp_path)
    assert f2.exists()  # not moved — the runner quarantines it to _conflicts/


# ---------------------------------------------------------------------------
# quarantine_file


def test_quarantine_handles_name_collision(tmp_path):
    qdir = tmp_path / "_unclassified"
    a = tmp_path / "in1" / "x.txt"
    b = tmp_path / "in2" / "x.txt"
    a.parent.mkdir(parents=True)
    b.parent.mkdir(parents=True)
    a.write_text("a")
    b.write_text("b")
    p1 = quarantine_file(a, qdir)
    p2 = quarantine_file(b, qdir)
    assert p1.name == "x.txt"
    assert p2.name == "x (1).txt"
    assert p1.exists() and p2.exists()
    assert not a.exists() and not b.exists()
