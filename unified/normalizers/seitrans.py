"""Seitrans → unified.

Native grain: 1 row = 1 expedición. Identity transform on grain.

Seitrans is Italian pallet freight (LTL). Origin: IT fixed unless the
mittente nazione says otherwise. Destination: from
`DESTINATARIO NAZIONE DESCRIZIONE` (Italian uppercase name).

Cost structure: a single `IMPORTO TOTALE VALUTA` column — no fuel/other
breakdown is available on the invoice, so `base_cost = total_net` and
the surcharge columns are null.
"""
from __future__ import annotations

import pandas as pd

from unified.country_codes import to_iso2
from unified.service_classifier import classify


def normalize(df: pd.DataFrame, source_file: str) -> pd.DataFrame:
    if df.empty:
        return _empty()

    out = pd.DataFrame(index=df.index)
    out["carrier"] = "seitrans"
    out["invoice_id"] = df["DOCUMENTO NUMERO"].astype("string")
    out["invoice_date"] = pd.to_datetime(df["DOCUMENTO_DATA"], errors="coerce")
    out["shipment_id"] = df["SPEDIZIONE NUMERO"].astype("string")
    # No separate service-date column. Use document date as posting date.
    out["posting_date"] = out["invoice_date"]
    out["customer_ref"] = df["RIFERIMENTO COMMITTENTE"].astype("string")
    out["service_raw"] = df["VOCE DESCRIZIONE"].astype("string")
    out["service_class"] = out["service_raw"].map(
        lambda v: classify("seitrans", v)
    ).astype("string")
    out["bultos_count"] = pd.to_numeric(df["IMBALLI"], errors="coerce").astype("Int64")
    out["weight_kg"] = pd.to_numeric(df["PESO LORDO"], errors="coerce").astype("float64")
    out["origin_country"] = df["MITTENTE NAZIONE DESCRIZIONE"].map(to_iso2).astype("string")
    out["destination_country"] = df["DESTINATARIO NAZIONE DESCRIZIONE"].map(to_iso2).astype("string")
    out["base_cost"] = pd.to_numeric(df["IMPORTO TOTALE VALUTA"], errors="coerce").astype("float64")
    out["fuel_surcharge"] = pd.Series(float("nan"), index=df.index, dtype="float64")
    out["other_surcharges"] = pd.Series(float("nan"), index=df.index, dtype="float64")
    out["total_net"] = out["base_cost"]
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
