"""Deterministic plausibility checks that catch *silent* format drift.

`assert_schema` already catches structural drift (renamed/added/missing
columns). This module catches the cases that survive a clean schema check but
look wrong on the values: wholesale NaN coercion (e.g. a courier shipping
European-comma decimals where we expect dot decimals), partial date-parse
failures, and date values outside the plausible range.

See `docs/drift_handling.md` for the broader strategy.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

import pandas as pd


class PlausibilityError(ValueError):
    """Parsed data passed schema validation but looks wrong on the values.

    Likely causes: silent dtype coercion (numbers/dates becoming NaN), a
    bulk-zero column, or dates outside the plausible window. The message lists
    every failing rule so the user can triage with one read.
    """


def assert_plausible(
    df: pd.DataFrame,
    *,
    no_null: Sequence[str] = (),
    min_non_null_rate: Mapping[str, float] | None = None,
    date_range: Mapping[str, tuple[date, date]] | None = None,
) -> None:
    """Run all checks; raise PlausibilityError listing every failure.

    Args:
        df: parsed and dtype-coerced DataFrame.
        no_null: columns where ANY null is a failure (typically primary-key-like
            fields: invoice number, line number, invoice date).
        min_non_null_rate: ``{column: threshold}`` — fail if non-null rate falls
            below threshold (e.g. 0.95 for routinely-populated numeric columns).
            This is the main detector of silent dtype-coercion drift.
        date_range: ``{column: (lo, hi)}`` — fail if any non-null value falls
            outside [lo, hi]. Catches mis-parsed dates (1970-epoch, year 9999).
    """
    min_non_null_rate = min_non_null_rate or {}
    date_range = date_range or {}
    problems: list[str] = []

    for col in no_null:
        if col not in df.columns:
            problems.append(f"{col!r}: missing column for no-null check")
            continue
        n_null = int(df[col].isna().sum())
        if n_null > 0:
            problems.append(f"{col!r}: {n_null} null values (expected 0)")

    for col, threshold in min_non_null_rate.items():
        if col not in df.columns:
            problems.append(f"{col!r}: missing column for non-null-rate check")
            continue
        if len(df) == 0:
            continue
        rate = float(df[col].notna().mean())
        if rate < threshold:
            problems.append(
                f"{col!r}: only {rate:.1%} non-null (threshold {threshold:.0%}) — "
                "possible silent format drift (e.g. number/date coercion to NaN)"
            )

    for col, (lo, hi) in date_range.items():
        if col not in df.columns:
            problems.append(f"{col!r}: missing column for date-range check")
            continue
        series = pd.to_datetime(df[col], errors="coerce").dropna()
        if series.empty:
            continue
        min_d = series.min().date()
        max_d = series.max().date()
        if min_d < lo or max_d > hi:
            problems.append(
                f"{col!r}: dates outside [{lo.isoformat()}..{hi.isoformat()}] "
                f"(observed {min_d.isoformat()}..{max_d.isoformat()})"
            )

    if problems:
        raise PlausibilityError(
            f"plausibility failed ({len(problems)} issue(s)): "
            + " | ".join(problems)
        )
