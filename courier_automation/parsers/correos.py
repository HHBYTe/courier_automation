"""Correos Express parser.

Raw invoices have an inconvenient "header band" on rows 0-2:
  - row 0: invoice-metadata labels (`Nº FACTURA`, `F.FACTURA`, `TOTAL (€)`, …)
  - row 1: invoice-metadata values (`F250114307`, `2025-01-31`, …)
  - row 2: actual column headers for the shipment-line table
  - row 3+: data
The parser reads with `header=2` so row 2 becomes the header. Invoice
number and date come from row 1 (read separately).

Historical Datos has 58 columns: 6 derived (`Año, Mes, Tipo Bulto,
Tipo Exp., Q Expediciones, País`) + the 51 raw + a trailing empty
`Column58` that historical Datos preserves and we mirror as None.

Derived-column rules (reverse-engineered from real Datos, n=53,076 rows):
  - `Año` = year(F.ADMISION) as int.
  - `Mes` = month(F.ADMISION) as int (NOT a date — different from Seitrans).
  - `Tipo Bulto` = weight bucket (`001 KG, 003 KG, 005 KG, 010 KG, …`)
    from `PESO KILOS`. The bucket is the smallest band whose upper bound
    >= weight; >200 kg → `MÁS 200 KG`.
  - `Tipo Exp.` = `BULTO` when PESO KILOS ≤ 50, `PALLET` otherwise.
    Validated: BULTO = [0, 50.00], PALLET = [50.15, 570.00].
  - `Q Expediciones` = 1 (constant). 100% of historical rows.
  - `País` = lookup from `C. PAIS` using the small mapping below.
"""

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
    to_clean_string,
)
from courier_automation.parsers.plausibility import assert_plausible

log = logging.getLogger(__name__)

CORREOS_RAW_COLUMNS: tuple[str, ...] = (
    "Nº ENVIO",
    "F.ALBARAN",
    "F.ADMISION",
    "REFERENCIA",
    "Nº ENVIO CLIENTE",
    "BULTOS",
    "PESO KILOS",
    "VOLUMEN",
    "C. LLAMADA",
    "PORTE",
    "G. REEMBOLSO",
    "DESEMBOLSO",
    "REEXPEDICION",
    "SEGUROS",
    "SEGURO ESPECIAL",
    "IMP. EXCESO MEDIDAS",
    "SUPLEMENTO RECOGIDA",
    "ENTREGA SABADO",
    "SUPLEMTO O/D PORTUGAL",
    "SUPLEMENTO DESTINO INGLATERRA",
    "SUPLEMENTO COMBUSTIBLE",
    "IMP. TOTAL",
    "TIPO IMPOSITIVO",
    "T. PORTE",
    "PRODUCTO",
    "C. REMITENTE",
    "N. REMITENTE",
    "DOM. REMITENTE",
    "POB. REMITENTE",
    "C. P. REM.",
    "TEL. REMITENTE",
    "C. C. REMITENTE",
    "C. DESTINATARIO",
    "N. DESTINATARIO",
    "DOM. DESTINATARIO",
    "POB. DESTINATARIO",
    "C. P. DESTINATARIO",
    "TEL. DESTINATARIO",
    "C. C. DESTINATARIO",
    "PLAZA ORIGEN",
    "PLAZA DESTINO",
    "PLAZA FACTURACION",
    "V. ASEGURADO",
    "IMP. REEMBOLSO",
    "IMP. DESEMBOLSO",
    "C. PAIS",
    "OBSERVACIONES",
    "CLIENTE IMPUTACION",
    "BAREMO",
    "F.ENTREGA",
    "HORA ENTREGA",
)
assert len(CORREOS_RAW_COLUMNS) == 51

DERIVED_COLUMNS: tuple[str, ...] = (
    "Año",
    "Mes",
    "Tipo Bulto",
    "Tipo Exp.",
    "Q Expediciones",
    "País",
)

CORREOS_COLUMNS: tuple[str, ...] = (
    DERIVED_COLUMNS + CORREOS_RAW_COLUMNS + ("Column58",)
)
assert len(CORREOS_COLUMNS) == 58

DATE_COLUMNS: tuple[str, ...] = ("F.ALBARAN", "F.ADMISION", "F.ENTREGA")
INT_COLUMNS: tuple[str, ...] = (
    "BULTOS",
    "Año",
    "Mes",
    "Q Expediciones",
)
# Spanish-format 5-digit postcodes. Excel stores them as numbers (losing
# leading zeros); the user zero-pads back to 5 digits when pasting into
# Datos. The parser must do the same to match.
POSTCODE_COLUMNS: tuple[str, ...] = ("C. P. REM.", "C. P. DESTINATARIO")

FLOAT_COLUMNS: tuple[str, ...] = (
    "PESO KILOS",
    "VOLUMEN",
    "PORTE",
    "G. REEMBOLSO",
    "DESEMBOLSO",
    "REEXPEDICION",
    "SEGUROS",
    "SEGURO ESPECIAL",
    "IMP. EXCESO MEDIDAS",
    "SUPLEMENTO RECOGIDA",
    "ENTREGA SABADO",
    "SUPLEMTO O/D PORTUGAL",
    "SUPLEMENTO DESTINO INGLATERRA",
    "SUPLEMENTO COMBUSTIBLE",
    "IMP. TOTAL",
    "TIPO IMPOSITIVO",
    "T. PORTE",
    "V. ASEGURADO",
    "IMP. REEMBOLSO",
    "IMP. DESEMBOLSO",
)

PLAUSIBILITY_NO_NULL: tuple[str, ...] = (
    "Nº ENVIO",
    "F.ADMISION",
    "BULTOS",
    "PESO KILOS",
)
PLAUSIBILITY_MIN_NON_NULL_RATE: dict[str, float] = {
    "F.ALBARAN": 0.95,
    "PORTE": 0.95,
    "IMP. TOTAL": 0.95,
}
PLAUSIBILITY_DATE_RANGE: dict[str, tuple[date, date]] = {
    "F.ADMISION": (date(2018, 1, 1), date(2035, 12, 31)),
}

# Reverse-engineered from the historical Datos sheet (n=53,076 rows).
# Six unique pairs; "BE" and "CY" both map to GERMANY (different German
# subdivisions). C. PAIS for Spain is the dial code "34" as a string.
PAIS_LOOKUP: dict[str, str] = {
    "34": "SPAIN",
    "BA": "FRANCE",
    "BE": "GERMANY",
    "BM": "ITALY",
    "BZ": "PORTUGAL",
    "CY": "GERMANY",
}

# ISO-2 country code derivation, used for the trailing `Column58` of
# historical Datos. Reverse-engineered from real data — same C. PAIS keys
# as PAIS_LOOKUP but ISO codes instead of full names.
ISO_LOOKUP: dict[str, str] = {
    "34": "ES",
    "BA": "FR",
    "BE": "DE",
    "BM": "IT",
    "BZ": "PT",
    "CY": "DE",
}

# Weight buckets (upper-bound, label) ordered ascending. Anything above the
# largest bucket → "MÁS 200 KG".
_WEIGHT_BUCKETS: tuple[tuple[float, str], ...] = (
    (1, "001 KG"),
    (3, "003 KG"),
    (5, "005 KG"),
    (10, "010 KG"),
    (15, "015 KG"),
    (20, "020 KG"),
    (25, "025 KG"),
    (30, "030 KG"),
    (40, "040 KG"),
    (50, "050 KG"),
    (75, "075 KG"),
    (100, "100 KG"),
    (125, "125 KG"),
    (150, "150 KG"),
    (175, "175 KG"),
    (200, "200 KG"),
)
_OVER_200_LABEL = "MÁS 200 KG"


def _tipo_bulto(peso: float) -> str | None:
    if pd.isna(peso):
        return None
    for upper, label in _WEIGHT_BUCKETS:
        if peso <= upper:
            return label
    return _OVER_200_LABEL


def _tipo_exp(peso: float) -> str | None:
    if pd.isna(peso):
        return None
    return "PALLET" if peso > 50 else "BULTO"


def _to_postcode(value: object) -> object:
    """Normalise a postcode value: zero-pad numeric values to 5 digits
    (Spanish format), pass alphanumeric values through unchanged."""
    if value is None:
        return None
    if isinstance(value, float):
        if pd.isna(value):
            return None
        return f"{int(value):05d}" if value.is_integer() else str(value)
    if isinstance(value, int):
        return f"{value:05d}"
    s = str(value).strip()
    if s == "":
        return None
    if s.lstrip("-").isdigit():
        return s.zfill(5)
    return s


def _pais(c_pais: object) -> str | None:
    if c_pais is None or (isinstance(c_pais, float) and pd.isna(c_pais)):
        return None
    key = str(c_pais).strip()
    return PAIS_LOOKUP.get(key)


def _iso_code(c_pais: object) -> str | None:
    if c_pais is None or (isinstance(c_pais, float) and pd.isna(c_pais)):
        return None
    key = str(c_pais).strip()
    return ISO_LOOKUP.get(key)


def _add_derived(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    admision = pd.to_datetime(df["F.ADMISION"], errors="coerce")
    df["Año"] = admision.dt.year.astype("Int64")
    df["Mes"] = admision.dt.month.astype("Int64")
    df["Tipo Bulto"] = df["PESO KILOS"].map(_tipo_bulto).astype("string")
    df["Tipo Exp."] = df["PESO KILOS"].map(_tipo_exp).astype("string")
    df["Q Expediciones"] = pd.Series(1, index=df.index, dtype="Int64")
    df["País"] = df["C. PAIS"].map(_pais).astype("string")
    df["Column58"] = df["C. PAIS"].map(_iso_code).astype("string")
    return df[list(CORREOS_COLUMNS)]


def coerce_correos_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a 58-col Correos-historical-shaped DataFrame. Used by both
    parser and golden-extraction script."""
    df = df.copy()
    for col in DATE_COLUMNS:
        df[col] = pd.to_datetime(df[col], errors="coerce")
    for col in INT_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    for col in FLOAT_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    typed = set(DATE_COLUMNS) | set(INT_COLUMNS) | set(FLOAT_COLUMNS)
    postcode_set = set(POSTCODE_COLUMNS)
    for col in df.columns:
        if col in typed:
            continue
        if col in postcode_set:
            df[col] = df[col].map(_to_postcode).astype("string")
        else:
            df[col] = df[col].map(to_clean_string).astype("string")
    return df


def _read_invoice_metadata(path: Path) -> tuple[str, date]:
    """Read the invoice-metadata band (rows 0-1) to extract the invoice
    number and date. Row 0 has labels (`Nº FACTURA`, `F.FACTURA`); row 1
    has values."""
    band = pd.read_excel(
        path, sheet_name=0, engine="openpyxl", header=None, nrows=2
    )
    if band.shape[0] < 2:
        raise ParserError(f"{path.name}: header band missing rows 0-1")
    labels = [str(v).strip() for v in band.iloc[0].tolist()]
    values = band.iloc[1].tolist()
    label_to_value = dict(zip(labels, values))

    inv = label_to_value.get("Nº FACTURA")
    if inv is None or (isinstance(inv, float) and pd.isna(inv)):
        raise ParserError(f"{path.name}: row 1 has no 'Nº FACTURA' value")
    invoice_number = str(inv).strip()

    raw_date = label_to_value.get("F.FACTURA")
    parsed_date = pd.to_datetime(raw_date, errors="coerce")
    if pd.isna(parsed_date):
        raise ParserError(
            f"{path.name}: row 1 'F.FACTURA' is not parseable: {raw_date!r}"
        )
    return invoice_number, parsed_date.date()


class CorreosParser:
    carrier: ClassVar[str] = "correos"
    expected_columns: ClassVar[tuple[str, ...]] = CORREOS_COLUMNS
    sheet_name: ClassVar[str] = "Sheet1"

    def parse(self, path: Path) -> ParseResult:
        path = Path(path)
        if not path.exists():
            raise ParserError(f"Correos invoice file not found: {path}")

        invoice_number, invoice_date = _read_invoice_metadata(path)

        try:
            df = pd.read_excel(
                path,
                sheet_name=self.sheet_name,
                engine="openpyxl",
                header=2,  # row 2 is the real header
            )
        except ValueError as e:
            raise ParserError(
                f"could not read sheet {self.sheet_name!r} from {path.name}: {e}"
            ) from e

        assert_schema(df, CORREOS_RAW_COLUMNS)
        df = _add_derived(df)
        df = coerce_correos_dtypes(df)
        assert_plausible(
            df,
            no_null=PLAUSIBILITY_NO_NULL,
            min_non_null_rate=PLAUSIBILITY_MIN_NON_NULL_RATE,
            date_range=PLAUSIBILITY_DATE_RANGE,
        )

        file_hash = compute_file_hash(path)
        log.info(
            "correos parser: %s | %d rows | hash=%s | source=%s",
            invoice_number,
            len(df),
            file_hash[:12],
            path.name,
        )
        return ParseResult(
            carrier=self.carrier,
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            rows=df,
            source_path=path,
            file_hash=file_hash,
        )
