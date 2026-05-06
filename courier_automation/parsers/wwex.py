"""Wwex (US) parser — **partial implementation**.

The Wwex layer is materially more complex than the other couriers and is
shipped here as a scaffold. Full production-readiness is deferred. Known
unfinished work:

1. **File-format drift.** Real Wwex invoices arrive as `.xls`, `.csv`, or
   `.xlsx` depending on the month (the SpeedShip portal changed format
   twice). Today this parser handles only `.xlsx`; `.xls` would need
   `xlrd>=2.0.1` added to requirements; `.csv` files are
   semicolon-separated (not comma) and would need a separate read path.
2. **Schema mapping raw → historical.** Raw Wwex files have **42**
   ALL_CAPS_UNDERSCORED columns (`CUSTOMER_NO, COMPANY_NAME, …`); the
   historical workbook (`Wwex USA Shippings Report.xlsx`, sheet `Data`)
   uses a different **44**-column schema with `Source System / SpeedShip`
   naming (`Source System, Tracking#, Ship Date, …`). The mapping is
   non-trivial and not yet reverse-engineered. Today the parser passes
   the raw 42 columns through unchanged — useful for a "raw archive"
   sheet but NOT a drop-in append into the historical Data sheet.
3. **Sheet name varies** (`shipmentDetailsUPS_W130089866_2`). Today the
   parser reads `sheet_name=0` (the first sheet), which works in
   practice but isn't validated.
4. **Golden test not built.** Once the mapping in #2 is settled the
   golden test should mirror Correos's: read raw, run mapping, compare
   to a Datos slice keyed on `TRACKING_NO`.

For now: schema validation against the 42-col raw layout, dtype
coercion for the obvious date/numeric columns, plausibility checks on
the key fields. Sufficient for the parser to be exercised in isolation
but **not yet wired into the CLI** until #1 and #2 are done.
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

WWEX_RAW_COLUMNS: tuple[str, ...] = (
    "CUSTOMER_NO",
    "COMPANY_NAME",
    "MARKET_NAME",
    "ACCOUNT_NO",
    "BILL_TO_ACCOUNT_NUMBER",
    "CREATION_DATE",
    "SHIPMENT_DATE",
    "SENDER",
    "ORIGIN_ADDRESS_LINE_1",
    "ORIGIN_ADDRESS_LINE_2",
    "ORIGIN_CITY",
    "ORIGIN_STATE",
    "ORIGIN_ZIP",
    "ORIGIN_COUNTRY",
    "CONSIGNEE_COMPANY_NAME",
    "DESTINATION_ADDRESS_LINE_1",
    "DESTINATION_ADDRESS_LINE_2",
    "DESTINATION_CITY",
    "DESTINATION_STATE",
    "DESTINATION_ZIP",
    "DESTINATION_COUNTRY",
    "STATUS",
    "TRACKING_NO",
    "SERVICE_TYPE",
    "ZONE",
    "ACTUAL_PICKUP_DATE",
    "ACTUAL_DELIVERY_DATE",
    "REFERENCE_NUMBER",
    "PACKAGE_COUNT",
    "TOTAL_WEIGHT",
    "TOTAL_RATED_WEIGHT",
    "PACKAGE_WEIGHT",
    "PACKAGE_RATED_WEIGHT",
    "SHIPPED DIMENSIONS",
    "BILLED DIMENSIONS",
    "IS_INSURED",
    "INSURED_AMOUNT",
    "COST OF INSURANCE",
    "ESTIMATED_TOTAL_PRICE",
    "LOGINID",
    "ACCESSORIAL_CHARGES",
    "TRACKING INFO",
)
assert len(WWEX_RAW_COLUMNS) == 42

DATE_COLUMNS: tuple[str, ...] = (
    "CREATION_DATE",
    "SHIPMENT_DATE",
    "ACTUAL_PICKUP_DATE",
    "ACTUAL_DELIVERY_DATE",
)
INT_COLUMNS: tuple[str, ...] = ("PACKAGE_COUNT",)
FLOAT_COLUMNS: tuple[str, ...] = (
    "TOTAL_WEIGHT",
    "TOTAL_RATED_WEIGHT",
    "PACKAGE_WEIGHT",
    "PACKAGE_RATED_WEIGHT",
    "INSURED_AMOUNT",
    "COST OF INSURANCE",
    "ESTIMATED_TOTAL_PRICE",
)

PLAUSIBILITY_NO_NULL: tuple[str, ...] = ("TRACKING_NO", "SHIPMENT_DATE")
PLAUSIBILITY_DATE_RANGE: dict[str, tuple[date, date]] = {
    "SHIPMENT_DATE": (date(2018, 1, 1), date(2035, 12, 31)),
}


def coerce_wwex_dtypes(df: pd.DataFrame) -> pd.DataFrame:
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


class WwexParser:
    carrier: ClassVar[str] = "wwex"
    expected_columns: ClassVar[tuple[str, ...]] = WWEX_RAW_COLUMNS

    def parse(self, path: Path) -> ParseResult:
        path = Path(path)
        if not path.exists():
            raise ParserError(f"Wwex invoice file not found: {path}")
        if path.suffix.lower() not in {".xlsx"}:
            raise ParserError(
                f"Wwex parser only supports .xlsx today; got {path.suffix!r}. "
                "Add xlrd>=2.0.1 + a .csv reader (semicolon separator) for "
                "full coverage — see parser module docstring."
            )

        try:
            df = pd.read_excel(
                path, sheet_name=0, engine="openpyxl",
                dtype=str, usecols=range(len(WWEX_RAW_COLUMNS)),
            )
        except Exception as e:  # noqa: BLE001
            raise ParserError(f"could not read {path.name}: {e}") from e

        assert_schema(df, WWEX_RAW_COLUMNS)
        df = coerce_wwex_dtypes(df)
        assert_plausible(
            df,
            no_null=PLAUSIBILITY_NO_NULL,
            date_range=PLAUSIBILITY_DATE_RANGE,
        )

        # Wwex raw doesn't contain an explicit invoice number. The whole
        # file IS the invoice for that month; we use the SHIPMENT_DATE
        # year-month as the synthetic invoice key.
        invoice_date = self._derive_invoice_date(df)
        invoice_number = f"wwex-{invoice_date.year}-{invoice_date.month:02d}"
        file_hash = compute_file_hash(path)
        log.info(
            "wwex parser: %s | %d rows | hash=%s | source=%s",
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
    def _derive_invoice_date(df: pd.DataFrame) -> date:
        s = df["SHIPMENT_DATE"].dropna()
        if s.empty:
            raise ParserError("no SHIPMENT_DATE values to derive invoice_date from")
        return s.iloc[0].date()
