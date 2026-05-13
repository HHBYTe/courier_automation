"""Correos Express → unified.

Native grain: 1 row = 1 expedición (97% of rows have BULTOS=1; the other
3% have BULTOS in {2,3,4,5} — same expedición with N bultos). Identity
transform on grain; column mapping only.

Origin: ES fixed (carrier is Spanish-domestic).
Destination: from `País` (uppercase Spanish country name) via the ISO2 map.
Currency: EUR fixed.
"""
from __future__ import annotations

import pandas as pd

from unified.country_codes import to_iso2
from unified.service_classifier import classify


def normalize(df: pd.DataFrame, source_file: str) -> pd.DataFrame:
    if df.empty:
        return _empty()

    out = pd.DataFrame(index=df.index)
    out["carrier"] = "correos"
    # Invoice id isn't directly in the historical schema; use file stem.
    out["invoice_id"] = source_file
    out["invoice_date"] = pd.to_datetime(df["F.ALBARAN"], errors="coerce")
    out["shipment_id"] = df["Nº ENVIO"].astype("string")
    out["posting_date"] = pd.to_datetime(df["F.ADMISION"], errors="coerce")
    out["customer_ref"] = df["REFERENCIA"].astype("string")
    out["service_raw"] = df["PRODUCTO"].astype("string")
    out["service_class"] = out.apply(
        lambda r: classify("correos", r["service_raw"]), axis=1
    ).astype("string")
    out["bultos_count"] = pd.to_numeric(df["BULTOS"], errors="coerce").astype("Int64")
    out["weight_kg"] = pd.to_numeric(df["PESO KILOS"], errors="coerce").astype("float64")
    out["origin_country"] = "ES"
    out["destination_country"] = df["País"].map(to_iso2).astype("string")
    out["base_cost"] = pd.to_numeric(df["PORTE"], errors="coerce").astype("float64")
    out["fuel_surcharge"] = pd.to_numeric(
        df["SUPLEMENTO COMBUSTIBLE"], errors="coerce"
    ).astype("float64")
    # Aggregate the small carrier-specific surcharges into other_surcharges.
    other_cols = [
        "G. REEMBOLSO", "DESEMBOLSO", "REEXPEDICION", "SEGUROS",
        "SEGURO ESPECIAL", "IMP. EXCESO MEDIDAS", "SUPLEMENTO RECOGIDA",
        "ENTREGA SABADO", "SUPLEMTO O/D PORTUGAL",
        "SUPLEMENTO DESTINO INGLATERRA",
    ]
    other = pd.DataFrame({c: pd.to_numeric(df[c], errors="coerce") for c in other_cols})
    out["other_surcharges"] = other.sum(axis=1, skipna=True).astype("float64")
    out["total_net"] = pd.to_numeric(df["IMP. TOTAL"], errors="coerce").astype("float64")
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
