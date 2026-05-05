"""Unit tests for the Seur parser."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from courier_automation.parsers.base import (
    ParserError,
    SchemaMismatch,
    compute_file_hash,
    extract_seur_invoice_number,
)
from courier_automation.parsers.seur import SEUR_COLUMNS, SeurParser


def test_seur_columns_constant_is_68_unique():
    assert len(SEUR_COLUMNS) == 68
    assert len(set(SEUR_COLUMNS)) == 68, "duplicate column name in SEUR_COLUMNS"


def test_parses_synthetic_invoice(seur_invoice_factory):
    path = seur_invoice_factory()
    result = SeurParser().parse(path)

    assert result.carrier == "seur"
    assert result.invoice_number == "0289992025D0289264"
    assert result.invoice_date == date(2025, 4, 30)
    assert result.row_count == 1
    assert tuple(result.rows.columns) == SEUR_COLUMNS
    assert result.source_path == path
    assert len(result.file_hash) == 64


def test_dtypes_after_coercion(seur_invoice_factory, default_seur_row):
    path = seur_invoice_factory(rows=[default_seur_row(1), default_seur_row(2)])
    df = SeurParser().parse(path).rows

    assert df["Bultos"].dtype.name == "Int64"
    assert df["Numero Linea"].dtype.name == "Int64"
    assert df["Peso"].dtype == "float64"
    assert df["Importe facturado (sin impuestos)"].dtype == "float64"
    assert df["Fecha Factura"].dtype.kind == "M"  # datetime64
    assert df["C. Postal Remitente"].dtype.name == "string"


def test_postcodes_preserve_leading_zeros(seur_invoice_factory, default_seur_row):
    row = default_seur_row(1)
    row["C. Postal Remitente"] = "08001"
    row["C. Postal Destinatario"] = "01234"
    path = seur_invoice_factory(rows=[row])

    df = SeurParser().parse(path).rows
    assert df["C. Postal Remitente"].iloc[0] == "08001"
    assert df["C. Postal Destinatario"].iloc[0] == "01234"


def test_schema_mismatch_emits_diff_with_renamed_column(seur_invoice_factory):
    bad_columns = list(SEUR_COLUMNS)
    bad_columns[bad_columns.index("Bultos")] = "Numero Bultos"  # renamed
    path = seur_invoice_factory(columns=tuple(bad_columns))

    with pytest.raises(SchemaMismatch) as exc:
        SeurParser().parse(path)
    msg = str(exc.value)
    assert "Bultos" in msg
    assert "Numero Bultos" in msg


def test_schema_mismatch_emits_diff_with_extra_column(seur_invoice_factory):
    bad_columns = list(SEUR_COLUMNS) + ["Columna Extra"]
    path = seur_invoice_factory(columns=tuple(bad_columns))

    with pytest.raises(SchemaMismatch) as exc:
        SeurParser().parse(path)
    assert "Columna Extra" in str(exc.value)


def test_invoice_number_extracted_from_filename():
    # Domestic Spain (D), Andorra (AD), France (FR) — all observed in real data.
    assert (
        extract_seur_invoice_number("0289992025D0289264.xlsx")
        == "0289992025D0289264"
    )
    assert (
        extract_seur_invoice_number("/some/path/0289992025D9999999.xlsx")
        == "0289992025D9999999"
    )
    assert (
        extract_seur_invoice_number("0289992025AD0001394.xlsx")
        == "0289992025AD0001394"
    )
    assert (
        extract_seur_invoice_number("0289992025FR0020268.xlsx")
        == "0289992025FR0020268"
    )


def test_invoice_number_raises_on_unrecognized_filename():
    with pytest.raises(ParserError):
        extract_seur_invoice_number("not-a-seur-invoice.xlsx")


def test_file_hash_is_stable_and_changes_with_content(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"hello world")
    h1 = compute_file_hash(p)
    h2 = compute_file_hash(p)
    assert h1 == h2

    p.write_bytes(b"hello world!")
    h3 = compute_file_hash(p)
    assert h1 != h3


def test_parser_raises_when_file_missing(tmp_path):
    with pytest.raises(ParserError):
        SeurParser().parse(tmp_path / "does-not-exist.xlsx")


def test_parser_raises_when_sheet_missing(tmp_path, default_seur_row):
    from openpyxl import Workbook

    path = tmp_path / "0289992025D0289264.xlsx"
    wb = Workbook()
    wb.active.title = "WrongName"
    wb.active.append(list(SEUR_COLUMNS))
    wb.save(path)

    with pytest.raises(ParserError):
        SeurParser().parse(path)


def test_real_seur_invoice_parses_cleanly(real_seur_invoice: Path):
    """Run the parser against a real invoice that lives in tests/fixtures/seur/raw/.
    Skipped automatically when no fixture is present."""
    result = SeurParser().parse(real_seur_invoice)
    assert result.row_count > 0
    assert tuple(result.rows.columns) == SEUR_COLUMNS
    assert result.invoice_number == real_seur_invoice.stem.upper()
    # invoice_date should be a real date in a recent year (sanity)
    assert 2020 <= result.invoice_date.year <= 2030
