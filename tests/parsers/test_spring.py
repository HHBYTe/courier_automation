"""Smoke tests for the Spring (FR) parser.

Partial implementation — see the parser module docstring for deferred
work. These tests verify the column tuple and that real fixtures parse.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from courier_automation.parsers.spring import SPRING_RAW_COLUMNS, SpringParser

_REAL_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "spring" / "raw"
_REAL = sorted(_REAL_DIR.glob("*.XLSX")) + sorted(_REAL_DIR.glob("*.xlsx")) if _REAL_DIR.exists() else []


def test_spring_columns_constant():
    assert len(SPRING_RAW_COLUMNS) == 22
    assert "Invoice Number" in SPRING_RAW_COLUMNS
    assert "CONNOTE" in SPRING_RAW_COLUMNS


@pytest.mark.parametrize("path", _REAL or [None], ids=lambda p: p.name if p else "none")
def test_real_spring_parses_cleanly(path):
    if path is None:
        pytest.skip(f"no Spring fixtures at {_REAL_DIR}")
    result = SpringParser().parse(path)
    assert result.row_count > 0
    assert tuple(result.rows.columns) == SPRING_RAW_COLUMNS
    assert result.invoice_number.startswith("E")
    assert 2018 <= result.invoice_date.year <= 2035
