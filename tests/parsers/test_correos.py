"""Unit tests for the Correos Express parser."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from courier_automation.parsers.base import ParserError, SchemaMismatch
from courier_automation.parsers.correos import (
    CORREOS_COLUMNS,
    CORREOS_RAW_COLUMNS,
    CorreosParser,
)


def test_correos_columns_constants():
    assert len(CORREOS_RAW_COLUMNS) == 51
    assert len(CORREOS_COLUMNS) == 58
    assert len(set(CORREOS_RAW_COLUMNS)) == 51
    # Derived columns at the front
    assert CORREOS_COLUMNS[:6] == (
        "Año",
        "Mes",
        "Tipo Bulto",
        "Tipo Exp.",
        "Q Expediciones",
        "País",
    )
    assert CORREOS_COLUMNS[-1] == "Column58"


def test_parses_synthetic_invoice(correos_invoice_factory):
    path = correos_invoice_factory()
    result = CorreosParser().parse(path)

    assert result.carrier == "correos"
    assert result.invoice_number == "F250114307"
    assert result.invoice_date == date(2025, 1, 31)
    assert result.row_count == 1
    assert tuple(result.rows.columns) == CORREOS_COLUMNS
    assert len(result.file_hash) == 64


def test_dtypes_after_coercion(correos_invoice_factory, default_correos_row):
    df = CorreosParser().parse(correos_invoice_factory()).rows

    assert df["BULTOS"].dtype.name == "Int64"
    assert df["Año"].dtype.name == "Int64"
    assert df["Mes"].dtype.name == "Int64"
    assert df["Q Expediciones"].dtype.name == "Int64"
    assert df["PESO KILOS"].dtype == "float64"
    assert df["IMP. TOTAL"].dtype == "float64"
    assert df["F.ADMISION"].dtype.kind == "M"
    assert df["F.ALBARAN"].dtype.kind == "M"
    assert df["Tipo Bulto"].dtype.name == "string"
    assert df["Tipo Exp."].dtype.name == "string"
    assert df["País"].dtype.name == "string"


def test_tipo_bulto_buckets(correos_invoice_factory, default_correos_row):
    cases = [
        (0.5, "001 KG"),
        (1.0, "001 KG"),
        (1.01, "003 KG"),
        (3.0, "003 KG"),
        (4.5, "005 KG"),
        (8.0, "010 KG"),
        (12.0, "015 KG"),
        (50.0, "050 KG"),
        (60.0, "075 KG"),
        (250.0, "MÁS 200 KG"),
    ]
    for peso, expected in cases:
        row = default_correos_row(1)
        row["PESO KILOS"] = peso
        path = correos_invoice_factory(rows=[row], filename=f"f{peso}.xlsx")
        df = CorreosParser().parse(path).rows
        assert df["Tipo Bulto"].iloc[0] == expected, (
            f"PESO {peso} → got {df['Tipo Bulto'].iloc[0]!r}, expected {expected!r}"
        )


def test_tipo_exp_threshold_is_50_kg(correos_invoice_factory, default_correos_row):
    cases = [(1.0, "BULTO"), (50.0, "BULTO"), (50.15, "PALLET"), (300.0, "PALLET")]
    for peso, expected in cases:
        row = default_correos_row(1)
        row["PESO KILOS"] = peso
        path = correos_invoice_factory(rows=[row], filename=f"f{peso}.xlsx")
        assert CorreosParser().parse(path).rows["Tipo Exp."].iloc[0] == expected


def test_pais_lookup(correos_invoice_factory, default_correos_row):
    cases = [("34", "SPAIN"), ("BZ", "PORTUGAL"), ("BA", "FRANCE"), ("XX", None)]
    for code, expected in cases:
        row = default_correos_row(1)
        row["C. PAIS"] = code
        path = correos_invoice_factory(rows=[row], filename=f"p{code}.xlsx")
        actual = CorreosParser().parse(path).rows["País"].iloc[0]
        if expected is None:
            assert actual is None or str(actual) == "<NA>" or actual != actual  # NaN
        else:
            assert actual == expected


def test_year_and_month_from_admision(correos_invoice_factory, default_correos_row):
    row = default_correos_row(1)
    row["F.ADMISION"] = datetime(2024, 7, 15)
    path = correos_invoice_factory(rows=[row])
    df = CorreosParser().parse(path).rows
    assert df["Año"].iloc[0] == 2024
    assert df["Mes"].iloc[0] == 7


def test_q_expediciones_always_one(correos_invoice_factory, default_correos_row):
    rows = [default_correos_row(i) for i in range(1, 4)]
    df = CorreosParser().parse(correos_invoice_factory(rows=rows)).rows
    assert df["Q Expediciones"].tolist() == [1, 1, 1]


def test_schema_mismatch_emits_diff_with_renamed_column(correos_invoice_factory):
    bad_columns = list(CORREOS_RAW_COLUMNS)
    bad_columns[bad_columns.index("BULTOS")] = "PIEZAS"
    path = correos_invoice_factory(columns=tuple(bad_columns))

    with pytest.raises(SchemaMismatch) as exc:
        CorreosParser().parse(path)
    msg = str(exc.value)
    assert "BULTOS" in msg
    assert "PIEZAS" in msg


def test_parser_raises_when_invoice_number_missing(tmp_path, default_correos_row):
    """If the header band is malformed, parser must reject loud."""
    from openpyxl import Workbook

    path = tmp_path / "broken.xlsx"
    wb = Workbook()
    wb.active.title = "Sheet1"
    wb.active.append(["Other", "Labels"])  # no Nº FACTURA in row 0
    wb.active.append(["foo", "bar"])
    wb.active.append(list(CORREOS_RAW_COLUMNS))
    wb.save(path)

    with pytest.raises(ParserError):
        CorreosParser().parse(path)


def test_real_correos_invoice_parses_cleanly(real_correos_invoice: Path):
    """Parametrized over every .xlsx in tests/fixtures/correos/raw/."""
    result = CorreosParser().parse(real_correos_invoice)
    assert result.row_count > 0
    assert tuple(result.rows.columns) == CORREOS_COLUMNS
    assert result.invoice_number.startswith("F")
    assert 2018 <= result.invoice_date.year <= 2035
