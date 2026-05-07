"""UPS (UK) parser.

Raw invoices come from the UPS Billing Center as headerless tabular dumps,
one file per weekly invoice. Filename pattern:
`Invoice_<Invoice Number>_<MMDDYY>.{csv,xlsx}`.

  - 2023–2025: `.csv` (250 cols, comma-separated, headerless).
  - 2026+:     `.xlsx` (same 250-col schema, but Excel trims trailing
               empty columns so reads can land at 244–250 cols; we
               right-pad to 250 to match the historical schema).

The historical workbook (`UPS Shippings Report.xlsx`, sheet `Data`) preserves
the 250-column UPS schema verbatim, with column names hard-coded below
(since the source ships without a header row in either format).

No derived columns. The parser is a passthrough that:
  1. Reads file (CSV or XLSX) with `header=None, dtype=str`.
  2. Pads to 250 columns if XLSX trimmed trailing empties.
  3. Coerces dates/numerics by column-name heuristics ("Date", "Amount",
     "Weight", etc.) — UPS has too many columns to enumerate manually.
  4. Returns rows ready to append to the Data sheet.

`invoice_number` and `invoice_date` are taken from the data (every row
within a single file shares the same Invoice Number / Invoice Date).
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

# 250 columns extracted from `UPS Shippings Report.xlsx` sheet `Data`.
# This is UPS's standard billing-extract schema and changes rarely.
UPS_COLUMNS: tuple[str, ...] = (
    "Version", "Recipient Number", "Account Number", "Account Country",
    "Invoice Date", "Invoice Number", "Invoice Type Code",
    "Invoice Type Detail Code", "Account Tax ID", "Invoice Currency Code",
    "Invoice Amount", "Transaction Date", "Pickup Record Number",
    "Lead Shipment Number", "World Ease Number", "Shipment Reference Number 1",
    "Shipment Reference Number 2", "Bill Option Code", "Package Quantity",
    "Oversize Quantity", "Tracking Number", "Package Reference Number 1",
    "Package Reference Number 2", "Package Reference Number 3",
    "Package Reference Number 4", "Package Reference Number 5",
    "Entered Weight", "Entered Weight Unit of Measure", "Billed Weight",
    "Billed Weight Unit of Measure", "Container Type", "Billed Weight Type",
    "Package Dimensions", "Zone", "Charge Category Code",
    "Charge Category Detail Code", "Charge Source", "Type Code 1",
    "Type Detail Code 1", "Type Detail Value 1", "Type Code 2",
    "Type Detail Code 2", "Type Detail Value 2", "Charge Classification Code",
    "Charge Description Code", "Charge Description", "Charged Unit Quantity",
    "Basis Currency Code", "Basis Value", "Tax Indicator",
    "Transaction Currency Code", "Incentive Amount", "Net Amount",
    "Miscellaneous Currency Code", "Miscellaneous Incentive Amount",
    "Miscellaneous Net Amount", "Alternate Invoicing Currency Code",
    "Alternate Invoice Amount", "Invoice Exchange Rate", "Tax Variance Amount",
    "Currency Variance Amount", "Invoice Level Charge", "Invoice Due Date",
    "Alternate Invoice Number", "Store Number", "Customer Reference Number",
    "Sender Name", "Sender Company Name", "Sender Address Line 1",
    "Sender Address Line 2", "Sender City", "Sender State", "Sender Postal",
    "Sender Country", "Receiver Name", "Receiver Company Name",
    "Receiver Address Line 1", "Receiver Address Line 2", "Receiver City",
    "Receiver State", "Receiver Postal", "Receiver Country", "Third Party Name",
    "Third Party Company Name", "Third Party Address Line 1",
    "Third Party Address Line 2", "Third Party City", "Third Party State",
    "Third Party Postal", "Third Party Country", "Sold To Name",
    "Sold To Company Name", "Sold To Address Line 1", "Sold To Address Line 2",
    "Sold To City", "Sold To State", "Sold To Postal", "Sold To Country",
    "Miscellaneous Address Qual 1", "Miscellaneous Address 1 Name",
    "Miscellaneous Address 1 Company Name",
    "Miscellaneous Address 1 Address Line 1",
    "Miscellaneous Address 1 Address Line 2", "Miscellaneous Address 1 City",
    "Miscellaneous Address 1 State", "Miscellaneous Address 1 Postal",
    "Miscellaneous Address 1 Country", "Miscellaneous Address Qual 2",
    "Miscellaneous Address 2 Name", "Miscellaneous Address 2 Company Name",
    "Miscellaneous Address 2 Address Line 1",
    "Miscellaneous Address 2 Address Line 2", "Miscellaneous Address 2 City",
    "Miscellaneous Address 2 State", "Miscellaneous Address 2 Postal",
    "Miscellaneous Address 2 Country", "Shipment Date", "Shipment Export Date",
    "Shipment Import Date", "Entry Date", "Direct Shipment Date",
    "Shipment Delivery Date", "Shipment Release Date", "Cycle Date",
    "EFT Date", "Validation Date", "Entry Port", "Entry Number", "Export Place",
    "Shipment Value Amount", "Shipment Description", "Entered Currency Code",
    "Customs Number", "Exchange Rate", "Master Air Waybill Number", "EPU",
    "Entry Type", "CPC Code", "Line Item Number", "Goods Description",
    "Entered Value", "Duty Amount", "Weight", "Unit of Measure",
    "Item Quantity", "Item Quantity Unit of Measure", "Import Tax ID",
    "Declaration Number",
    "Carrier Name/Clinical Trial Identification Number/SDS ID ",
    "CCCD Number", "Cycle Number", "Foreign Trade Reference Number",
    "Job Number", "Transport Mode", "Tax Type", "Tariff Code", "Tariff Rate",
    "Tariff Treatment Number", "Contact Name", "Class Number", "Document Type",
    "Office Number", "Document Number", "Duty Value", "Total Value for Duty",
    "Excise Tax Amount", "Excise Tax Rate", "GST Amount", "GST Rate",
    "Order In Council", "Origin Country", "SIMA Access", "Tax Value",
    "Total Customs Amount", "Miscellaneous Line 1", "Miscellaneous Line 2",
    "Miscellaneous Line 3", "Miscellaneous Line 4", "Miscellaneous Line 5",
    "Payor Role Code", "Miscellaneous Line 7", "Miscellaneous Line 8",
    "Miscellaneous Line 9", "Miscellaneous Line 10", "Miscellaneous Line 11",
    "Duty Rate", "VAT Basis Amount", "VAT Amount", "VAT Rate",
    "Other Basis Amount", "Other Amount", "Other Rate",
    "Other Customs Number Indicator", "Other Customs Number",
    "Customs Office Name", "Package Dimension Unit Of Measure",
    "Original Shipment Package Quantity", "Corrected Zone",
    "Tax Law Article Number", "Tax Law Article Basis Amount",
    "Original tracking number", "Scale weight quantity",
    "Scale Weight Unit of Measure", "Raw dimension unit of measure",
    "Raw dimension length", "BOL # 1", "BOL # 2", "BOL # 3", "BOL # 4",
    "BOL # 5", "PO # 1", "PO # 2", "PO # 3", "PO # 4", "PO # 5", "PO # 6",
    "PO # 7", "PO # 8", "PO # 9", "PO # 10", "NMFC", "Detail Class",
    "Freight Sequence Number", "Declared Freight Class", "EORI Number",
    "Detail Keyed Dim", "Detail Keyed Unit of Measure",
    "Detail Keyed Billed Dimension", "Detail Keyed Billed Unit of Measure",
    "Original Service Description", "Promo Discount Applied Indicator",
    "Promo Discount Alias", "SDS Match Level Cd", "SDS RDR Date",
    "SDS Delivery Date", "SDS Error Code", "Place Holder 46",
    "Place Holder 47", "Place Holder 48", "SCC Scale Weight", "Place Holder 50",
    "Place Holder 51", "Place Holder 52", "Place Holder 53", "Place Holder 54",
    "Place Holder 55", "Place Holder 56", "Place Holder 57", "Place Holder 58",
    "Place Holder 59",
)
assert len(UPS_COLUMNS) == 250

# Auto-detected dtype groups based on column-name heuristics. Far too many
# columns to enumerate by hand; the heuristic catches the obvious cases and
# everything else falls through to text. Validated against real Datos via
# the golden test once fixtures are landed.
_DATE_KEYWORDS = ("Date",)
_FLOAT_KEYWORDS = (
    "Amount", "Value", "Weight", "Charge", "Rate", "Variance",
    "Basis", "Quantity",
)


# Columns that real Data stores as numeric (int, not zero-padded string).
# Hard-coded rather than keyword-matched because most "Number" columns
# (Tracking Number, World Ease Number, …) are alphanumeric and must stay
# string. Validated against the production `Data` sheet.
_INT_COLUMNS: tuple[str, ...] = (
    "Invoice Number",
)


def _is_date_col(name: str) -> bool:
    return any(k in name for k in _DATE_KEYWORDS)


def _is_float_col(name: str) -> bool:
    if _is_date_col(name):
        return False
    return any(k in name for k in _FLOAT_KEYWORDS)


PLAUSIBILITY_NO_NULL: tuple[str, ...] = (
    "Invoice Number",
    "Invoice Date",
    "Tracking Number",
)
PLAUSIBILITY_DATE_RANGE: dict[str, tuple[date, date]] = {
    "Invoice Date": (date(2018, 1, 1), date(2035, 12, 31)),
}


def coerce_ups_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a 250-col UPS-shaped DataFrame. Used by parser and the
    golden-extraction script."""
    df = df.copy()
    int_cols = set(_INT_COLUMNS)
    for col in df.columns:
        if col in int_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        elif _is_date_col(col):
            df[col] = pd.to_datetime(df[col], errors="coerce")
        elif _is_float_col(col):
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
        else:
            df[col] = df[col].map(to_clean_string).astype("string")
    return df


class UpsParser:
    carrier: ClassVar[str] = "ups"
    expected_columns: ClassVar[tuple[str, ...]] = UPS_COLUMNS

    def parse(self, path: Path) -> ParseResult:
        path = Path(path)
        if not path.exists():
            raise ParserError(f"UPS invoice file not found: {path}")

        suffix = path.suffix.lower()
        try:
            if suffix == ".csv":
                df = pd.read_csv(
                    path,
                    header=None,
                    dtype=str,
                    encoding="utf-8",
                    low_memory=False,
                )
            elif suffix == ".xlsx":
                df = pd.read_excel(
                    path,
                    header=None,
                    dtype=str,
                    engine="openpyxl",
                )
            else:
                raise ParserError(
                    f"{path.name}: unsupported extension {suffix!r} "
                    "(expected .csv or .xlsx)"
                )
        except ParserError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ParserError(f"could not read {path.name}: {e}") from e

        # XLSX legitimately trims trailing empty columns; right-pad to 250.
        # CSV with the wrong column count is malformed input — surface loudly.
        if suffix == ".xlsx" and df.shape[1] < len(UPS_COLUMNS):
            for i in range(df.shape[1], len(UPS_COLUMNS)):
                df[i] = pd.NA
        if df.shape[1] != len(UPS_COLUMNS):
            raise ParserError(
                f"{path.name}: expected {len(UPS_COLUMNS)} columns, got "
                f"{df.shape[1]}"
            )
        df.columns = list(UPS_COLUMNS)
        # No header to validate against (CSV is headerless), but the
        # column-count assertion above is the structural check.
        assert_schema(df, UPS_COLUMNS)
        df = coerce_ups_dtypes(df)
        assert_plausible(
            df,
            no_null=PLAUSIBILITY_NO_NULL,
            date_range=PLAUSIBILITY_DATE_RANGE,
        )

        invoice_number = self._derive_invoice_number(df, path)
        invoice_date = self._derive_invoice_date(df, path)
        file_hash = compute_file_hash(path)
        log.info(
            "ups parser: %s | %d rows | hash=%s | source=%s",
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
    def _derive_invoice_number(df: pd.DataFrame, path: Path) -> str:
        s = df["Invoice Number"].dropna()
        if s.empty:
            raise ParserError(
                f"{path.name}: no Invoice Number found in any row"
            )
        unique = s.astype(str).str.strip().unique()
        if len(unique) > 1:
            raise ParserError(
                f"{path.name}: multiple Invoice Numbers in one CSV "
                f"({list(unique)[:3]}); expected exactly one"
            )
        return unique[0]

    @staticmethod
    def _derive_invoice_date(df: pd.DataFrame, path: Path) -> date:
        s = df["Invoice Date"].dropna()
        if s.empty:
            raise ParserError(f"{path.name}: no Invoice Date found")
        return s.iloc[0].date()
