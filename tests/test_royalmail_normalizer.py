"""Tests for the Royal Mail unified normalizer.

Royal Mail stays at docket grain — Quantity maps to bultos_count, no row
explosion (see the `royalmail-grain-decision` project note). Penalty/admin
lines must be rejected, not kept; negative Net Value must not be silently
kept (unified.build reroutes valid ones to refunds).
"""
from __future__ import annotations

import pandas as pd

from courier_automation.parsers.royalmail import ROYALMAIL_COLUMNS
from unified.normalizers.royalmail import normalize
from unified.schema import UNIFIED_COLUMN_NAMES


def _royalmail_frame() -> pd.DataFrame:
    """A 3-row docket frame: a normal Tracked docket, an admin penalty,
    and a negative-value credit."""
    rows = [
        # normal Tracked 24 docket
        {
            "Document Number": "9075855419", "Account Number": "0751634000",
            "Invoice Date": pd.Timestamp("2026-05-11"), "Pay By": "2026-05-18",
            "Total Net": 635.85, "Total VAT": 127.17, "Total Gross": 763.02,
            "Customer Address": "HUDDERSFIELD HD1 5DG",
            "Docket Number": "7155969914", "Posting Date": pd.Timestamp("2026-05-05"),
            "Poster": "HD1 5DG", "Senders Ref": "0384", "Format": "",
            "Service": "ROYAL MAIL TRACKED 24", "Quantity": 65.0,
            "Weight (kg)": 0.0, "Unit Cost": float("nan"), "Net Value": 270.92,
            "VAT Code": "T", "Año": 2026, "Mes": 5,
        },
        # admin penalty — not a shipment, must be rejected
        {
            "Document Number": "9075855419", "Account Number": "0751634000",
            "Invoice Date": pd.Timestamp("2026-05-11"), "Pay By": "2026-05-18",
            "Total Net": 635.85, "Total VAT": 127.17, "Total Gross": 763.02,
            "Customer Address": "HUDDERSFIELD HD1 5DG",
            "Docket Number": "7156064415", "Posting Date": pd.Timestamp("2026-04-27"),
            "Poster": "HD1 5DG", "Senders Ref": "IPROL Week 05", "Format": "",
            "Service": "Sales Order Admin Charge LL or P", "Quantity": 5.0,
            "Weight (kg)": 0.0, "Unit Cost": 1.25, "Net Value": 6.25,
            "VAT Code": "T", "Año": 2026, "Mes": 4,
        },
        # negative Net Value credit — must NOT be silently kept
        {
            "Document Number": "9075855419", "Account Number": "0751634000",
            "Invoice Date": pd.Timestamp("2026-05-11"), "Pay By": "2026-05-18",
            "Total Net": 635.85, "Total VAT": 127.17, "Total Gross": 763.02,
            "Customer Address": "HUDDERSFIELD HD1 5DG",
            "Docket Number": "7155490396", "Posting Date": pd.Timestamp("2026-04-29"),
            "Poster": "HD1 5DG", "Senders Ref": "8118", "Format": "",
            "Service": "ROYAL MAIL TRACKED 24", "Quantity": 1.0,
            "Weight (kg)": 0.0, "Unit Cost": float("nan"), "Net Value": -3.99,
            "VAT Code": "T", "Año": 2026, "Mes": 4,
        },
    ]
    return pd.DataFrame(rows, columns=list(ROYALMAIL_COLUMNS))


def test_output_has_canonical_columns():
    out = normalize(_royalmail_frame(), source_file="2026-05.parquet")
    expected = set(UNIFIED_COLUMN_NAMES) - {
        # EUR columns are added later by unified.build._add_eur_columns.
        "fx_rate_to_eur", "total_net_eur", "base_cost_eur",
        "fuel_surcharge_eur", "other_surcharges_eur",
    }
    assert expected.issubset(set(out.columns))
    assert "_reject_reason" in out.columns


def test_docket_grain_and_constants():
    out = normalize(_royalmail_frame(), source_file="2026-05.parquet")
    assert (out["carrier"] == "royalmail").all()
    assert (out["currency"] == "GBP").all()
    assert (out["origin_country"] == "GB").all()
    assert out["destination_country"].isna().all()
    # Quantity -> bultos_count, docket grain (no row explosion).
    assert out["bultos_count"].tolist() == [65, 5, 1]
    assert out["shipment_id"].tolist() == ["7155969914", "7156064415", "7155490396"]
    assert str(out["bultos_count"].dtype) == "Int64"


def test_normal_docket_kept():
    out = normalize(_royalmail_frame(), source_file="x.parquet")
    assert pd.isna(out.loc[0, "_reject_reason"])
    assert out.loc[0, "service_class"] == "parcel"
    assert out.loc[0, "total_net"] == 270.92
    assert out.loc[0, "base_cost"] == 270.92


def test_admin_penalty_rejected():
    out = normalize(_royalmail_frame(), source_file="x.parquet")
    assert out.loc[1, "_reject_reason"] == "service not classifiable"


def test_negative_net_value_not_silently_kept():
    out = normalize(_royalmail_frame(), source_file="x.parquet")
    # Negative Net Value trips the reject filter; unified.build then
    # reroutes it to refunds (valid shipment_id + posting_date).
    assert out.loc[2, "_reject_reason"] == "total_net <= 0"
    assert out.loc[2, "total_net"] == -3.99


def test_empty_frame_returns_canonical_shape():
    out = normalize(pd.DataFrame(columns=list(ROYALMAIL_COLUMNS)), source_file="x")
    assert "_reject_reason" in out.columns
    assert len(out) == 0
