"""Dachser parser.

Raw invoices ship in two distinct Excel layouts. Both carry the same 55
data columns; only the header row position and the column-name spellings
differ.

  - **Bracketed**: a junk title row at row 0 ("Salida dinámica de lista
    …"), blank rows 1–2, the actual header at row 3, blank row 4, data
    starting row 5. Column 0 of every row is a phantom blank ("Unnamed:
    0"). Column names are abbreviations (`Doc.vtas.`, `OfVta`,
    `FechaFact/`, …). Both ES and FR files use this layout, with minor
    whitespace differences in the header text.
  - **Clean**: header at row 0, data starting row 1. Long, fully-spelled
    column names (`Documento de ventas`, `Oficina de ventas`,
    `Fecha factura`, …).

The historical workbook (`Expediciones Dachser.xlsx`, sheet `New Datos`)
has 60 columns: 5 derived columns at the front (`Tipo Bulto`,
`Q Expediciones`, `Año`, `Mes`, `Tipo Exp.`) and the same 55 raw columns
under their canonical compact names.

Parser flow:
  1. Sniff which raw layout the file uses.
  2. Read the data rows; drop the phantom column 0 if present.
  3. Strip whitespace from header names.
  4. Rename to the canonical compact names (`_RENAME_BRACKETED`/
     `_RENAME_CLEAN`).
  5. Add the 5 derived columns.
  6. Coerce dtypes on the 60-column historical schema.
  7. Plausibility checks.
  8. Derive `invoice_number` from the `Factura` column (first row's
     value; multi-Factura files take the first as the canonical key).

Derivation rules — verified against the master `New Datos` sheet:
  - **Año / Mes** = year / month of `Fecha factura` (100% match across
    6,117 historical rows).
  - **Tipo Bulto** = weight tier from `Peso`. Buckets:
    {1, 3, 5, 10, 15, 20, 25, 30, 40, 50, 75, 100, 125, 150, 175, 200} —
    each weight is rounded UP to the smallest containing bucket and
    formatted as `"NNN KG"` (zero-padded to 3 digits). Weights >200 →
    `"MÁS 200 KG"`.
  - **Tipo Exp.** = `"Pallet"` if `Peso > 50` else `"Bulto"` (clean split
    confirmed across all 5,904 typed rows).
  - **Q Expediciones** = first-occurrence dedup on `Doc. Vtas` (1 on
    first row, 0 on later duplicates within the file). The historical
    sheet has occasional running counts up to 140 that this rule
    doesn't reproduce; ~33% divergence is expected and excluded from any
    future golden comparison, mirroring the seitrans approach for the
    same shape of column.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import ClassVar

import pandas as pd

from courier_automation.parsers.base import (
    ParseResult,
    ParserError,
    SchemaMismatch,
    assert_schema,
    compute_file_hash,
    to_clean_string,
)
from courier_automation.parsers.plausibility import assert_plausible

log = logging.getLogger(__name__)

# Canonical (master) raw column names — what we rename both variants to
# before adding derived columns.
DACHSER_RAW_CANONICAL: tuple[str, ...] = (
    "Doc. Vtas",
    "Oficina de ventas",
    "Solicitante",
    "Nombre 1",
    # Master column header has a trailing space + NBSP. Match exactly so
    # the schema check against the live workbook lines up.
    "Código Tráfico ",
    "C. Traf. B.",
    "Factura",
    "Fecha factura",
    "Clase factura",
    "Denominación",
    "Sal./Lleg.",
    "Fecha doc.",
    "N Exp.",
    "Pedido",
    "N único",
    "Peso",
    "Volumen",
    "Bultos",
    "Plz. Orig",
    "Origen",
    "Región Ori",
    "CP Origen",
    "País Ori",
    "Remitente",
    "Plz. Dest.",
    "Destino",
    "Región Des",
    "CP Destino",
    "País Dest.",
    "Consignatario",
    "II",
    "Significado",
    "Portes",
    "Reexp+Des",
    "Seguro",
    "Reembolso",
    "Suplidos",
    "Servicios",
    "Otros",
    "Manipulac.",
    "Administ",
    "Distrib.",
    "Almacenaje",
    "Importe neto",
    "Ref1",
    "FR LUMP-S",
    "BACK BILL.",
    "TRANSIT FR",
    "ADMINISTR.",
    "WAREHOUSE",
    "UNLOAD&DIS",
    "SERV.CHARG",
    "Incot",
    "Incoterms2",
    "ID Consol.",
)
assert len(DACHSER_RAW_CANONICAL) == 55

# Bracketed-variant raw header → canonical. Headers are stripped of
# surrounding whitespace before lookup; values that match canonical
# directly (Volumen, Bultos, …) are absent here.
_RENAME_BRACKETED: dict[str, str] = {
    "Doc.vtas.": "Doc. Vtas",
    "OfVta": "Oficina de ventas",
    "Solic.": "Solicitante",
    "Cod. Traf.": "Código Tráfico ",
    "C. Traf. B": "C. Traf. B.",
    "FechaFact/": "Fecha factura",
    "FechaFact.": "Fecha factura",
    "ClFac": "Clase factura",
    "Fecha doc/": "Fecha doc.",
    "Fecha doc.": "Fecha doc.",
    # Headers use the masculine-ordinal "º" (U+00BA), not the degree
    # sign "°" (U+00B0). Spaces around "Único" vary between files.
    "Nº Exp.": "N Exp.",
    "Nº Único": "N único",
    "Nº  Único": "N único",
    "Nº único": "N único",
    "Neto": "Peso",
    "Plz. Orig.": "Plz. Orig",
    "País Orig.": "País Ori",
    "Administ.": "Administ",
    "Total": "Importe neto",
}

# Clean-variant raw header → canonical.
_RENAME_CLEAN: dict[str, str] = {
    "Documento de ventas": "Doc. Vtas",
    "Oficina de ventas": "Oficina de ventas",
    "Solicitante": "Solicitante",
    "Nombre 1": "Nombre 1",
    "Código de Tráfico": "Código Tráfico ",
    "Código de Tráfico Bidea": "C. Traf. B.",
    "Factura": "Factura",
    "Fecha factura": "Fecha factura",
    "Clase de factura": "Clase factura",
    "Denominación": "Denominación",
    "Salida/Llegada": "Sal./Lleg.",
    "Fecha documento": "Fecha doc.",
    "Nº Expedición": "N Exp.",
    "Nº de pedido": "Pedido",
    "Nº único": "N único",
    "Nº Único": "N único",
    "Peso neto": "Peso",
    "Plaza Origen": "Plz. Orig",
    "Región Origen": "Región Ori",
    "País Origen": "País Ori",
    "Plaza Destino": "Plz. Dest.",
    "Región Destino": "Región Des",
    "País Destino": "País Dest.",
    "Indicador impuestos": "II",
    "Reexpedición + Desembolso": "Reexp+Des",
    "Manipulación": "Manipulac.",
    "Administración": "Administ",
    "Distribución": "Distrib.",
    "Total": "Importe neto",
    "Referencia1": "Ref1",
    "FREIGHT LUMP-SUM": "FR LUMP-S",
    "BACK BILLING": "BACK BILL.",
    "TRANSIT FREIGHT": "TRANSIT FR",
    "ADMINISTRATION": "ADMINISTR.",
    "WAREHOUSE HANDLING": "WAREHOUSE",
    "UNLOADING AND DISTRIBUTION": "UNLOAD&DIS",
    "SERVICE CHARGE PLATFORM": "SERV.CHARG",
    "Incoterms": "Incot",
    "Incoterms, parte 2": "Incoterms2",
    "ID Consolidación": "ID Consol.",
}

DERIVED_COLUMNS: tuple[str, ...] = (
    "Tipo Bulto",
    "Q Expediciones",
    "Año",
    "Mes",
    "Tipo Exp.",
)

DACHSER_COLUMNS: tuple[str, ...] = DERIVED_COLUMNS + DACHSER_RAW_CANONICAL
assert len(DACHSER_COLUMNS) == 60

DATE_COLUMNS: tuple[str, ...] = ("Fecha factura", "Fecha doc.")
INT_COLUMNS: tuple[str, ...] = (
    "Año",
    "Mes",
    "Q Expediciones",
    "Bultos",
    "Doc. Vtas",
    "Solicitante",
    "Factura",
    "N Exp.",
    "N único",
)
FLOAT_COLUMNS: tuple[str, ...] = (
    "Peso",
    "Volumen",
    "Portes",
    "Reexp+Des",
    "Seguro",
    "Reembolso",
    "Suplidos",
    "Servicios",
    "Otros",
    "Manipulac.",
    "Administ",
    "Distrib.",
    "Almacenaje",
    "Importe neto",
    "FR LUMP-S",
    "BACK BILL.",
    "TRANSIT FR",
    "ADMINISTR.",
    "WAREHOUSE",
    "UNLOAD&DIS",
    "SERV.CHARG",
)

PLAUSIBILITY_NO_NULL: tuple[str, ...] = (
    "Doc. Vtas",
    "Factura",
    "Fecha factura",
)
PLAUSIBILITY_MIN_NON_NULL_RATE: dict[str, float] = {
    "Peso": 0.95,
    "Bultos": 0.95,
    "Importe neto": 0.95,
}
PLAUSIBILITY_DATE_RANGE: dict[str, tuple[date, date]] = {
    "Fecha factura": (date(2018, 1, 1), date(2035, 12, 31)),
}

# Ascending bucket cutoffs for `Tipo Bulto`. A weight is mapped to the
# smallest bucket >= weight; weights > 200 → "MÁS 200 KG".
_TIPO_BULTO_BUCKETS: tuple[int, ...] = (
    1, 3, 5, 10, 15, 20, 25, 30, 40, 50, 75, 100, 125, 150, 175, 200,
)


def _read_raw(path: Path, *, nrows: int | None = None) -> pd.DataFrame:
    """Read a Dachser file under either layout, returning a DataFrame
    whose columns are the canonical 55 names in canonical order.

    `nrows=0` reads the header only — used by `DachserParser.sniff`."""
    # Peek the first cell to decide layout. The bracketed layout opens
    # with a long auto-generated title; the clean layout opens with the
    # column header "Documento de ventas".
    probe = pd.read_excel(path, sheet_name=0, header=None, dtype=str, nrows=1)
    first_cell = "" if probe.empty else str(probe.iloc[0, 0] or "")

    if "Documento de ventas" in first_cell:
        df = pd.read_excel(path, sheet_name=0, header=0, dtype=str, nrows=nrows)
        rename_map = _RENAME_CLEAN
    else:
        df = pd.read_excel(path, sheet_name=0, header=3, dtype=str, nrows=nrows)
        # Bracketed files prepend a phantom blank column (`Unnamed: 0`).
        if df.columns[0].startswith("Unnamed"):
            df = df.iloc[:, 1:]
        rename_map = _RENAME_BRACKETED

    df.columns = [str(c).strip() for c in df.columns]
    df = df.rename(columns=rename_map)

    # Drop rows where every canonical-data column is blank (the bracketed
    # layout often has trailing summary/footer rows).
    canonical_present = [c for c in DACHSER_RAW_CANONICAL if c in df.columns]
    if canonical_present:
        df = df.dropna(how="all", subset=canonical_present).reset_index(drop=True)
    return df


def _tipo_bulto(peso: object) -> object:
    """Map a Peso value to its zero-padded weight-tier label."""
    if peso is None or (isinstance(peso, float) and pd.isna(peso)):
        return None
    try:
        w = float(peso)
    except (TypeError, ValueError):
        return None
    if pd.isna(w):
        return None
    if w > 200:
        return "MÁS 200 KG"
    for bucket in _TIPO_BULTO_BUCKETS:
        if w <= bucket:
            return f"{bucket:03d} KG"
    return "MÁS 200 KG"


def _to_float_eu(value: object) -> float:
    """Parse a Dachser numeric string. ES files use period decimals
    (`9.45`); FR files use comma decimals with leading whitespace
    (` 27,34`). pd.to_numeric only handles the former."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return float("nan")
    s = str(value).strip().replace(",", ".")
    if not s:
        return float("nan")
    try:
        return float(s)
    except ValueError:
        return float("nan")


def _parse_date_eu(s: pd.Series) -> pd.Series:
    """ES files emit ISO `YYYY-MM-DD HH:MM:SS`; FR files emit
    `DD.MM.YYYY`. Both parse correctly with `dayfirst=True` since the
    ISO form is unambiguous."""
    return pd.to_datetime(s, errors="coerce", format="mixed", dayfirst=True)


def _add_derived(df: pd.DataFrame) -> pd.DataFrame:
    """Add the 5 derived columns and reorder to DACHSER_COLUMNS."""
    fecha = _parse_date_eu(df["Fecha factura"])
    peso = df["Peso"].map(_to_float_eu)

    df["Año"] = fecha.dt.year.astype("Int64")
    df["Mes"] = fecha.dt.month.astype("Int64")
    df["Tipo Bulto"] = peso.map(_tipo_bulto).astype("string")
    tipo_exp = pd.Series(pd.NA, index=df.index, dtype="string")
    tipo_exp[peso > 50] = "Pallet"
    tipo_exp[(peso > 0) & (peso <= 50)] = "Bulto"
    df["Tipo Exp."] = tipo_exp
    # Q Expediciones: 1 on first occurrence of Doc. Vtas in the file, 0
    # afterwards. Operator-overridden running counts in historical Datos
    # are not reproduced — see module docstring.
    is_first = ~df.duplicated(subset=["Doc. Vtas"], keep="first")
    df["Q Expediciones"] = is_first.astype("Int64")
    return df[list(DACHSER_COLUMNS)]


def coerce_dachser_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a 60-col Dachser-historical-shaped DataFrame."""
    df = df.copy()
    for col in DATE_COLUMNS:
        df[col] = _parse_date_eu(df[col])
    for col in INT_COLUMNS:
        if col in ("Año", "Mes", "Q Expediciones"):
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        else:
            df[col] = df[col].map(_to_float_eu).astype("Int64")
    for col in FLOAT_COLUMNS:
        df[col] = df[col].map(_to_float_eu).astype("float64")

    typed = set(DATE_COLUMNS) | set(INT_COLUMNS) | set(FLOAT_COLUMNS)
    for col in df.columns:
        if col in typed:
            continue
        df[col] = df[col].map(to_clean_string).astype("string")
    return df


class DachserParser:
    carrier: ClassVar[str] = "dachser"
    expected_columns: ClassVar[tuple[str, ...]] = DACHSER_COLUMNS
    sheet_name: ClassVar[str] = "New Datos"

    def parse(self, path: Path) -> ParseResult:
        path = Path(path)
        if not path.exists():
            raise ParserError(f"Dachser invoice file not found: {path}")

        try:
            df = _read_raw(path)
        except ParserError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ParserError(f"could not read {path.name}: {e}") from e

        # The schema check uses the canonical names, so both layouts
        # surface the same diff message if a column goes missing. Guard
        # the reindex first: a missing column would otherwise raise a bare
        # KeyError instead of the clean SchemaMismatch diff (a renamed
        # raw header that `_RENAME_*` doesn't cover lands here).
        missing = [c for c in DACHSER_RAW_CANONICAL if c not in df.columns]
        if missing:
            raise SchemaMismatch(
                f"schema mismatch ({len(df.columns)} cols vs expected "
                f"{len(DACHSER_RAW_CANONICAL)}): missing={missing}"
            )
        assert_schema(df[list(DACHSER_RAW_CANONICAL)], DACHSER_RAW_CANONICAL)

        df = _add_derived(df)
        df = coerce_dachser_dtypes(df)
        assert_plausible(
            df,
            no_null=PLAUSIBILITY_NO_NULL,
            min_non_null_rate=PLAUSIBILITY_MIN_NON_NULL_RATE,
            date_range=PLAUSIBILITY_DATE_RANGE,
        )

        invoice_number = self._derive_invoice_number(df, path)
        invoice_date = self._derive_invoice_date(df, path)
        file_hash = compute_file_hash(path)
        log.info(
            "dachser parser: %s | %d rows | hash=%s | source=%s",
            invoice_number, len(df), file_hash[:12], path.name,
        )
        return ParseResult(
            carrier=self.carrier,
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            rows=df,
            source_path=path,
            file_hash=file_hash,
        )

    def sniff(self, path: Path) -> bool:
        """True if `path` looks like a Dachser invoice — header only, no
        full parse. Used by the intake classifier to disambiguate bare
        `.xlsx` files (Seitrans vs Dachser have no filename signature)."""
        path = Path(path)
        if not path.exists():
            return False
        try:
            df = _read_raw(path, nrows=0)
            # assert_schema raises SchemaMismatch on the wrong shape; any
            # other read failure (wrong file type, corrupt zip, bad layout)
            # just means "not a Dachser file" — sniff never raises.
            assert_schema(df[list(DACHSER_RAW_CANONICAL)], DACHSER_RAW_CANONICAL)
        except Exception:  # noqa: BLE001
            return False
        return True

    @staticmethod
    def _derive_invoice_number(df: pd.DataFrame, path: Path) -> str:
        s = df["Factura"].dropna()
        if s.empty:
            raise ParserError(f"{path.name}: no Factura values found")
        # Some files contain multiple Facturas (an ES file can include
        # an associated 120xxx or 130xxx adjustment row). Use the first
        # for the manifest key — additional rows still ride along.
        return str(int(s.iloc[0]))

    @staticmethod
    def _derive_invoice_date(df: pd.DataFrame, path: Path) -> date:
        s = df["Fecha factura"].dropna()
        if s.empty:
            raise ParserError(f"{path.name}: no Fecha factura found")
        return s.iloc[0].date()
