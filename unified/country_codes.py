"""Country-name → ISO 3166-1 alpha-2 mapping.

Carriers report destination country as:
  - Correos:   uppercase full Spanish name ('SPAIN', 'PORTUGAL', ...)
  - Seitrans:  uppercase full Italian name ('ITALIA', 'FRANCIA', ...)
  - Spring:    already ISO2 ('FR', 'DE', ...)
  - Dachser:   ISO2 ('ES', 'FR', ...)
  - UPS/WWEX:  ISO2

The mapping is intentionally exact-match (no fuzzy). An unmapped country
returns None — the row keeps the canonical null rather than guessing.
"""
from __future__ import annotations

_NAME_TO_ISO2: dict[str, str] = {
    # Spanish (Correos)
    "ESPAÑA": "ES", "SPAIN": "ES",
    "PORTUGAL": "PT",
    "FRANCIA": "FR", "FRANCE": "FR",
    "ALEMANIA": "DE", "GERMANY": "DE",
    "ITALIA": "IT", "ITALY": "IT",
    "REINO UNIDO": "GB", "UNITED KINGDOM": "GB", "INGLATERRA": "GB",
    "PAÍSES BAJOS": "NL", "PAISES BAJOS": "NL", "HOLANDA": "NL",
    "BÉLGICA": "BE", "BELGICA": "BE", "BELGIUM": "BE",
    "ANDORRA": "AD",
    "AUSTRIA": "AT",
    "SUIZA": "CH", "SWITZERLAND": "CH",
    "POLONIA": "PL", "POLAND": "PL",
    "DINAMARCA": "DK",
    "SUECIA": "SE",
    "NORUEGA": "NO",
    "IRLANDA": "IE", "IRELAND": "IE",
    "GRECIA": "GR", "GREECE": "GR",
    "FINLANDIA": "FI",
    "REPÚBLICA CHECA": "CZ", "REPUBLICA CHECA": "CZ", "CHEQUIA": "CZ",
    "HUNGRÍA": "HU", "HUNGRIA": "HU",
    "RUMANÍA": "RO", "RUMANIA": "RO",
    "ESLOVAQUIA": "SK",
    "ESLOVENIA": "SI",
    "LITUANIA": "LT",
    "LETONIA": "LV",
    "ESTONIA": "EE",
    "BULGARIA": "BG",
    "CROACIA": "HR",
    "LUXEMBURGO": "LU", "LUXEMBOURG": "LU",
    "MALTA": "MT",
    "CHIPRE": "CY",
    "ESTADOS UNIDOS": "US", "USA": "US", "UNITED STATES": "US",
    "MARRUECOS": "MA",
    "ANDORRA, PRINCIPADO DE": "AD",
    # Italian (Seitrans)
    "REGNO UNITO": "GB",
    "GERMANIA": "DE",
    "OLANDA": "NL", "PAESI BASSI": "NL",
    "BELGIO": "BE",
    "SPAGNA": "ES",
    "SVIZZERA": "CH",
}


def to_iso2(value: object) -> str | None:
    """Map a country name or code to ISO2; return None if unmappable."""
    if value is None:
        return None
    s = str(value).strip().upper()
    if not s:
        return None
    # Already-ISO2 passthrough (alphabetic 2-letter codes).
    if len(s) == 2 and s.isalpha():
        return s
    return _NAME_TO_ISO2.get(s)
