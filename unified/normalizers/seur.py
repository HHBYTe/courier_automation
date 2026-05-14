"""SEUR → unified.

Native grain: 1 row = 1 invoice line. `Tipo Línea` distinguishes:
  - 'PORTES'   : main shipping charge → KEEP (one row per expedición)
  - 'VENTA'    : sales/billing line → REJECT (not a shipment)
  - 'MANUALES' : manual adjustment → REJECT

Within PORTES, each `Numero Expedicion` appears once (validated against
2026-04 sample: 1666 PORTES rows = 1666 expediciones). No GROUP BY
needed for the kept rows.

Origin: ES fixed (SEUR is Spain-domestic-or-Iberian). Destination: the
`Destino` column is an internal SEUR plaza code, not ISO. SEUR's own
reference sheet `data/seur/other/Destino.csv` maps every plaza code to
a country — we read it and translate to ISO alpha-2. Codes absent from
that sheet but already valid ISO (international plazas) pass through;
anything still unknown stays null.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from unified.service_classifier import classify

# SEUR's reference sheet lives alongside the invoice parquets. The
# normalizer only runs when data/seur/ exists, so this path is safe.
_DESTINO_CSV = (
    Path(__file__).resolve().parents[2] / "data" / "seur" / "other" / "Destino.csv"
)

# Destino.csv "País" column (Spanish country names) -> ISO 3166-1 alpha-2.
_PAIS_TO_ISO = {
    "España": "ES", "Portugal": "PT", "Andorra": "AD",
    "Italia": "IT", "Francia": "FR", "Alemania": "DE",
    "Reino Unido": "GB", "Holanda": "NL", "Dinamarca": "DK",
    "Bélgica": "BE", "Finlandia": "FI", "República Checa": "CZ",
    "Grecia": "GR", "Polonia": "PL", "Suiza": "CH", "Austria": "AT",
    "Bulgaria": "BG", "Hungría": "HU", "Suecia": "SE", "Lituania": "LT",
    "Eslovenia": "SI", "Croacia": "HR", "Luxemburgo": "LU", "Letonia": "LV",
    "Isla Reunión": "RE", "Estonia": "EE", "Irlanda": "IE",
    "Rumanía": "RO", "Turquía": "TR",
}

# Codes seen in invoice data but absent from Destino.csv. The 2-letter
# ones are already valid ISO alpha-2 (Albania, China, Monaco, Malta,
# Norway, Russia, Singapore, Slovakia — international plazas); FUE and
# LAN are Canary Islands. SCP and the numeric "28" are unidentified and
# deliberately omitted — they fall through to null (~15 rows total).
_EXTRA_DEST_TO_ISO = {
    "AL": "AL", "CN": "CN", "MC": "MC", "MT": "MT",
    "NO": "NO", "RU": "RU", "SG": "SG", "SK": "SK",
    "FUE": "ES", "LAN": "ES",
}


def _load_destino_map() -> dict[str, str]:
    """Plaza code -> ISO alpha-2, built from SEUR's own reference sheet."""
    if not _DESTINO_CSV.exists():
        raise FileNotFoundError(
            f"SEUR destination reference sheet missing: {_DESTINO_CSV}. "
            f"It ships with the SEUR invoice export — restore it before building."
        )
    ref = pd.read_csv(_DESTINO_CSV)
    mapping: dict[str, str] = {}
    for code, pais in zip(ref["Código Destino"], ref["País"]):
        iso = _PAIS_TO_ISO.get(str(pais).strip())
        if iso is None:
            raise ValueError(
                f"Destino.csv País {pais!r} (code {code!r}) has no ISO "
                f"mapping — add it to _PAIS_TO_ISO in seur.py"
            )
        mapping[str(code).strip()] = iso
    return {**mapping, **_EXTRA_DEST_TO_ISO}


_DEST_TO_ISO = _load_destino_map()


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
    # Translate SEUR's internal `Destino` plaza code to ISO alpha-2 via
    # the reference sheet. Unknown codes (~15 rows) map to null.
    out["destination_country"] = (
        work["Destino"].astype("string").str.strip().map(_DEST_TO_ISO).astype("string")
    )
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
