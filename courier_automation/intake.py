"""Invoice intake: classify a dropped file to a carrier and file it into
the carrier's Facturas tree.

The collector workflow (n8n Cloud) is a dumb pipe — it drops every
invoice email attachment into one OneDrive inbox folder
(`Operations - Couriers/_inbox/`) with NO carrier logic. All the smarts
live here, in version-controlled Python:

  classify_invoice_file(path)  -> which carrier? (filename regex, then a
                                  parser header-sniff fallback)
  place_invoice_file(path, c)  -> move into Facturas/<YYYY>/<NN> - <Mes>/

`scripts/run_collector.py` is the scheduled runner that ties these to
the pipeline. This module imports `carriers` + `parsers` only — never
`cli` or `pipeline`.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from courier_automation.carriers import CARRIERS
from courier_automation.parsers.base import ParseResult, compute_file_hash
from courier_automation.storage import LocalStorage, OpsLocator, Storage

log = logging.getLogger("courier_automation.intake")

# Repo root — intake.py lives in courier_automation/, so parent.parent is
# the repo root (mirrors pipeline.ROOT). All carrier paths in carriers.py
# are repo-relative; resolve them against this so the runner works
# regardless of the process cwd.
ROOT = Path(__file__).resolve().parent.parent

# The single inbox folder n8n writes into (and where the operator drops
# the manual UPS / WWEX / Royal Mail files). Override with COURIER_INBOX
# (precedent: COURIER_AUTOMATION_MANIFEST in manifest/registry.py).
_INBOX_ENV = os.environ.get("COURIER_INBOX")
INBOX = (
    Path(_INBOX_ENV)
    if _INBOX_ENV
    else ROOT / "Operations - Couriers" / "_inbox"
)
UNCLASSIFIED = INBOX / "_unclassified"  # nothing matched — needs a human
CONFLICTS = INBOX / "_conflicts"        # collides with an existing file

# Folder names use Spanish month names: "01 - Enero" ... "12 - Diciembre".
SPANISH_MONTHS: dict[int, str] = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}

# Tier-A order: filename-pattern carriers first (royalmail before ups so
# the two `Invoice`-bearing patterns can never be confused — both are
# anchored, so this is belt-and-braces). Tier-B probe carriers last.
_PATTERN_ORDER: tuple[str, ...] = (
    "royalmail", "ups", "seur", "correos", "wwex", "spring",
)
_PROBE_ORDER: tuple[str, ...] = ("seitrans", "dachser")


class IntakeConflict(Exception):
    """A file in `_inbox/` collides — same name, different content — with
    one already in the carrier's Facturas folder. Not moved; the runner
    quarantines it to `_conflicts/` for manual resolution."""

    def __init__(self, inbox_path: Path, existing_path: Path) -> None:
        super().__init__(
            f"{inbox_path.name}: differs from the file already at "
            f"{existing_path}"
        )
        self.inbox_path = inbox_path
        self.existing_path = existing_path


@dataclass(frozen=True)
class Classification:
    """Result of classify_invoice_file. `carrier is None` => quarantine.
    `parse_result` is set only when the probe tier already parsed the
    file, so place_invoice_file can reuse it instead of parsing twice."""

    carrier: str | None
    parse_result: ParseResult | None
    reason: str


def classify_invoice_file(path: Path) -> Classification:
    """Map a dropped invoice file to a carrier.

    Tier A — case-insensitive filename regex (`CarrierConfig.classify_patterns`).
    Tier B — for files Tier A missed, the parser header `sniff()` of the
    `classify_probe` carriers (seitrans, dachser have no filename signature).
    No match anywhere -> `Classification(carrier=None, ...)`.
    """
    path = Path(path)
    name = path.name

    # Tier A: filename fast path.
    for carrier in _PATTERN_ORDER:
        cfg = CARRIERS[carrier]
        for pattern in cfg.classify_patterns:
            if re.search(pattern, name, re.IGNORECASE):
                return Classification(carrier, None, f"filename ~ /{pattern}/")

    # Tier B: parser header sniff (content probe).
    for carrier in _PROBE_ORDER:
        parser = CARRIERS[carrier].parser_factory()
        sniff = getattr(parser, "sniff", None)
        if callable(sniff) and sniff(path):
            return Classification(carrier, None, f"{carrier} header sniff")

    return Classification(None, None, "no carrier matched filename or schema")


def place_invoice_file(
    path: Path,
    carrier: str,
    *,
    parse_result: ParseResult | None = None,
    base_dir: Path | None = None,
    storage: Storage | None = None,
) -> tuple[Path, ParseResult]:
    """Move a classified inbox file into its carrier's Facturas tree.

    The destination month comes from the file's parsed `invoice_date` —
    never the OS file date. Layout is always
    `Facturas/<YYYY>/<NN> - <Mes>/` (the pipeline's month discovery only
    scans `<NN> - <Mes>/` subfolders, so the collector normalises every
    carrier to that, even Seur/Spring which were historically flat).

    Pass `storage` to route all destination I/O through a Storage
    backend; if omitted, a `LocalStorage` rooted at `base_dir` (or the
    repo root) is built on the fly. Tests pass `base_dir=tmp_path` and
    get exactly the same semantics they had before the Storage refactor.

    Returns (final_path, parse_result). Raises:
      - the parser's exceptions (SchemaMismatch / ParserError / ...) if
        the file matched a carrier but doesn't actually parse,
      - IntakeConflict if a different-content file already sits at the
        destination (the inbox file is left in place for the runner to
        quarantine).
    After a normal return the file is in Facturas and gone from `_inbox/`.
    """
    path = Path(path)
    cfg = CARRIERS[carrier]

    if parse_result is None:
        parse_result = cfg.parser_factory().parse(path)
    d = parse_result.invoice_date

    if storage is None:
        storage = LocalStorage(ops_root=base_dir or ROOT)

    # `cfg.facturas_root` is a relative `Path` like
    # "Operations - Couriers/01. Seur/Facturas". Convert to an OpsLocator
    # against the storage's ops root.
    facturas_loc = OpsLocator(cfg.facturas_root.as_posix())
    target_loc = (
        facturas_loc
        / str(d.year)
        / f"{d.month:02d} - {SPANISH_MONTHS[d.month]}"
    )
    storage.ensure_dir(target_loc)
    dest_loc = target_loc / path.name

    # Resolve a real Path for return value + log lines. For LocalStorage
    # this is the on-disk path; for GraphStorage callers we'd need a
    # different shape — not used in PR1.
    dest_path = (
        storage.local_path(dest_loc)
        if isinstance(storage, LocalStorage)
        else Path(str(dest_loc))
    )

    if storage.exists(dest_loc):
        if storage.file_hash(dest_loc) == compute_file_hash(path):
            # Identical re-drop — already collected. Drop the inbox copy.
            path.unlink()
            log.info("%s: already at %s (identical) — inbox copy removed",
                     path.name, dest_path)
            return dest_path, parse_result
        raise IntakeConflict(path, dest_path)

    storage.move_in(path, dest_loc)
    log.info("%s: placed -> %s", path.name, dest_path)
    return dest_path, parse_result


def quarantine_file(path: Path, dest_dir: Path) -> Path:
    """Move `path` into a quarantine dir (`_unclassified/` or `_conflicts/`),
    appending a counter if the name is already taken. Returns the final
    path."""
    path = Path(path)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        n = 1
        while dest.exists():
            dest = dest_dir / f"{stem} ({n}){suffix}"
            n += 1
    shutil.move(str(path), str(dest))
    log.warning("%s: quarantined -> %s", path.name, dest)
    return dest


__all__ = [
    "ROOT",
    "INBOX",
    "UNCLASSIFIED",
    "CONFLICTS",
    "SPANISH_MONTHS",
    "Classification",
    "IntakeConflict",
    "classify_invoice_file",
    "place_invoice_file",
    "quarantine_file",
]
