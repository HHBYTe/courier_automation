"""Per-carrier normalizers.

Each module exposes `normalize(df: pd.DataFrame, source_file: str) ->
pd.DataFrame`. The output has the canonical schema columns plus an
internal `_reject_reason` (string, nullable). The caller routes rows
with `_reject_reason is not null` to the rejection log.

Currency is preserved (`currency` column = EUR / GBP / USD); no FX
conversion happens here.
"""
from __future__ import annotations

from . import correos, dachser, royalmail, seitrans, seur, spring, ups, wwex

REGISTRY = {
    "correos": correos.normalize,
    "seitrans": seitrans.normalize,
    "seur": seur.normalize,
    "dachser": dachser.normalize,
    "spring": spring.normalize,
    "ups": ups.normalize,
    "wwex": wwex.normalize,
    "royalmail": royalmail.normalize,
}

__all__ = ["REGISTRY"]
