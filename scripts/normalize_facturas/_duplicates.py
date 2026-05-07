"""Shared duplicate-detection helper for the Facturas normalize scripts.

Walks a carrier's tree, groups files by SHA-256, and removes all-but-one copy
from each duplicate set. Keeper preference: deepest path (most-organised
location) > shortest filename > alphabetical.
"""

from __future__ import annotations

import hashlib
import sys
from collections import defaultdict
from pathlib import Path


def _hash_file(path: Path, *, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def _depth(path: Path, root: Path) -> int:
    return len(path.relative_to(root).parts)


def _pick_keeper(paths: list[Path], root: Path) -> Path:
    """Among byte-identical files, keep the one buried deepest in the tree
    (typically a properly-bucketed `<YYYY>/<MM> - <Mes>/` path), then with
    the shortest filename, then alphabetical for determinism."""
    return min(paths, key=lambda p: (-_depth(p, root), len(p.name), str(p)))


def dedupe(root: Path, *, apply: bool) -> tuple[int, int]:
    """Find content-duplicates under `root` and (when apply=True) delete all
    but one copy of each.

    Returns (groups_found, files_deleted).
    """
    if not root.is_dir():
        return (0, 0)

    groups: dict[str, list[Path]] = defaultdict(list)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            digest = _hash_file(path)
        except OSError as e:
            print(f"  ! cannot hash {path}: {e}", file=sys.stderr)
            continue
        groups[digest].append(path)

    duplicates = {h: ps for h, ps in groups.items() if len(ps) >= 2}
    if not duplicates:
        print("\n[dedupe] no duplicates found")
        return (0, 0)

    print(f"\n[dedupe] {len(duplicates)} duplicate group(s)")
    deleted = 0
    for digest, paths in sorted(duplicates.items(), key=lambda kv: str(kv[1][0])):
        keeper = _pick_keeper(paths, root)
        print(f"  keep:   {keeper.relative_to(root)}")
        for p in sorted(paths):
            if p == keeper:
                continue
            tag = "DELETE" if apply else "PLAN-DELETE"
            print(f"  {tag}: {p.relative_to(root)}")
            if apply:
                try:
                    p.unlink()
                except OSError as e:
                    print(f"  ! failed to delete {p}: {e}", file=sys.stderr)
                    continue
            deleted += 1
    return (len(duplicates), deleted)
