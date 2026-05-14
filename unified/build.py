"""Build the unified shipments fact table from per-carrier parquets.

Reads `data/<carrier>/*.parquet` for every implemented carrier, applies
the carrier-specific normalizer, splits each carrier's rows into three
buckets, adds frozen-rate EUR-normalized columns (see `unified.fx_rates`),
validates the canonical schema, and writes:

  unified/output/unified_shipments.parquet  — kept rows (= true shipments)
  unified/output/refunds.parquet            — refund/credit rows (negative
                                              total_net, valid shipment_id
                                              and posting_date), same
                                              canonical schema. Join to
                                              shipments on shipment_id.
  unified/output/rejections.parquet         — everything else dropped,
                                              with `_reject_reason`
  unified/output/unified_shipments.csv      — kept rows, for inspection
  unified/output/manifest.json              — counts + date ranges

Run with:  python -m unified.build
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import sys
import time
from pathlib import Path

import pandas as pd

from unified import schema
from unified.fx_rates import FROZEN_RATE_AS_OF, RATE_TO_EUR
from unified.normalizers import REGISTRY

log = logging.getLogger("unified.build")

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "unified" / "output"


def _discover(carrier: str) -> list[Path]:
    return sorted(
        Path(p) for p in glob.glob(str(DATA_DIR / carrier / "*.parquet"))
    )


def _normalize_carrier(
    carrier: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """Return (kept, refunds, rejected, stats) for one carrier."""
    files = _discover(carrier)
    if not files:
        log.warning("%s: no parquets found in %s", carrier, DATA_DIR / carrier)
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {
            "carrier": carrier, "files": 0, "raw_rows": 0,
            "kept": 0, "refunds": 0, "rejected": 0, "reject_breakdown": {},
        }

    fn = REGISTRY[carrier]
    frames: list[pd.DataFrame] = []
    raw_rows = 0
    for path in files:
        try:
            raw = pd.read_parquet(path)
        except Exception as e:  # noqa: BLE001
            log.error("  SKIP %s: %s", path.name, e)
            continue
        raw_rows += len(raw)
        normalized = fn(raw, source_file=path.name)
        frames.append(normalized)

    if not frames:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {
            "carrier": carrier, "files": len(files), "raw_rows": raw_rows,
            "kept": 0, "refunds": 0, "rejected": 0, "reject_breakdown": {},
        }

    combined = pd.concat(frames, ignore_index=True)
    keep_mask = combined["_reject_reason"].isna()
    kept = combined.loc[keep_mask].drop(columns=["_reject_reason"])

    rest = combined.loc[~keep_mask].copy()
    # Refund/credit pattern: negative billing referencing a real shipment.
    # Lives on a separate axis from "rejected garbage" so a Power BI user
    # can compute net cost as SUM(shipments.total_net) + SUM(refunds.total_net).
    refund_mask = (
        (rest["total_net"] < 0)
        & rest["shipment_id"].notna()
        & rest["posting_date"].notna()
    )
    refunds = rest.loc[refund_mask].drop(columns=["_reject_reason"]).copy()
    rejected = rest.loc[~refund_mask].copy()

    # Carriers issue credit lines inconsistently: SEUR negates only the
    # money, Dachser sign-flips the whole row (weight, bultos too). The
    # money columns SHOULD stay negative — that's what makes
    # Net Cost = Gross + Refund work — but weight_kg / bultos_count are
    # descriptive, not additive, so negative values there are meaningless.
    # Force them positive so refund rows are consistent across carriers.
    for col in ("weight_kg", "bultos_count"):
        refunds[col] = refunds[col].abs()

    breakdown = (
        rejected["_reject_reason"].value_counts(dropna=False).to_dict()
        if not rejected.empty else {}
    )
    stats = {
        "carrier": carrier,
        "files": len(files),
        "raw_rows": raw_rows,
        "kept": int(len(kept)),
        "refunds": int(len(refunds)),
        "rejected": int(len(rejected)),
        "reject_breakdown": {str(k): int(v) for k, v in breakdown.items()},
    }
    log.info(
        "  %s: %d files, %d raw -> %d kept, %d refunds, %d rejected",
        carrier, len(files), raw_rows, len(kept), len(refunds), len(rejected),
    )
    return kept, refunds, rejected, stats


def _add_eur_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add frozen-rate EUR-normalized companion columns.

    Every amount is converted with a single frozen rate per currency
    from `unified.fx_rates`. EUR rows convert at 1.0 (no-op). See that
    module for the historical-distortion tradeoff this accepts.
    """
    df = df.copy()
    unknown = sorted(set(df["currency"].dropna()) - set(RATE_TO_EUR))
    if unknown:
        raise SystemExit(
            f"no frozen FX rate for currency/currencies {unknown} — "
            f"add them to unified/fx_rates.py"
        )
    rate = df["currency"].map(RATE_TO_EUR).astype("float64")
    df["fx_rate_to_eur"] = rate
    df["total_net_eur"] = (df["total_net"] * rate).astype("float64")
    df["base_cost_eur"] = (df["base_cost"] * rate).astype("float64")
    df["fuel_surcharge_eur"] = (df["fuel_surcharge"] * rate).astype("float64")
    df["other_surcharges_eur"] = (df["other_surcharges"] * rate).astype("float64")
    return df


def _write_outputs(
    kept_frames: list[pd.DataFrame],
    refund_frames: list[pd.DataFrame],
    rejected_frames: list[pd.DataFrame],
    stats: list[dict],
    out_dir: Path,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    if kept_frames:
        kept = pd.concat(kept_frames, ignore_index=True)
        kept = _add_eur_columns(kept)
        kept = schema.coerce(kept)
        errors = schema.validate(kept)
        if errors:
            log.error("schema validation failed:")
            for e in errors:
                log.error("  %s", e)
            raise SystemExit(2)
        kept = kept.sort_values(
            by=["posting_date", "carrier", "shipment_id"],
            na_position="last", kind="stable",
        ).reset_index(drop=True)
        kept.to_parquet(out_dir / "unified_shipments.parquet", index=False)
        kept.to_csv(
            out_dir / "unified_shipments.csv", index=False, encoding="utf-8-sig"
        )
        log.info("wrote %d kept rows to %s", len(kept), out_dir.name)
    else:
        log.warning("no kept rows")
        kept = schema.empty_frame()

    if refund_frames:
        refunds = pd.concat(refund_frames, ignore_index=True)
        refunds = _add_eur_columns(refunds)
        # Refunds share the canonical schema with shipments — coerce so
        # Power BI sees identical dtypes on the two tables.
        refunds = schema.coerce(refunds)
        refunds = refunds.sort_values(
            by=["posting_date", "carrier", "shipment_id"],
            na_position="last", kind="stable",
        ).reset_index(drop=True)
        refunds.to_parquet(out_dir / "refunds.parquet", index=False)
        refunds.to_csv(out_dir / "refunds.csv", index=False, encoding="utf-8-sig")
        log.info("wrote %d refund rows to %s", len(refunds), out_dir.name)
    else:
        refunds = schema.empty_frame()

    if rejected_frames:
        rejected = pd.concat(rejected_frames, ignore_index=True)
        rejected.to_parquet(out_dir / "rejections.parquet", index=False)
        log.info("wrote %d rejected rows to %s", len(rejected), out_dir.name)

    manifest = {
        "built_at": pd.Timestamp.utcnow().isoformat(),
        "fx_rates": {"as_of": FROZEN_RATE_AS_OF, "rate_to_eur": RATE_TO_EUR},
        "total_kept": int(len(kept)),
        "total_refunds": int(len(refunds)),
        "total_rejected": sum(int(len(f)) for f in rejected_frames),
        "carriers": stats,
        "kept_summary": _summarize(kept) if len(kept) else {},
        "refunds_summary": _summarize_refunds(refunds) if len(refunds) else {},
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log.info("wrote manifest to %s/manifest.json", out_dir.name)


def _summarize_refunds(refunds: pd.DataFrame) -> dict:
    by_carrier = (
        refunds.groupby("carrier")
            .agg(
                rows=("shipment_id", "size"),
                total=("total_net", "sum"),
                total_eur=("total_net_eur", "sum"),
            )
            .reset_index()
    )
    return {
        "by_carrier": [
            {
                "carrier": r["carrier"],
                "rows": int(r["rows"]),
                "total": float(r["total"]),
                "total_eur": float(r["total_eur"]),
            }
            for _, r in by_carrier.iterrows()
        ],
    }


def _summarize(kept: pd.DataFrame) -> dict:
    by_carrier = (
        kept.groupby("carrier")
            .agg(
                rows=("shipment_id", "size"),
                bultos=("bultos_count", "sum"),
                total_net=("total_net", "sum"),
                total_net_eur=("total_net_eur", "sum"),
                date_min=("posting_date", "min"),
                date_max=("posting_date", "max"),
            )
            .reset_index()
    )
    rows = []
    for _, r in by_carrier.iterrows():
        rows.append({
            "carrier": r["carrier"],
            "rows": int(r["rows"]),
            "bultos": int(r["bultos"]) if pd.notna(r["bultos"]) else 0,
            "total_net": float(r["total_net"]) if pd.notna(r["total_net"]) else 0.0,
            "total_net_eur": float(r["total_net_eur"]) if pd.notna(r["total_net_eur"]) else 0.0,
            "date_min": str(r["date_min"].date()) if pd.notna(r["date_min"]) else None,
            "date_max": str(r["date_max"].date()) if pd.notna(r["date_max"]) else None,
        })
    by_currency = kept.groupby("currency").size().to_dict()
    return {
        "by_carrier": rows,
        "by_currency": {str(k): int(v) for k, v in by_currency.items()},
        "date_range": {
            "min": str(kept["posting_date"].min().date()) if pd.notna(kept["posting_date"].min()) else None,
            "max": str(kept["posting_date"].max().date()) if pd.notna(kept["posting_date"].max()) else None,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--carriers", nargs="*", default=None,
        help="restrict to a subset of carriers (default: all)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s"
    )

    carriers = args.carriers or list(schema.CARRIERS)
    unknown = [c for c in carriers if c not in REGISTRY]
    if unknown:
        log.error("unknown carriers: %s", unknown)
        return 2

    t0 = time.time()
    log.info("building unified shipments from %s", DATA_DIR)
    kept_frames: list[pd.DataFrame] = []
    refund_frames: list[pd.DataFrame] = []
    rejected_frames: list[pd.DataFrame] = []
    stats: list[dict] = []
    for c in carriers:
        kept, refunds, rejected, stat = _normalize_carrier(c)
        stats.append(stat)
        if not kept.empty:
            kept_frames.append(kept)
        if not refunds.empty:
            refund_frames.append(refunds)
        if not rejected.empty:
            rejected_frames.append(rejected)

    _write_outputs(kept_frames, refund_frames, rejected_frames, stats, OUT_DIR)
    log.info("done in %.1fs", time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
