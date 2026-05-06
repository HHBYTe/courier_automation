"""Spring (FR) parser — **partial implementation**.

Real Spring invoices arrive as a single `.XLSX` (uppercase extension)
with a sheet named after the invoice ID (`E2509827`, `E2510060`, …) and
a tiny `Summary` sheet that's effectively empty. The data sheet has 22
columns: `Invoice Number, Invoice Date, Account Number, CONNOTE, …,
Amount, Amount Incl. VAT`.

The data-exploration doc mentions a 114-col `REPORT` sheet, but that
schema appears in the historical workbook (Spring's "operations" stream)
rather than per-invoice files. The per-invoice files we actually receive
are the 22-col billing detail.

Deferred work:
- The historical workbook has a separate `INVOICES` sheet with 24 cols
  that's the closest match to per-invoice files. Mapping the parser's
  22 cols → the historical 24 cols hasn't been done. Today the parser
  is raw-passthrough; not yet wired into the CLI for the same reason
  as Wwex.
- Golden test against historical Datos.
- The 114-col `REPORT` operations stream is a separate parser job.

For now: schema validation against the 22-col layout, dtype coercion,
plausibility on the key fields. Sheet read by index 0 (the invoice-ID
sheet name varies per file).
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

SPRING_RAW_COLUMNS: tuple[str, ...] = (
    "Invoice Number",
    "Invoice Date",
    "Account Number",
    "CONNOTE",
    "Sell-to Customer No.",
    "Product",
    "Product Description",
    "Shipment Date",
    "Customer Ref",
    "Country",
    "Format",
    "Option Code",
    "Option Description",
    "Items",
    "Item Charge",
    "Actual Kilos",
    "Actual Grammes",
    "Volumetric Kilos",
    "Volumetric Grammes",
    "Weight Charge",
    "Amount",
    "Amount Incl. VAT",
)
assert len(SPRING_RAW_COLUMNS) == 22

DATE_COLUMNS: tuple[str, ...] = ("Invoice Date", "Shipment Date")
INT_COLUMNS: tuple[str, ...] = ("Items",)
FLOAT_COLUMNS: tuple[str, ...] = (
    "Item Charge",
    "Actual Kilos",
    "Actual Grammes",
    "Volumetric Kilos",
    "Volumetric Grammes",
    "Weight Charge",
    "Amount",
    "Amount Incl. VAT",
)

PLAUSIBILITY_NO_NULL: tuple[str, ...] = (
    "Invoice Number",
    "Invoice Date",
    "CONNOTE",
)
PLAUSIBILITY_DATE_RANGE: dict[str, tuple[date, date]] = {
    "Invoice Date": (date(2018, 1, 1), date(2035, 12, 31)),
}


def coerce_spring_dtypes(df: pd.DataFrame) -> pd.DataFrame:
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


class SpringParser:
    carrier: ClassVar[str] = "spring"
    expected_columns: ClassVar[tuple[str, ...]] = SPRING_RAW_COLUMNS

    def parse(self, path: Path) -> ParseResult:
        path = Path(path)
        if not path.exists():
            raise ParserError(f"Spring invoice file not found: {path}")

        try:
            # Sheet name varies (it's the invoice ID like 'E2509827');
            # reading by index 0 sidesteps that.
            df = pd.read_excel(path, sheet_name=0, engine="openpyxl", dtype=str)
        except Exception as e:  # noqa: BLE001
            raise ParserError(f"could not read {path.name}: {e}") from e

        assert_schema(df, SPRING_RAW_COLUMNS)
        df = coerce_spring_dtypes(df)
        assert_plausible(
            df,
            no_null=PLAUSIBILITY_NO_NULL,
            date_range=PLAUSIBILITY_DATE_RANGE,
        )

        invoice_number = self._derive_invoice_number(df, path)
        invoice_date = self._derive_invoice_date(df, path)
        file_hash = compute_file_hash(path)
        log.info(
            "spring parser: %s | %d rows | hash=%s | source=%s",
            invoice_number, len(df), file_hash[:12], path.name,
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
    def _derive_invoice_number(df: pd.DataFrame, path: Path) -> str:
        s = df["Invoice Number"].dropna()
        if s.empty:
            raise ParserError(f"{path.name}: no Invoice Number found")
        unique = s.astype(str).str.strip().unique()
        if len(unique) > 1:
            raise ParserError(
                f"{path.name}: multiple Invoice Numbers in one file ({list(unique)[:3]})"
            )
        return unique[0]

    @staticmethod
    def _derive_invoice_date(df: pd.DataFrame, path: Path) -> date:
        s = df["Invoice Date"].dropna()
        if s.empty:
            raise ParserError(f"{path.name}: no Invoice Date found")
        return s.iloc[0].date()
