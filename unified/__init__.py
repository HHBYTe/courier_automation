"""Unified shipment fact table.

Reads per-carrier parquets from `/data/<carrier>/*.parquet`, normalizes each
to a single 20-column canonical schema, drops anything that isn't a real
shipment, and writes one cross-carrier fact table to `unified/output/`.

Run with:  python -m unified.build
"""
