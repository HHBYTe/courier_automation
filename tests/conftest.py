"""Shared test fixtures: synthetic Seur invoice/workbook builders and
pointers to real-data fixtures."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import pytest
from openpyxl import Workbook

from courier_automation.parsers.seur import SEUR_COLUMNS
from courier_automation.parsers.seitrans import SEITRANS_COLUMNS, SEITRANS_RAW_COLUMNS

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SEUR_RAW_DIR = FIXTURES_DIR / "seur" / "raw"
SEUR_GOLDEN_DIR = FIXTURES_DIR / "seur" / "golden"
SEUR_SYNTHETIC_DIR = FIXTURES_DIR / "seur" / "synthetic"
SEITRANS_RAW_DIR = FIXTURES_DIR / "seitrans" / "raw"
SEITRANS_GOLDEN_DIR = FIXTURES_DIR / "seitrans" / "golden"


def _make_default_row(line_number: int = 1) -> dict[str, Any]:
    """Build a single Seur row with sensible defaults for every column.

    Tests can override individual fields by passing a dict to `make_seur_invoice`.
    Numeric columns get 0/0.0; date columns get a fixed date; text columns get
    a placeholder string.
    """
    row: dict[str, Any] = {
        "Codigo Cliente": "012345",
        "Serie Factura": "A",
        "Numero Factura": 289264,
        "Fecha Factura": datetime(2025, 4, 30),
        "Numero Linea": line_number,
        "Fecha Servicio": datetime(2025, 4, 15),
        "Salida / Entrada": "S",
        "Origen": "08",
        "Nombre Completo Origen": "Barcelona",
        "Destino": "28",
        "Nombre Completo Destino": "Madrid",
        "Servicio": "13",
        "Nombre Completo Servicio": "Servicio Estándar",
        "Producto": "1",
        "Nombre Completo Producto": "Producto Estándar",
        "U.A. Exp.": "U001",
        "Centro": "0289",
        "Numero Expedicion": f"E{line_number:08d}",
        "Fecha Exp.": datetime(2025, 4, 15),
        "Informacion Adicional": "",
        "Remitente": "Artero",
        "Direccion Remitente": "Calle Falsa 123",
        "Poblacion Remitente": "Barcelona",
        "C. Postal Remitente": "08001",
        "Destinatario": "Cliente",
        "Direccion Destinatario": "Avenida Real 456",
        "Poblacion Destinatario": "Madrid",
        "C. Postal Destinatario": "28001",
        "Referencia": f"REF{line_number:05d}",
        "Tipo Línea": "P",
        "Claves Expedicion": "",
        "Bultos": 1,
        "Peso": 1.5,
        "Peso Volumetrico": 1.0,
        "Ancho": 30.0,
        "Alto": 20.0,
        "Largo": 40.0,
        "Volumen": 0.024,
        "Clave Impuesto": "21",
        "Importe facturado (sin impuestos)": 5.50,
        "Valor Reembolso": 0.0,
        "Valor Asegurado": 0.0,
        "U.A. Consol.": "",
        "Codigo Cliente Consolidado": "",
        "Alias Razon Social CCC Consolidado": "",
        "Poliza flotante porte": 0.0,
        "Poliza flotante valor declarado": 0.0,
        "Portes": 4.50,
        "Reexpedicion Especial": 0.0,
        "Gestion Reembolso": 0.0,
        "Seguro": 0.0,
        "Cargo Combustible": 0.45,
        "Comprobante de entrega": 0.0,
        "Servicios Sabados": 0.0,
        "Sobrecargos No Encintable": 0.0,
        "Tasa Seguridad Int": 0.0,
        "Tasa Calidad del Dato": 0.0,
        "Tasa Islas": 0.0,
        "Tasa B2C": 0.55,
        "Tasa Cliente No Integrado": 0.0,
        "Suplemento Andorra": 0.0,
        "Zonas Remotas": 0.0,
        "Gestion Aduanas Salidas": 0.0,
        "Gestion Aduanas Llegadas": 0.0,
        "Suplidos": 0.0,
        "Aforos": 0.0,
        "Descuentos": 0.0,
        "Otros": 0.0,
    }
    assert set(row) == set(SEUR_COLUMNS), (
        f"default row schema drift: missing={set(SEUR_COLUMNS) - set(row)}, "
        f"extra={set(row) - set(SEUR_COLUMNS)}"
    )
    return row


def make_seur_invoice(
    path: Path,
    rows: Iterable[dict[str, Any]] | None = None,
    *,
    columns: tuple[str, ...] = SEUR_COLUMNS,
    sheet_name: str = "Sheet1",
) -> Path:
    """Write a Seur-shaped xlsx at `path` using openpyxl directly (so text cells
    stay text). If `rows` is None, writes one default row.
    """
    if rows is None:
        rows = [_make_default_row()]
    rows = list(rows)

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(list(columns))
    for row in rows:
        ws.append([row.get(col) for col in columns])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def make_empty_seur_workbook(path: Path) -> Path:
    """Build a workbook with a `Datos` sheet that has the 68 Seur headers and no
    data rows. Used as a writer-test target."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Datos"
    ws.append(list(SEUR_COLUMNS))
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def _make_default_seitrans_row(line_number: int = 1) -> dict[str, Any]:
    row: dict[str, Any] = {
        "CLIENTE_RAGIONE_SOCIALE": "Cliente Srl",
        # Real Seitrans: one DOCUMENTO_NUMERO per file, multiple SPEDIZIONE_NUMERO
        # rows underneath. Constant default mirrors that.
        "DOCUMENTO_NUMERO": 289264,
        "SPEDIZIONE_NUMERO": f"S{line_number:08d}",
        "MITTENTE_RAGIONE_SOCIALE": "Artero",
        "MITTENTE_NAZIONE_DESCRIZIONE": "Italia",
        "MITTENTE_CAP": "00100",
        "DESTINATARIO_RAGIONE_SOCIALE": "Cliente SL",
        "DESTINATARIO_LOCALITA": "Madrid",
        "DESTINATARIO_CAP": "28001",
        "DESTINATARIO_NAZIONE_DESCRIZIONE": "España",
        "IMBALLI": 1,
        "PESO_LORDO": 12.5,
        "VOLUME": 0.85,
        "PESO_TASSABILE": 12.5,
        "METRI_LINEARI": 0.0,
        "VOCE_DESCRIZIONE": "Documento",
        "IMPORTO_TOTALE_VALUTA": 120.0,
        "RIFERIMENTO_COMMITTENTE": f"REF{line_number:04d}",
        "RESA_DESCRIZIONE": "DAP",
        "SETTORE_DESCRIZIONE": "Logistica",
        "DOCUMENTO_DATA": datetime(2025, 4, 30),
    }
    assert set(row) == set(SEITRANS_RAW_COLUMNS), (
        f"default row schema drift: missing={set(SEITRANS_RAW_COLUMNS) - set(row)}, "
        f"extra={set(row) - set(SEITRANS_RAW_COLUMNS)}"
    )
    return row


def make_seitrans_invoice(
    path: Path,
    rows: Iterable[dict[str, Any]] | None = None,
    *,
    columns: tuple[str, ...] = SEITRANS_RAW_COLUMNS,
    sheet_name: str = "Risultato",
) -> Path:
    """Write a Seitrans-shaped xlsx at `path` using openpyxl directly."""
    if rows is None:
        rows = [_make_default_seitrans_row()]
    rows = list(rows)

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(list(columns))
    for row in rows:
        ws.append([row.get(col) for col in columns])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def make_empty_seitrans_workbook(path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Datos"
    ws.append(list(SEITRANS_COLUMNS))
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


@pytest.fixture
def seur_invoice_factory(tmp_path: Path):
    """Returns a callable that writes a synthetic Seur invoice xlsx into tmp_path."""

    def _factory(
        rows: Iterable[dict[str, Any]] | None = None,
        *,
        filename: str = "0289992025D0289264.xlsx",
        columns: tuple[str, ...] = SEUR_COLUMNS,
    ) -> Path:
        return make_seur_invoice(tmp_path / filename, rows, columns=columns)

    return _factory


@pytest.fixture
def empty_seur_workbook(tmp_path: Path) -> Path:
    return make_empty_seur_workbook(tmp_path / "seur_workbook.xlsx")


@pytest.fixture
def default_seur_row():
    return _make_default_row


@pytest.fixture
def seitrans_invoice_factory(tmp_path: Path):
    """Returns a callable that writes a synthetic Seitrans invoice xlsx into tmp_path."""

    def _factory(
        rows: Iterable[dict[str, Any]] | None = None,
        *,
        filename: str = "2025_04_30_INV0289264.xlsx",
        columns: tuple[str, ...] = SEITRANS_RAW_COLUMNS,
    ) -> Path:
        return make_seitrans_invoice(tmp_path / filename, rows, columns=columns)

    return _factory


@pytest.fixture
def empty_seitrans_workbook(tmp_path: Path) -> Path:
    return make_empty_seitrans_workbook(tmp_path / "seitrans_workbook.xlsx")


@pytest.fixture
def default_seitrans_row():
    return _make_default_seitrans_row


def _glob_xlsx(d: Path) -> list[Path]:
    return sorted(d.glob("*.xlsx")) if d.exists() else []


_REAL_SEUR_INVOICES = _glob_xlsx(SEUR_RAW_DIR)
_REAL_SEITRANS_INVOICES = _glob_xlsx(SEITRANS_RAW_DIR)


@pytest.fixture(
    params=_REAL_SEUR_INVOICES or [None],
    ids=lambda p: p.name if p else "no-fixtures",
)
def real_seur_invoice(request) -> Path:
    """Parametrized over every .xlsx in tests/fixtures/seur/raw/. When no
    fixtures are present, runs once and skips with a helpful message."""
    if request.param is None:
        pytest.skip(
            f"no real Seur fixtures at {SEUR_RAW_DIR} "
            "(copy a real invoice in to enable real-data tests)"
        )
    return request.param


@pytest.fixture(
    params=_REAL_SEITRANS_INVOICES or [None],
    ids=lambda p: p.name if p else "no-fixtures",
)
def real_seitrans_invoice(request) -> Path:
    """Parametrized over every .xlsx in tests/fixtures/seitrans/raw/."""
    if request.param is None:
        pytest.skip(
            f"no real Seitrans fixtures at {SEITRANS_RAW_DIR} "
            "(copy a real invoice in to enable real-data tests)"
        )
    return request.param
