"""Seur parser. Raw schema (68 cols on `Sheet1`) is identical to the historical
`Datos` sheet, so this parser is a near-passthrough — it validates the schema,
coerces dates and numerics, and returns a ready-to-append DataFrame."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import ClassVar

import pandas as pd

from courier_automation.parsers.base import (
    ParseResult,
    ParserError,
    assert_schema,
    compute_file_hash,
    extract_seur_invoice_number,
    to_clean_string,
)
from courier_automation.parsers.plausibility import assert_plausible

log = logging.getLogger(__name__)


SEUR_COLUMNS: tuple[str, ...] = (
    "Codigo Cliente",
    "Serie Factura",
    "Numero Factura",
    "Fecha Factura",
    "Numero Linea",
    "Fecha Servicio",
    "Salida / Entrada",
    "Origen",
    "Nombre Completo Origen",
    "Destino",
    "Nombre Completo Destino",
    "Servicio",
    "Nombre Completo Servicio",
    "Producto",
    "Nombre Completo Producto",
    "U.A. Exp.",
    "Centro",
    "Numero Expedicion",
    "Fecha Exp.",
    "Informacion Adicional",
    "Remitente",
    "Direccion Remitente",
    "Poblacion Remitente",
    "C. Postal Remitente",
    "Destinatario",
    "Direccion Destinatario",
    "Poblacion Destinatario",
    "C. Postal Destinatario",
    "Referencia",
    "Tipo Línea",
    "Claves Expedicion",
    "Bultos",
    "Peso",
    "Peso Volumetrico",
    "Ancho",
    "Alto",
    "Largo",
    "Volumen",
    "Clave Impuesto",
    "Importe facturado (sin impuestos)",
    "Valor Reembolso",
    "Valor Asegurado",
    "U.A. Consol.",
    "Codigo Cliente Consolidado",
    "Alias Razon Social CCC Consolidado",
    "Poliza flotante porte",
    "Poliza flotante valor declarado",
    "Portes",
    "Reexpedicion Especial",
    "Gestion Reembolso",
    "Seguro",
    "Cargo Combustible",
    "Comprobante de entrega",
    "Servicios Sabados",
    "Sobrecargos No Encintable",
    "Tasa Seguridad Int",
    "Tasa Calidad del Dato",
    "Tasa Islas",
    "Tasa B2C",
    "Tasa Cliente No Integrado",
    "Suplemento Andorra",
    "Zonas Remotas",
    "Gestion Aduanas Salidas",
    "Gestion Aduanas Llegadas",
    "Suplidos",
    "Aforos",
    "Descuentos",
    "Otros",
)

assert len(SEUR_COLUMNS) == 68, f"SEUR_COLUMNS must be 68, got {len(SEUR_COLUMNS)}"

DATE_COLUMNS: tuple[str, ...] = (
    "Fecha Factura",
    "Fecha Servicio",
    "Fecha Exp.",
)

INT_COLUMNS: tuple[str, ...] = (
    "Numero Factura",
    "Numero Linea",
    "Bultos",
)

FLOAT_COLUMNS: tuple[str, ...] = (
    "Peso",
    "Peso Volumetrico",
    "Ancho",
    "Alto",
    "Largo",
    "Volumen",
    # amount columns
    "Importe facturado (sin impuestos)",
    "Valor Reembolso",
    "Valor Asegurado",
    "Poliza flotante porte",
    "Poliza flotante valor declarado",
    "Portes",
    "Reexpedicion Especial",
    "Gestion Reembolso",
    "Seguro",
    "Cargo Combustible",
    "Comprobante de entrega",
    "Servicios Sabados",
    "Sobrecargos No Encintable",
    "Tasa Seguridad Int",
    "Tasa Calidad del Dato",
    "Tasa Islas",
    "Tasa B2C",
    "Tasa Cliente No Integrado",
    "Suplemento Andorra",
    "Zonas Remotas",
    "Gestion Aduanas Salidas",
    "Gestion Aduanas Llegadas",
    "Suplidos",
    "Aforos",
    "Descuentos",
    "Otros",
)

# Plausibility rules. Tuned conservatively so they fail loud only when a real
# format drift occurs — not on legitimate edge cases (zero-amount credits,
# missing optional fields, etc.).
PLAUSIBILITY_NO_NULL: tuple[str, ...] = (
    "Numero Factura",
    "Numero Linea",
    "Fecha Factura",
    "Numero Expedicion",
)
PLAUSIBILITY_MIN_NON_NULL_RATE: dict[str, float] = {
    # Routinely-populated fields. A sudden drop signals NaN coercion from a
    # format change (e.g. comma-decimal → pd.to_numeric returns NaN).
    # `Fecha Exp.` is intentionally NOT here — observed empty in real invoices
    # (shipment not yet picked up at invoice time).
    "Bultos": 0.95,
    "Peso": 0.95,
    "Portes": 0.95,
    "Importe facturado (sin impuestos)": 0.95,
    "Fecha Servicio": 0.95,
}
PLAUSIBILITY_DATE_RANGE: dict[str, tuple[date, date]] = {
    "Fecha Factura": (date(2018, 1, 1), date(2035, 12, 31)),
    "Fecha Servicio": (date(2018, 1, 1), date(2035, 12, 31)),
    "Fecha Exp.": (date(2018, 1, 1), date(2035, 12, 31)),
}


# Columns that must remain text — postcodes and ID-like columns where leading
# zeros and embedded letters matter. NOTE: `Numero Factura` is an integer in
# both raw invoices and the historical Datos sheet (just the trailing 7-digit
# number, e.g. 235697 — NOT the full filename `0289992025D0235697`); leaving
# it numeric is what makes golden comparison work.
STRING_COLUMNS: tuple[str, ...] = (
    "Codigo Cliente",
    "Serie Factura",
    "Origen",
    "Destino",
    "Servicio",
    "Producto",
    "U.A. Exp.",
    "Centro",
    "Numero Expedicion",
    "C. Postal Remitente",
    "C. Postal Destinatario",
    "Codigo Cliente Consolidado",
    "U.A. Consol.",
    "Clave Impuesto",
)


def coerce_seur_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a Seur-shaped DataFrame to the parser's canonical dtypes
    (dates, ints, floats, strings).

    Public so the golden-extraction script can apply the same coercion to
    Datos rows before snapshotting them — that way the parquet snapshot
    matches what the parser produces, and the golden test compares apples
    to apples.
    """
    df = df.copy()
    for col in DATE_COLUMNS:
        df[col] = pd.to_datetime(df[col], errors="coerce")
    for col in INT_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    for col in FLOAT_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    # Every column not in the typed groups above is a code or free-text field.
    # Run them all through the same string cleaner regardless of inferred dtype
    # — Referencia comes back as float in some invoices (all-numeric) and
    # object in others; Centro is float in Datos and int in raw. Cleaning
    # uniformly is what makes the golden comparison sound.
    typed = set(DATE_COLUMNS) | set(INT_COLUMNS) | set(FLOAT_COLUMNS)
    for col in df.columns:
        if col in typed:
            continue
        df[col] = df[col].map(to_clean_string).astype("string")
    return df


class SeurParser:
    carrier: ClassVar[str] = "seur"
    expected_columns: ClassVar[tuple[str, ...]] = SEUR_COLUMNS
    sheet_name: ClassVar[str] = "Sheet1"

    def parse(self, path: Path) -> ParseResult:
        path = Path(path)
        if not path.exists():
            raise ParserError(f"Seur invoice file not found: {path}")

        # dtype=str on STRING_COLUMNS preserves leading zeros and embedded letters
        # in postcodes / IDs when the source cell is text-formatted. (Numeric cells
        # already lost their leading zeros upstream — that loss is a source-side
        # fact, not one we can recover.)
        string_dtypes = dict.fromkeys(STRING_COLUMNS, str)
        try:
            df = pd.read_excel(
                path,
                sheet_name=self.sheet_name,
                engine="openpyxl",
                dtype=string_dtypes,
            )
        except ValueError as e:
            raise ParserError(
                f"could not read sheet {self.sheet_name!r} from {path.name}: {e}"
            ) from e

        assert_schema(df, self.expected_columns)
        df = self._coerce_dtypes(df)
        assert_plausible(
            df,
            no_null=PLAUSIBILITY_NO_NULL,
            min_non_null_rate=PLAUSIBILITY_MIN_NON_NULL_RATE,
            date_range=PLAUSIBILITY_DATE_RANGE,
        )

        invoice_number = extract_seur_invoice_number(path.name)
        invoice_date = self._derive_invoice_date(df)
        file_hash = compute_file_hash(path)

        log.info(
            "seur parser: %s | %d rows | hash=%s",
            invoice_number,
            len(df),
            file_hash[:12],
        )
        return ParseResult(
            carrier=self.carrier,
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            rows=df,
            source_path=path,
            file_hash=file_hash,
        )

    @staticmethod
    def _coerce_dtypes(df: pd.DataFrame) -> pd.DataFrame:
        return coerce_seur_dtypes(df)

    @staticmethod
    def _derive_invoice_date(df: pd.DataFrame) -> date:
        s = df["Fecha Factura"].dropna()
        if s.empty:
            raise ParserError("no Fecha Factura values to derive invoice_date from")
        return s.iloc[0].date()
