"""UPS (UK) → unified.

Native grain: 1 row = 1 charge on 1 package on 1 shipment. A shipment
identified by `Lead Shipment Number` typically spans 4 rows across
`Charge Category Code` ∈ {SHP (shipping), ADJ (adjustment), MIS (misc),
RTN (return)}.

Strategy: aggregate per Lead Shipment Number.
  - SHP rows → base_cost (sum Net Amount)
  - ADJ/MIS/RTN → other_surcharges (sum Net Amount)
  - Fuel surcharges in UPS appear as separate charge rows with
    `Charge Description` matching 'FUEL' — we route those to
    fuel_surcharge instead of other_surcharges.
  - Shipment metadata (service, weight, addresses) is read from the
    first SHP row per Lead Shipment Number.

Currency: from `Transaction Currency Code` per row (UK invoices: GBP).
Weight: convert from `Billed Weight` + `Billed Weight Unit of Measure`
(LB → KG when needed).
"""
from __future__ import annotations

import pandas as pd

from unified.country_codes import to_iso2
from unified.service_classifier import classify

_LB_TO_KG = 0.453592


def normalize(df: pd.DataFrame, source_file: str) -> pd.DataFrame:
    if df.empty:
        return _empty()

    work = df.copy()
    work["__net"] = pd.to_numeric(work["Net Amount"], errors="coerce")
    work["__category"] = work["Charge Category Code"].astype("string").str.strip()
    work["__desc"] = work["Charge Description"].astype("string").str.upper().fillna("")
    work["__is_shp"] = (work["__category"] == "SHP").fillna(False).astype(bool)
    work["__is_fuel"] = (
        work["__desc"].str.contains("FUEL", regex=False).fillna(False).astype(bool)
    )

    # Group key: the real Lead Shipment Number, or a per-row synthetic key
    # for charge lines that have none. The synthetic key keeps those rows
    # as individual output rows (they're rejected downstream for a null
    # shipment_id, but visibly — not silently swallowed by one NaN group).
    lead = work["Lead Shipment Number"]
    work["__group_key"] = lead.where(
        lead.notna(), "__nokey_" + work.index.astype("string")
    )

    base_cost = (
        work.loc[work["__is_shp"] & ~work["__is_fuel"]]
            .groupby("__group_key")["__net"].sum()
            .rename("base_cost")
    )
    fuel = (
        work.loc[work["__is_fuel"]]
            .groupby("__group_key")["__net"].sum()
            .rename("fuel_surcharge")
    )
    other = (
        work.loc[~work["__is_shp"] & ~work["__is_fuel"]]
            .groupby("__group_key")["__net"].sum()
            .rename("other_surcharges")
    )
    total = work.groupby("__group_key")["__net"].sum().rename("total_net")

    # One representative row per group — prefer a SHP row (carries the
    # shipment-level metadata); fall back to the first row of any category
    # so shipments billed only as ADJ/RTN/MIS aren't dropped. They flow
    # through and pick up a reject reason downstream instead of vanishing.
    work["__shp_rank"] = (~work["__is_shp"]).astype("int8")
    meta = (
        work.sort_values(["__group_key", "__shp_rank"], kind="stable")
            .drop_duplicates(subset=["__group_key"], keep="first")
            .set_index("__group_key")
    )

    joined = (
        meta.join(base_cost, how="left")
            .join(fuel, how="left")
            .join(other, how="left")
            .join(total, how="left")
            .reset_index()
    )

    out = pd.DataFrame(index=joined.index)
    out["carrier"] = "ups"
    out["invoice_id"] = joined["Invoice Number"].astype("string")
    out["invoice_date"] = pd.to_datetime(joined["Invoice Date"], errors="coerce")
    out["shipment_id"] = joined["Lead Shipment Number"].astype("string")
    # UPS extracts ship `Shipment Date` empty for this customer; the
    # `Transaction Date` is the per-line "when was this charge incurred"
    # value and is 100% populated. Use it as the posting date.
    posting = pd.to_datetime(joined["Shipment Date"], errors="coerce")
    fallback = pd.to_datetime(joined["Transaction Date"], errors="coerce")
    out["posting_date"] = posting.where(posting.notna(), fallback)
    out["customer_ref"] = joined["Shipment Reference Number 1"].astype("string")
    out["service_raw"] = joined["Bill Option Code"].astype("string")
    out["service_class"] = out["service_raw"].map(
        lambda v: classify("ups", v)
    ).astype("string")
    out["bultos_count"] = pd.to_numeric(
        joined["Package Quantity"], errors="coerce"
    ).astype("Int64")
    out["weight_kg"] = _weight_to_kg(
        joined["Billed Weight"], joined["Billed Weight Unit of Measure"]
    )
    out["origin_country"] = joined["Sender Country"].map(to_iso2).astype("string")
    out["destination_country"] = joined["Receiver Country"].map(to_iso2).astype("string")
    out["base_cost"] = pd.to_numeric(joined["base_cost"], errors="coerce").astype("float64")
    out["fuel_surcharge"] = pd.to_numeric(joined["fuel_surcharge"], errors="coerce").astype("float64")
    out["other_surcharges"] = pd.to_numeric(joined["other_surcharges"], errors="coerce").astype("float64")
    out["total_net"] = pd.to_numeric(joined["total_net"], errors="coerce").astype("float64")
    out["currency"] = joined["Transaction Currency Code"].astype("string").fillna("GBP")
    out["año"] = out["posting_date"].dt.year.astype("Int64")
    out["mes"] = out["posting_date"].dt.month.astype("Int64")
    out["source_file"] = source_file

    out["_reject_reason"] = _reject_reasons(out)
    return out


def _weight_to_kg(weight: pd.Series, unit: pd.Series) -> pd.Series:
    w = pd.to_numeric(weight, errors="coerce")
    u = unit.astype("string").str.upper().str.strip()
    is_lb = u == "LB"
    return (w.where(~is_lb, w * _LB_TO_KG)).astype("float64")


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
    reason = reason.mask(
        reason.isna() & ~out["currency"].isin(["EUR", "GBP", "USD"]),
        "unknown currency",
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
