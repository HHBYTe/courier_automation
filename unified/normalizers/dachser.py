"""Dachser → unified.

Native grain: 1 row = 1 sales doc (`Doc. Vtas`). Validated 1:1 against
the 2025-12 sample (238 rows / 238 unique Doc.Vtas). Identity transform.

Filters:
  - `Sal./Lleg.` = 'S' (Salida = outbound) kept; 'L' (Llegada = inbound)
    rejected — inbound rows aren't customer shipments we ship.

Origin/destination: `País Ori` / `País Dest.` are already ISO2 in the
sample. Pass through. Currency: EUR.
"""
from __future__ import annotations

import pandas as pd

from unified.country_codes import to_iso2
from unified.service_classifier import classify


def normalize(df: pd.DataFrame, source_file: str) -> pd.DataFrame:
    if df.empty:
        return _empty()

    work = df.copy()
    direction = work["Sal./Lleg."].astype("string").str.strip()
    pre_reject = direction.where(direction != "S", other=pd.NA).map(
        lambda v: f"Sal./Lleg. = {v}" if pd.notna(v) else pd.NA
    )

    out = pd.DataFrame(index=work.index)
    out["carrier"] = "dachser"
    out["invoice_id"] = work["Factura"].astype("string")
    out["invoice_date"] = pd.to_datetime(work["Fecha factura"], errors="coerce")
    out["shipment_id"] = work["Doc. Vtas"].astype("string")
    out["posting_date"] = pd.to_datetime(work["Fecha doc."], errors="coerce")
    out["customer_ref"] = work["Pedido"].astype("string")
    out["service_raw"] = work["Denominación"].astype("string")
    out["service_class"] = out["service_raw"].map(
        lambda v: classify("dachser", v)
    ).astype("string")
    out["bultos_count"] = pd.to_numeric(work["Bultos"], errors="coerce").astype("Int64")
    out["weight_kg"] = pd.to_numeric(work["Peso"], errors="coerce").astype("float64")
    out["origin_country"] = work["País Ori"].map(to_iso2).astype("string")
    out["destination_country"] = work["País Dest."].map(to_iso2).astype("string")
    out["base_cost"] = pd.to_numeric(work["Portes"], errors="coerce").astype("float64")
    out["fuel_surcharge"] = pd.Series(float("nan"), index=work.index, dtype="float64")
    other_cols = [
        "Reexp+Des", "Seguro", "Reembolso", "Suplidos", "Servicios", "Otros",
        "Manipulac.", "Administ", "Distrib.", "Almacenaje",
    ]
    other = pd.DataFrame({
        c: pd.to_numeric(work[c], errors="coerce") for c in other_cols if c in work.columns
    })
    out["other_surcharges"] = other.sum(axis=1, skipna=True).astype("float64")
    out["total_net"] = pd.to_numeric(work["Importe neto"], errors="coerce").astype("float64")
    out["currency"] = "EUR"
    out["año"] = out["posting_date"].dt.year.astype("Int64")
    out["mes"] = out["posting_date"].dt.month.astype("Int64")
    out["source_file"] = source_file

    out["_reject_reason"] = _reject_reasons(out, pre_reject)
    return out


def _reject_reasons(out: pd.DataFrame, pre_reject: pd.Series) -> pd.Series:
    reason = pre_reject.astype("string")
    reason = reason.mask(reason.isna() & out["posting_date"].isna(), "posting_date null")
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
