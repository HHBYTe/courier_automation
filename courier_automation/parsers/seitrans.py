"""Seitrans parser.

Raw invoices are clean Excel files with a single `Risultato` sheet and 21
columns. The historical workbook adds 4 derived columns at the front
(`Tipo expedición`, `Q Expediciones`, `Año`, `Mes`) and renames underscores to
spaces in the raw field names.
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

SEITRANS_RAW_COLUMNS: tuple[str, ...] = (
    "CLIENTE_RAGIONE_SOCIALE",
    "DOCUMENTO_NUMERO",
    "SPEDIZIONE_NUMERO",
    "MITTENTE_RAGIONE_SOCIALE",
    "MITTENTE_NAZIONE_DESCRIZIONE",
    "MITTENTE_CAP",
    "DESTINATARIO_RAGIONE_SOCIALE",
    "DESTINATARIO_LOCALITA",
    "DESTINATARIO_CAP",
    "DESTINATARIO_NAZIONE_DESCRIZIONE",
    "IMBALLI",
    "PESO_LORDO",
    "VOLUME",
    "PESO_TASSABILE",
    "METRI_LINEARI",
    "VOCE_DESCRIZIONE",
    "IMPORTO_TOTALE_VALUTA",
    "RIFERIMENTO_COMMITTENTE",
    "RESA_DESCRIZIONE",
    "SETTORE_DESCRIZIONE",
    "DOCUMENTO_DATA",
)

DERIVED_COLUMNS: tuple[str, ...] = (
    "Tipo expedición",
    "Q Expediciones",
    "Año",
    "Mes",
)

SEITRANS_COLUMNS: tuple[str, ...] = DERIVED_COLUMNS + tuple(
    col.replace("_", " ") for col in SEITRANS_RAW_COLUMNS
)

assert len(SEITRANS_RAW_COLUMNS) == 21, (
    f"SEITRANS_RAW_COLUMNS must be 21, got {len(SEITRANS_RAW_COLUMNS)}"
)
assert len(SEITRANS_COLUMNS) == 25, (
    f"SEITRANS_COLUMNS must be 25, got {len(SEITRANS_COLUMNS)}"
)

DATE_COLUMNS: tuple[str, ...] = ("DOCUMENTO_DATA",)
INT_COLUMNS: tuple[str, ...] = (
    "DOCUMENTO_NUMERO",
    "IMBALLI",
)
FLOAT_COLUMNS: tuple[str, ...] = (
    "PESO_LORDO",
    "VOLUME",
    "PESO_TASSABILE",
    "METRI_LINEARI",
    "IMPORTO_TOTALE_VALUTA",
)

STRING_COLUMNS: tuple[str, ...] = tuple(
    c
    for c in SEITRANS_RAW_COLUMNS
    if c not in set(DATE_COLUMNS) | set(INT_COLUMNS) | set(FLOAT_COLUMNS)
)

PLAUSIBILITY_NO_NULL: tuple[str, ...] = (
    "DOCUMENTO_NUMERO",
    "SPEDIZIONE_NUMERO",
    "DOCUMENTO_DATA",
)
PLAUSIBILITY_MIN_NON_NULL_RATE: dict[str, float] = {
    "IMBALLI": 0.95,
    "PESO_LORDO": 0.95,
    "IMPORTO_TOTALE_VALUTA": 0.95,
    "VOLUME": 0.90,
}
PLAUSIBILITY_DATE_RANGE: dict[str, tuple[date, date]] = {
    "DOCUMENTO_DATA": (date(2018, 1, 1), date(2035, 12, 31)),
}

def _infer_tipo_expedicion(df: pd.DataFrame) -> pd.Series:
    result = pd.Series("Bulto", index=df.index, dtype="string")
    if "IMBALLI" in df.columns:
        baskets = df["IMBALLI"].fillna(0)
        result[baskets > 1] = "Pallet"
    return result


def coerce_seitrans_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in DATE_COLUMNS:
        df[col] = pd.to_datetime(df[col], errors="coerce")
    for col in INT_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    for col in FLOAT_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")

    typed = set(DATE_COLUMNS) | set(INT_COLUMNS) | set(FLOAT_COLUMNS)
    for col in df.columns:
        if col in typed:
            continue
        df[col] = df[col].map(to_clean_string).astype("string")
    return df


class SeitransParser:
    carrier: ClassVar[str] = "seitrans"
    expected_columns: ClassVar[tuple[str, ...]] = SEITRANS_COLUMNS
    sheet_name: ClassVar[str] = "Risultato"

    def parse(self, path: Path) -> ParseResult:
        path = Path(path)
        if not path.exists():
            raise ParserError(f"Seitrans invoice file not found: {path}")

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

        assert_schema(df, SEITRANS_RAW_COLUMNS)
        df = self._coerce_dtypes(df)
        assert_plausible(
            df,
            no_null=PLAUSIBILITY_NO_NULL,
            min_non_null_rate=PLAUSIBILITY_MIN_NON_NULL_RATE,
            date_range=PLAUSIBILITY_DATE_RANGE,
        )
        df = self._normalize_columns(df)

        # Seitrans filenames are inconsistent (`2025_01_31 3065.xlsx`,
        # `2024_12_31 Factura 48172.xlsx`, `2025_06_30_24633.xlsx`). The
        # invoice number is reliably stored in the file's `DOCUMENTO NUMERO`
        # column instead. Namespace by invoice year so trailing numbers
        # don't collide across years.
        invoice_date = self._derive_invoice_date(df)
        documento_numero = int(df["DOCUMENTO NUMERO"].iloc[0])
        invoice_number = f"{invoice_date.year}-{documento_numero}"
        file_hash = compute_file_hash(path)

        log.info(
            "seitrans parser: %s | %d rows | hash=%s | source=%s",
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

    @staticmethod
    def _coerce_dtypes(df: pd.DataFrame) -> pd.DataFrame:
        return coerce_seitrans_dtypes(df)

    @staticmethod
    def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
        renamed = {col: col.replace("_", " ") for col in SEITRANS_RAW_COLUMNS}
        df = df.rename(columns=renamed)
        df["Tipo expedición"] = _infer_tipo_expedicion(df)
        df["Q Expediciones"] = pd.Series(1, index=df.index, dtype="Int64")
        df["Año"] = df["DOCUMENTO DATA"].dt.year.astype("Int64")
        df["Mes"] = df["DOCUMENTO DATA"].dt.month.astype("Int64")
        return df[list(SEITRANS_COLUMNS)]

    @staticmethod
    def _derive_invoice_date(df: pd.DataFrame) -> date:
        s = df["DOCUMENTO DATA"].dropna()
        if s.empty:
            raise ParserError("no DOCUMENTO DATA values to derive invoice_date from")
        return s.iloc[0].date()
