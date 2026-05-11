"""Spring (FR) parser.

Real Spring invoices arrive as a single `.XLSX` (uppercase extension)
with a sheet named after the invoice ID (`E2509827`, `E2510060`, …) and
a tiny `Summary` sheet that's effectively empty. The data sheet has 22
columns: `Invoice Number, Invoice Date, …, Amount, Amount Incl. VAT`.

The historical workbook (`Shipment Report.xlsx`, sheet `INVOICES`) has
24 columns: the same 22 raw columns plus `MONTH` and `YEAR` derived from
`Shipment Date`. The historical sheet stores those derived columns as
Excel formulas (`=MONTH(Table3[[#This Row],[Shipment Date]])`); we write
computed integer values instead, since appended rows aren't guaranteed
to fall inside the table that the formulas reference.

Deferred work:
- Golden test against historical INVOICES.
- The 114-col `REPORT` operations stream (separate parser).
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

SPRING_HISTORICAL_COLUMNS: tuple[str, ...] = SPRING_RAW_COLUMNS + ("MONTH", "YEAR")
assert len(SPRING_HISTORICAL_COLUMNS) == 24

DATE_COLUMNS: tuple[str, ...] = ("Invoice Date", "Shipment Date")
INT_COLUMNS: tuple[str, ...] = ("Items", "MONTH", "YEAR")
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


# Spring's French invoices use dd/mm/yy uniformly across both date
# columns. Without an explicit format pandas defaults to mm/dd-first,
# silently flipping ambiguous dates (e.g. 06/04/26 → June 4 instead of
# April 6). Pin the format so parsing is deterministic.
_DATE_FORMAT = "%d/%m/%y"


def _add_derived(df: pd.DataFrame) -> pd.DataFrame:
    """Add MONTH and YEAR columns from `Shipment Date`. Matches the
    historical INVOICES sheet's two formula-derived columns, but stores
    integer values rather than formulas."""
    df = df.copy()
    shipment = pd.to_datetime(df["Shipment Date"], format=_DATE_FORMAT, errors="coerce")
    df["MONTH"] = shipment.dt.month.astype("Int64")
    df["YEAR"] = shipment.dt.year.astype("Int64")
    return df[list(SPRING_HISTORICAL_COLUMNS)]


def coerce_spring_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in DATE_COLUMNS:
        df[col] = pd.to_datetime(df[col], format=_DATE_FORMAT, errors="coerce")
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
    expected_columns: ClassVar[tuple[str, ...]] = SPRING_HISTORICAL_COLUMNS
    # Source files use dd/mm/yy; render the same way in the sidecar so the
    # operator's eye can scan the export against the original invoice.
    export_date_formats: ClassVar[dict[str, str]] = {
        "Invoice Date": "dd/mm/yy",
        "Shipment Date": "dd/mm/yy",
    }

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

        # Spring's portal occasionally inserts extra metadata columns
        # (observed Mar 2026: "ALBARÁN" duplicating Customer Ref, and
        # "EMRPRESA" — typo of EMPRESA — the buyer company name). Drop
        # any non-canonical columns before schema validation.
        extras = [c for c in df.columns if c not in SPRING_RAW_COLUMNS]
        if extras:
            log.info("spring parser: dropping extra columns %s", extras)
            df = df.drop(columns=extras)
        assert_schema(df, SPRING_RAW_COLUMNS)
        df = _add_derived(df)
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
