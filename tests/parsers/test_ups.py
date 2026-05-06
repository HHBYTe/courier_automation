"""Unit tests for the UPS (UK) parser."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from courier_automation.parsers.base import ParserError
from courier_automation.parsers.ups import UPS_COLUMNS, UpsParser


def test_ups_columns_constant():
    assert len(UPS_COLUMNS) == 250
    assert len(set(UPS_COLUMNS)) == 250
    assert UPS_COLUMNS[0] == "Version"
    assert "Invoice Number" in UPS_COLUMNS
    assert "Tracking Number" in UPS_COLUMNS


def test_parses_synthetic_invoice(ups_invoice_factory):
    path = ups_invoice_factory()
    result = UpsParser().parse(path)

    assert result.carrier == "ups"
    assert result.invoice_number == "000003961958"
    assert result.invoice_date == date(2025, 1, 22)
    assert result.row_count == 1
    assert tuple(result.rows.columns) == UPS_COLUMNS
    assert len(result.file_hash) == 64


def test_dtypes_after_coercion(ups_invoice_factory, default_ups_row):
    df = UpsParser().parse(ups_invoice_factory()).rows
    assert df["Invoice Date"].dtype.kind == "M"
    assert df["Shipment Date"].dtype.kind == "M"
    assert df["Invoice Amount"].dtype == "float64"
    assert df["Net Amount"].dtype == "float64"
    assert df["Entered Weight"].dtype == "float64"
    assert df["Tracking Number"].dtype.name == "string"


def test_rejects_csv_with_wrong_column_count(tmp_path):
    path = tmp_path / "bad.csv"
    # Only 5 columns, not 250.
    path.write_text("a,b,c,d,e\n1,2,3,4,5\n", encoding="utf-8")
    with pytest.raises(ParserError, match="expected 250 columns"):
        UpsParser().parse(path)


def test_rejects_csv_with_no_invoice_number(ups_invoice_factory, default_ups_row):
    row = default_ups_row(1)
    row["Invoice Number"] = ""  # plausibility no_null fires
    path = ups_invoice_factory(rows=[row])
    from courier_automation.parsers.plausibility import PlausibilityError

    with pytest.raises(PlausibilityError):
        UpsParser().parse(path)


def test_rejects_csv_with_mixed_invoice_numbers(ups_invoice_factory, default_ups_row):
    a = default_ups_row(1)
    b = default_ups_row(2)
    b["Invoice Number"] = "000003961959"  # different from a's
    path = ups_invoice_factory(rows=[a, b])
    with pytest.raises(ParserError, match="multiple Invoice Numbers"):
        UpsParser().parse(path)


def test_real_ups_invoice_parses_cleanly(real_ups_invoice: Path):
    """Parametrized over every .csv in tests/fixtures/ups/raw/."""
    result = UpsParser().parse(real_ups_invoice)
    assert result.row_count > 0
    assert tuple(result.rows.columns) == UPS_COLUMNS
    assert 2018 <= result.invoice_date.year <= 2035
