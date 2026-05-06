"""Smoke tests for the Wwex (US) parser.

The parser is partially implemented (xlsx-only, raw-passthrough — see
the parser module docstring for what's deferred). These tests just
verify the column tuple and that real .xlsx fixtures parse cleanly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from courier_automation.parsers.base import ParserError
from courier_automation.parsers.wwex import WWEX_RAW_COLUMNS, WwexParser

_REAL_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "wwex" / "raw"
_REAL = sorted(_REAL_DIR.glob("*.xlsx")) if _REAL_DIR.exists() else []


def test_wwex_columns_constant():
    assert len(WWEX_RAW_COLUMNS) == 42
    assert "TRACKING_NO" in WWEX_RAW_COLUMNS
    assert "SHIPMENT_DATE" in WWEX_RAW_COLUMNS


def test_rejects_xls_today(tmp_path):
    """Until xlrd is added, .xls files should error cleanly."""
    p = tmp_path / "x.xls"
    p.write_bytes(b"fake")
    with pytest.raises(ParserError, match="only supports .xlsx"):
        WwexParser().parse(p)


@pytest.mark.parametrize("path", _REAL or [None], ids=lambda p: p.name if p else "none")
def test_real_wwex_xlsx_parses_cleanly(path):
    if path is None:
        pytest.skip(f"no Wwex fixtures at {_REAL_DIR}")
    result = WwexParser().parse(path)
    assert result.row_count > 0
    assert tuple(result.rows.columns) == WWEX_RAW_COLUMNS
    assert 2018 <= result.invoice_date.year <= 2035
