"""Frozen FX rates for EUR normalization.

Per the 2026-05 decision, all non-EUR carrier amounts are converted to
EUR using a SINGLE frozen rate — not historical monthly rates. Tradeoff
accepted: historical rows (2018-2023) carry an FX distortion because
they are converted at a recent rate rather than the rate that was real
at the time. Magnitude is roughly 7-15% on the oldest USD/GBP rows and
~0% on recent rows.

>>> REVIEW THESE RATES <<<
The values below are placeholders at approximate early-2026 levels.
Set them to whatever rate the business wants frozen, then re-run
`python -m unified.build`. `FROZEN_RATE_AS_OF` is an audit note only —
update it to the date the chosen rate represents.

To switch to monthly historical rates later:
  - replace RATE_TO_EUR with a (currency, year_month) -> rate table
  - convert per row's posting_date in `unified.build._add_eur_columns`
The rest of the pipeline and Power BI need no changes — they only
consume the `*_eur` columns.
"""
from __future__ import annotations

# Audit note: the date the frozen rate represents. Not used in math.
FROZEN_RATE_AS_OF = "2026-05-01"

# EUR value of 1 unit of each currency. EUR is 1.0 by definition.
#   total_net_eur = total_net * RATE_TO_EUR[currency]
RATE_TO_EUR: dict[str, float] = {
    "EUR": 1.00,
    "GBP": 1.15,
    "USD": 0.85,
}


def rate_to_eur(currency: str) -> float:
    """EUR multiplier for a currency code. Raises on unknown currency."""
    try:
        return RATE_TO_EUR[currency]
    except KeyError:
        raise ValueError(
            f"no frozen FX rate for currency {currency!r} — "
            f"add it to unified/fx_rates.py"
        )
