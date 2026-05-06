"""Seitrans parser.

Raw invoices are clean Excel files with a single `Risultato` sheet and 21
columns named in ALL_CAPS_WITH_UNDERSCORES (Italian field names). The
historical workbook (`Análisis envíos Seitrans.xlsx`, sheet `Datos`) has 25
columns: 4 derived columns at the front (`Tipo expedición`, `Q Expediciones`,
`Año`, `Mes`) and the same 21 raw columns with underscores replaced by
spaces.

The parser flow:
  1. Read raw 21-col xlsx.
  2. Validate raw schema.
  3. Rename underscore->space and add the 4 derived columns. From this
     point on the DataFrame is shaped like the historical Datos sheet.
  4. Coerce dtypes on the 25-col historical schema.
  5. Plausibility checks on the 25-col historical schema.
  6. Derive invoice_number from `DOCUMENTO NUMERO` (the filename is
     unreliable — observed variants: `2025_01_31 3065.xlsx`,
     `2024_12_31 Factura 48172.xlsx`, `2025_06_30_24633.xlsx`).

The matching `coerce_seitrans_dtypes` is the same function the
golden-extraction script applies to Datos rows, so the parser output and
the parquet snapshot use identical dtypes — the only way `assert_frame_equal`
can be sound.
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

# In the actual historical Datos sheet, `DOCUMENTO_DATA` keeps its
# underscore while every other raw column got `_` → ` ` renamed (presumably
# the user missed that one when setting up the sheet). The parser matches
# what's in production rather than fighting it.
_KEEP_UNDERSCORE: frozenset[str] = frozenset({"DOCUMENTO_DATA"})


def _historical_name(raw_col: str) -> str:
    return raw_col if raw_col in _KEEP_UNDERSCORE else raw_col.replace("_", " ")


SEITRANS_COLUMNS: tuple[str, ...] = DERIVED_COLUMNS + tuple(
    _historical_name(c) for c in SEITRANS_RAW_COLUMNS
)

assert len(SEITRANS_RAW_COLUMNS) == 21, (
    f"SEITRANS_RAW_COLUMNS must be 21, got {len(SEITRANS_RAW_COLUMNS)}"
)
assert len(SEITRANS_COLUMNS) == 25, (
    f"SEITRANS_COLUMNS must be 25, got {len(SEITRANS_COLUMNS)}"
)

# All typed-column groups use the historical-Datos column names. This is
# the schema after _rename_and_derive runs. Note: `Mes` is a *datetime*
# (first day of the month) in the historical sheet, not an int — that's
# Excel-style "January 2025" with a date underneath the cell formatting.
DATE_COLUMNS: tuple[str, ...] = ("DOCUMENTO_DATA", "Mes")
INT_COLUMNS: tuple[str, ...] = (
    "DOCUMENTO NUMERO",
    "IMBALLI",
    "Q Expediciones",
    "Año",
)
FLOAT_COLUMNS: tuple[str, ...] = (
    "PESO LORDO",
    "VOLUME",
    "PESO TASSABILE",
    "METRI LINEARI",
    "IMPORTO TOTALE VALUTA",
)

# Read-time dtype hint: keep code/text columns as text so leading zeros and
# embedded letters survive `pd.read_excel`'s type inference. Uses the *raw*
# (underscore) names because read happens before _rename_and_derive.
_TYPED_HISTORICAL: frozenset[str] = (
    frozenset(DATE_COLUMNS) | frozenset(INT_COLUMNS) | frozenset(FLOAT_COLUMNS)
)
_READ_AS_STRING_RAW: tuple[str, ...] = tuple(
    c for c in SEITRANS_RAW_COLUMNS if _historical_name(c) not in _TYPED_HISTORICAL
)

PLAUSIBILITY_NO_NULL: tuple[str, ...] = (
    "DOCUMENTO NUMERO",
    "SPEDIZIONE NUMERO",
    "DOCUMENTO_DATA",
)
PLAUSIBILITY_MIN_NON_NULL_RATE: dict[str, float] = {
    "IMBALLI": 0.95,
    "PESO LORDO": 0.95,
    "IMPORTO TOTALE VALUTA": 0.95,
    "VOLUME": 0.90,
}
PLAUSIBILITY_DATE_RANGE: dict[str, tuple[date, date]] = {
    "DOCUMENTO_DATA": (date(2018, 1, 1), date(2035, 12, 31)),
}


def _infer_tipo_expedicion(df: pd.DataFrame) -> pd.Series:
    """Always `Pallet`. Confirmed against the full Seitrans Datos sheet
    (3,464 rows, 100% Pallet). Seitrans is a pallet-only courier as far as
    Artero uses it. If a `Bulto` ever appears in Datos, revisit this."""
    return pd.Series("Pallet", index=df.index, dtype="string")


def _rename_and_derive(df: pd.DataFrame) -> pd.DataFrame:
    """Rename raw underscore-names to historical-Datos names and add the 4
    derived columns. Output shape matches historical Datos (25 columns).
    Note: `DOCUMENTO_DATA` keeps its underscore — see `_KEEP_UNDERSCORE`.
    """
    # Parse DOCUMENTO_DATA inline so we can derive Año/Mes before main coerce.
    # `format="mixed"` lets pandas parse each value's format individually —
    # observed in real Datos: some rows use Italian DD/MM/YYYY HH:MM, others
    # ISO YYYY-MM-DD. Without this, dayfirst=True commits to the first
    # format seen and silently coerces the others to NaT.
    docdata = pd.to_datetime(
        df["DOCUMENTO_DATA"], errors="coerce", format="mixed", dayfirst=True
    )

    renamed = {
        col: _historical_name(col)
        for col in SEITRANS_RAW_COLUMNS
        if _historical_name(col) != col
    }
    df = df.rename(columns=renamed)
    df["DOCUMENTO_DATA"] = docdata

    df["Tipo expedición"] = _infer_tipo_expedicion(df)
    # Q Expediciones is a deduplication marker: 1 on the first row for each
    # SPEDIZIONE NUMERO within the file, 0 on subsequent line items. Summing
    # the column then gives the expedition count regardless of how many lines
    # each shipment broke into. Validated against the full historical Datos:
    # sum(Q)=1404 vs unique SPEDIZIONE NUMERO=1402 (≈99.86% match — the 2-row
    # gap is operator override we don't try to replicate).
    is_first_of_spedizione = ~df.duplicated(subset=["SPEDIZIONE NUMERO"], keep="first")
    df["Q Expediciones"] = is_first_of_spedizione.astype("Int64")
    df["Año"] = docdata.dt.year.astype("Int64")
    # Mes in historical Datos is a datetime (first day of the month), not
    # the month integer. Excel cells often render it as "Enero 2025" via
    # cell formatting while the underlying value is a date.
    df["Mes"] = docdata.dt.to_period("M").dt.to_timestamp()

    return df[list(SEITRANS_COLUMNS)]


def coerce_seitrans_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a 25-col Seitrans-historical-shaped DataFrame to canonical
    dtypes. Used by the parser AND by the golden-extraction script — running
    the same coercion on both sides is what makes the comparison sound."""
    df = df.copy()
    for col in DATE_COLUMNS:
        df[col] = pd.to_datetime(df[col], errors="coerce", format="mixed", dayfirst=True)
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

        string_dtypes = dict.fromkeys(_READ_AS_STRING_RAW, str)
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
        df = _rename_and_derive(df)
        df = coerce_seitrans_dtypes(df)
        assert_plausible(
            df,
            no_null=PLAUSIBILITY_NO_NULL,
            min_non_null_rate=PLAUSIBILITY_MIN_NON_NULL_RATE,
            date_range=PLAUSIBILITY_DATE_RANGE,
        )

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
    def _derive_invoice_date(df: pd.DataFrame) -> date:
        s = df["DOCUMENTO_DATA"].dropna()
        if s.empty:
            raise ParserError("no DOCUMENTO_DATA values to derive invoice_date from")
        return s.iloc[0].date()
