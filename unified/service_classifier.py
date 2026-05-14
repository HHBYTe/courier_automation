"""Per-carrier service-name → service_class classifier.

Maps each carrier's free-text service name to one of:
  - "parcel"  : parcel courier (tracked/express/standard parcel delivery)
  - "pallet"  : pallet freight
  - "letter"  : letter-class
  - "freight" : LTL/FTL or other non-parcel transport
  - "other"   : known shipment but no clean classification
  - None      : NOT a shipment (admin penalty, surcharge-only line, etc.)
                → caller rejects the row.

`None` is the strict signal: unrecognised service names from carriers
that emit non-shipment rows are rejected. For carriers whose every row
is by construction a shipment (Correos, Seitrans, WWEX), the fallback
is "other" rather than None so we don't accidentally drop legitimate
shipments because a service code is new.
"""
from __future__ import annotations

# Royal Mail-style RCN surcharge / admin penalty markers — even though
# Royal Mail is not currently in /data, we keep the list here so the
# classifier is complete if anyone wires it in later.
_REJECT_KEYWORDS: tuple[str, ...] = (
    "admin charge",
    "label incorrectly applied",
    "unreadable barcode",
    "oversize",
    "overweight",
    "rcn surcharge",
)


def classify(carrier: str, service_raw: object) -> str | None:
    if service_raw is None:
        # Carrier-specific fallbacks for missing service.
        return _missing_service_fallback(carrier)
    s = str(service_raw).strip()
    if not s:
        return _missing_service_fallback(carrier)
    low = s.lower()
    if any(k in low for k in _REJECT_KEYWORDS):
        return None

    if carrier == "correos":
        return "parcel"
    if carrier == "seitrans":
        return "pallet"
    if carrier == "seur":
        # SEUR Paq / Tracked are parcel; "Pallet" service marker = pallet.
        if "pallet" in low or "palet" in low:
            return "pallet"
        return "parcel"
    if carrier == "dachser":
        # Dachser is pallet/LTL freight.
        return "pallet"
    if carrier == "spring":
        if low in ("sign", "trck") or "tracked" in low or "signed" in low:
            return "parcel"
        return "other"
    if carrier == "ups":
        # All UPS parcel services map to parcel; freight services map to freight.
        if "freight" in low or "ltl" in low:
            return "freight"
        return "parcel"
    if carrier == "wwex":
        # WWEX is parcel + occasional LTL.
        if "ltl" in low or "ftl" in low or "freight" in low:
            return "freight"
        return "parcel"
    if carrier == "royalmail":
        # Check parcel markers before "letter": service names like
        # "ROYAL MAIL TRACKED 24" contain neither, but a "Large Letter"
        # weight-band line should land in "letter".
        if "tracked" in low or "parcel" in low or "signed" in low:
            return "parcel"
        if "letter" in low:
            return "letter"
        return "other"
    return "other"


def _missing_service_fallback(carrier: str) -> str | None:
    # If a row has no service name at all, it isn't a shipment we can
    # categorise. Reject.
    return None
