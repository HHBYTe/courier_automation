"""Storage abstraction for the courier automation pipeline.

Two interchangeable backends — local filesystem and Microsoft Graph —
share a single Protocol so the same pipeline code runs on the PC and in
the cloud. See docs/architecture.md and the plan at
~/.claude/plans/set-up-level-3-misty-tide.md for the rationale.

Backend selection is env-driven via `factory.get_storage()`:
- `COURIER_BACKEND=local` (default) → LocalStorage
- `COURIER_BACKEND=graph` → GraphStorage (Level 3)
"""
from __future__ import annotations

from courier_automation.storage.base import (
    OpsEntry,
    OpsLocator,
    Storage,
    StorageLocked,
    StorageNotFound,
)

__all__ = [
    "OpsEntry",
    "OpsLocator",
    "Storage",
    "StorageLocked",
    "StorageNotFound",
]
