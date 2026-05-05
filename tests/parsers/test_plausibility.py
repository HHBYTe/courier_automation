"""Tests for the plausibility detector and its integration into SeurParser."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from courier_automation.parsers.plausibility import (
    PlausibilityError,
    assert_plausible,
)
from courier_automation.parsers.seur import SeurParser


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# --- assert_plausible: unit tests --------------------------------------------


def test_passes_with_default_data():
    df = _df([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
    assert_plausible(df, no_null=("a", "b"))


def test_passes_with_empty_dataframe():
    """No rows = vacuously OK; otherwise we'd block legitimate empty months."""
    df = pd.DataFrame(columns=["a", "b"])
    assert_plausible(df, no_null=("a",), min_non_null_rate={"a": 0.95})


def test_no_null_violation_is_caught():
    df = _df([{"a": 1}, {"a": None}, {"a": 3}])
    with pytest.raises(PlausibilityError, match="'a': 1 null values"):
        assert_plausible(df, no_null=("a",))


def test_missing_column_for_no_null_check_is_caught():
    df = _df([{"a": 1}])
    with pytest.raises(PlausibilityError, match="missing column for no-null check"):
        assert_plausible(df, no_null=("does_not_exist",))


def test_min_non_null_rate_violation_is_caught():
    # 6 of 10 are null → 40% non-null, below 0.95 threshold
    df = _df([{"a": v} for v in [1, 2, 3, 4, None, None, None, None, None, None]])
    with pytest.raises(PlausibilityError, match="silent format drift"):
        assert_plausible(df, min_non_null_rate={"a": 0.95})


def test_min_non_null_rate_passes_at_threshold():
    df = _df([{"a": v} for v in [1, 2, 3, 4, None]])  # 80% non-null
    assert_plausible(df, min_non_null_rate={"a": 0.80})


def test_date_range_violation_is_caught():
    df = _df([{"d": "2025-04-15"}, {"d": "1999-01-01"}])
    with pytest.raises(PlausibilityError, match="dates outside"):
        assert_plausible(
            df, date_range={"d": (date(2020, 1, 1), date(2030, 12, 31))}
        )


def test_date_range_passes_when_all_in_range():
    df = _df([{"d": "2025-04-15"}, {"d": "2024-12-31"}])
    assert_plausible(
        df, date_range={"d": (date(2020, 1, 1), date(2030, 12, 31))}
    )


def test_date_range_ignores_unparseable_values():
    """Date-range should only police values that survive parsing — partial date
    parse failures are caught by min_non_null_rate, not by this check."""
    df = _df([{"d": "2025-04-15"}, {"d": "garbage"}])
    assert_plausible(
        df, date_range={"d": (date(2020, 1, 1), date(2030, 12, 31))}
    )


def test_aggregates_multiple_failures_in_one_message():
    df = _df([{"a": None, "b": None, "b_filler": None}])
    with pytest.raises(PlausibilityError) as exc:
        assert_plausible(
            df,
            no_null=("a",),
            min_non_null_rate={"b": 0.95},
        )
    msg = str(exc.value)
    assert "'a'" in msg
    assert "'b'" in msg
    assert "2 issue(s)" in msg


# --- Seur parser integration -------------------------------------------------


def test_seur_parser_passes_plausibility_on_default_row(seur_invoice_factory):
    SeurParser().parse(seur_invoice_factory())  # default row is plausible


def test_seur_parser_fails_when_critical_column_is_null(
    seur_invoice_factory, default_seur_row
):
    """Numero Factura is in PLAUSIBILITY_NO_NULL — must reject."""
    row = default_seur_row(1)
    row["Numero Factura"] = None
    invoice = seur_invoice_factory(rows=[row])
    with pytest.raises(PlausibilityError, match="Numero Factura"):
        SeurParser().parse(invoice)


def test_seur_parser_fails_when_peso_is_mostly_null(
    seur_invoice_factory, default_seur_row
):
    """Simulate a number-format drift where pd.to_numeric coerces most Pesos
    to NaN — the min_non_null_rate rule must catch it."""
    rows = []
    for n in range(20):
        r = default_seur_row(n + 1)
        if n >= 5:  # 15 of 20 (75%) become NaN
            r["Peso"] = "1,5"  # comma-decimal that to_numeric can't parse
        rows.append(r)
    invoice = seur_invoice_factory(rows=rows)
    with pytest.raises(PlausibilityError, match="Peso"):
        SeurParser().parse(invoice)


def test_seur_parser_fails_when_invoice_date_is_implausible(
    seur_invoice_factory, default_seur_row
):
    from datetime import datetime

    row = default_seur_row(1)
    row["Fecha Factura"] = datetime(1999, 1, 1)
    invoice = seur_invoice_factory(rows=[row])
    with pytest.raises(PlausibilityError, match="Fecha Factura"):
        SeurParser().parse(invoice)
