"""Per-carrier configuration registry.

Single source of truth for what used to be scattered across `cli.py`:
the `DEFAULT_<CARRIER>_*` path constants and the per-subcommand literals
(data-sheet name, file globs, name_filter). Both the `ingest <carrier>`
subcommands and the `pipeline` command read `CARRIERS` from here.

Imports parser classes + stdlib only — never `cli.py` or `pipeline.py`
(that would be a circular import).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from courier_automation.parsers.base import CourierParser
from courier_automation.parsers.correos import CorreosParser
from courier_automation.parsers.dachser import DachserParser
from courier_automation.parsers.royalmail import RoyalMailParser
from courier_automation.parsers.seitrans import SeitransParser
from courier_automation.parsers.seur import SeurParser
from courier_automation.parsers.spring import SpringParser
from courier_automation.parsers.ups import UpsParser
from courier_automation.parsers.wwex import WwexParser

_OPS = Path("Operations - Couriers")


@dataclass(frozen=True)
class CarrierConfig:
    """Everything the CLI / pipeline needs to handle one carrier."""

    name: str
    parser_factory: Callable[[], CourierParser]  # the parser class (zero-arg)
    workbook: Path                               # master historical workbook
    facturas_root: Path                          # <root>/<YYYY>/<MM - Mes>/
    data_sheet: str                              # "Datos"/"Data"/"New Datos"/"INVOICES"
    file_globs: tuple[str, ...]
    fallback_globs: tuple[str, ...] = ()
    name_filter: Optional[Callable[[Path], bool]] = None
    # Duplicate-guard config. `guard_invoice_column` names a column in the
    # carrier's row/master schema holding the invoice id — the primary
    # heuristic (overlap of incoming vs. master invoice ids). When it is
    # None the guard falls back to counting master rows in the target
    # month via `guard_month_column`.
    guard_invoice_column: Optional[str] = None
    guard_month_column: Optional[str] = None
    # Royal Mail has no append-friendly master; the pipeline rebuilds it
    # from scratch each run instead of appending, so the guard is skipped.
    rebuild_mode: bool = False
    # Intake classification (courier_automation.intake). `classify_patterns`
    # are case-insensitive filename regexes — the fast path. Carriers with
    # no reliable filename signature (seitrans, dachser: bare/date-prefixed
    # .xlsx) instead set `classify_probe=True` and are disambiguated by the
    # parser's header `sniff()`.
    classify_patterns: tuple[str, ...] = ()
    classify_probe: bool = False


def _name_contains(token: str) -> Callable[[Path], bool]:
    return lambda p: token in p.name.lower()


CARRIERS: dict[str, CarrierConfig] = {
    "seur": CarrierConfig(
        name="seur",
        parser_factory=SeurParser,
        workbook=_OPS / "01. Seur" / "NEW Análisis expediciones SEUR.xlsx",
        facturas_root=_OPS / "01. Seur" / "Facturas",
        data_sheet="Datos",
        file_globs=("*.xlsx",),
        guard_invoice_column="Numero Factura",
        # e.g. 0289992025D0289264.xlsx (10 digits + 1-3 letters + 7 digits).
        classify_patterns=(r"^\d{10}[A-Z]{1,3}\d{7}\.xlsx$",),
    ),
    "seitrans": CarrierConfig(
        name="seitrans",
        parser_factory=SeitransParser,
        workbook=_OPS / "04. Seitrans" / "Análisis envíos Seitrans.xlsx",
        facturas_root=_OPS / "04. Seitrans" / "Facturas",
        data_sheet="Datos",
        file_globs=("*.xlsx",),
        guard_invoice_column="DOCUMENTO NUMERO",
        # Bare/date-prefixed .xlsx with no signature — disambiguated by
        # the parser header sniff (see intake.classify_invoice_file).
        classify_probe=True,
    ),
    "dachser": CarrierConfig(
        name="dachser",
        parser_factory=DachserParser,
        workbook=_OPS / "03. Dachser" / "Expediciones Dachser.xlsx",
        facturas_root=_OPS / "03. Dachser" / "Facturas",
        data_sheet="New Datos",
        file_globs=("*.xlsx", "*.XLSX", "*.xls", "*.XLS"),
        guard_invoice_column="Factura",
        # No reliable filename signature — disambiguated by header sniff.
        classify_probe=True,
    ),
    "correos": CarrierConfig(
        name="correos",
        parser_factory=CorreosParser,
        workbook=_OPS / "05. Correos Express"
        / "Análisis Envíos Correos Express V2.xlsx",
        facturas_root=_OPS / "05. Correos Express" / "Facturas",
        data_sheet="Datos",
        file_globs=("*.xlsx",),
        # The invoice number lives only in the raw header band, not in the
        # row schema — fall back to month-overlap on F.ADMISION.
        guard_month_column="F.ADMISION",
        # e.g. FAC_UNICO_F2506_14077.xlsx
        classify_patterns=(r"FAC_UNICO",),
    ),
    "ups": CarrierConfig(
        name="ups",
        parser_factory=UpsParser,
        workbook=_OPS / "07. UPS (UK)" / "UPS Shippings Report.xlsx",
        facturas_root=_OPS / "07. UPS (UK)" / "Facturas",
        data_sheet="Data",
        file_globs=("*.csv",),
        fallback_globs=("*.xlsx",),
        guard_invoice_column="Invoice Number",
        # e.g. Invoice_59586753_010224.csv
        classify_patterns=(r"^Invoice_\d+_\d+\.(?:csv|xlsx)$",),
    ),
    "wwex": CarrierConfig(
        name="wwex",
        parser_factory=WwexParser,
        workbook=_OPS / "11. Wwex (US)" / "Wwex USA Shippings Report.xlsx",
        facturas_root=_OPS / "11. Wwex (US)" / "Facturas",
        data_sheet="Data",
        file_globs=("*.xlsx", "*.xls", "*.csv"),
        name_filter=_name_contains("shipment"),
        # invoice_number is synthetic (wwex-YYYY-MM) with no row column —
        # fall back to month-overlap on Ship Date.
        guard_month_column="Ship Date",
        # e.g. shipment_detail_report.xlsx, shipmentDetailsUPS_W130089866_...xlsx
        classify_patterns=(r"shipment[_ ]?detail", r"shipmentdetails"),
    ),
    "spring": CarrierConfig(
        name="spring",
        parser_factory=SpringParser,
        workbook=_OPS / "13. Spring (FR)" / "Shipment Report.xlsx",
        facturas_root=_OPS / "13. Spring (FR)" / "Facturas",
        data_sheet="INVOICES",
        file_globs=("*.xlsx", "*.XLSX"),
        name_filter=_name_contains("details of invoice"),
        guard_invoice_column="Invoice Number",
        classify_patterns=(r"details of invoice",),
    ),
    "royalmail": CarrierConfig(
        name="royalmail",
        parser_factory=RoyalMailParser,
        workbook=_OPS / "12. Royal Mail (UK)" / "Royal Mail Shipments Report.xlsx",
        facturas_root=_OPS / "12. Royal Mail (UK)" / "Facturas",
        data_sheet="Datos",
        file_globs=("*.csv",),
        name_filter=_name_contains("invoice"),
        rebuild_mode=True,
        # e.g. 20260511_9075855419_Invoice_0751634000.csv
        classify_patterns=(r"^\d{8}_\d+_Invoice_\d+\.csv$",),
    ),
}

__all__ = ["CarrierConfig", "CARRIERS"]
