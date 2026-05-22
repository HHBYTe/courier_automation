"""SEUR invoice cost checker — single-file, deterministic.

Recomputes what each shipment SHOULD cost from SEUR's published 2026
tariffs and compares it to what SEUR billed, to catch overpayments.

Driven by an Outvio invoice-comparison export (one month per file).
For every order the checker takes the *declared* weight from Outvio
and the routing (service, destination) + billed amounts from the
matched SEUR invoice line, recomputes the base carriage and fuel, and
flags any gap.

Inputs:
  - checker/data/*.csv           Outvio invoice-comparison export(s), one
                                 per month — declared weight, carrier
                                 total, Outvio's own (untrusted) estimate
  - data/seur/2026-??.parquet    SEUR invoice lines — joined by order id
                                 (Referencia) for service, destination
                                 and the billed amounts
  - data/seur/other/Destino.csv  destination code -> provincia/comunidad
  - checker/tariffs/*.pdf        the tariffs the rate tables came from

Why both files: the Outvio export carries the independently-declared
weight (so weight inflation can't hide behind SEUR's own invoice) but
has no destination or service; the SEUR invoice has those. They join
on the order id, which is present in both.

Tariffs covered (TARIFA NACIONAL / INTERNACIONAL 2026):
  - S-24  -> national  "S-1 (24 H.)"          table
  - *B2C  -> national  "ENTREGA PARTICULARES" table
  - CLS   -> international "CLASSIC TERRESTRE" table (per-country)

Scope and known limitations:
  - The rate is recomputed on SEUR's billed weight, so the euro figure
    verifies SEUR applied the right tariff cell — it does not by itself
    prove the weight is honest. Artero's declared weight (`Peso original`)
    drives a separate `weight_check` flag: STRAY-HIGH marks orders billed
    well above the declared weight, to investigate (not a euro figure).
    Values <= 0.1 kg are a package-type placeholder and are skipped.
  - Fuel is checked against the FUEL_RATES periods on base = Portes +
    Zonas Remotas + Reexpedición. The Jan–Apr 2026 rates there are
    *reconstructed* (approximate); only official-notice periods are exact.
  - National peninsular Spain has four distance tiers (Area BCN / Corto /
    Medio / Largo) SEUR assigns by plaza pair, a table we don't have —
    the checker accepts a billed Portes matching ANY tier for the weight
    band. Island/Ceuta/Melilla/Portugal and international are pinned exactly.
  - Per-bulto / special small-print surcharges are out of scope.

Run:  python checker/check_seur.py            (writes checker/seur_check.csv)
      python checker/check_seur.py out.csv    (custom output path)
"""
from __future__ import annotations

import glob
import math
import sys
import unicodedata
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PARQUET_GLOB = str(ROOT / "data" / "seur" / "2026-??.parquet")
DESTINO_CSV = ROOT / "data" / "seur" / "other" / "Destino.csv"
OUTVIO_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_OUT = Path(__file__).resolve().parent / "seur_check.csv"

TOL = 0.011       # euros; tariff rates carry 2 decimals
FUEL_TOL = 0.03   # euros; looser — fuel % is rounded and reconstructed
WEIGHT_STRAY_RATIO = 1.5  # billed vs declared weight: flag beyond this ratio
WEIGHT_STRAY_KG = 0.5     # ...and beyond this absolute kg gap

# --- Fuel surcharge -------------------------------------------------------
# SEUR's fuel ("tasa de energía") index changes weekly and is published on
# seur.com — it is NOT in the tariff PDF. Each notice gives an effective
# date range and two percentages: one for national + Portugal, one for
# international. Transcribe each notice as a row below — dates inclusive,
# percentages as fractions (16,77% -> 0.1677). The check matches each
# line's `Fecha Servicio` against these ranges; a line outside every range
# is reported NO-INDEX. Leave the list empty to skip the fuel check.
#   (start, end, national+Portugal fraction, international fraction)
FUEL_RATES: list[tuple[str, str, float, float]] = [
    # Jan–Apr 2026: reconstructed from the invoices themselves (the per-week
    # median of Cargo Combustible / Portes). These confirm consistency and
    # catch per-line outliers; they are not an independent source.
    ("2025-12-29", "2026-01-04", 0.0686, 0.1674),
    ("2026-01-05", "2026-01-11", 0.0684, 0.1671),
    ("2026-01-12", "2026-01-18", 0.0684, 0.1672),
    ("2026-01-19", "2026-01-25", 0.0684, 0.1673),
    ("2026-01-26", "2026-02-01", 0.0687, 0.1673),
    ("2026-02-02", "2026-02-08", 0.0665, 0.1670),
    ("2026-02-09", "2026-02-15", 0.0665, 0.1670),
    ("2026-02-16", "2026-02-22", 0.0664, 0.1671),
    ("2026-02-23", "2026-03-01", 0.0665, 0.1671),
    ("2026-03-02", "2026-03-08", 0.0665, 0.1671),
    ("2026-03-09", "2026-03-15", 0.0697, 0.1790),
    ("2026-03-16", "2026-03-22", 0.0732, 0.1827),
    ("2026-03-23", "2026-03-29", 0.0772, 0.1905),
    ("2026-03-30", "2026-04-05", 0.0773, 0.1952),
    ("2026-04-06", "2026-04-12", 0.0774, 0.1952),
    ("2026-04-13", "2026-04-19", 0.0757, 0.1998),
    ("2026-04-20", "2026-04-26", 0.0843, 0.2034),
    ("2026-04-27", "2026-05-03", 0.0832, 0.1998),
    # From here on: official SEUR weekly notices ("tasa de energía").
    ("2026-05-18", "2026-05-24", 0.1677, 0.2000),
]

# --- National tariff: S-24  ("S-1 (24 H.)") -------------------------------
# Weight bands in kg; rate tuples align to S24_BANDS index-for-index.
S24_BANDS = (1, 3, 5, 10, 15, 20, 25, 30, 40, 50)
S24_RATES = {
    "area_bcn":          (2.96, 3.02, 3.18, 4.33, 4.89, 5.58, 6.23, 6.90, 8.21, 9.53),
    "corto":             (2.96, 3.02, 3.63, 4.42, 4.71, 5.39, 5.97, 7.08, 8.35, 9.65),
    "medio":             (3.02, 3.10, 4.37, 5.41, 6.04, 7.13, 8.07, 9.83, 11.96, 14.11),
    "largo":             (3.02, 3.10, 4.37, 5.41, 6.04, 7.38, 8.07, 9.83, 11.96, 14.11),
    "portugal":          (3.47, 3.88, 4.36, 6.10, 7.64, 9.88, 12.40, 14.90, 20.05, 24.21),
    "baleares":          (5.81, 7.27, 8.51, 13.14, 16.36, 20.56, 25.29, 30.36, 36.28, 42.19),
    "canarias_menores":  (10.83, 19.43, 27.93, 59.79, 81.05, 112.89, 145.52, 176.56, 219.04, 261.53),
    "canarias_mayores":  (10.83, 19.43, 27.93, 59.79, 81.05, 112.89, 145.52, 176.56, 219.04, 261.53),
    "ceuta":             (10.57, 13.17, 15.45, 24.04, 29.78, 38.34, 44.21, 55.94, 67.36, 78.78),
    "melilla":           (10.57, 13.17, 15.45, 24.04, 29.78, 38.34, 44.21, 55.94, 67.36, 78.78),
}
# Above the top band: (flat rate, extra €/kg, kg from which the per-kg
# applies). S-24's per-kg starts right at the 50 kg band.
S24_OVER = {
    "area_bcn": (9.53, 0.18, 50), "corto": (9.65, 0.19, 50), "medio": (14.11, 0.28, 50),
    "largo": (14.11, 0.28, 50), "portugal": (24.21, 0.47, 50), "baleares": (42.19, 0.59, 50),
    "canarias_menores": (261.53, 4.25, 50), "canarias_mayores": (261.53, 4.25, 50),
    "ceuta": (78.78, 1.14, 50), "melilla": (78.78, 1.14, 50),
}

# --- National tariff: *B2C  ("ENTREGA PARTICULARES") ----------------------
B2C_BANDS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 20, 25, 30)
B2C_RATES = {
    "area_bcn":         (3.06, 3.28, 3.48, 3.70, 3.93, 4.22, 4.51, 4.80, 5.10, 5.39, 6.45, 7.98, 9.74, 11.19),
    "corto":            (3.39, 3.64, 3.88, 4.12, 4.36, 4.68, 5.00, 5.32, 5.65, 5.97, 7.17, 8.87, 10.82, 12.43),
    "medio":            (3.39, 3.64, 3.88, 4.12, 4.36, 4.68, 5.00, 5.32, 5.65, 5.97, 7.17, 8.87, 10.82, 12.43),
    "largo":            (3.39, 3.64, 3.88, 4.12, 4.36, 4.77, 5.19, 5.64, 6.20, 6.94, 8.55, 10.94, 14.07, 16.79),
    "portugal":         (3.47, 3.71, 3.96, 4.20, 4.44, 5.10, 5.54, 6.04, 6.65, 7.42, 9.15, 11.70, 15.05, 17.96),
    "baleares":         (5.81, 6.56, 7.32, 7.86, 8.51, 9.26, 10.12, 11.09, 12.11, 13.14, 16.47, 20.56, 25.51, 31.00),
    "canarias_menores": (9.33, 12.39, 15.41, 18.32, 21.24, 24.21, 28.25, 32.94, 38.65, 46.03, 57.61, 79.40, 102.49, 132.53),
    "canarias_mayores": (9.33, 12.39, 15.41, 18.32, 21.24, 24.21, 28.25, 32.94, 38.65, 46.03, 57.61, 79.40, 102.49, 132.53),
    "ceuta":            (10.57, 11.91, 13.17, 14.32, 15.45, 18.32, 21.18, 21.75, 22.78, 24.04, 29.78, 38.34, 44.21, 55.94),
    "melilla":          (10.57, 11.91, 13.17, 14.32, 15.45, 18.32, 21.18, 21.75, 22.78, 24.04, 29.78, 38.34, 44.21, 55.94),
}
# Above the 30 kg top band: flat at the 30 kg rate up to 50 kg, then the
# per-kg extension applies from 50 kg on (verified against real invoices).
B2C_OVER = {
    "area_bcn": (11.19, 0.30, 50), "corto": (12.43, 0.33, 50), "medio": (12.43, 0.33, 50),
    "largo": (16.79, 0.44, 50), "portugal": (17.96, 0.47, 50), "baleares": (31.00, 0.63, 50),
    "canarias_menores": (132.53, 3.01, 50), "canarias_mayores": (132.53, 3.01, 50),
    "ceuta": (55.94, 1.18, 50), "melilla": (55.94, 1.18, 50),
}

# --- International tariff: CLS  ("CLASSIC TERRESTRE") ---------------------
# One column per country. Rate tuples align to INTL_BANDS.
INTL_BANDS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 20, 25, 32)
INTL_RATES = {
    "ALEMANIA":        (8.25, 8.90, 9.55, 9.97, 10.41, 10.78, 11.15, 11.41, 11.66, 11.90, 13.82, 14.83, 20.74, 22.72),
    "AUSTRIA":         (9.12, 9.97, 10.84, 11.71, 12.58, 13.09, 13.61, 13.96, 14.31, 14.65, 17.28, 18.77, 23.72, 25.70),
    "BELGICA":         (8.25, 8.90, 9.55, 9.97, 10.41, 10.78, 11.15, 11.41, 11.66, 11.90, 13.82, 14.83, 20.74, 22.72),
    "BOSNIA Y HERZEGOVINA": (26.88, 27.76, 28.63, 31.24, 33.83, 34.76, 35.68, 36.30, 36.91, 37.52, 42.47, 45.46, 66.20, 68.17),
    "BULGARIA":        (18.21, 19.08, 19.96, 20.82, 21.69, 22.45, 23.20, 23.70, 24.21, 24.71, 28.66, 30.63, 43.47, 46.45),
    "CROACIA":         (15.62, 16.92, 18.21, 19.08, 19.96, 20.69, 21.41, 21.91, 22.39, 22.88, 26.68, 29.63, 34.58, 39.53),
    "DINAMARCA":       (9.55, 10.42, 11.28, 12.79, 14.31, 15.07, 15.85, 16.36, 16.87, 17.39, 19.75, 20.74, 28.66, 34.58),
    "ESLOVAQUIA":      (9.55, 10.42, 11.28, 12.79, 14.31, 15.07, 15.85, 16.36, 16.87, 17.39, 19.75, 20.74, 28.66, 34.58),
    "ESLOVENIA":       (9.55, 10.42, 11.28, 12.79, 14.31, 15.07, 15.85, 16.36, 16.87, 17.39, 19.75, 20.74, 28.66, 34.58),
    "ESTONIA":         (15.62, 16.92, 18.21, 19.08, 19.96, 20.69, 21.41, 21.91, 22.39, 22.88, 26.68, 29.63, 34.58, 39.53),
    "FINLANDIA":       (18.21, 19.08, 19.96, 20.82, 21.69, 22.45, 23.20, 23.70, 24.21, 24.71, 28.66, 30.63, 43.47, 46.45),
    "FRANCIA":         (7.84, 8.46, 9.07, 9.47, 9.89, 10.24, 10.59, 10.84, 11.08, 11.31, 13.13, 14.09, 19.70, 21.58),
    "GRECIA":          (18.21, 19.08, 19.96, 20.82, 21.69, 22.45, 23.20, 23.70, 24.21, 24.71, 28.66, 30.63, 43.47, 46.45),
    "HUNGRIA":         (9.55, 10.42, 11.28, 12.79, 14.31, 15.07, 15.85, 16.36, 16.87, 17.39, 19.75, 20.74, 28.66, 34.58),
    "IRLANDA":         (9.55, 10.42, 11.28, 12.79, 14.31, 15.07, 15.85, 16.36, 16.87, 17.39, 19.75, 20.74, 28.66, 34.58),
    "ITALIA":          (8.66, 9.47, 10.30, 11.12, 11.95, 12.44, 12.93, 13.26, 13.59, 13.92, 16.42, 17.83, 22.53, 24.42),
    "LETONIA":         (15.62, 16.92, 18.21, 19.08, 19.96, 20.69, 21.41, 21.91, 22.39, 22.88, 26.68, 29.63, 34.58, 39.53),
    "LITUANIA":        (15.62, 16.92, 18.21, 19.08, 19.96, 20.69, 21.41, 21.91, 22.39, 22.88, 26.68, 29.63, 34.58, 39.53),
    "LUXEMBURGO":      (8.25, 8.90, 9.55, 9.97, 10.41, 10.78, 11.15, 11.41, 11.66, 11.90, 13.82, 14.83, 20.74, 22.72),
    "NORUEGA":         (26.88, 27.76, 28.63, 31.24, 33.83, 34.76, 35.68, 36.30, 36.91, 37.52, 42.47, 45.46, 66.20, 68.17),
    "PAISES BAJOS":    (8.25, 8.90, 9.55, 9.97, 10.41, 10.78, 11.15, 11.41, 11.66, 11.90, 13.82, 14.83, 20.74, 22.72),
    "POLONIA":         (9.55, 10.42, 11.28, 12.79, 14.31, 15.07, 15.85, 16.36, 16.87, 17.39, 19.75, 20.74, 28.66, 34.58),
    "REINO UNIDO":     (9.12, 9.97, 10.84, 11.71, 12.58, 13.09, 13.61, 13.96, 14.31, 14.65, 17.28, 18.77, 23.72, 25.70),
    "REPUBLICA CHECA": (9.12, 9.97, 10.84, 11.71, 12.58, 13.09, 13.61, 13.96, 14.31, 14.65, 17.28, 18.77, 23.72, 25.70),
    "RUMANIA":         (18.21, 19.08, 19.96, 20.82, 21.69, 22.45, 23.20, 23.70, 24.21, 24.71, 28.66, 30.63, 43.47, 46.45),
    "SERBIA Y MONTENEGRO": (26.88, 27.76, 28.63, 31.24, 33.83, 34.76, 35.68, 36.30, 36.91, 37.52, 42.47, 45.46, 66.20, 68.17),
    "SUECIA":          (15.62, 16.92, 18.21, 19.08, 19.96, 20.69, 21.41, 21.91, 22.39, 22.88, 26.68, 29.63, 34.58, 39.53),
    "SUIZA":           (18.21, 19.08, 19.96, 20.82, 21.69, 22.45, 23.20, 23.70, 24.21, 24.71, 28.66, 30.63, 43.47, 46.45),
}
# Above 32 kg: flat at the 32 kg rate, except where a per-kg extension applies.
INTL_OVER_PERKG = {"REINO UNIDO": 1.18}
# País names as they appear in Destino.csv -> tariff column key.
INTL_COUNTRY_ALIAS = {"HOLANDA": "PAISES BAJOS"}


def num(value: object) -> float:
    """Float value, with NA / NaN / non-numeric coerced to 0.0."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(f) else f


def fuel_rate(service_date: object, scope: str) -> tuple[str, float | None]:
    """(period label, fuel fraction) for a service date — ('', None) if none."""
    d = pd.to_datetime(service_date, errors="coerce")
    if pd.isna(d):
        return "", None
    for start, end, national, international in FUEL_RATES:
        if pd.Timestamp(start) <= d <= pd.Timestamp(end):
            label = f"{start}..{end}"
            if scope == "national":
                return label, national
            if scope == "international":
                return label, international
            return label, None
    return "", None


def norm(value: object) -> str:
    """Uppercase, accent-stripped, trimmed string (NA-safe)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    s = unicodedata.normalize("NFKD", str(value))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.strip().upper()


def load_destinos() -> dict[str, dict[str, str]]:
    """Destino code -> {provincia, comunidad, pais}."""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            df = pd.read_csv(DESTINO_CSV, encoding=enc)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    else:
        raise SystemExit(f"could not read {DESTINO_CSV}")
    code_col, prov_col, com_col, pais_col = df.columns[:4]
    out: dict[str, dict[str, str]] = {}
    for _, r in df.iterrows():
        out[norm(r[code_col])] = {
            "provincia": norm(r[prov_col]),
            "comunidad": norm(r[com_col]),
            "pais": norm(r[pais_col]),
        }
    return out


def band_rate(bands: tuple[int, ...], rates: tuple[float, ...],
              over: tuple[float, float, int], weight: float) -> tuple[float, bool]:
    """Rate for a weight against one zone/country column.

    Returns (expected_rate, is_heavy). is_heavy=True means the per-kg
    extension above `from_kg` was used.
    """
    for i, b in enumerate(bands):
        if weight <= b + 1e-9:
            return rates[i], False
    base, per_kg, from_kg = over
    if weight <= from_kg + 1e-9:
        return base, False  # flat between the top band and from_kg
    # per-kg extension; weight rounded UP to a whole kg (matches SEUR).
    return round(base + per_kg * (math.ceil(weight) - from_kg), 2), True


PENINSULAR = ["area_bcn", "corto", "medio", "largo"]


def national_zones(dest: dict[str, str] | None, postcode: object) -> list[str]:
    """Candidate national zone keys for a destination (empty = can't place).

    Island / Ceuta / Melilla zones are resolved from the destination
    postcode (the real address) first, since the `Destino` code is a
    SEUR plaza and can differ from where the parcel actually goes.
    """
    pc = norm(postcode)
    pc2 = pc[:2] if (len(pc) == 5 and pc.isdigit()) else ""
    if pc2 == "07":
        return ["baleares"]
    if pc2 in ("35", "38"):
        return ["canarias_menores", "canarias_mayores"]
    if pc2 == "51":
        return ["ceuta"]
    if pc2 == "52":
        return ["melilla"]
    # Postcode absent or peninsular: fall back to the destination record.
    if dest is not None:
        pais, com, prov = dest["pais"], dest["comunidad"], dest["provincia"]
        if pais == "PORTUGAL":
            return ["portugal"]
        if "BALEARES" in com:
            return ["baleares"]
        if "CANARIAS" in com:
            return ["canarias_menores", "canarias_mayores"]
        if "CEUTA" in (com, prov):
            return ["ceuta"]
        if "MELILLA" in (com, prov):
            return ["melilla"]
        if pais in ("ESPANA", "ESPAÑA"):
            # Peninsular Spain: four distance tiers we can't pin (see docstring).
            return PENINSULAR
        if pais:
            return []  # a foreign country — not a national shipment
    # No destination record but a valid 5-digit ES postcode: peninsular.
    return PENINSULAR if pc2 else []


def check_national(service_rates, service_bands, service_over,
                   zones: list[str], weight: float, billed: float):
    """Return (expected_repr, expected_nearest, verdict, note)."""
    candidates = []
    heavy = False
    for z in zones:
        rate, is_heavy = band_rate(service_bands, service_rates[z],
                                   service_over[z], weight)
        candidates.append((z, rate))
        heavy = heavy or is_heavy
    note = "heavy-approx" if heavy else ""
    rates = [r for _, r in candidates]
    match = next((r for r in rates if abs(billed - r) <= TOL), None)
    if match is not None:
        return f"{match:.2f}", match, "OK", note
    nearest = min(rates, key=lambda r: abs(billed - r))
    lo, hi = min(rates), max(rates)
    verdict = "OVERCHARGE" if billed > hi else "UNDERCHARGE" if billed < lo else "MISMATCH"
    rng = f"{lo:.2f}" if lo == hi else f"{lo:.2f}-{hi:.2f}"
    return rng, nearest, verdict, note


def check_international(country: str, weight: float, billed: float):
    """Return (expected_repr, expected, verdict, note)."""
    rates = INTL_RATES[country]
    for i, b in enumerate(INTL_BANDS):
        if weight <= b + 1e-9:
            expected, heavy = rates[i], False
            break
    else:
        base = rates[-1]
        per_kg = INTL_OVER_PERKG.get(country, 0.0)
        expected = round(base + per_kg * (math.ceil(weight) - INTL_BANDS[-1]), 2)
        heavy = True
    note = "heavy-approx" if heavy else ""
    if abs(billed - expected) <= TOL:
        verdict = "OK"
    elif billed > expected:
        verdict = "OVERCHARGE"
    else:
        verdict = "UNDERCHARGE"
    return f"{expected:.2f}", expected, verdict, note


def check_order(o_row, inv_by_ref, destinos):
    """Evaluate one Outvio order against the SEUR tariff -> result dict.

    The rate is recomputed on SEUR's billed weight; routing (service,
    destination) and the billed amounts come from the matched SEUR
    invoice line. Artero's declared weight (Outvio `Peso original`)
    feeds a separate weight-sanity flag — it does not drive the price.
    """
    order_id = str(o_row["ID del pedido"]).strip()
    declared = num(o_row["Peso original"])
    res = {
        "order_id": order_id,
        "tracking": o_row["Seguimiento courier"],
        "declared_weight": round(declared, 3) if declared > 0.1 else "",
        "carrier_total": round(num(o_row["Total mensajería"]), 2),
        "outvio_estimate": round(num(o_row["Total original"]), 2),
        "factura": "", "servicio": "", "destino": "",
        "billed_weight": "", "weight_check": "",
        "billed_portes": "", "importe": "",
        "scope": "", "zone_or_country": "", "expected_portes": "",
        "diff": "", "verdict": "", "note": "",
    }

    inv = inv_by_ref.get(order_id)
    if inv is None:
        res.update(verdict="UNVERIFIED", note="order not in the SEUR invoice data")
        return res

    portes = num(inv["Portes"])
    service = norm(inv["Servicio"])
    weight = max(num(inv["Peso"]), num(inv["Peso Volumetrico"]))
    dest = destinos.get(norm(inv["Destino"]))

    # Weight sanity vs Artero's declared weight. `Peso original` <= 0.1 is
    # a package-type placeholder, not a real weight — skip those.
    if declared <= 0.1:
        weight_check = "no-declared"
    elif (weight > declared * WEIGHT_STRAY_RATIO
          and weight - declared > WEIGHT_STRAY_KG):
        weight_check = "STRAY-HIGH"  # SEUR's weight well above ours
    elif (declared > weight * WEIGHT_STRAY_RATIO
          and declared - weight > WEIGHT_STRAY_KG):
        weight_check = "stray-low"
    else:
        weight_check = "ok"

    res.update(
        factura=inv["Numero Factura"], servicio=inv["Servicio"],
        destino=inv["Destino"], billed_weight=round(weight, 3),
        weight_check=weight_check, billed_portes=round(portes, 2),
        importe=round(num(inv["Importe facturado (sin impuestos)"]), 2),
    )

    if portes <= 0:
        res.update(verdict="UNVERIFIED", note="no carriage charge on the invoice")
        return res
    if weight <= 0:
        res.update(verdict="UNVERIFIED", note="no weight available")
        return res

    if service in ("S-24", "*B2C"):
        zones = national_zones(dest, inv["C. Postal Destinatario"])
        if not zones:
            res.update(scope="national", verdict="UNVERIFIED",
                       note=f"destination not placeable ({norm(inv['Destino'])})")
            return res
        rates, bands, over = ((S24_RATES, S24_BANDS, S24_OVER) if service == "S-24"
                              else (B2C_RATES, B2C_BANDS, B2C_OVER))
        rep, nearest, verdict, note = check_national(
            rates, bands, over, zones, weight, portes)
        res.update(scope="national", zone_or_country="/".join(zones),
                   expected_portes=rep, diff=round(portes - nearest, 2),
                   verdict=verdict, note=note)
    elif service == "CLS":
        pais = dest["pais"] if dest else ""
        country = INTL_COUNTRY_ALIAS.get(pais, pais)
        if country not in INTL_RATES:
            res.update(scope="international", verdict="UNVERIFIED",
                       note=f"country not in tariff ({pais or norm(inv['Destino'])})")
            return res
        rep, expected, verdict, note = check_international(country, weight, portes)
        res.update(scope="international", zone_or_country=country,
                   expected_portes=rep, diff=round(portes - expected, 2),
                   verdict=verdict, note=note)
    else:
        res.update(verdict="UNVERIFIED",
                   note=f"service not modelled ({inv['Servicio']})")
    return res


def add_fuel_check(res: dict, row) -> None:
    """Append fuel-line columns from FUEL_RATES.

    National + Portugal and international carry different percentages; the
    one to apply is chosen from the line's scope.
    """
    period, pct = fuel_rate(row["Fecha Servicio"], res.get("scope", ""))
    res["fuel_period"] = period
    res["billed_fuel"] = round(num(row["Cargo Combustible"]), 2)
    if pct is None:
        res["expected_fuel"] = ""
        res["fuel_verdict"] = "NO-INDEX"
    else:
        # The fuel index is applied to portes + the transport-related extras
        # (remote-zone and special reexpedición), not the fixed tasas —
        # verified against the invoices. FUEL_TOL absorbs 2-decimal rounding.
        base = (num(row["Portes"]) + num(row["Zonas Remotas"])
                + num(row["Reexpedicion Especial"]))
        expected = round(base * pct, 2)
        res["expected_fuel"] = f"{expected:.2f}"
        res["fuel_verdict"] = (
            "OK" if abs(res["billed_fuel"] - expected) <= FUEL_TOL
            else "MISMATCH")

    # Per-line billed-vs-tariff gap, base + fuel combined where each is known.
    portes_gap = res["diff"] if isinstance(res["diff"], (int, float)) else None
    fuel_gap = (round(res["billed_fuel"] - float(res["expected_fuel"]), 2)
                if res["expected_fuel"] != "" else None)
    res["fuel_diff"] = fuel_gap if fuel_gap is not None else ""
    gaps = [g for g in (portes_gap, fuel_gap) if g is not None]
    res["cost_diff"] = round(sum(gaps), 2) if gaps else ""


def write_comparison_csv(outvio_df: pd.DataFrame, results: pd.DataFrame,
                         path: Path) -> None:
    """Write the Outvio export with our independent verdict appended.

    Four extra columns so each order shows Outvio's estimate and ours
    side by side: our recomputed expected total, our gap (billed minus
    expected), our verdict and the reason.
    """
    res = results.drop_duplicates("order_id", keep="first").set_index("order_id")
    expected, gap, verdict, reason = [], [], [], []
    for oid in outvio_df["ID del pedido"].astype(str).str.strip():
        if oid not in res.index:
            expected.append("")
            gap.append("")
            verdict.append("")
            reason.append("")
            continue
        r = res.loc[oid]
        cd = r["cost_diff"]
        if isinstance(cd, (int, float)) and not pd.isna(cd):
            gap.append(round(cd, 2))
            expected.append(round(num(r["carrier_total"]) - cd, 2))
        else:
            gap.append("")
            expected.append("")
        verdict.append(r["verdict"])
        tokens = []
        if r["verdict"] in ("OVERCHARGE", "UNDERCHARGE", "MISMATCH"):
            tokens.append("Tarifa base")
        if r["fuel_verdict"] == "MISMATCH":
            tokens.append("Combustible")
        if r["weight_check"] == "STRAY-HIGH":
            tokens.append("Peso")
        reason.append("; ".join(tokens))
    out = outvio_df.copy()
    out["Total esperado (checker)"] = expected
    out["Sobrecargo (checker)"] = gap
    out["Veredicto (checker)"] = verdict
    out["Razón (checker)"] = reason
    out.to_csv(path, index=False, encoding="utf-8-sig")


def _summary(out: pd.DataFrame, n_files: int, out_path: Path,
             cmp_path: Path) -> None:
    print(f"SEUR cost check — {len(out)} orders from {n_files} Outvio export(s)")
    print(f"  report:     {out_path}")
    print(f"  comparison: {cmp_path}")

    counts = out["verdict"].value_counts()
    verd = "   ".join(f"{v} {counts[v]}" for v in
                      ("OK", "OVERCHARGE", "UNDERCHARGE", "MISMATCH", "UNVERIFIED")
                      if v in counts)
    print(f"\n  base-carriage verdicts:  {verd}")

    priced = out[out["diff"] != ""].copy()
    priced["diff"] = pd.to_numeric(priced["diff"])
    billed_p = pd.to_numeric(priced["billed_portes"]).sum()
    diff_p = priced["diff"].sum()
    over = priced.loc[priced["diff"] > TOL, "diff"]
    under = priced.loc[priced["diff"] < -TOL, "diff"]
    print("\n  COST COMPARISON — billed vs SEUR tariff "
          "(recomputed on the billed weight)")
    print(f"  Base carriage — {len(priced)} orders verified")
    print(f"    billed              {billed_p:>13,.2f} €")
    print(f"    tariff (expected)   {billed_p - diff_p:>13,.2f} €")
    print(f"    billed over tariff  {diff_p:>+13,.2f} €    "
          f"(over +{over.sum():,.2f} / {len(over)} lines,"
          f" under {under.sum():,.2f} / {len(under)} lines)")

    fuel = out[~out["fuel_verdict"].isin(["NO-INDEX", "NO-INVOICE"])].copy()
    fuel_gap = 0.0
    if fuel.empty:
        print("\n  Fuel — not checked (no order falls in a FUEL_RATES period)")
    else:
        fuel["expected_fuel"] = pd.to_numeric(fuel["expected_fuel"])
        fuel_gap = fuel["billed_fuel"].sum() - fuel["expected_fuel"].sum()
        mm = int((fuel["fuel_verdict"] == "MISMATCH").sum())
        print(f"\n  Fuel — {len(fuel)} orders checked   "
              "[reconstructed rates, soft signal]")
        print(f"    billed over expected {fuel_gap:>+13,.2f} €  ({mm} mismatches)")

    print(f"\n  Combined net billed over tariff  {diff_p + fuel_gap:>+13,.2f} €")

    wc = out["weight_check"].value_counts()
    high = int(wc.get("STRAY-HIGH", 0))
    declared_n = len(out) - int(wc.get("no-declared", 0))
    print("\n  WEIGHT SANITY — billed weight vs Artero's declared weight")
    print(f"    {declared_n} orders with a usable declared weight; "
          f"{high} billed well above it (STRAY-HIGH).")
    print("    A stray is something to investigate, not a euro figure — the")
    print("    carrier may have a heavier package or our declared weight was off.")


def main(out_path: Path) -> int:
    outvio_files = sorted(glob.glob(str(OUTVIO_DIR / "*.csv")))
    if not outvio_files:
        print(f"no Outvio export CSVs in {OUTVIO_DIR}", file=sys.stderr)
        return 1
    files = sorted(glob.glob(PARQUET_GLOB))
    if not files:
        print(f"no SEUR parquet found at {PARQUET_GLOB}", file=sys.stderr)
        return 1

    outvio = pd.concat([pd.read_csv(f) for f in outvio_files], ignore_index=True)
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    destinos = load_destinos()

    # One invoice row per order id (the principal carriage line).
    inv = df[df["Portes"].fillna(0) > 0].copy()
    inv["_ref"] = inv["Referencia"].astype("string").fillna("")
    inv = inv.sort_values("Portes").drop_duplicates("_ref", keep="last")
    inv_by_ref = inv.set_index("_ref").to_dict("index")

    results = []
    for _, o_row in outvio.iterrows():
        res = check_order(o_row, inv_by_ref, destinos)
        inv_row = inv_by_ref.get(res["order_id"])
        if inv_row is not None:
            add_fuel_check(res, inv_row)
        else:
            res.update(fuel_period="", billed_fuel="", expected_fuel="",
                       fuel_verdict="NO-INVOICE", fuel_diff="", cost_diff="")
        results.append(res)

    out = pd.DataFrame(results)
    out.to_csv(out_path, index=False, encoding="utf-8-sig")
    cmp_path = out_path.with_name(out_path.stem + "_outvio.csv")
    write_comparison_csv(outvio, out, cmp_path)
    _summary(out, len(outvio_files), out_path, cmp_path)
    return 0


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    raise SystemExit(main(out))
