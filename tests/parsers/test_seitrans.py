"""Unit tests for the Seitrans parser."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from courier_automation.parsers.base import ParserError, SchemaMismatch
from courier_automation.parsers.seitrans import (
    SEITRANS_COLUMNS,
    SEITRANS_RAW_COLUMNS,
    SeitransParser,
)


def test_seitrans_columns_constant_is_21_unique():
    assert len(SEITRANS_RAW_COLUMNS) == 21
    assert len(set(SEITRANS_RAW_COLUMNS)) == 21
    assert len(SEITRANS_COLUMNS) == 25
    assert SEITRANS_COLUMNS[:4] == (
        "Tipo expedición",
        "Q Expediciones",
        "Año",
        "Mes",
    )


def test_parses_synthetic_invoice(seitrans_invoice_factory):
    path = seitrans_invoice_factory()
    result = SeitransParser().parse(path)

    assert result.carrier == "seitrans"
    # invoice_number is now derived from data: f"{year}-{DOCUMENTO_NUMERO}".
    # Default conftest row uses DOCUMENTO_NUMERO=289264 + 2025-04-30.
    assert result.invoice_number == "2025-289264"
    assert result.invoice_date == date(2025, 4, 30)
    assert result.row_count == 1
    assert tuple(result.rows.columns) == SEITRANS_COLUMNS
    assert result.source_path == path
    assert len(result.file_hash) == 64


def test_dtypes_after_coercion(seitrans_invoice_factory, default_seitrans_row):
    row = default_seitrans_row(1)
    row["IMBALLI"] = 2
    path = seitrans_invoice_factory(rows=[row])
    df = SeitransParser().parse(path).rows

    assert df["IMBALLI"].dtype.name == "Int64"
    assert df["PESO LORDO"].dtype == "float64"
    assert df["IMPORTO TOTALE VALUTA"].dtype == "float64"
    assert df["DOCUMENTO DATA"].dtype.kind == "M"
    assert df["Q Expediciones"].dtype.name == "Int64"
    assert df["Tipo expedición"].dtype.name == "string"


def test_tipo_expedicion_is_pallet_when_multiple_packages(seitrans_invoice_factory, default_seitrans_row):
    row = default_seitrans_row(1)
    row["IMBALLI"] = 3
    path = seitrans_invoice_factory(rows=[row])
    df = SeitransParser().parse(path).rows

    assert df["Tipo expedición"].iloc[0] == "Pallet"
    assert df["Q Expediciones"].iloc[0] == 1


def test_schema_mismatch_emits_diff_with_renamed_column(seitrans_invoice_factory):
    bad_columns = list(SEITRANS_RAW_COLUMNS)
    bad_columns[bad_columns.index("IMBALLI")] = "IMBALLO"
    path = seitrans_invoice_factory(columns=tuple(bad_columns))

    with pytest.raises(SchemaMismatch) as exc:
        SeitransParser().parse(path)
    msg = str(exc.value)
    assert "IMBALLI" in msg
    assert "IMBALLO" in msg


def test_invoice_number_namespaced_by_year(seitrans_invoice_factory, default_seitrans_row):
    """Two files with the same DOCUMENTO_NUMERO but different years must
    produce different ParseResult.invoice_number values (so the manifest
    doesn't conflate them)."""
    from datetime import datetime

    row_2024 = default_seitrans_row(1)
    row_2024["DOCUMENTO_NUMERO"] = 1394
    row_2024["DOCUMENTO_DATA"] = datetime(2024, 12, 31)
    a = seitrans_invoice_factory(rows=[row_2024], filename="a.xlsx")

    row_2025 = default_seitrans_row(1)
    row_2025["DOCUMENTO_NUMERO"] = 1394
    row_2025["DOCUMENTO_DATA"] = datetime(2025, 1, 31)
    b = seitrans_invoice_factory(rows=[row_2025], filename="b.xlsx")

    assert SeitransParser().parse(a).invoice_number == "2024-1394"
    assert SeitransParser().parse(b).invoice_number == "2025-1394"


def test_parser_raises_when_sheet_missing(tmp_path, default_seitrans_row):
    from openpyxl import Workbook

    path = tmp_path / "weird-filename.xlsx"
    wb = Workbook()
    wb.active.title = "WrongName"
    wb.active.append(list(SEITRANS_RAW_COLUMNS))
    wb.save(path)

    with pytest.raises(ParserError):
        SeitransParser().parse(path)


def test_real_seitrans_invoice_parses_cleanly(real_seitrans_invoice: Path):
    """Parametrized over every .xlsx in tests/fixtures/seitrans/raw/.
    Skipped automatically when no fixture is present."""
    result = SeitransParser().parse(real_seitrans_invoice)
    assert result.row_count > 0
    assert tuple(result.rows.columns) == SEITRANS_COLUMNS
    assert result.invoice_number.startswith(f"{result.invoice_date.year}-")
    assert 2018 <= result.invoice_date.year <= 2035
