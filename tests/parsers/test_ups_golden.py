"""Golden test for UPS (UK).

Setup once:
  python scripts/extract_ups_golden.py --period <tag>

Skipped when the snapshot or raw fixtures are missing.
"""

from __future__ import annotations


import pandas as pd
import pytest

from courier_automation.parsers.ups import UpsParser
from tests.conftest import UPS_GOLDEN_DIR, UPS_RAW_DIR


@pytest.mark.golden
def test_parser_output_matches_golden():
    if not UPS_GOLDEN_DIR.exists():
        pytest.skip(f"no golden dir at {UPS_GOLDEN_DIR}")
    parquets = sorted(UPS_GOLDEN_DIR.glob("*-data.parquet"))
    if not parquets:
        pytest.skip(
            f"no *-data.parquet under {UPS_GOLDEN_DIR} "
            "— run scripts/extract_ups_golden.py"
        )
    golden_path = parquets[-1]

    if not UPS_RAW_DIR.exists() or not list(UPS_RAW_DIR.glob("*.csv")):
        pytest.skip(f"no raw UPS invoices under {UPS_RAW_DIR}")

    golden = pd.read_parquet(golden_path)
    parser = UpsParser()
    parsed = pd.concat(
        (parser.parse(p).rows for p in sorted(UPS_RAW_DIR.glob("*.csv"))),
        ignore_index=True,
    )

    # UPS rows are uniquely keyed by Tracking Number + Charge Description.
    # Compare the *intersection* of keys — the operator may filter charge
    # lines when pasting (similar to Correos).
    parsed = parsed.copy()
    golden = golden.copy()
    parsed["_key"] = (
        parsed["Tracking Number"].astype(str).str.strip()
        + "|"
        + parsed["Charge Description"].astype(str).str.strip()
    )
    golden["_key"] = (
        golden["Tracking Number"].astype(str).str.strip()
        + "|"
        + golden["Charge Description"].astype(str).str.strip()
    )
    parsed_keys = set(parsed["_key"])
    golden_keys = set(golden["_key"])
    common = parsed_keys & golden_keys
    assert common, "no rows in common between parsed and golden"

    missing_from_parsed = golden_keys - parsed_keys
    assert not missing_from_parsed, (
        f"{len(missing_from_parsed)} Datos rows have keys the parser didn't "
        f"produce: {list(missing_from_parsed)[:5]}"
    )

    parsed_sorted = (
        parsed[parsed["_key"].isin(common)]
        .drop(columns="_key")
        .sort_values(["Tracking Number", "Charge Description"])
        .reset_index(drop=True)
    )
    golden_sorted = (
        golden[golden["_key"].isin(common)]
        .drop(columns="_key")
        .sort_values(["Tracking Number", "Charge Description"])
        .reset_index(drop=True)
    )
    assert list(parsed_sorted.columns) == list(golden_sorted.columns)

    # Place Holder columns are empty padding; exclude from comparison.
    place_holders = [c for c in parsed_sorted.columns if c.startswith("Place Holder")]
    parsed_cmp = _normalize_numeric_strings(parsed_sorted.drop(columns=place_holders))
    golden_cmp = _normalize_numeric_strings(golden_sorted.drop(columns=place_holders))
    pd.testing.assert_frame_equal(
        parsed_cmp, golden_cmp, check_dtype=False, rtol=1e-9, atol=1e-9,
    )


def _normalize_numeric_strings(df: pd.DataFrame) -> pd.DataFrame:
    """Strip leading zeros from numeric-looking string values, on both
    parsed and golden sides. UPS has many code columns (Line Item Number,
    Pickup Record Number, …) that the raw CSV stores as `00000` but Excel
    auto-coerces to `0` when pasted into Data. Normalising both sides to
    canonical-int-as-string form makes comparison sound without having to
    enumerate every code column in the parser's INT_COLUMNS list."""
    df = df.copy()
    for col in df.columns:
        if df[col].dtype.name != "string":
            continue

        def _norm(v: object) -> object:
            if v is None:
                return None
            s = str(v).strip()
            if s == "" or s == "<NA>":
                return None
            if s.lstrip("-").isdigit():
                return str(int(s))
            # `0.00`, `5.50`, `-1.00` — string-encoded floats.
            try:
                f = float(s)
            except ValueError:
                return s
            return str(int(f)) if f.is_integer() else str(f)

        df[col] = df[col].map(_norm).astype("string")
    return df
