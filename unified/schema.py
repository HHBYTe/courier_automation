"""Canonical schema for the unified shipments fact table.

Twenty-five columns, strict dtypes. Amounts are kept BOTH in their
native currency (`total_net`, `base_cost`, … + `currency`) AND
converted to EUR (`total_net_eur`, … + `fx_rate_to_eur`). The EUR
columns use a single frozen rate from `unified.fx_rates` so every
carrier is comparable on one scale; the native columns are retained
for audit and for anyone who wants the original figures.

Nullability rules:
- Columns marked `nullable=True` may be null when the source carrier
  genuinely doesn't supply the data (e.g. Royal Mail destination, weight
  on Tracked).
- Columns marked `nullable=False` must be non-null on every kept row.
  Rows missing a non-nullable column are rejected to the rejection table.

Row-level rejection criteria are enforced in `unified.build`, not here.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Column:
    name: str
    dtype: str  # pandas dtype string
    nullable: bool


CARRIERS: tuple[str, ...] = (
    "correos",
    "seitrans",
    "seur",
    "dachser",
    "spring",
    "ups",
    "wwex",
    "royalmail",
)

CURRENCIES: tuple[str, ...] = ("EUR", "GBP", "USD")

SERVICE_CLASSES: tuple[str, ...] = (
    "parcel",   # parcel-grade courier (Spring TRCK, Correos Paq, Seur 1/4, UPS Express, WWEX)
    "pallet",   # pallet freight (Seitrans, Dachser)
    "letter",   # letter-class (RM Lge Letter, Account Mail)
    "freight",  # LTL/FTL / non-parcel
    "other",    # known shipment but unclassified
)


UNIFIED_COLUMNS: tuple[Column, ...] = (
    Column("carrier",             "string",         nullable=False),
    Column("invoice_id",          "string",         nullable=False),
    Column("invoice_date",        "datetime64[ns]", nullable=False),
    Column("shipment_id",         "string",         nullable=False),
    Column("posting_date",        "datetime64[ns]", nullable=False),
    Column("customer_ref",        "string",         nullable=True),
    Column("service_raw",         "string",         nullable=True),
    Column("service_class",       "string",         nullable=False),
    Column("bultos_count",        "Int64",          nullable=False),
    Column("weight_kg",           "float64",        nullable=True),
    Column("origin_country",      "string",         nullable=True),
    Column("destination_country", "string",         nullable=True),
    Column("base_cost",           "float64",        nullable=True),
    Column("fuel_surcharge",      "float64",        nullable=True),
    Column("other_surcharges",    "float64",        nullable=True),
    Column("total_net",           "float64",        nullable=False),
    Column("currency",            "string",         nullable=False),
    Column("fx_rate_to_eur",      "float64",        nullable=False),
    Column("total_net_eur",       "float64",        nullable=False),
    Column("base_cost_eur",       "float64",        nullable=True),
    Column("fuel_surcharge_eur",  "float64",        nullable=True),
    Column("other_surcharges_eur","float64",        nullable=True),
    Column("año",                 "Int64",          nullable=False),
    Column("mes",                 "Int64",          nullable=False),
    Column("source_file",         "string",         nullable=False),
)

UNIFIED_COLUMN_NAMES: tuple[str, ...] = tuple(c.name for c in UNIFIED_COLUMNS)


def empty_frame() -> pd.DataFrame:
    """Return an empty DataFrame with the canonical schema applied."""
    return _apply_dtypes(pd.DataFrame({c.name: [] for c in UNIFIED_COLUMNS}))


def coerce(df: pd.DataFrame) -> pd.DataFrame:
    """Reorder columns and coerce dtypes to the canonical schema.

    Raises KeyError if the frame is missing any canonical column.
    """
    missing = [c.name for c in UNIFIED_COLUMNS if c.name not in df.columns]
    if missing:
        raise KeyError(f"unified frame missing columns: {missing}")
    out = df[list(UNIFIED_COLUMN_NAMES)].copy()
    return _apply_dtypes(out)


def _apply_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    for col in UNIFIED_COLUMNS:
        if col.dtype.startswith("datetime"):
            df[col.name] = pd.to_datetime(df[col.name], errors="coerce")
        elif col.dtype == "Int64":
            df[col.name] = pd.to_numeric(df[col.name], errors="coerce").astype("Int64")
        elif col.dtype == "float64":
            df[col.name] = pd.to_numeric(df[col.name], errors="coerce").astype("float64")
        else:
            df[col.name] = df[col.name].astype("string")
    return df


def validate(df: pd.DataFrame) -> list[str]:
    """Return a list of validation errors. Empty list = valid."""
    errors: list[str] = []
    for col in UNIFIED_COLUMNS:
        if col.name not in df.columns:
            errors.append(f"missing column: {col.name}")
            continue
        actual = str(df[col.name].dtype)
        if not _dtype_match(actual, col.dtype):
            errors.append(f"{col.name}: expected {col.dtype}, got {actual}")
        if not col.nullable and df[col.name].isna().any():
            n = int(df[col.name].isna().sum())
            errors.append(f"{col.name}: {n} null values (column is non-nullable)")
    bad_carrier = ~df["carrier"].isin(CARRIERS)
    if bad_carrier.any():
        errors.append(f"carrier: {int(bad_carrier.sum())} rows have unknown carrier")
    bad_currency = ~df["currency"].isin(CURRENCIES)
    if bad_currency.any():
        errors.append(f"currency: {int(bad_currency.sum())} rows have unknown currency")
    bad_class = ~df["service_class"].isin(SERVICE_CLASSES)
    if bad_class.any():
        errors.append(
            f"service_class: {int(bad_class.sum())} rows have unknown class"
        )
    bad_bultos = (df["bultos_count"] < 1).any()
    if bad_bultos:
        n = int((df["bultos_count"] < 1).sum())
        errors.append(f"bultos_count: {n} rows with value < 1")
    bad_net = (df["total_net"] <= 0).any()
    if bad_net:
        n = int((df["total_net"] <= 0).sum())
        errors.append(f"total_net: {n} rows with value <= 0")
    return errors


def _dtype_match(actual: str, expected: str) -> bool:
    if expected.startswith("datetime"):
        return actual.startswith("datetime64")
    return actual == expected
