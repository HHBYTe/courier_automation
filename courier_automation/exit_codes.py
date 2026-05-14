"""Process exit codes shared by the CLI and the pipeline orchestrator.

Lives in its own module so both `cli.py` and `pipeline.py` can import
it without a circular dependency (`cli.py` imports `pipeline.py`).

  0  success
  1  usage error
  2  schema mismatch (loud — the diff is in the message)
  3  workbook lock timeout
  4  manifest conflict (same invoice number, different file hash)
  5  plausibility check failed (silent-drift detector)
  6  duplicate guard tripped (the month is already in the master)
  7  unified build failed (non-schema)
"""
from __future__ import annotations

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_SCHEMA = 2
EXIT_LOCK = 3
EXIT_MANIFEST_CONFLICT = 4
EXIT_PLAUSIBILITY = 5
EXIT_DUPLICATE = 6
EXIT_UNIFIED = 7
