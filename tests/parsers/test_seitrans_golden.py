"""Golden test for Seitrans: parser output must match the rows the user
pastes into `Análisis envíos Seitrans.xlsx` `Datos` today.

Setup once:
  python scripts/extract_seitrans_golden.py --period <tag>

Skipped when the snapshot or raw fixtures are missing — fresh checkouts run
cleanly without the production data.
"""

from __future__ import annotations


import pandas as pd
import pytest

from courier_automation.parsers.seitrans import SeitransParser
from tests.conftest import SEITRANS_GOLDEN_DIR, SEITRANS_RAW_DIR


@pytest.mark.golden
def test_parser_output_matches_golden():
    if not SEITRANS_GOLDEN_DIR.exists():
        pytest.skip(f"no golden dir at {SEITRANS_GOLDEN_DIR}")
    parquets = sorted(SEITRANS_GOLDEN_DIR.glob("*-datos.parquet"))
    if not parquets:
        pytest.skip(
            f"no *-datos.parquet snapshots under {SEITRANS_GOLDEN_DIR} "
            "— run scripts/extract_seitrans_golden.py"
        )
    golden_path = parquets[-1]

    if not SEITRANS_RAW_DIR.exists() or not list(SEITRANS_RAW_DIR.glob("*.xlsx")):
        pytest.skip(f"no raw Seitrans invoices under {SEITRANS_RAW_DIR}")

    golden = pd.read_parquet(golden_path)
    parser = SeitransParser()
    parsed = pd.concat(
        (parser.parse(p).rows for p in sorted(SEITRANS_RAW_DIR.glob("*.xlsx"))),
        ignore_index=True,
    )

    # Each row in Datos is one shipment line. SPEDIZIONE NUMERO is the
    # natural per-line key; DOCUMENTO NUMERO disambiguates across documents.
    key_cols = ["DOCUMENTO NUMERO", "SPEDIZIONE NUMERO"]
    parsed_keys = set(map(tuple, parsed[key_cols].astype(str).to_numpy()))
    golden_keys = set(map(tuple, golden[key_cols].astype(str).to_numpy()))
    missing_in_parsed = golden_keys - parsed_keys
    missing_in_golden = parsed_keys - golden_keys
    assert not missing_in_parsed, (
        f"{len(missing_in_parsed)} rows in golden missing from parsed: "
        f"{list(missing_in_parsed)[:5]}"
    )
    assert not missing_in_golden, (
        f"{len(missing_in_golden)} rows in parsed missing from golden: "
        f"{list(missing_in_golden)[:5]}"
    )

    parsed_sorted = parsed.sort_values(key_cols).reset_index(drop=True)
    golden_sorted = golden.sort_values(key_cols).reset_index(drop=True)
    assert list(parsed_sorted.columns) == list(golden_sorted.columns), (
        "column order differs between parser output and golden snapshot"
    )

    # `Q Expediciones` is excluded from the golden comparison: the user marks
    # 1 only on the *first global* occurrence of a SPEDIZIONE NUMERO across
    # all of Datos, but the parser only sees one file at a time and can't
    # know whether a shipment number was already counted in a previous
    # invoice. The parser produces per-file dedup as a best effort; cross-
    # file dedup is left to whoever consumes Datos. ~8% of fixture rows
    # differ for this reason; the rest of the columns must match exactly.
    drop = ["Q Expediciones"]
    pd.testing.assert_frame_equal(
        parsed_sorted.drop(columns=drop),
        golden_sorted.drop(columns=drop),
        check_dtype=False,
        rtol=1e-9,
        atol=1e-9,
    )
