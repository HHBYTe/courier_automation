"""UPS (UK) parser.

Raw invoices come from the UPS Billing Center as headerless dumps, one
file per weekly invoice. Filename pattern:
`Invoice_<Invoice Number>_<MMDDYY>.{csv,xlsx}`.

The CSVs are the authoritative source — two delimiter variants coexist
across years (comma for some files, semicolon for the GB-locale variant)
and are detected per-file. The `.xlsx` files are operator-converted from
those same CSVs and are accepted as a fallback when no CSV is available
for a month; they may carry data-quality issues from the manual step.

The historical workbook (`UPS Shippings Report.xlsx`, sheet `Data`)
preserves the 250-column UPS schema verbatim, with column names hard-coded
below (since the source ships without a header row).

No derived columns. The parser is a passthrough that:
  1. Reads the file with `header=None, dtype=str` (CSV: sniff separator;
     XLSX: openpyxl).
  2. Drops a leading header row if the XLSX variant has one.
  3. Right-pads to 250 columns when the source trimmed trailing empties.
  4. Coerces dates/numerics by column-name heuristics ("Date", "Amount",
     "Weight", etc.) with explicit overrides for identifier-like columns
     ("Charge Category Code", "Basis Currency Code", …).
  5. Returns rows ready to append to the Data sheet.

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

# A keyword-matched column whose name ends in one of these suffixes is an
# identifier or label, not a numeric value. Without this guard, columns
# like `Charge Category Code` ("ADJ"), `Charge Description` ("20.000 %
# Tax"), `Basis Currency Code` ("GBP"), or `Billed Weight Unit of Measure`
# ("K") would be coerced to NaN by `pd.to_numeric` and silently blanked.
_NEVER_FLOAT_SUFFIXES: tuple[str, ...] = (
    " Code", " Description", " Source", " Type", " of Measure",
)


# Columns that real Data stores as numeric (int, not zero-padded string).
# Hard-coded rather than keyword-matched because most "Number" columns
# (Tracking Number, World Ease Number, …) are alphanumeric and must stay
# string. Validated against the production `Data` sheet.
_INT_COLUMNS: tuple[str, ...] = (
    "Invoice Number",
    "Invoice Type Detail Code",
    "Line Item Number",
    "SIMA Access",
    "Scale weight quantity",
    "Freight Sequence Number",
    "Place Holder 53",
    "Payor Role Code",
    "Tax Indicator",
    "Billed Weight Type",
)

# Zone is stored zero-padded ("001"); the master sheet wants a plain
# integer with 0 displayed as blank. Handled inline in coerce.
_ZONE_COL = "Zone"
# Charge Description Code is a mixed-type column ("FSC", "RES", "WSC" for
# surcharges; "001", "003", "01" for adjustments/taxes). Strip leading
# zeros from numeric values, leave letter codes alone. Handled inline.
_CHARGE_DESC_CODE_COL = "Charge Description Code"
# Version ships as "2.1" and the master sheet renders it with a comma
# decimal in the Spanish locale. Coerce to float and format on export.
_VERSION_COL = "Version"


def _is_date_col(name: str) -> bool:
    return any(k in name for k in _DATE_KEYWORDS)


def _is_float_col(name: str) -> bool:
    if _is_date_col(name):
        return False
    if name in _INT_COLUMNS:
        return False
    if any(name.endswith(s) for s in _NEVER_FLOAT_SUFFIXES):
        return False
    return any(k in name for k in _FLOAT_KEYWORDS)


def _strip_leading_zeros_or_keep(value: object) -> object:
    """For Charge Description Code: digit-only → int, anything else → text."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.lstrip("-").isdigit():
        return int(s)
    return s


PLAUSIBILITY_NO_NULL: tuple[str, ...] = (
    "Invoice Number",
    "Invoice Date",
)
# No min-rate rules: Tracking Number was a candidate but it's empty on
# invoice-level charges (Charge Category ADJ/MIS) and some invoices are
# entirely such charges (e.g. weekly fuel-surcharge corrections). Invoice
# Number + Invoice Date no_null and the Invoice Date range catch the
# realistic drift modes.
PLAUSIBILITY_DATE_RANGE: dict[str, tuple[date, date]] = {
    "Invoice Date": (date(2018, 1, 1), date(2035, 12, 31)),
}


def _sniff_separator(path: Path) -> str:
    """UPS Billing Center exports both comma- and semicolon-separated CSVs
    (the GB locale switches to ``;`` so commas can be used as decimal
    separators). Picking the wrong delimiter collapses the row to one
    column, so we detect by reading the header and comparing counts."""
    with path.open("r", encoding="utf-8") as f:
        first = f.readline()
    return ";" if first.count(";") > first.count(",") else ","


def coerce_ups_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a 250-col UPS-shaped DataFrame. Used by parser and the
    golden-extraction script."""
    df = df.copy()
    int_cols = set(_INT_COLUMNS)
    for col in df.columns:
        if col == _ZONE_COL:
            zone = pd.to_numeric(df[col], errors="coerce").astype("Int64")
            # 0 renders as blank in the master; keep only meaningful zones.
            df[col] = zone.where(zone != 0, pd.NA)
        elif col == _CHARGE_DESC_CODE_COL:
            # Mixed int/string column — keep as object so int and text
            # values both write through openpyxl with their native types.
            df[col] = df[col].map(_strip_leading_zeros_or_keep)
        elif col == _VERSION_COL:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
        elif col in int_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        elif _is_date_col(col):
            # UPS ships dates in two formats across years: ISO YYYY-MM-DD
            # in the comma-separated CSVs and DD/MM/YYYY in the semicolon
            # variant. dayfirst=True correctly parses both (ISO is
            # unambiguous; the European order disambiguates the rest).
            df[col] = pd.to_datetime(df[col], errors="coerce", dayfirst=True)
        elif _is_float_col(col):
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
        else:
            df[col] = df[col].map(to_clean_string).astype("string")
    return df


class UpsParser:
    carrier: ClassVar[str] = "ups"
    expected_columns: ClassVar[tuple[str, ...]] = UPS_COLUMNS
    # Version is a float ("2.1") that the master sheet displays with a
    # comma decimal under the Spanish locale ([$-0C0A]). Force the locale
    # in the format string so it renders the same on non-Spanish Excel.
    export_number_formats: ClassVar[dict[str, str]] = {
        "Version": "[$-0C0A]0.0",
    }

    def parse(self, path: Path) -> ParseResult:
        path = Path(path)
        if not path.exists():
            raise ParserError(f"UPS invoice file not found: {path}")

        suffix = path.suffix.lower()
        if suffix == ".csv":
            sep = _sniff_separator(path)
            try:
                df = pd.read_csv(
                    path,
                    header=None,
                    dtype=str,
                    sep=sep,
                    encoding="utf-8",
                    low_memory=False,
                )
            except Exception as e:  # noqa: BLE001
                raise ParserError(f"could not read {path.name}: {e}") from e
        elif suffix == ".xlsx":
            try:
                df = pd.read_excel(
                    path, header=None, dtype=str, engine="openpyxl",
                )
            except Exception as e:  # noqa: BLE001
                raise ParserError(f"could not read {path.name}: {e}") from e
            # Operator-converted XLSX sometimes carry a leading header row
            # ("Version", "Recipient Number", …). Drop it when the cell at
            # the canonical Invoice Number position holds the literal name.
            if len(df) > 0:
                inv_pos = UPS_COLUMNS.index("Invoice Number")
                first = str(df.iloc[0, inv_pos]).strip() if inv_pos < df.shape[1] else ""
                if first == "Invoice Number":
                    df = df.iloc[1:].reset_index(drop=True)
        else:
            raise ParserError(
                f"{path.name}: unsupported extension {suffix!r} "
                "(expected .csv or .xlsx)"
            )

        # CSV semicolon variant and XLSX both sometimes trim the trailing
        # always-empty placeholder columns ("Place Holder 54"–"Place
        # Holder 59"); pad them back so the schema is uniform.
        if df.shape[1] < len(UPS_COLUMNS):
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
