"""Generate dimension parquets for the Power BI star schema.

Reads `unified/output/unified_shipments.parquet` to size the date
dimension to the data's actual range. Writes:

  powerbi/output/dim_date.parquet
  powerbi/output/dim_carrier.parquet
  powerbi/output/dim_service_class.parquet

Re-run after any unified build that changes the date range.

Run with:  python -m powerbi.build_dims
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

log = logging.getLogger("powerbi.build_dims")

ROOT = Path(__file__).resolve().parent.parent
UNIFIED_OUT = ROOT / "unified" / "output"
OUT_DIR = ROOT / "powerbi" / "output"


def _build_date_dim(min_date: pd.Timestamp, max_date: pd.Timestamp) -> pd.DataFrame:
    # Round to full years so YoY measures always have a comparable prior period.
    start = pd.Timestamp(year=min_date.year, month=1, day=1)
    end = pd.Timestamp(year=max_date.year, month=12, day=31)
    dates = pd.date_range(start=start, end=end, freq="D")
    df = pd.DataFrame({"date": dates})
    df["year"] = df["date"].dt.year.astype("Int32")
    df["quarter"] = df["date"].dt.quarter.astype("Int32")
    df["month"] = df["date"].dt.month.astype("Int32")
    df["day"] = df["date"].dt.day.astype("Int32")
    df["year_month"] = df["date"].dt.strftime("%Y-%m")
    df["month_name"] = df["date"].dt.strftime("%B")
    df["month_name_short"] = df["date"].dt.strftime("%b")
    df["day_of_week"] = df["date"].dt.dayofweek.astype("Int32")  # Mon=0
    df["day_name"] = df["date"].dt.strftime("%A")
    df["week_of_year"] = df["date"].dt.isocalendar().week.astype("Int32")
    df["is_weekend"] = df["day_of_week"].isin([5, 6])
    df["is_business_day"] = ~df["is_weekend"]
    return df


def _build_carrier_dim() -> pd.DataFrame:
    rows = [
        ("correos",  "Correos Express", "ES", "parcel",        "EUR"),
        ("seitrans", "Seitrans",        "IT", "pallet",        "EUR"),
        ("seur",     "SEUR",            "ES", "parcel",        "EUR"),
        ("dachser",  "Dachser",         "ES", "pallet/freight","EUR"),
        ("spring",   "Spring",          "FR", "parcel",        "EUR"),
        ("ups",      "UPS",             "GB", "parcel/express","GBP"),
        ("wwex",     "WWEX SpeedShip",  "US", "parcel/LTL",    "USD"),
        ("royalmail","Royal Mail",      "GB", "parcel",        "GBP"),
    ]
    return pd.DataFrame(
        rows,
        columns=["carrier_code", "carrier_name", "origin_country",
                 "modality", "native_currency"],
    )


def _build_service_class_dim() -> pd.DataFrame:
    rows = [
        ("parcel",  "Parcel courier (tracked/express/standard)"),
        ("pallet",  "Pallet freight"),
        ("letter",  "Letter-class"),
        ("freight", "LTL / FTL freight"),
        ("other",   "Other / unclassified shipment"),
    ]
    return pd.DataFrame(rows, columns=["service_class", "description"])


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s"
    )
    shipments_path = UNIFIED_OUT / "unified_shipments.parquet"
    if not shipments_path.exists():
        log.error("missing %s — run `python -m unified.build` first", shipments_path)
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    shipments = pd.read_parquet(
        shipments_path, columns=["posting_date"]
    )
    min_d = shipments["posting_date"].min()
    max_d = shipments["posting_date"].max()
    log.info("date range in unified data: %s .. %s", min_d.date(), max_d.date())

    dim_date = _build_date_dim(min_d, max_d)
    dim_date.to_parquet(OUT_DIR / "dim_date.parquet", index=False)
    log.info("wrote dim_date: %d rows (%s..%s)",
             len(dim_date), dim_date["date"].min().date(),
             dim_date["date"].max().date())

    dim_carrier = _build_carrier_dim()
    dim_carrier.to_parquet(OUT_DIR / "dim_carrier.parquet", index=False)
    log.info("wrote dim_carrier: %d rows", len(dim_carrier))

    dim_service = _build_service_class_dim()
    dim_service.to_parquet(OUT_DIR / "dim_service_class.parquet", index=False)
    log.info("wrote dim_service_class: %d rows", len(dim_service))

    return 0


if __name__ == "__main__":
    sys.exit(main())
