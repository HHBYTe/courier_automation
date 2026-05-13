"""Spring (FR) → unified.

Native grain: multiple rows per `CONNOTE` — one base shipping row plus
1–3 surcharge rows. The base row has `Option Code IS NULL`; surcharge
rows carry codes like 'FUS' (Suplemento Energético / fuel),
'T FUS', 'UNDEL'.

Strategy: aggregate per CONNOTE, keeping the base-row metadata and
summing the surcharges into fuel/other buckets.
  - Option Code IS NULL → base shipping (1 row per CONNOTE)
  - Option Code = 'FUS' → fuel_surcharge
  - Option Code ∈ {'T FUS', 'UNDEL', ...} → other_surcharges

Origin: FR fixed. Destination: `Country` (already ISO2). Currency: EUR.
"""
from __future__ import annotations

import pandas as pd

from unified.country_codes import to_iso2
from unified.service_classifier import classify


def normalize(df: pd.DataFrame, source_file: str) -> pd.DataFrame:
    if df.empty:
        return _empty()

    work = df.copy()
    work["__amount"] = pd.to_numeric(work["Amount"], errors="coerce")

    base_mask = work["Option Code"].isna()
    fuel_mask = work["Option Code"].astype("string").str.strip() == "FUS"
    other_mask = work["Option Code"].notna() & ~fuel_mask

    # Aggregate fuel and other surcharges per CONNOTE.
    fuel_by_cn = (
        work.loc[fuel_mask].groupby("CONNOTE")["__amount"].sum().rename("fuel_surcharge")
    )
    other_by_cn = (
        work.loc[other_mask].groupby("CONNOTE")["__amount"].sum().rename("other_surcharges")
    )
    total_by_cn = work.groupby("CONNOTE")["__amount"].sum().rename("total_net")

    base = work.loc[base_mask].copy()
    base = base.drop_duplicates(subset=["CONNOTE"], keep="first")
    base = (
        base.merge(fuel_by_cn, on="CONNOTE", how="left")
            .merge(other_by_cn, on="CONNOTE", how="left")
            .merge(total_by_cn, on="CONNOTE", how="left")
    )

    out = pd.DataFrame(index=base.index)
    out["carrier"] = "spring"
    out["invoice_id"] = base["Invoice Number"].astype("string")
    out["invoice_date"] = pd.to_datetime(base["Invoice Date"], errors="coerce")
    out["shipment_id"] = base["CONNOTE"].astype("string")
    out["posting_date"] = pd.to_datetime(base["Shipment Date"], errors="coerce")
    out["customer_ref"] = base["Customer Ref"].astype("string")
    out["service_raw"] = base["Product"].astype("string")
    out["service_class"] = out["service_raw"].map(
        lambda v: classify("spring", v)
    ).astype("string")
    out["bultos_count"] = pd.to_numeric(base["Items"], errors="coerce").astype("Int64")
    out["weight_kg"] = pd.to_numeric(base["Actual Kilos"], errors="coerce").astype("float64")
    out["origin_country"] = "FR"
    out["destination_country"] = base["Country"].map(to_iso2).astype("string")
    out["base_cost"] = pd.to_numeric(base["__amount"], errors="coerce").astype("float64")
    out["fuel_surcharge"] = pd.to_numeric(base["fuel_surcharge"], errors="coerce").astype("float64")
    out["other_surcharges"] = pd.to_numeric(base["other_surcharges"], errors="coerce").astype("float64")
    out["total_net"] = pd.to_numeric(base["total_net"], errors="coerce").astype("float64")
    out["currency"] = "EUR"
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
