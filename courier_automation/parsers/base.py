"""Adapter interface and shared helpers for per-courier parsers."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import ClassVar, Protocol, runtime_checkable

import pandas as pd


class SchemaMismatch(ValueError):
    """Raised when a courier file's columns don't match the expected schema.

    The message includes a diff (missing / added / reordered) so the user can
    fix the parser's `expected_columns` or escalate to the courier.
    """


class ParserError(ValueError):
    """Generic parser error (file unreadable, sheet missing, etc.)."""


@dataclass(frozen=True)
class ParseResult:
    carrier: str
    invoice_number: str
    invoice_date: date
    rows: pd.DataFrame
    source_path: Path
    file_hash: str

    @property
    def row_count(self) -> int:
        return len(self.rows)


@runtime_checkable
class CourierParser(Protocol):
    carrier: ClassVar[str]
    expected_columns: ClassVar[tuple[str, ...]]

    def parse(self, path: Path) -> ParseResult: ...


def assert_schema(df: pd.DataFrame, expected: tuple[str, ...]) -> None:
    """Raise SchemaMismatch with a clean diff if df.columns != expected."""
    actual = tuple(str(c) for c in df.columns)
    if actual == expected:
        return

    expected_set = set(expected)
    actual_set = set(actual)
    missing = [c for c in expected if c not in actual_set]
    added = [c for c in actual if c not in expected_set]
    reordered = [c for c in actual if c in expected_set] != [
        c for c in expected if c in actual_set
    ]

    parts: list[str] = []
    if missing:
        parts.append(f"missing={missing}")
    if added:
        parts.append(f"added={added}")
    if not missing and not added and reordered:
        parts.append("columns reordered")
    raise SchemaMismatch(
        f"schema mismatch ({len(actual)} cols vs expected {len(expected)}): "
        + "; ".join(parts)
    )


def compute_file_hash(path: Path, *, chunk_size: int = 1 << 20) -> str:
    """SHA-256 hex digest of the file's bytes. Stable across runs."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def to_clean_string(value: object) -> object:
    """Normalise a single value to a clean string, dropping the spurious `.0`
    that Excel introduces when it auto-stores a code field as a number.

    Examples: 685 -> "685", 685.0 -> "685", "685.0" -> "685", "ABC" -> "ABC".
    Returns None for nulls so the resulting Series can be `astype("string")`.

    Used by every per-courier parser to keep raw-vs-Datos comparison
    type-consistent (Excel auto-converts code columns to numbers in some files
    but not others; cleaning uniformly is what makes the golden test sound).
    """
    if value is None:
        return None
    if isinstance(value, float):
        if pd.isna(value):
            return None
        if value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "" or stripped == "<NA>":
            return None
        if stripped.endswith(".0"):
            core = stripped[:-2]
            if core.lstrip("-").isdigit():
                return core
        return stripped
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


# Seur uses 10 digits + a 1-3 letter prefix + 7 digits. Observed prefixes:
# D (domestic ES), AD (Andorra), FR (France). The regex is permissive about
# the letters so a new prefix from Seur doesn't break ingest silently.
_SEUR_INVOICE_RE = re.compile(r"(\d{10}[A-Z]{1,3}\d{7})", re.IGNORECASE)


def extract_seur_invoice_number(filename: str) -> str:
    """Parse the invoice number out of a Seur filename like
    `0289992025D0289264.xlsx` → `0289992025D0289264`,
    `0289992025AD0001394.xlsx` → `0289992025AD0001394`,
    `0289992025FR0020268.xlsx` → `0289992025FR0020268`.

    Accepts a full path or just a basename.
    """
    stem = Path(filename).stem
    m = _SEUR_INVOICE_RE.search(stem)
    if not m:
        raise ParserError(
            f"Seur invoice number not found in filename: {filename!r} "
            "(expected pattern like 0289992025D0289264, 0289992025AD0001394, "
            "or 0289992025FR0020268)"
        )
    return m.group(1).upper()
