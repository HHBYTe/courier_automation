"""Golden test for Wwex (US).

Setup once:
  python scripts/extract_wwex_golden.py --period <tag>
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from courier_automation.parsers.wwex import WwexParser

WWEX_GOLDEN_DIR = (
    Path(__file__).resolve().parent.parent / "fixtures" / "wwex" / "golden"
)
WWEX_RAW_DIR = (
    Path(__file__).resolve().parent.parent / "fixtures" / "wwex" / "raw"
)


@pytest.mark.golden
def test_parser_output_matches_golden():
    if not WWEX_GOLDEN_DIR.exists():
        pytest.skip(f"no golden dir at {WWEX_GOLDEN_DIR}")
    parquets = sorted(WWEX_GOLDEN_DIR.glob("*-data.parquet"))
    if not parquets:
        pytest.skip(
            f"no *-data.parquet under {WWEX_GOLDEN_DIR} "
            "— run scripts/extract_wwex_golden.py"
        )
    golden_path = parquets[-1]

    raws = sorted(WWEX_RAW_DIR.glob("*.xlsx"))
    if not raws:
        pytest.skip(f"no raw Wwex .xlsx fixtures under {WWEX_RAW_DIR}")

    golden = pd.read_parquet(golden_path)
    parser = WwexParser()
    parsed = pd.concat(
        (parser.parse(p).rows for p in raws), ignore_index=True,
    )

    parsed = parsed.copy()
    golden = golden.copy()
    parsed["_key"] = parsed["Tracking#"].astype(str).str.strip()
    golden["_key"] = golden["Tracking#"].astype(str).str.strip()
    common = set(parsed["_key"]) & set(golden["_key"])
    assert common, "no Tracking# in common between parsed and golden"

    parsed_sorted = (
        parsed[parsed["_key"].isin(common)]
        .drop(columns="_key")
        .sort_values("Tracking#")
        .drop_duplicates(subset=["Tracking#"], keep="first")
        .reset_index(drop=True)
    )
    golden_sorted = (
        golden[golden["_key"].isin(common)]
        .drop(columns="_key")
        .sort_values("Tracking#")
        .drop_duplicates(subset=["Tracking#"], keep="first")
        .reset_index(drop=True)
    )
    assert list(parsed_sorted.columns) == list(golden_sorted.columns)

    # Wwex's historical Data is heavily operator-post-processed: blank
    # cells get filled from external sources (Ship Date), addresses get
    # cleaned, weights/charges get occasional manual overrides, country
    # → DOM/INT gets manually overridden. The golden test verifies only
    # the columns the operator doesn't touch:
    #   - `Source System`: constant
    #   - `Account#`: 1:1 rename of ACCOUNT_NO
    #   - `Tracking#`: 1:1 rename of TRACKING_NO (the join key)
    #   - `Package Count`: 1:1 rename of PACKAGE_COUNT
    # That's a narrow contract, but it's the contract that's actually
    # stable. The full row-shape correctness is verified by the
    # synthetic test (`test_real_wwex_xlsx_parses_cleanly`).
    stable = ["Source System", "Account#", "Tracking#", "Package Count"]
    pd.testing.assert_frame_equal(
        parsed_sorted[stable],
        golden_sorted[stable],
        check_dtype=False,
    )
