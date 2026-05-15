"""One-shot helper to bootstrap GraphStorage configuration.

Run this once, on the operator's PC, after the Azure AD app registration
is in place and `Sites.Selected` has been granted on the target site.
It echoes the values that need to land in GitHub repo secrets as
``GRAPH_TENANT_ID``, ``GRAPH_CLIENT_ID``, ``GRAPH_CLIENT_SECRET``, and
``GRAPH_DRIVE_ID``.

Required env vars (these you set locally before running this script):

- ``GRAPH_TENANT_ID``    — the artero.com tenant GUID
- ``GRAPH_CLIENT_ID``    — the app registration's client ID
- ``GRAPH_CLIENT_SECRET``— the app's client secret value
- ``GRAPH_USER_PRINCIPAL`` (optional) — the upn of the user whose
  OneDrive holds ``Operations - Couriers/``. If set, resolves the user's
  personal drive. If unset, resolves the default drive of the site the
  service principal has been granted on.

Run with::

    .venv\\Scripts\\python.exe scripts/graph_bootstrap.py

If it prints a clean drive id and a list of top-level folders that
includes ``Operations - Couriers``, you're wired correctly. Paste the
four env values into the GH Actions repo secrets, set
``COURIER_BACKEND=graph``, and you're ready to run the cloud variant.
"""
from __future__ import annotations

import os
import sys

from courier_automation.storage import OpsLocator
from courier_automation.storage.graph import GraphStorage
from courier_automation.storage.graph_auth import GraphTokenProvider


def _resolve_drive_id(token: str) -> str:
    """Resolve a drive id from one of:

    - ``GRAPH_DRIVE_ID`` (preferred; skips discovery)
    - ``GRAPH_SITE_ID`` (returns the site's default document library)
    - ``GRAPH_USER_PRINCIPAL`` (returns the user's personal OneDrive)
    """
    import httpx  # local import: only needed for bootstrap

    explicit = os.environ.get("GRAPH_DRIVE_ID")
    if explicit:
        print(f"GRAPH_DRIVE_ID (from env): {explicit}")
        return explicit

    headers = {"Authorization": f"Bearer {token}"}
    site_id = os.environ.get("GRAPH_SITE_ID")
    if site_id:
        url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive"
        r = httpx.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        drive_id = r.json()["id"]
        print(f"resolved site {site_id} -> drive {drive_id}")
        return drive_id

    upn = os.environ.get("GRAPH_USER_PRINCIPAL")
    if upn:
        url = f"https://graph.microsoft.com/v1.0/users/{upn}/drive"
        r = httpx.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        drive_id = r.json()["id"]
        print(f"resolved user {upn} -> drive {drive_id}")
        return drive_id

    raise SystemExit(
        "set one of: GRAPH_DRIVE_ID (preferred), GRAPH_SITE_ID, "
        "or GRAPH_USER_PRINCIPAL — bootstrap cannot pick one for you."
    )


def main() -> int:
    print("=== Courier Pipeline Graph bootstrap ===\n")
    provider = GraphTokenProvider()
    token = provider.token()
    print(f"GRAPH_TENANT_ID = {provider.tenant_id}")
    print(f"GRAPH_CLIENT_ID = {provider.client_id}")
    print("GRAPH_CLIENT_SECRET = <hidden>")
    print()

    drive_id = _resolve_drive_id(token)
    storage = GraphStorage(token_provider=provider, drive_id=drive_id)

    print("\n--- top-level folders on the drive ---")
    try:
        entries = storage.list_dir(OpsLocator())
    except Exception as exc:  # noqa: BLE001 — bootstrap is a smoke-test
        print(f"ERROR listing drive root: {exc}", file=sys.stderr)
        return 2
    for e in entries:
        marker = "[dir]" if e.is_dir else "     "
        print(f"  {marker}  {e.name}")

    names = {e.name for e in entries}
    if "Operations - Couriers" not in names:
        print(
            "\nWARN: 'Operations - Couriers' not found at the drive root. "
            "Either the drive id is wrong, or the service principal "
            "hasn't been granted Sites.Selected on this site.",
            file=sys.stderr,
        )
        return 1

    print("\nGRAPH_DRIVE_ID:", drive_id)
    print("\nPaste these four values into the repo's GH Actions secrets:")
    print(f"  GRAPH_TENANT_ID     = {provider.tenant_id}")
    print(f"  GRAPH_CLIENT_ID     = {provider.client_id}")
    print("  GRAPH_CLIENT_SECRET = <the client secret value>")
    print(f"  GRAPH_DRIVE_ID      = {drive_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
