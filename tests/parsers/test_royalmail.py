"""Smoke tests for the Royal Mail (UK) parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from courier_automation.parsers.royalmail import (
    ROYALMAIL_COLUMNS,
    ROYALMAIL_RAW_COLUMNS,
    RoyalMailParser,
)

_REAL_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "royalmail" / "raw"
_REAL = sorted(_REAL_DIR.glob("*.csv")) if _REAL_DIR.exists() else []


def test_royalmail_columns_constants():
    assert len(ROYALMAIL_RAW_COLUMNS) == 34
    assert len(ROYALMAIL_COLUMNS) == 17
    assert "Document Number" in ROYALMAIL_COLUMNS
    assert "Docket Number" in ROYALMAIL_COLUMNS
    assert "Año" in ROYALMAIL_COLUMNS
    assert "Mes" in ROYALMAIL_COLUMNS


@pytest.mark.parametrize("path", _REAL or [None], ids=lambda p: p.name if p else "none")
def test_real_royalmail_parses_cleanly(path):
    if path is None:
        pytest.skip(f"no Royal Mail fixtures at {_REAL_DIR}")
    result = RoyalMailParser().parse(path)
    assert result.row_count > 0
    assert tuple(result.rows.columns) == ROYALMAIL_COLUMNS
    # Invoice fields are propagated identically onto every docket row.
    assert result.rows["Document Number"].nunique() == 1
    assert result.rows["Account Number"].nunique() == 1
    # Derived Año/Mes match the Posting Date of each docket.
    posting = result.rows["Posting Date"].dropna()
    assert not posting.empty
    first = posting.iloc[0]
    assert int(result.rows.loc[posting.index[0], "Año"]) == first.year
    assert int(result.rows.loc[posting.index[0], "Mes"]) == first.month
    # Invoice number is the Document Number from row 2.
    assert result.invoice_number == str(result.rows["Document Number"].iloc[0])
    assert 2018 <= result.invoice_date.year <= 2035
