"""Microsoft Graph authentication for the cloud Storage backend.

MSAL `ConfidentialClientApplication` with the client-credentials flow
(service principal). No interactive sign-in — runs unattended in CI.
The Azure AD app registration must have application permission
`Sites.Selected` granted at admin consent time, and be explicitly
authorised on the target SharePoint site (see docs/graph_backend.md).

Tokens are cached in-memory only. The CI runner is ephemeral, so a
persistent cache adds nothing.
"""
from __future__ import annotations

import os
import time

try:
    import msal  # type: ignore[import-not-found]
except ImportError as exc:  # pragma: no cover - msal is in requirements.txt
    raise ImportError(
        "msal is required for the Graph storage backend. "
        "Install it (`pip install msal`) or use COURIER_BACKEND=local."
    ) from exc

_GRAPH_DEFAULT_SCOPE = "https://graph.microsoft.com/.default"


class GraphTokenProvider:
    """Acquire and cache a bearer token for Microsoft Graph.

    Reads the service-principal credentials from env vars:

    - ``GRAPH_TENANT_ID``    — artero.com tenant GUID
    - ``GRAPH_CLIENT_ID``    — the app registration's client ID
    - ``GRAPH_CLIENT_SECRET``— the app registration's client secret
    """

    def __init__(
        self,
        tenant_id: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        self.tenant_id = tenant_id or os.environ["GRAPH_TENANT_ID"]
        self.client_id = client_id or os.environ["GRAPH_CLIENT_ID"]
        self.client_secret = client_secret or os.environ["GRAPH_CLIENT_SECRET"]
        self._app = msal.ConfidentialClientApplication(
            self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            client_credential=self.client_secret,
        )
        self._cached: tuple[str, float] | None = None  # (token, expires_at_epoch)

    def token(self) -> str:
        """Return a valid access token, acquiring or refreshing on demand."""
        now = time.time()
        if self._cached and self._cached[1] - now > 60:
            return self._cached[0]
        result = self._app.acquire_token_for_client(scopes=[_GRAPH_DEFAULT_SCOPE])
        if "access_token" not in result:
            raise RuntimeError(
                f"MSAL token acquisition failed: "
                f"{result.get('error')} - {result.get('error_description')}"
            )
        self._cached = (result["access_token"], now + int(result["expires_in"]))
        return self._cached[0]


__all__ = ["GraphTokenProvider"]
