"""Golden test for Correos Express: parser output must match the rows the
user pastes into `Análisis Envíos Correos Express V2.xlsx` `Datos` today.

Setup once:
  python scripts/extract_correos_golden.py --period <tag>
"""

from __future__ import annotations


import pandas as pd
import pytest

from courier_automation.parsers.correos import CorreosParser
from tests.conftest import CORREOS_GOLDEN_DIR, CORREOS_RAW_DIR


@pytest.mark.golden
def test_parser_output_matches_golden():
    if not CORREOS_GOLDEN_DIR.exists():
        pytest.skip(f"no golden dir at {CORREOS_GOLDEN_DIR}")
    parquets = sorted(CORREOS_GOLDEN_DIR.glob("*-datos.parquet"))
    if not parquets:
        pytest.skip(
            f"no *-datos.parquet under {CORREOS_GOLDEN_DIR} "
            "— run scripts/extract_correos_golden.py"
        )
    golden_path = parquets[-1]

    if not CORREOS_RAW_DIR.exists() or not list(CORREOS_RAW_DIR.glob("*.xlsx")):
        pytest.skip(f"no raw Correos invoices under {CORREOS_RAW_DIR}")

    golden = pd.read_parquet(golden_path)
    parser = CorreosParser()
    parsed = pd.concat(
        (parser.parse(p).rows for p in sorted(CORREOS_RAW_DIR.glob("*.xlsx"))),
        ignore_index=True,
    )

    # Each row is a unique shipment line. `Nº ENVIO` is the natural key.
    # Compare the *intersection* of keys: empirically the user manually
    # filters out a small fraction of rows when pasting (~5% in observed
    # data), so we don't require parsed ⊇ golden — only that the rows
    # which DO appear in both match exactly.
    key_cols = ["Nº ENVIO"]
    parsed["_key"] = parsed["Nº ENVIO"].astype(str).str.strip()
    golden["_key"] = golden["Nº ENVIO"].astype(str).str.strip()
    parsed_keys = set(parsed["_key"])
    golden_keys = set(golden["_key"])
    common = parsed_keys & golden_keys
    assert common, "no shipment IDs in common between parsed and golden"

    missing_from_parsed = golden_keys - parsed_keys
    assert not missing_from_parsed, (
        f"{len(missing_from_parsed)} Datos rows have shipment IDs the parser "
        f"didn't produce: {list(missing_from_parsed)[:5]} "
        "(expected the parser to be a superset of Datos)"
    )

    parsed_sorted = (
        parsed[parsed["_key"].isin(common)]
        .drop(columns="_key")
        .sort_values(key_cols)
        .reset_index(drop=True)
    )
    golden_sorted = (
        golden[golden["_key"].isin(common)]
        .drop(columns="_key")
        .sort_values(key_cols)
        .reset_index(drop=True)
    )
    assert list(parsed_sorted.columns) == list(golden_sorted.columns)

    # Operator post-processing in Datos that the parser can't replicate:
    # - phone columns get a `+34` prefix typed in by hand
    # - `Column58` (ISO country) is mostly derivable from `C. PAIS` but
    #   the operator overrides ~0.15% of values manually.
    drop = ["TEL. REMITENTE", "TEL. DESTINATARIO", "Column58"]
    pd.testing.assert_frame_equal(
        parsed_sorted.drop(columns=drop),
        golden_sorted.drop(columns=drop),
        check_dtype=False,
        rtol=1e-9,
        atol=1e-9,
    )
