"""Wwex (US) parser.

Real Wwex invoices arrive as `.xls`, `.csv`, or `.xlsx` files (the
SpeedShip portal changed format twice). All three formats share the same
42-column raw schema; the parser tries each engine in turn:

  - `.xls`  via `xlrd>=2.0.1` (added to requirements).
  - `.csv`  read as semicolon-separated.
  - `.xlsx` via `openpyxl`, with `usecols=range(42)` to clip the
    16,000+ phantom trailing columns openpyxl reports for these files.

The historical workbook (`Wwex USA Shippings Report.xlsx`, sheet `Data`)
uses a different 44-column schema with `Source System / SpeedShip`
naming. The parser maps raw → historical via `_map_to_historical`,
reverse-engineered from a side-by-side inspection of one shipment.

Mapping rules (validated against TRACKING_NO `1Z2F8W440316139841|...`):
  - Most columns are a direct rename (CUSTOMER_NO → Account#, SENDER →
    Ship From Company, ORIGIN_* → Ship From *, DESTINATION_* → Ship To *,
    TOTAL_WEIGHT → Package Weight, etc.).
  - `Source System` is the constant `"SpeedShip 2.0"`.
  - `Domestic/International` is `"DOM"` when origin country == destination
    country, `"INT"` otherwise.
  - `Weight per package` = TOTAL_WEIGHT / PACKAGE_COUNT.
  - Several historical columns (`LoginId`, `Bill To*`, `Ship Ref1/2`,
    `Sent By`, `Ship*Phone`, `Service`, `Insured Value`, `Packaging`,
    `Package Dimensions`, etc.) are not in raw and are emitted as None —
    the operator fills them in by hand. Today's golden test excludes
    those columns (see `test_wwex_golden.py`).
"""

from __future__ import annotations

import csv
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

WWEX_COLUMNS: tuple[str, ...] = (
    "Source System",
    "Domestic/International",
    "Account#",
    "LoginId",
    "Tracking#",
    "Ship Date",
    "Ship Ref1",
    "Ship Ref2",
    "Ship From Company",
    "Ship From Addr1",
    "Ship From Addr2",
    "Ship From Addr3",
    "Ship From City",
    "Ship From State",
    "Ship From Postal Code",
    "Ship From Country",
    "Ship From Phone",
    "Sent By",
    "Ship To Company",
    "Ship To Addr1",
    "Ship To Addr2",
    "Ship To Addr3",
    "Ship To City",
    "Ship To State",
    "Ship To Postal Code",
    "Ship To Country",
    "Ship To Phone",
    "Ship To Contact",
    "Bill To",
    "Bill To Acct#",
    "Bill Duty To",
    "Bill Duty To Acct#",
    "Package Weight",
    "Billed Weight",
    "Packaging",
    "Customs Value",
    "Package Dimensions",
    "Service",
    "Insured Value",
    "Est Transportation Charges",
    "Est Other Charges",
    "Insurance",
    "Package Count",
    "Weight per package",
)
assert len(WWEX_COLUMNS) == 44

# Direct renames: historical → raw.
_RENAME: dict[str, str] = {
    "Account#": "ACCOUNT_NO",
    "Tracking#": "TRACKING_NO",
    # Ship Date is mapped via _coalesce_ship_date below — many raw
    # SHIPMENT_DATEs are empty for shipments not yet picked up at invoice
    # time, and the operator fills the gap from ACTUAL_PICKUP_DATE or
    # CREATION_DATE.
    "Ship From Company": "SENDER",
    "Ship From Addr1": "ORIGIN_ADDRESS_LINE_1",
    "Ship From Addr2": "ORIGIN_ADDRESS_LINE_2",
    "Ship From City": "ORIGIN_CITY",
    "Ship From State": "ORIGIN_STATE",
    "Ship From Postal Code": "ORIGIN_ZIP",
    "Ship From Country": "ORIGIN_COUNTRY",
    "Ship To Company": "CONSIGNEE_COMPANY_NAME",
    "Ship To Addr1": "DESTINATION_ADDRESS_LINE_1",
    "Ship To Addr2": "DESTINATION_ADDRESS_LINE_2",
    "Ship To City": "DESTINATION_CITY",
    "Ship To State": "DESTINATION_STATE",
    "Ship To Postal Code": "DESTINATION_ZIP",
    "Ship To Country": "DESTINATION_COUNTRY",
    "Package Weight": "TOTAL_WEIGHT",
    "Billed Weight": "TOTAL_RATED_WEIGHT",
    "Est Transportation Charges": "ESTIMATED_TOTAL_PRICE",
    "Package Count": "PACKAGE_COUNT",
}

# Columns the operator fills in manually after pasting (no raw source);
# parser emits None and the golden test excludes them from comparison.
_OPERATOR_FILLED: tuple[str, ...] = (
    "LoginId",
    "Ship Ref1",
    "Ship Ref2",
    "Ship From Addr3",
    "Ship From Phone",
    "Sent By",
    "Ship To Addr3",
    "Ship To Phone",
    "Ship To Contact",
    "Bill To",
    "Bill To Acct#",
    "Bill Duty To",
    "Bill Duty To Acct#",
    "Packaging",
    "Customs Value",
    "Package Dimensions",
    "Service",
    "Insured Value",
    "Est Other Charges",
    "Insurance",
)

DATE_COLUMNS: tuple[str, ...] = ("Ship Date",)
INT_COLUMNS: tuple[str, ...] = ("Package Count",)
FLOAT_COLUMNS: tuple[str, ...] = (
    "Package Weight",
    "Billed Weight",
    "Est Transportation Charges",
    "Weight per package",
)

PLAUSIBILITY_NO_NULL: tuple[str, ...] = ("Tracking#",)
PLAUSIBILITY_DATE_RANGE: dict[str, tuple[date, date]] = {
    "Ship Date": (date(2018, 1, 1), date(2035, 12, 31)),
}


def _map_to_historical(raw: pd.DataFrame) -> pd.DataFrame:
    """Map a 42-col raw Wwex DataFrame to the 44-col historical schema."""
    out = pd.DataFrame(index=raw.index)
    out["Source System"] = "SpeedShip 2.0"
    out["Domestic/International"] = (
        (raw["ORIGIN_COUNTRY"].astype(str).str.strip()
         == raw["DESTINATION_COUNTRY"].astype(str).str.strip())
        .map({True: "DOM", False: "INT"})
    )
    # Ship Date — coalesce SHIPMENT_DATE → ACTUAL_PICKUP_DATE →
    # CREATION_DATE → ACTUAL_DELIVERY_DATE. Many raw SHIPMENT_DATEs are
    # empty (shipments not yet picked up); the operator falls back to
    # the next available date when pasting. Order chosen empirically —
    # delivery as last resort since it can be later than the actual ship.
    ship_date = pd.to_datetime(raw["SHIPMENT_DATE"], errors="coerce")
    pickup = pd.to_datetime(raw["ACTUAL_PICKUP_DATE"], errors="coerce")
    creation = pd.to_datetime(raw["CREATION_DATE"], errors="coerce")
    delivery = pd.to_datetime(raw["ACTUAL_DELIVERY_DATE"], errors="coerce")
    out["Ship Date"] = (
        ship_date.fillna(pickup).fillna(creation).fillna(delivery)
    )

    for hist_col, raw_col in _RENAME.items():
        out[hist_col] = raw[raw_col]
    for col in _OPERATOR_FILLED:
        out[col] = pd.Series([None] * len(raw), dtype="string", index=raw.index)
    # Weight per package = total / count, both numeric.
    weight = pd.to_numeric(raw["TOTAL_WEIGHT"], errors="coerce")
    count = pd.to_numeric(raw["PACKAGE_COUNT"], errors="coerce")
    out["Weight per package"] = weight / count
    return out[list(WWEX_COLUMNS)]


def coerce_wwex_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a 44-col Wwex-historical-shaped DataFrame."""
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


# Wwex 2026 portal change: REFERENCE_NUMBER was split into REFERENCE1 +
# REFERENCE2, and a new Column1 was inserted between PACKAGE_COUNT and
# TOTAL_WEIGHT. Reshape to the canonical 42-col layout: keep REFERENCE1 as
# REFERENCE_NUMBER and drop the other two new columns.
def _reshape_v2_to_v1(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "REFERENCE1" in out.columns:
        out = out.rename(columns={"REFERENCE1": "REFERENCE_NUMBER"})
    for col in ("REFERENCE2", "Column1"):
        if col in out.columns:
            out = out.drop(columns=col)
    return out


def _read_raw(path: Path) -> pd.DataFrame:
    """Read Wwex raw data across the four observed formats (xlsx v1/v2, xls, csv)."""
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        # Read all columns (no usecols clip) so we can detect v2 by header.
        df = pd.read_excel(path, sheet_name=0, engine="openpyxl", dtype=str)
        if "REFERENCE1" in df.columns:
            df = _reshape_v2_to_v1(df)
        # Now clip phantom trailing columns to the canonical 42.
        return df.iloc[:, : len(WWEX_RAW_COLUMNS)]
    if suffix == ".xls":
        return pd.read_excel(
            path, sheet_name=0, engine="xlrd", dtype=str,
            usecols=range(len(WWEX_RAW_COLUMNS)),
        )
    if suffix == ".csv":
        # Wwex CSVs from the SpeedShip export are semicolon-separated.
        return pd.read_csv(
            path, sep=";", dtype=str, encoding="utf-8",
            usecols=range(len(WWEX_RAW_COLUMNS)),
            engine="python",
            quoting=csv.QUOTE_MINIMAL,
        )
    raise ParserError(
        f"Wwex parser doesn't recognise extension {suffix!r}; "
        "expected .xlsx, .xls, or .csv."
    )


class WwexParser:
    carrier: ClassVar[str] = "wwex"
    expected_columns: ClassVar[tuple[str, ...]] = WWEX_COLUMNS

    def parse(self, path: Path) -> ParseResult:
        path = Path(path)
        if not path.exists():
            raise ParserError(f"Wwex invoice file not found: {path}")

        try:
            raw = _read_raw(path)
        except ParserError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ParserError(f"could not read {path.name}: {e}") from e

        assert_schema(raw, WWEX_RAW_COLUMNS)
        df = _map_to_historical(raw)
        df = coerce_wwex_dtypes(df)
        assert_plausible(
            df,
            no_null=PLAUSIBILITY_NO_NULL,
            date_range=PLAUSIBILITY_DATE_RANGE,
        )

        # Wwex raw doesn't carry an explicit invoice number; one file is
        # one month of shipments. Use the year-month from Ship Date as
        # the synthetic invoice key.
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
        s = df["Ship Date"].dropna()
        if s.empty:
            raise ParserError("no Ship Date values to derive invoice_date from")
        return s.iloc[0].date()
