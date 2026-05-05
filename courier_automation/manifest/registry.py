"""SQLite-backed registry of ingested invoice files.

Idempotency model:
  - PRIMARY KEY is (carrier, invoice_number, file_hash). The same physical file
    can never produce duplicate writes.
  - `supersedes()` flags the case where the same invoice number was previously
    ingested with a *different* hash — i.e. the courier reissued the file. This
    is a human-review signal, not an automatic decision.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_ENV_VAR = "COURIER_AUTOMATION_MANIFEST"
DEFAULT_DB_PATH = Path.home() / ".courier_automation" / "manifest.sqlite"


class ManifestRegistry:
    def __init__(self, db_path: Path | None = None) -> None:
        if db_path is None:
            env = os.environ.get(DEFAULT_DB_ENV_VAR)
            db_path = Path(env) if env else DEFAULT_DB_PATH
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        # WAL gives us safe concurrent reads + a single writer.
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=10.0)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS files(
                    carrier        TEXT    NOT NULL,
                    invoice_number TEXT    NOT NULL,
                    file_hash      TEXT    NOT NULL,
                    source_path    TEXT    NOT NULL,
                    ingested_at    TEXT    NOT NULL,
                    rows_written   INTEGER NOT NULL,
                    PRIMARY KEY (carrier, invoice_number, file_hash)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_files_invoice "
                "ON files(carrier, invoice_number)"
            )

    def has_seen(self, carrier: str, invoice_number: str, file_hash: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM files WHERE carrier=? AND invoice_number=? AND file_hash=?",
                (carrier, invoice_number, file_hash),
            ).fetchone()
        return row is not None

    def register(
        self,
        *,
        carrier: str,
        invoice_number: str,
        file_hash: str,
        source_path: str | Path,
        rows_written: int,
    ) -> None:
        ingested_at = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO files(
                    carrier, invoice_number, file_hash,
                    source_path, ingested_at, rows_written
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(carrier, invoice_number, file_hash)
                DO UPDATE SET
                    source_path  = excluded.source_path,
                    ingested_at  = excluded.ingested_at,
                    rows_written = excluded.rows_written
                """,
                (
                    carrier,
                    invoice_number,
                    file_hash,
                    str(source_path),
                    ingested_at,
                    rows_written,
                ),
            )

    def supersedes(
        self, carrier: str, invoice_number: str, new_hash: str
    ) -> str | None:
        """Return the hash of the most recent prior ingest of the same invoice
        number with a *different* hash, or None if there is no such conflict."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT file_hash FROM files
                WHERE carrier=? AND invoice_number=? AND file_hash != ?
                ORDER BY ingested_at DESC
                LIMIT 1
                """,
                (carrier, invoice_number, new_hash),
            ).fetchone()
        return row[0] if row else None

    def all_for_invoice(
        self, carrier: str, invoice_number: str
    ) -> list[dict[str, object]]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM files WHERE carrier=? AND invoice_number=? "
                "ORDER BY ingested_at DESC",
                (carrier, invoice_number),
            ).fetchall()
        return [dict(r) for r in rows]
