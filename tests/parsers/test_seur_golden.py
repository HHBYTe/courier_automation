"""Golden test: parser output must match the rows the user pastes by hand
into the historical workbook today.

Setup once: run `python scripts/extract_seur_golden.py` against the production
`Datos` sheet. That writes `tests/fixtures/seur/golden/<period>-datos.parquet`.
Then place the matching raw invoice(s) into `tests/fixtures/seur/raw/`.

When either fixture is missing, the test is skipped — so a fresh checkout
runs cleanly without the production data.
"""

from __future__ import annotations


import pandas as pd
import pytest

from courier_automation.parsers.seur import SeurParser
from tests.conftest import SEUR_GOLDEN_DIR, SEUR_RAW_DIR


@pytest.mark.golden
def test_parser_output_matches_golden():
    """Compares parser output against any `*-datos.parquet` snapshot in the
    golden dir. Skipped when no snapshot exists yet — run
    `scripts/extract_seur_golden.py --period <tag>` to land one."""
    if not SEUR_GOLDEN_DIR.exists():
        pytest.skip(f"no golden dir at {SEUR_GOLDEN_DIR}")
    parquets = sorted(SEUR_GOLDEN_DIR.glob("*-datos.parquet"))
    if not parquets:
        pytest.skip(
            f"no *-datos.parquet snapshots under {SEUR_GOLDEN_DIR} "
            "— run scripts/extract_seur_golden.py"
        )
    golden_path = parquets[-1]  # most recent snapshot wins

    if not SEUR_RAW_DIR.exists() or not list(SEUR_RAW_DIR.glob("*.xlsx")):
        pytest.skip(f"no raw Seur invoices under {SEUR_RAW_DIR}")

    golden = pd.read_parquet(golden_path)
    parser = SeurParser()

    parsed_frames: list[pd.DataFrame] = []
    for raw_path in sorted(SEUR_RAW_DIR.glob("*.xlsx")):
        try:
            result = parser.parse(raw_path)
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"parser failed on {raw_path.name}: {e}")
        parsed_frames.append(result.rows)
    parsed = pd.concat(parsed_frames, ignore_index=True)

    # Set comparison on (Numero Factura, Numero Linea) — order-independent
    key_cols = ["Numero Factura", "Numero Linea"]
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

    # Element-wise comparison after sorting
    parsed_sorted = parsed.sort_values(key_cols).reset_index(drop=True)
    golden_sorted = golden.sort_values(key_cols).reset_index(drop=True)
    assert list(parsed_sorted.columns) == list(golden_sorted.columns), (
        "column order differs between parser output and golden snapshot"
    )
    pd.testing.assert_frame_equal(
        parsed_sorted,
        golden_sorted,
        check_dtype=False,  # historical datos may have looser dtypes
        rtol=1e-9,
        atol=1e-9,
    )
