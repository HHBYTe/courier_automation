"""Smoke tests for the Wwex (US) parser.

The parser is partially implemented (xlsx-only, raw-passthrough — see
the parser module docstring for what's deferred). These tests just
verify the column tuple and that real .xlsx fixtures parse cleanly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from courier_automation.parsers.wwex import (
    WWEX_COLUMNS,
    WWEX_RAW_COLUMNS,
    WwexParser,
)

_REAL_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "wwex" / "raw"
_REAL = sorted(_REAL_DIR.glob("*.xlsx")) if _REAL_DIR.exists() else []


def test_wwex_columns_constants():
    assert len(WWEX_RAW_COLUMNS) == 42
    assert len(WWEX_COLUMNS) == 44
    assert "TRACKING_NO" in WWEX_RAW_COLUMNS
    assert "Tracking#" in WWEX_COLUMNS
    assert WWEX_COLUMNS[0] == "Source System"


@pytest.mark.parametrize("path", _REAL or [None], ids=lambda p: p.name if p else "none")
def test_real_wwex_xlsx_parses_cleanly(path):
    if path is None:
        pytest.skip(f"no Wwex fixtures at {_REAL_DIR}")
    result = WwexParser().parse(path)
    assert result.row_count > 0
    # Parser produces the historical (44-col) schema, not raw.
    assert tuple(result.rows.columns) == WWEX_COLUMNS
    assert 2018 <= result.invoice_date.year <= 2035
    # Source System is the SpeedShip 2.0 constant.
    assert (result.rows["Source System"] == "SpeedShip 2.0").all()
    # Domestic/International derived from country comparison.
    assert set(result.rows["Domestic/International"].dropna().unique()) <= {"DOM", "INT"}
    # Weight per package = TOTAL_WEIGHT / PACKAGE_COUNT
    sample_count = result.rows["Package Count"].dropna()
    assert (sample_count > 0).all()
