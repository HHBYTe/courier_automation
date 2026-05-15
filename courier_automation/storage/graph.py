"""Microsoft Graph backend for the Storage Protocol.

Implements every Storage method against the Graph API for a specified
OneDrive/SharePoint drive. All locators are addressed by path
(``/drives/{drive_id}/root:/{relpath}:``) so a folder rename on the
SharePoint side doesn't require a code change.

Authentication is provided by `GraphTokenProvider` (service principal,
`Sites.Selected` scope, see `graph_auth.py`). The token is fetched
lazily and cached for the process lifetime.

Concurrency for the transactional `update_xlsx_atomically` method uses
the Graph ETag with `If-Match` on PUT: if another writer changes the
file between download and upload, the upload returns 412 and the whole
loop restarts. Matches LocalStorage's lock-and-replace semantics in
spirit, just via HTTP-level optimistic concurrency.
"""
from __future__ import annotations

import hashlib
import logging
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import quote

try:
    import httpx  # type: ignore[import-not-found]
except ImportError as exc:  # pragma: no cover - httpx is in requirements.txt
    raise ImportError(
        "httpx is required for the Graph storage backend. "
        "Install it (`pip install httpx`) or use COURIER_BACKEND=local."
    ) from exc

from courier_automation.storage.base import (
    OpsEntry,
    OpsLocator,
    StorageLocked,
    StorageNotFound,
)
from courier_automation.storage.graph_auth import GraphTokenProvider

log = logging.getLogger(__name__)

_GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
_UPLOAD_SESSION_THRESHOLD = 4 * 1024 * 1024  # 4 MB — Graph's simple PUT limit


class GraphAPIError(RuntimeError):
    """Raised on a non-2xx Graph response that isn't one of the special-cased
    status codes (404 → StorageNotFound, 412/423 → StorageLocked)."""


class GraphStorage:
    """Storage backed by a Microsoft Graph drive (OneDrive / SharePoint).

    Path-based addressing throughout: locators map to
    ``/drives/{drive_id}/root:/{relpath}:`` — no per-call item-ID lookups.

    The client is a single `httpx.Client` with a 30 s timeout; if Graph
    is slow under unusual load the operator notices via the workflow's
    job timeout (30 min) rather than a silently-hung run.
    """

    def __init__(
        self,
        token_provider: GraphTokenProvider,
        drive_id: str,
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.token_provider = token_provider
        self.drive_id = drive_id
        self._client = httpx.Client(timeout=timeout_seconds)
        self._drive_url = f"{_GRAPH_ROOT}/drives/{drive_id}"

    # ------------------------------------------------------------------
    # HTTP plumbing
    # ------------------------------------------------------------------

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.token_provider.token()}"}
        if extra:
            h.update(extra)
        return h

    def _path_url(self, loc: OpsLocator, suffix: str = "") -> str:
        rel = "/".join(quote(part, safe="") for part in loc.parts)
        return f"{self._drive_url}/root:/{rel}:{suffix}" if rel else f"{self._drive_url}/root{suffix.lstrip(':')}"

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
        json: object | None = None,
        ok_codes: tuple[int, ...] = (200, 201, 204),
    ) -> httpx.Response:
        resp = self._client.request(
            method, url,
            headers=self._headers(headers),
            content=content,
            json=json,
        )
        if resp.status_code == 404:
            raise StorageNotFound(url)
        if resp.status_code in (412, 423):
            raise StorageLocked(
                f"{method} {url} -> {resp.status_code} ({resp.text[:200]})"
            )
        if resp.status_code not in ok_codes:
            raise GraphAPIError(
                f"{method} {url} -> {resp.status_code} ({resp.text[:500]})"
            )
        return resp

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def exists(self, loc: OpsLocator) -> bool:
        try:
            self._request("GET", self._path_url(loc), ok_codes=(200,))
            return True
        except StorageNotFound:
            return False

    def is_dir(self, loc: OpsLocator) -> bool:
        try:
            r = self._request("GET", self._path_url(loc), ok_codes=(200,))
        except StorageNotFound:
            return False
        return "folder" in r.json()

    def list_dir(self, loc: OpsLocator) -> list[OpsEntry]:
        # Graph: /drives/{id}/root:/{path}:/children
        suffix = ":/children" if loc.parts else "/children"
        url = self._path_url(loc, suffix)
        entries: list[OpsEntry] = []
        while url:
            r = self._request("GET", url, ok_codes=(200,))
            body = r.json()
            for item in body.get("value", []):
                is_dir = "folder" in item
                entries.append(
                    OpsEntry(
                        name=item["name"],
                        is_dir=is_dir,
                        size=None if is_dir else int(item.get("size", 0)),
                        mtime_iso=None if is_dir else item.get("lastModifiedDateTime"),
                    )
                )
            url = body.get("@odata.nextLink")
        # Stable ordering matches LocalStorage's `sorted(iterdir())`.
        entries.sort(key=lambda e: e.name)
        return entries

    def glob_names(
        self,
        loc: OpsLocator,
        patterns: tuple[str, ...],
        *,
        case_insensitive: bool = True,
    ) -> list[OpsLocator]:
        import fnmatch

        try:
            children = self.list_dir(loc)
        except StorageNotFound:
            return []
        seen: dict[str, None] = {}
        for child in children:
            if child.is_dir:
                continue
            for pat in patterns:
                hay = child.name.lower() if case_insensitive else child.name
                needle = pat.lower() if case_insensitive else pat
                if fnmatch.fnmatchcase(hay, needle):
                    seen[child.name] = None
                    break
        return [loc / name for name in seen]

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read_bytes(self, loc: OpsLocator) -> bytes:
        meta = self._request("GET", self._path_url(loc), ok_codes=(200,)).json()
        download_url = meta.get("@microsoft.graph.downloadUrl")
        if not download_url:
            # Folder, or download URL not provisioned — pull via content endpoint.
            r = self._request(
                "GET", self._path_url(loc, ":/content"), ok_codes=(200,)
            )
            return r.content
        # The downloadUrl is pre-authed and short-lived; skip Authorization
        # to avoid the "duplicate auth" rejection some CDNs do.
        r = self._client.get(download_url)
        if r.status_code != 200:
            raise GraphAPIError(
                f"download {loc} -> {r.status_code} ({r.text[:200]})"
            )
        return r.content

    @contextmanager
    def open_local_copy(self, loc: OpsLocator) -> Iterator[Path]:
        data = self.read_bytes(loc)
        with tempfile.NamedTemporaryFile(
            suffix=Path(loc.name).suffix, delete=False
        ) as f:
            f.write(data)
            tmp_path = Path(f.name)
        try:
            yield tmp_path
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Write-once
    # ------------------------------------------------------------------

    def write_bytes(
        self, loc: OpsLocator, data: bytes, *, overwrite: bool = False
    ) -> None:
        if not overwrite and self.exists(loc):
            raise FileExistsError(str(loc))
        self._upload_bytes(loc, data)

    def write_from_local(
        self, loc: OpsLocator, src: Path, *, overwrite: bool = False
    ) -> None:
        if not overwrite and self.exists(loc):
            raise FileExistsError(str(loc))
        data = Path(src).read_bytes()
        self._upload_bytes(loc, data)

    def _upload_bytes(self, loc: OpsLocator, data: bytes) -> None:
        if len(data) <= _UPLOAD_SESSION_THRESHOLD:
            self._request(
                "PUT",
                self._path_url(loc, ":/content"),
                headers={"Content-Type": "application/octet-stream"},
                content=data,
                ok_codes=(200, 201),
            )
            return
        # Large file: createUploadSession + chunked PUT.
        session = self._request(
            "POST",
            self._path_url(loc, ":/createUploadSession"),
            json={"@microsoft.graph.conflictBehavior": "replace"},
            ok_codes=(200, 201),
        ).json()
        upload_url = session["uploadUrl"]
        chunk = 5 * 1024 * 1024  # 5 MB per PUT (Graph requires 320 KiB-aligned)
        total = len(data)
        for offset in range(0, total, chunk):
            piece = data[offset : offset + chunk]
            end = offset + len(piece) - 1
            r = self._client.put(
                upload_url,
                headers={
                    "Content-Length": str(len(piece)),
                    "Content-Range": f"bytes {offset}-{end}/{total}",
                },
                content=piece,
            )
            if r.status_code not in (200, 201, 202):
                raise GraphAPIError(
                    f"upload chunk {offset}-{end}/{total} -> {r.status_code} "
                    f"({r.text[:200]})"
                )

    # ------------------------------------------------------------------
    # Placement
    # ------------------------------------------------------------------

    def ensure_dir(self, loc: OpsLocator) -> None:
        if not loc.parts:
            return
        if self.exists(loc):
            return
        # Recursively create parents.
        parent = OpsLocator(*loc.parts[:-1]) if len(loc.parts) > 1 else OpsLocator()
        if parent.parts:
            self.ensure_dir(parent)
        parent_suffix = ":/children" if parent.parts else "/children"
        self._request(
            "POST",
            self._path_url(parent, parent_suffix),
            json={
                "name": loc.parts[-1],
                "folder": {},
                "@microsoft.graph.conflictBehavior": "replace",
            },
            ok_codes=(200, 201),
        )

    def move_in(self, src_local: Path, dest: OpsLocator) -> None:
        src_local = Path(src_local)
        self._upload_bytes(dest, src_local.read_bytes())
        try:
            src_local.unlink()
        except OSError:
            log.warning("move_in: could not unlink local %s after upload", src_local)

    def delete(self, loc: OpsLocator, *, missing_ok: bool = True) -> None:
        try:
            self._request(
                "DELETE", self._path_url(loc), ok_codes=(200, 204)
            )
        except StorageNotFound:
            if not missing_ok:
                raise

    def rename(self, src: OpsLocator, dest: OpsLocator) -> None:
        data = self.read_bytes(src)
        self._upload_bytes(dest, data)
        self.delete(src, missing_ok=True)

    def file_hash(self, loc: OpsLocator) -> str:
        # Graph exposes quickXorHash / sha1Hash / sha256Hash, but only sha1
        # is universally populated and we want SHA-256 to match LocalStorage's
        # compute_file_hash semantics. Stream the bytes ourselves.
        data = self.read_bytes(loc)
        return hashlib.sha256(data).hexdigest()

    # ------------------------------------------------------------------
    # Transactional update
    # ------------------------------------------------------------------

    def update_xlsx_atomically(
        self,
        loc: OpsLocator,
        mutator: Callable[[Path], int],
        *,
        retries: int = 6,
        retry_seconds: float = 5.0,
    ) -> int:
        for attempt in range(1, retries + 1):
            meta = self._request(
                "GET", self._path_url(loc), ok_codes=(200,)
            ).json()
            etag = meta.get("eTag") or meta.get("@odata.etag")
            if etag is None:
                raise GraphAPIError(f"{loc}: no eTag on item metadata")
            data = self.read_bytes(loc)
            with tempfile.NamedTemporaryFile(
                suffix=Path(loc.name).suffix, delete=False
            ) as f:
                f.write(data)
                tmp_path = Path(f.name)
            try:
                rows = mutator(tmp_path)
                new_bytes = tmp_path.read_bytes()
            finally:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            try:
                self._request(
                    "PUT",
                    self._path_url(loc, ":/content"),
                    headers={
                        "Content-Type": "application/octet-stream",
                        "If-Match": etag,
                    },
                    content=new_bytes,
                    ok_codes=(200, 201),
                )
                return rows
            except StorageLocked:
                log.info(
                    "etag mismatch / locked on %s — retry %d/%d in %.1fs",
                    loc, attempt, retries, retry_seconds,
                )
                if attempt == retries:
                    raise
                time.sleep(retry_seconds)
        raise StorageLocked(f"could not publish {loc} after {retries} retries")


__all__ = ["GraphStorage", "GraphAPIError"]
