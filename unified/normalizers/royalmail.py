"""Royal Mail → unified.

Native grain: 1 row = 1 docket (a batch posting). Royal Mail bills at
docket granularity, not per-parcel — the `Quantity` column is how many
shipments are in the docket. We deliberately keep that grain (see the
`royalmail-grain-decision` project note): no row explosion, `Quantity`
maps to `bultos_count`, and Power BI absorbs the shape difference vs.
the 1-row-per-shipment carriers.

Cost structure: a single `Net Value` per docket (surcharge sub-rows are
already dropped by the parser, and `Net Value` is surcharge-inclusive),
so `base_cost = total_net` and the fuel/other columns are null.

Destination is not on the Royal Mail invoice. Royal Mail is a UK
domestic-first carrier in this dataset (all observed services are
1st/2nd class or Tracked 24/48), so we default destination_country
to GB rather than leaving it null — this puts RM on the map and in
domestic Direction bucket instead of Unknown. Origin is GB.
Currency is GBP. Penalty/admin lines (admin charge, label incorrectly
applied, unreadable barcode, oversize) classify to None via the shared
service classifier and are rejected — they aren't shipments.
"""
from __future__ import annotations

import pandas as pd

from unified.service_classifier import classify


def normalize(df: pd.DataFrame, source_file: str) -> pd.DataFrame:
    if df.empty:
        return _empty()

    out = pd.DataFrame(index=df.index)
    out["carrier"] = "royalmail"
    out["invoice_id"] = df["Document Number"].astype("string")
    out["invoice_date"] = pd.to_datetime(df["Invoice Date"], errors="coerce")
    out["shipment_id"] = df["Docket Number"].astype("string")
    out["posting_date"] = pd.to_datetime(df["Posting Date"], errors="coerce")
    out["customer_ref"] = df["Senders Ref"].astype("string")
    out["service_raw"] = df["Service"].astype("string")
    out["service_class"] = out["service_raw"].map(
        lambda v: classify("royalmail", v)
    ).astype("string")
    # Docket-grain: Quantity = shipments in the docket → bultos_count.
    out["bultos_count"] = pd.to_numeric(df["Quantity"], errors="coerce").astype("Int64")
    out["weight_kg"] = pd.to_numeric(df["Weight (kg)"], errors="coerce").astype("float64")
    out["origin_country"] = pd.Series("GB", index=df.index, dtype="string")
    out["destination_country"] = pd.Series("GB", index=df.index, dtype="string")
    # Net Value is the surcharge-inclusive docket total — no fuel/other split.
    out["base_cost"] = pd.to_numeric(df["Net Value"], errors="coerce").astype("float64")
    out["fuel_surcharge"] = pd.Series(float("nan"), index=df.index, dtype="float64")
    out["other_surcharges"] = pd.Series(float("nan"), index=df.index, dtype="float64")
    out["total_net"] = out["base_cost"]
    out["currency"] = "GBP"
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
    # Negative Net Value (a credit) trips this — unified.build then reroutes
    # rows with a valid shipment_id + posting_date to the refunds table.
    reason = reason.mask(
        reason.isna() & (out["total_net"].isna() | (out["total_net"] <= 0)),
        "total_net <= 0",
    )
    reason = reason.mask(
        reason.isna() & out["service_class"].isna(),
        "service not classifiable",
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
