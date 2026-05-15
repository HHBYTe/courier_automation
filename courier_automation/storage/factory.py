"""Env-driven backend selection.

`get_storage()` returns whichever backend `COURIER_BACKEND` selects:

- `local` (default) → `LocalStorage` rooted at `COURIER_OPS_ROOT` (or the
  repo-relative `Operations - Couriers/` if unset).
- `graph` → `GraphStorage` (Level 3; not yet implemented in this PR).
"""
from __future__ import annotations

import os
from pathlib import Path

from courier_automation.storage.base import Storage
from courier_automation.storage.local import LocalStorage

_REPO_ROOT = Path(__file__).resolve().parents[2]


def get_storage() -> Storage:
    """Build the Storage backend selected by the environment.

    The default ops root is the repo root, **not** the
    ``Operations - Couriers/`` subfolder, so locators can address
    siblings like ``data/<carrier>/...`` and
    ``unified/output/...`` alongside ``Operations - Couriers/...``.
    Locators carry the subtree prefix explicitly (e.g.
    ``OpsLocator("Operations - Couriers/01. Seur/...")``).

    Override with ``COURIER_OPS_ROOT`` for tests / shadow runs.
    """
    backend = os.environ.get("COURIER_BACKEND", "local").lower()
    if backend == "local":
        ops_root = Path(os.environ.get("COURIER_OPS_ROOT", str(_REPO_ROOT)))
        return LocalStorage(ops_root=ops_root)
    if backend == "graph":
        # Imported lazily so msal/httpx remain optional deps until PR2.
        from courier_automation.storage.graph import GraphStorage
        from courier_automation.storage.graph_auth import GraphTokenProvider

        return GraphStorage(
            token_provider=GraphTokenProvider(),
            drive_id=os.environ["GRAPH_DRIVE_ID"],
        )
    raise ValueError(f"unknown COURIER_BACKEND={backend!r}")


__all__ = ["get_storage"]
