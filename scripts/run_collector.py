"""Scheduled local runner: the second half of the collector pipeline.

n8n Cloud is a dumb pipe — it drops every invoice email attachment into
one OneDrive inbox folder (`Operations - Couriers/_inbox/`). This script,
run on the PC by Windows Task Scheduler, does the rest:

  1. scan `_inbox/` for files OneDrive has finished syncing,
  2. classify each to a carrier and move it into the carrier's
     `Facturas/<YYYY>/<NN> - <Mes>/` folder (unrecognised -> `_unclassified/`,
     content collisions -> `_conflicts/`),
  3. sweep `pipeline` for all 8 carriers — affected carriers on their new
     month, idle carriers auto-discovering the latest (which also picks up
     manually-dropped UPS / WWEX / Royal Mail files),
  4. rebuild the unified table once if anything was appended,
  5. write a timestamped log and email a summary.

The duplicate guard inside `pipeline` makes this safe to run on a tight
schedule — an already-ingested month is a cheap no-op.

Run from the repo root:  python scripts/run_collector.py

SMTP config (env vars; email is skipped entirely if COURIER_SMTP_HOST is
unset). Microsoft 365 disables SMTP AUTH per-mailbox by default — a
tenant admin must enable it on the sending mailbox, and an app password
is needed when MFA is on. Use port 587 + STARTTLS.
  COURIER_SMTP_HOST       e.g. smtp.office365.com   (unset => no email)
  COURIER_SMTP_PORT       default 587
  COURIER_SMTP_USER       sending mailbox
  COURIER_SMTP_PASSWORD   app password / secret
  COURIER_SMTP_TO         comma-separated recipients (default: SMTP_USER)
  COURIER_SMTP_FROM       default: COURIER_SMTP_USER
  COURIER_SMTP_STARTTLS   "1" (default) for 587 STARTTLS, "0" for SMTPS/465
  COURIER_SMTP_SKIP_EMPTY "1" => no email when nothing was collected and
                          every carrier was a clean no-op
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import smtplib
import sys
import time
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from courier_automation import cli  # noqa: E402
from courier_automation.carriers import CARRIERS  # noqa: E402
from courier_automation.exit_codes import (  # noqa: E402
    EXIT_DUPLICATE,
    EXIT_OK,
    EXIT_SCHEMA,
    EXIT_USAGE,
)
from courier_automation.intake import (  # noqa: E402
    CONFLICTS,
    INBOX,
    UNCLASSIFIED,
    IntakeConflict,
    classify_invoice_file,
    place_invoice_file,
    quarantine_file,
)
import typer  # noqa: E402
import unified.build as unified_build  # noqa: E402

log = logging.getLogger("courier_automation.collector")

LOG_DIR = ROOT / "logs" / "collector"
LOCK_FILE = LOG_DIR / ".lock"
LOCK_STALE_SECONDS = 2 * 3600  # a lock older than this is assumed dead
SYNC_SETTLE_SECONDS = 60       # skip files modified within the last minute

# Windows cloud-only placeholder attributes — a file present in the
# listing but not actually downloaded yet.
_FILE_ATTRIBUTE_OFFLINE = 0x1000
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000


@dataclass
class CollectedFile:
    name: str
    carrier: str | None
    placed_path: str | None = None      # where it landed in Facturas
    quarantine: str | None = None       # "unclassified" | "conflict"
    detail: str = ""


@dataclass
class CarrierRun:
    carrier: str
    month: str | None
    exit_code: int
    detail: str = ""  # error message when the pipeline crashed

    @property
    def status(self) -> str:
        if self.exit_code == EXIT_OK:
            return "ok"
        if self.exit_code == EXIT_DUPLICATE:
            return "duplicate"
        if self.exit_code == EXIT_USAGE:
            return "no-files"
        return f"error({self.exit_code})"

    @property
    def is_error(self) -> bool:
        return self.exit_code not in (EXIT_OK, EXIT_DUPLICATE, EXIT_USAGE)


@dataclass
class RunReport:
    started: dt.datetime
    log_path: Path
    collected: list[CollectedFile] = field(default_factory=list)
    carrier_runs: list[CarrierRun] = field(default_factory=list)
    unified_status: str = "skipped"
    unified_totals: dict = field(default_factory=dict)
    fatal: str | None = None  # set when the run aborts before the sweep

    @property
    def errors(self) -> list[str]:
        out: list[str] = []
        if self.fatal:
            out.append(self.fatal)
        for c in self.collected:
            if c.quarantine == "unclassified":
                out.append(f"unclassified file: {c.name} ({c.detail})")
            elif c.quarantine == "conflict":
                out.append(f"content conflict: {c.name} ({c.detail})")
        for r in self.carrier_runs:
            if r.is_error:
                msg = f"carrier {r.carrier}: {r.status}"
                if r.detail:
                    msg += f" — {r.detail}"
                out.append(msg)
        return out

    @property
    def nothing_happened(self) -> bool:
        return (
            not self.collected
            and not self.errors
            and all(r.status in ("duplicate", "no-files") for r in self.carrier_runs)
        )


# ---------------------------------------------------------------------------
# lock file


def _acquire_lock() -> bool:
    """Return True if we got the lock. A lock older than LOCK_STALE_SECONDS
    is treated as a dead run and stolen."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if LOCK_FILE.exists():
        age = time.time() - LOCK_FILE.stat().st_mtime
        if age < LOCK_STALE_SECONDS:
            log.warning(
                "lock %s held (%.0fs old) — another run is active, exiting",
                LOCK_FILE, age,
            )
            return False
        log.warning("stale lock %s (%.0fs old) — stealing", LOCK_FILE, age)
    LOCK_FILE.write_text(
        f"pid={os.getpid()} started={dt.datetime.now().isoformat()}\n",
        encoding="utf-8",
    )
    return True


def _release_lock() -> None:
    LOCK_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# inbox scan


def _is_ready(path: Path) -> bool:
    """True if `path` is a real, fully-synced invoice file worth processing.
    Skips dotfiles / Office lock files / temp files, OneDrive cloud-only
    placeholders, and files still being written or synced."""
    name = path.name
    if not path.is_file():
        return False
    if name.startswith((".", "~$")) or name.lower().endswith(".tmp"):
        return False
    st = path.stat()
    attrs = getattr(st, "st_file_attributes", 0)
    if attrs & (_FILE_ATTRIBUTE_OFFLINE | _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS):
        log.info("skip %s — OneDrive cloud-only placeholder, not synced yet", name)
        return False
    if time.time() - st.st_mtime < SYNC_SETTLE_SECONDS:
        log.info("skip %s — modified <%ds ago, may still be syncing",
                 name, SYNC_SETTLE_SECONDS)
        return False
    return True


def _scan_inbox() -> list[Path]:
    if not INBOX.is_dir():
        log.info("inbox %s does not exist yet — nothing to collect", INBOX)
        return []
    return sorted(p for p in INBOX.iterdir() if _is_ready(p))


# ---------------------------------------------------------------------------
# intake: classify + place each inbox file


def _intake(report: RunReport) -> dict[str, set[str]]:
    """Classify and place every ready inbox file. Returns
    {carrier: {"YYYY-MM", ...}} for the carriers that got new files."""
    affected: dict[str, set[str]] = {}
    for path in _scan_inbox():
        cls = classify_invoice_file(path)
        if cls.carrier is None:
            quarantine_file(path, UNCLASSIFIED)
            report.collected.append(CollectedFile(
                path.name, None, quarantine="unclassified", detail=cls.reason,
            ))
            continue
        try:
            placed, parsed = place_invoice_file(
                path, cls.carrier, parse_result=cls.parse_result,
            )
        except IntakeConflict as e:
            quarantine_file(path, CONFLICTS)
            report.collected.append(CollectedFile(
                path.name, cls.carrier, quarantine="conflict",
                detail=f"differs from {e.existing_path}",
            ))
            continue
        except Exception as e:  # noqa: BLE001 — parser drift, corrupt file, ...
            quarantine_file(path, UNCLASSIFIED)
            report.collected.append(CollectedFile(
                path.name, cls.carrier, quarantine="unclassified",
                detail=f"classified {cls.carrier} but failed to parse: {e}",
            ))
            continue
        month = f"{parsed.invoice_date.year:04d}-{parsed.invoice_date.month:02d}"
        report.collected.append(CollectedFile(
            path.name, cls.carrier, placed_path=str(placed), detail=cls.reason,
        ))
        affected.setdefault(cls.carrier, set()).add(month)
    return affected


# ---------------------------------------------------------------------------
# pipeline sweep


def _run_pipeline(carrier: str, month: str | None) -> tuple[int, str]:
    """Invoke the `pipeline` CLI command in-process (reuses all of its file
    discovery), returning (exit_code, detail). `--skip-unified` — the sweep
    rebuilds unified once at the end. Any uncaught exception from one
    carrier is contained here so it can't abort the whole sweep."""
    try:
        cli.pipeline(
            carrier=carrier,
            month=month,
            dry_run=False,
            json_out=False,
            force=False,
            guard_threshold=cli.DEFAULT_GUARD_THRESHOLD,
            skip_unified=True,
        )
    except typer.Exit as e:
        return int(e.exit_code or 0), ""
    except Exception as e:  # noqa: BLE001 — one carrier must not abort the sweep
        log.exception("pipeline %s crashed", carrier)
        return EXIT_SCHEMA, f"{type(e).__name__}: {e}"
    return EXIT_OK, ""


def _sweep(report: RunReport, affected: dict[str, set[str]]) -> None:
    """Run the pipeline for every carrier. Affected carriers run once per
    placed month; idle carriers run with month=None (auto-discover latest)."""
    for carrier in CARRIERS:
        months = sorted(affected.get(carrier, set())) or [None]
        for month in months:
            code, detail = _run_pipeline(carrier, month)
            report.carrier_runs.append(CarrierRun(carrier, month, code, detail))
            log.info("pipeline %s %s -> exit %d", carrier, month or "(latest)", code)


def _rebuild_unified(report: RunReport) -> None:
    if not any(r.exit_code == EXIT_OK for r in report.carrier_runs):
        log.info("no carrier appended rows — skipping unified rebuild")
        return
    try:
        rc = unified_build.main([])
    except SystemExit as e:
        report.unified_status = f"failed (SystemExit {e.code})"
        log.error("unified build failed: SystemExit %s", e.code)
        return
    if rc != 0:
        report.unified_status = f"failed (rc {rc})"
        log.error("unified build returned %d", rc)
        return
    report.unified_status = "rebuilt"
    manifest = unified_build.OUT_DIR / "manifest.json"
    if manifest.exists():
        import json
        m = json.loads(manifest.read_text(encoding="utf-8"))
        report.unified_totals = {
            k: m.get(k) for k in ("total_kept", "total_refunds", "total_rejected")
        }


# ---------------------------------------------------------------------------
# reporting: log summary + email


def _summary_text(report: RunReport) -> str:
    lines: list[str] = []
    lines.append(f"Courier collector run {report.started:%Y-%m-%d %H:%M:%S}")
    lines.append(f"  inbox: {INBOX}")
    lines.append(f"  log:   {report.log_path}")
    lines.append("")

    if report.fatal:
        lines.append(f"ABORTED: {report.fatal}")
        return "\n".join(lines)

    lines.append(f"Collected files ({len(report.collected)}):")
    if not report.collected:
        lines.append("  (none)")
    for c in report.collected:
        if c.quarantine == "unclassified":
            lines.append(f"  {c.name}  ->  UNCLASSIFIED  ({c.detail})")
        elif c.quarantine == "conflict":
            lines.append(f"  {c.name}  ->  CONFLICT  ({c.detail})")
        else:
            lines.append(f"  {c.name}  ->  {c.carrier}  ->  {c.placed_path}")
    lines.append("")

    lines.append("Pipeline sweep:")
    lines.append(f"  {'carrier':<12}{'month':<10}status")
    for r in report.carrier_runs:
        lines.append(f"  {r.carrier:<12}{(r.month or 'latest'):<10}{r.status}")
    lines.append("")

    if report.unified_totals:
        t = report.unified_totals
        lines.append(
            f"Unified: {report.unified_status}  "
            f"(kept={t.get('total_kept')}, refunds={t.get('total_refunds')}, "
            f"rejected={t.get('total_rejected')})"
        )
    else:
        lines.append(f"Unified: {report.unified_status}")
    lines.append("")

    errors = report.errors
    if errors:
        lines.append(f"ATTENTION NEEDED ({len(errors)}):")
        for e in errors:
            lines.append(f"  - {e}")
    else:
        lines.append("No errors.")
    return "\n".join(lines)


def _send_email(report: RunReport, body: str) -> bool:
    """Send the summary via SMTP. Returns True on success, False on a
    configured-but-failed send. If COURIER_SMTP_HOST is unset, email is
    disabled — returns True (not an error)."""
    host = os.environ.get("COURIER_SMTP_HOST")
    if not host:
        log.info("COURIER_SMTP_HOST unset — email disabled")
        return True
    if os.environ.get("COURIER_SMTP_SKIP_EMPTY") == "1" and report.nothing_happened:
        log.info("nothing collected and no errors — skipping email (SKIP_EMPTY)")
        return True

    user = os.environ.get("COURIER_SMTP_USER", "")
    password = os.environ.get("COURIER_SMTP_PASSWORD", "")
    port = int(os.environ.get("COURIER_SMTP_PORT", "587"))
    sender = os.environ.get("COURIER_SMTP_FROM") or user
    recipients = [
        a.strip() for a in os.environ.get("COURIER_SMTP_TO", user).split(",")
        if a.strip()
    ]
    starttls = os.environ.get("COURIER_SMTP_STARTTLS", "1") != "0"

    n_err = len(report.errors)
    tag = f"{n_err} issue(s)" if n_err else "ok"
    msg = EmailMessage()
    msg["Subject"] = (
        f"[courier collector] {report.started:%Y-%m-%d %H:%M} — {tag}"
    )
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    try:
        if starttls:
            with smtplib.SMTP(host, port, timeout=60) as s:
                s.starttls()
                if user:
                    s.login(user, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP_SSL(host, port, timeout=60) as s:
                if user:
                    s.login(user, password)
                s.send_message(msg)
    except Exception as e:  # noqa: BLE001
        log.error("SMTP send failed: %s", e)
        return False
    log.info("summary email sent to %s", ", ".join(recipients))
    return True


# ---------------------------------------------------------------------------
# entry point


def _setup_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.handlers = [fh, sh]


def main() -> int:
    started = dt.datetime.now()
    log_path = LOG_DIR / f"{started:%Y%m%d-%H%M%S}.log"
    _setup_logging(log_path)
    report = RunReport(started=started, log_path=log_path)

    if not _acquire_lock():
        report.fatal = "another collector run is active (lock held)"
        _send_email(report, _summary_text(report))
        return 1

    try:
        log.info("collector run start — inbox=%s", INBOX)
        affected = _intake(report)
        log.info("intake done — %d file(s), affected carriers: %s",
                 len(report.collected), {k: sorted(v) for k, v in affected.items()})
        _sweep(report, affected)
        _rebuild_unified(report)
    except Exception as e:  # noqa: BLE001
        report.fatal = f"unhandled error: {e}"
        log.exception("collector run failed")
    finally:
        _release_lock()

    body = _summary_text(report)
    for line in body.splitlines():
        log.info("%s", line)
    email_ok = _send_email(report, body)

    failed = bool(report.fatal) or bool(report.errors) or not email_ok
    log.info("collector run end — %s", "FAILED" if failed else "ok")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
