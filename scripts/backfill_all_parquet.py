"""Run every per-courier parquet backfill in turn."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

CARRIERS: tuple[str, ...] = ("seur", "seitrans", "correos", "ups", "wwex", "spring")


def main() -> int:
    failures: list[str] = []
    for carrier in CARRIERS:
        print(f"\n=== {carrier} ===")
        mod = importlib.import_module(f"backfill_{carrier}_parquet")
        try:
            rc = mod.main()
        except Exception as e:  # noqa: BLE001
            print(f"  FAILED: {e}")
            failures.append(carrier)
            continue
        if rc != 0:
            failures.append(carrier)
    if failures:
        print(f"\nfailures: {failures}")
        return 1
    print("\nall carriers backfilled OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
