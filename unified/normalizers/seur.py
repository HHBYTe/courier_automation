"""SEUR → unified.

Native grain: 1 row = 1 invoice line. `Tipo Línea` distinguishes:
  - 'PORTES'   : main shipping charge → KEEP (one row per expedición)
  - 'VENTA'    : sales/billing line → REJECT (not a shipment)
  - 'MANUALES' : manual adjustment → REJECT

Within PORTES, each `Numero Expedicion` appears once (validated against
2026-04 sample: 1666 PORTES rows = 1666 expediciones). No GROUP BY
needed for the kept rows.

Origin: ES fixed (SEUR is Spain-domestic-or-Iberian). Destination: from
`Direccion Destinatario` etc.; SEUR uses an internal `Destino` plaza code
not ISO — fall back to ES unless we can derive otherwise from the
postcode.
"""
from __future__ import annotations

import pandas as pd

from unified.service_classifier import classify


def normalize(df: pd.DataFrame, source_file: str) -> pd.DataFrame:
    if df.empty:
        return _empty()

    work = df.copy()
    # Pre-reject non-PORTES line types so downstream null checks don't
    # confuse "not a shipment row" with "missing shipment data".
    line_type = work["Tipo Línea"].astype("string").str.strip()
    pre_reject = line_type.where(
        line_type.isin(["VENTA", "MANUALES"]),
        other=pd.NA,
    ).map(lambda v: f"Tipo Línea = {v}" if pd.notna(v) else pd.NA)

    out = pd.DataFrame(index=work.index)
    out["carrier"] = "seur"
    out["invoice_id"] = work["Numero Factura"].astype("string")
    out["invoice_date"] = pd.to_datetime(work["Fecha Factura"], errors="coerce")
    out["shipment_id"] = work["Numero Expedicion"].astype("string")
    out["posting_date"] = pd.to_datetime(work["Fecha Servicio"], errors="coerce")
    out["customer_ref"] = work["Referencia"].astype("string")
    out["service_raw"] = work["Nombre Completo Servicio"].astype("string")
    out["service_class"] = out["service_raw"].map(
        lambda v: classify("seur", v)
    ).astype("string")
    out["bultos_count"] = pd.to_numeric(work["Bultos"], errors="coerce").astype("Int64")
    out["weight_kg"] = pd.to_numeric(work["Peso"], errors="coerce").astype("float64")
    out["origin_country"] = "ES"
    # SEUR is Iberian-domestic + Andorra; absent a country column, leave null
    # for all destinations (Power BI will show "Spain (assumed)" as needed).
    out["destination_country"] = pd.Series(pd.NA, index=work.index, dtype="string")
    out["base_cost"] = pd.to_numeric(work["Portes"], errors="coerce").astype("float64")
    out["fuel_surcharge"] = pd.to_numeric(
        work["Cargo Combustible"], errors="coerce"
    ).astype("float64")
    other_cols = [
        "Reexpedicion Especial", "Gestion Reembolso", "Seguro",
        "Comprobante de entrega", "Servicios Sabados", "Sobrecargos No Encintable",
        "Tasa Seguridad Int", "Tasa Calidad del Dato", "Tasa Islas",
        "Tasa B2C", "Tasa Cliente No Integrado", "Suplemento Andorra",
        "Zonas Remotas", "Gestion Aduanas Salidas", "Gestion Aduanas Llegadas",
        "Suplidos", "Aforos", "Descuentos", "Otros",
    ]
    other = pd.DataFrame({
        c: pd.to_numeric(work[c], errors="coerce") for c in other_cols if c in work.columns
    })
    out["other_surcharges"] = other.sum(axis=1, skipna=True).astype("float64")
    out["total_net"] = pd.to_numeric(
        work["Importe facturado (sin impuestos)"], errors="coerce"
    ).astype("float64")
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
