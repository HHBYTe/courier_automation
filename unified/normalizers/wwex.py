"""WWEX (US) → unified.

Native grain: 1 row = 1 shipment (`Tracking#`). Identity transform.

Weight is in pounds — convert to kg. Currency: USD fixed.
Cost breakdown: WWEX exposes `Est Transportation Charges` (base),
`Est Other Charges` + `Insurance` (other). No explicit fuel column —
fuel is rolled into one of those buckets by the carrier.
"""
from __future__ import annotations

import pandas as pd

from unified.country_codes import to_iso2
from unified.service_classifier import classify

_LB_TO_KG = 0.453592


def normalize(df: pd.DataFrame, source_file: str) -> pd.DataFrame:
    if df.empty:
        return _empty()

    out = pd.DataFrame(index=df.index)
    out["carrier"] = "wwex"
    out["invoice_id"] = df["Account#"].astype("string")
    out["invoice_date"] = pd.to_datetime(df["Ship Date"], errors="coerce")
    out["shipment_id"] = df["Tracking#"].astype("string")
    out["posting_date"] = pd.to_datetime(df["Ship Date"], errors="coerce")
    out["customer_ref"] = df["Ship Ref1"].astype("string")
    out["service_raw"] = df["Service"].astype("string")
    out["service_class"] = out["service_raw"].map(
        lambda v: classify("wwex", v)
    ).astype("string")
    # Older WWEX exports (pre-2025-09) lack `Package Count`. Every WWEX
    # row is a real shipment with a Tracking# and a positive total, so
    # default to 1 when null — every shipment is by definition >=1 parcel.
    pc = pd.to_numeric(df["Package Count"], errors="coerce")
    out["bultos_count"] = pc.fillna(1).astype("Int64")
    out["weight_kg"] = (
        pd.to_numeric(df["Package Weight"], errors="coerce") * _LB_TO_KG
    ).astype("float64")
    out["origin_country"] = df["Ship From Country"].map(to_iso2).astype("string")
    out["destination_country"] = df["Ship To Country"].map(to_iso2).astype("string")
    out["base_cost"] = pd.to_numeric(
        df["Est Transportation Charges"], errors="coerce"
    ).astype("float64")
    out["fuel_surcharge"] = pd.Series(float("nan"), index=df.index, dtype="float64")
    other = pd.to_numeric(df["Est Other Charges"], errors="coerce").fillna(0) \
        + pd.to_numeric(df["Insurance"], errors="coerce").fillna(0)
    out["other_surcharges"] = other.astype("float64")
    out["total_net"] = (
        out["base_cost"].fillna(0) + out["other_surcharges"].fillna(0)
    ).astype("float64")
    out["currency"] = "USD"
    out["año"] = out["posting_date"].dt.year.astype("Int64")
    out["mes"] = out["posting_date"].dt.month.astype("Int64")
    out["source_file"] = source_file

    out["_reject_reason"] = _reject_reasons(out)
    return out


def _reject_reasons(out: pd.DataFrame) -> pd.Series:
    reason = pd.Series(pd.NA, index=out.index, dtype="string")
    reason = reason.mask(out["posting_date"].isna(), "posting_date null")
    reason = reason.mask(reason.isna() & out["shipment_id"].isna(), "shipment_id null")
    reason = reason.mask(
        reason.isna() & (out["bultos_count"].isna() | (out["bultos_count"] < 1)),
        "bultos_count < 1",
    )
    reason = reason.mask(
        reason.isna() & (out["total_net"].isna() | (out["total_net"] <= 0)),
        "total_net <= 0",
    )
    reason = reason.mask(
        reason.isna() & out["service_class"].isna(), "service not classifiable"
    )
    return reason


def _empty() -> pd.DataFrame:
    cols = [
        "carrier", "invoice_id", "invoice_date", "shipment_id", "posting_date",
        "customer_ref", "service_raw", "service_class", "bultos_count",
        "weight_kg", "origin_country", "destination_country", "base_cost",
        "fuel_surcharge", "other_surcharges", "total_net", "currency",
        "año", "mes", "source_file", "_reject_reason",
    ]
    return pd.DataFrame({c: [] for c in cols})
