"""Royal Mail (UK) parser.

Real Royal Mail invoices arrive as a single pipe-separated `.csv` per
invoice, encoded as cp1252 (the £ sign is the giveaway: byte 0xA3, not
UTF-8). The raw layout interleaves three kinds of rows under one 34-col
header:

  - row 0 (data idx 0): invoice-level summary — `Royal Mail Address`,
    `Document Number`, `Account Number`, totals, `Invoice Date`, customer
    address, VAT-rate/total pairs (cols 1-23 populated). The trailing
    docket-detail columns are empty.
  - row N (data idx 1, 4, 7, …): one row per docket — `Docket Number`,
    `Posting Date`, `Poster`, `Senders Ref`, `Format`, `Service`,
    `Quantity`, `Weight (kg)`, `Unit Cost`, `Net Value`, `VAT Code`
    (cols 24-34 populated). Invoice-level columns are empty here.
  - sub-rows (data idx 2-3, 5-6, …): free-text commentary — surcharges
    (e.g. "Green Surcharge of £0.70"), Total surcharge lines, "Lge
    Letter" weight-band breakdowns. These are descriptive, not real
    shipments. They have an empty `Docket Number`.

The parser keeps only real-docket rows (Docket Number non-null), then
propagates the invoice header fields (`Document Number`, `Account
Number`, `Invoice Date`, `Pay By`) onto every docket. Two derived
columns (`Año`, `Mes` from Posting Date) match the Correos/Seitrans
convention.

There is no historical workbook for Royal Mail yet — the 17-column
schema below is parser-defined and will become the target shape when
one is built.
"""

from __future__ import annotations

import logging
import re
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


# 34-col raw header (pipe-separated CSV). Five pairs of `VAT Rate` /
# `VAT Total` columns repeat — pandas de-duplicates them on read by
# appending `.1`, `.2`, … to the later occurrences. We don't reference
# them after the invoice-header pluck, so the dedup naming is harmless.
ROYALMAIL_RAW_COLUMNS: tuple[str, ...] = (
    "Royal Mail Address",
    "Document Type",
    "Document Number",
    "Account Number",
    "Total Net",
    "Total VAT",
    "Total Gross",
    "Invoice Date",
    "Customer Address",
    "Terms",
    "Payment Type/Code",
    "Legal Entity",
    "Pay By",
    "VAT Rate",
    "VAT Total",
    "VAT Rate.1",
    "VAT Total.1",
    "VAT Rate.2",
    "VAT Total.2",
    "VAT Rate.3",
    "VAT Total.3",
    "VAT Rate.4",
    "VAT Total.4",
    "Docket Number",
    "Posting Date",
    "Poster",
    "Senders Ref",
    "Format",
    "Service",
    "Quantity",
    "Weight (kg)",
    "Unit Cost",
    "Net Value",
    "VAT Code",
)
assert len(ROYALMAIL_RAW_COLUMNS) == 34

# Invoice-level fields propagated to every docket row.
INVOICE_COLUMNS: tuple[str, ...] = (
    "Document Number",
    "Account Number",
    "Invoice Date",
    "Pay By",
    "Total Net",
    "Total VAT",
    "Total Gross",
    "Customer Address",
)

# Docket-level columns kept verbatim.
DOCKET_COLUMNS: tuple[str, ...] = (
    "Docket Number",
    "Posting Date",
    "Poster",
    "Senders Ref",
    "Format",
    "Service",
    "Quantity",
    "Weight (kg)",
    "Unit Cost",
    "Net Value",
    "VAT Code",
)

DERIVED_COLUMNS: tuple[str, ...] = ("Año", "Mes")

ROYALMAIL_COLUMNS: tuple[str, ...] = (
    INVOICE_COLUMNS + DOCKET_COLUMNS + DERIVED_COLUMNS
)
assert len(ROYALMAIL_COLUMNS) == 21

DATE_COLUMNS: tuple[str, ...] = ("Invoice Date", "Posting Date")
INT_COLUMNS: tuple[str, ...] = ("Año", "Mes")
FLOAT_COLUMNS: tuple[str, ...] = (
    "Quantity",
    "Weight (kg)",
    "Unit Cost",
    "Net Value",
    "Total Net",
    "Total VAT",
    "Total Gross",
)

PLAUSIBILITY_NO_NULL: tuple[str, ...] = (
    "Document Number",
    "Docket Number",
    "Net Value",
)
PLAUSIBILITY_MIN_NON_NULL_RATE: dict[str, float] = {
    "Posting Date": 0.95,
    "Quantity": 0.95,
}
PLAUSIBILITY_DATE_RANGE: dict[str, tuple[date, date]] = {
    "Posting Date": (date(2018, 1, 1), date(2035, 12, 31)),
}


def _parse_invoice_date(value: object) -> object:
    """Royal Mail prints `Invoice Date` as `dd MMM yyyy` (e.g. `02 Mar 2026`)."""
    if value is None or value == "" or pd.isna(value):
        return pd.NaT
    if isinstance(value, str):
        s = value.strip()
        parsed = pd.to_datetime(s, format="%d %b %Y", errors="coerce")
        if pd.isna(parsed):
            parsed = pd.to_datetime(s, errors="coerce", dayfirst=True)
        return parsed
    return pd.to_datetime(value, errors="coerce", dayfirst=True)


def _parse_posting_date(value: object) -> object:
    """Posting Date is `dd/mm/yyyy`."""
    if value is None or value == "" or pd.isna(value):
        return pd.NaT
    return pd.to_datetime(value, errors="coerce", dayfirst=True)


def _select_dockets(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    """Split the raw frame into (docket rows, invoice-header dict).

    The invoice header lives on the single row with `Document Number`
    populated; dockets are rows with `Docket Number` populated.
    """
    invoice_rows = df[df["Document Number"].notna()]
    if invoice_rows.empty:
        raise ParserError("no invoice header row (Document Number is null in all rows)")
    if len(invoice_rows) > 1:
        raise ParserError(
            f"expected exactly 1 invoice header row, got {len(invoice_rows)}"
        )
    header = invoice_rows.iloc[0]
    invoice_fields = {col: header[col] for col in INVOICE_COLUMNS}

    dockets = df[df["Docket Number"].notna()].copy()
    return dockets, invoice_fields


def _build_output(
    dockets: pd.DataFrame, invoice_fields: dict[str, object]
) -> pd.DataFrame:
    out = pd.DataFrame(index=range(len(dockets)))
    for col in INVOICE_COLUMNS:
        out[col] = invoice_fields[col]
    for col in DOCKET_COLUMNS:
        out[col] = dockets[col].to_numpy()
    posting = pd.to_datetime(out["Posting Date"].map(_parse_posting_date), errors="coerce")
    out["Año"] = posting.dt.year.astype("Int64")
    out["Mes"] = posting.dt.month.astype("Int64")
    return out[list(ROYALMAIL_COLUMNS)]


def coerce_royalmail_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a 17-col Royal-Mail-shaped DataFrame."""
    df = df.copy()
    df["Invoice Date"] = pd.to_datetime(
        df["Invoice Date"].map(_parse_invoice_date), errors="coerce"
    )
    df["Posting Date"] = pd.to_datetime(
        df["Posting Date"].map(_parse_posting_date), errors="coerce"
    )
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


_FILENAME_RE = re.compile(r"^\d{8}_(\d+)_Invoice_\d+\.csv$", re.IGNORECASE)


class RoyalMailParser:
    carrier: ClassVar[str] = "royalmail"
    expected_columns: ClassVar[tuple[str, ...]] = ROYALMAIL_COLUMNS
    # Match the human-readable formats from the source CSV so the
    # operator's eye can scan the sidecar against the raw invoice.
    export_date_formats: ClassVar[dict[str, str]] = {
        "Invoice Date": "dd mmm yyyy",
        "Posting Date": "dd/mm/yyyy",
    }

    def parse(self, path: Path) -> ParseResult:
        path = Path(path)
        if not path.exists():
            raise ParserError(f"Royal Mail invoice file not found: {path}")

        try:
            df = pd.read_csv(
                path,
                sep="|",
                dtype=str,
                encoding="cp1252",
                keep_default_na=False,
                na_values=[""],
                engine="python",
            )
        except Exception as e:  # noqa: BLE001
            raise ParserError(f"could not read {path.name}: {e}") from e

        assert_schema(df, ROYALMAIL_RAW_COLUMNS)
        dockets, invoice_fields = _select_dockets(df)
        out = _build_output(dockets, invoice_fields)
        out = coerce_royalmail_dtypes(out)
        assert_plausible(
            out,
            no_null=PLAUSIBILITY_NO_NULL,
            min_non_null_rate=PLAUSIBILITY_MIN_NON_NULL_RATE,
            date_range=PLAUSIBILITY_DATE_RANGE,
        )

        invoice_number = self._derive_invoice_number(invoice_fields, path)
        invoice_date = self._derive_invoice_date(invoice_fields, path)
        file_hash = compute_file_hash(path)
        log.info(
            "royalmail parser: %s | %d rows | hash=%s | source=%s",
            invoice_number,
            len(out),
            file_hash[:12],
            path.name,
        )
        return ParseResult(
            carrier=self.carrier,
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            rows=out,
            source_path=path,
            file_hash=file_hash,
        )

    @staticmethod
    def _derive_invoice_number(
        invoice_fields: dict[str, object], path: Path
    ) -> str:
        raw = invoice_fields.get("Document Number")
        if raw is None or (isinstance(raw, float) and pd.isna(raw)):
            # Fall back to the filename (`YYYYMMDD_<docnum>_Invoice_<acct>.csv`).
            m = _FILENAME_RE.match(path.name)
            if not m:
                raise ParserError(
                    f"{path.name}: no Document Number in data and filename "
                    "doesn't match YYYYMMDD_<docnum>_Invoice_<acct>.csv"
                )
            return m.group(1)
        return str(raw).strip()

    @staticmethod
    def _derive_invoice_date(
        invoice_fields: dict[str, object], path: Path
    ) -> date:
        parsed = _parse_invoice_date(invoice_fields.get("Invoice Date"))
        if pd.isna(parsed):
            raise ParserError(
                f"{path.name}: Invoice Date is missing or unparseable: "
                f"{invoice_fields.get('Invoice Date')!r}"
            )
        return parsed.date()
