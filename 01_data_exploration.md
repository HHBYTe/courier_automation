# Data Exploration — Operations / Couriers

> Source root: `OneDrive - Artero\Escritorio\Operations - Couriers`
> Date of exploration: 2026-05-05

---

## 1. High-level layout

The root contains **13 numbered courier folders**, plus three cross-courier artefacts:

| Path | Purpose |
|---|---|
| `01. Seur` … `13.Spring (FR)` | One folder per carrier (raw + consolidated + Power BI) |
| `Global Transport Report.xlsx` | Manual P&L roll-up (Domestic / UE / Export) by year |
| `Outvio-v0.0.pbix` | Separate Power BI on the Outvio shipping platform |
| `Operations - 01. Proyectos\` | Adjacent projects (Adyen, Amazon Market USA, Outvio, Tickelia…) — out of scope |
| `Slides Logística Global per Comitè.pptx` | Monthly committee deck — consumes the BI outputs |

Inside almost every courier folder the same three artefacts repeat:

1. **`Facturas\` (or `Invoices\` / yearly subfolders)** — raw monthly invoice + detail file received from the carrier.
2. **`<Análisis | Shipping Report | Expediciones | Shipment Report>.xlsx`** — the consolidated historical workbook the user maintains by hand. This is what we call the "big file" per courier.
3. **`*.pbix`** — Power BI report fed from that consolidated workbook.

Folder naming and the structure of each artefact are **not standardised across couriers** — see §3.

---

## 2. Carrier inventory

| # | Carrier | Country / region | Source format | Cadence | Identifier in invoice file |
|---|---|---|---|---|---|
| 01 | **Seur** | ES (domestic + intl) | `.xlsx` + `.pdf`, multiple invoices/month | weekly-ish (≈120 invoices/year) | `0289992025D000XXXX.xlsx` |
| 02 | **VASP** | PT | `.xlsx` (multi-sheet, wide preamble) + signed `.pdf` | monthly | `01 Detalle factura Enero 2025.xlsx` |
| 03 | **Dachser** | EU export / road | `.xlsx` (legacy SAP `XLS` saved as `.xlsx`) | monthly | `MM-YYYY IN <invoice#>.xlsx` |
| 04 | **Seitrans** | IT export | `.xlsx` (clean tabular) + occasional VE `.pdf` | monthly | `YYYY_MM_DD_<invoice#>.xlsx` |
| 05 | **Correos Express** | ES domestic | `.xlsx` (header + data block) + `.pdf` | monthly | `FAC_UNICO_FYYMM_NNNNN.xlsx` |
| 06 | **Amazon** | ES (VAT report only) | `.xlsx` (Amazon VAT scheme) | unclear — only one historical file present | `IVA YYYY-MM` sheet |
| 07 | **UPS (UK)** | UK | `.xlsx` (UPS standard 250-col layout) — `Invoices/YYYY/` | weekly (very many) | `EYYNNNNN_ES_…XLSX` + matching `.pdf` |
| 08 | **Lynda's Transport** | UK | `.xlsx` (8-col Tabula-extracted from PDF) — `YYYY/` | monthly | `YYYY_MM_DD IN <invoice#> Lynda's.xlsx` |
| 09 | **DPD France** | FR | `.xlsx` "complement_facture" — header-less, mostly numeric — `YYYY/` | monthly (often 2 invoices) | `YYYY_MM_DD <accountcode> YYMM NNNNN complement_facture.xlsx` |
| 10 | **Express Catalan** | FR | `.xls` (binary, free-form layout) — `YYYY/` | monthly | `YYYY_MM_DD FA <invoice#>.xls` |
| 11 | **Wwex (US)** | US | `.xlsx` / `.xls` / `.csv` (mix) — `YYYY/` | monthly | `YYYY_MM_DD shipment_detail_report.<ext>` |
| 12 | **Royal Mail** | UK | only a tariff PDF — **no historical operational data yet** | n/a | n/a |
| 13 | **Spring** | FR / international parcels | `.XLSX` + `.pdf` per invoice — `YYYY/<Month>/` | weekly (≈40+/year) | `EYYNNNNN_ES_Details of Invoice_O_110003790_<ts>.XLSX` |

**Observations on cadence and volume**
- Seur and UPS dominate by file count (each 100+ invoice files per year).
- Spring also produces many small weekly files.
- VASP / Dachser / Seitrans / Correos / DPD / Express Catalan / Lynda's / Wwex are **one (or two) consolidated invoice per month** — easiest targets to automate first.
- Royal Mail is a future addition (only a tariff is filed).
- Amazon "06" is anomalous — it is an Amazon-side VAT report, not a courier invoice. It does not have its own pbix and is likely unrelated to the courier-cost workstream.

---

## 3. Per-courier raw-data schemas

Schemas listed below are the columns of the **detail (line-level) sheet** of one recent invoice. They are the inputs the automation must accept.

### 3.1 Seur — `Facturas/<year>/0289992025DXXXXXX.xlsx` → sheet `Sheet1`
Clean, **68 columns**, one row per shipment line:

```
Codigo Cliente, Serie Factura, Numero Factura, Fecha Factura, Numero Linea,
Fecha Servicio, Salida / Entrada, Origen, Nombre Completo Origen, Destino,
Nombre Completo Destino, Servicio, Nombre Completo Servicio, Producto,
Nombre Completo Producto, U.A. Exp., Centro, Numero Expedicion, Fecha Exp.,
Informacion Adicional, Remitente, Direccion Remitente, Poblacion Remitente,
C. Postal Remitente, Destinatario, Direccion Destinatario, Poblacion Destinatario,
C. Postal Destinatario, Referencia, Tipo Línea, Claves Expedicion, Bultos, Peso,
Peso Volumetrico, Ancho, Alto, Largo, Volumen, Clave Impuesto,
Importe facturado (sin impuestos), Valor Reembolso, Valor Asegurado,
U.A. Consol., Codigo Cliente Consolidado, Alias Razon Social CCC Consolidado,
Poliza flotante porte, Poliza flotante valor declarado, Portes,
Reexpedicion Especial, Gestion Reembolso, Seguro, Cargo Combustible,
Comprobante de entrega, Servicios Sabados, Sobrecargos No Encintable,
Tasa Seguridad Int, Tasa Calidad del Dato, Tasa Islas, Tasa B2C,
Tasa Cliente No Integrado, Suplemento Andorra, Zonas Remotas,
Gestion Aduanas Salidas, Gestion Aduanas Llegadas, Suplidos, Aforos,
Descuentos, Otros
```
**The Seur historical file (`NEW Análisis expediciones SEUR.xlsx`, sheet `Datos`) has the exact same 68 columns** — the consolidation step is essentially "append rows". Other sheets in the historical workbook are reference dimensions: `Origen`, `Destino`, `Delegaciones`, `ClaveFactura`, `Códigos IC`. **Easiest courier to automate first.**

### 3.2 VASP — `Facturas/<year>/MM Detalle factura <Mes> YYYY.xlsx`
Multi-sheet with a **`Document map`** sheet, then `Sumário`, `Sumário Diário`, **`Detalhe`** (line-level). The `Detalhe` sheet has a 2-row preamble ("Cliente / Origem" + "Artero…"); real header is at **row 3** with 24 columns:
```
Cliente, Factura, Data Expedição, Nº Entrega, Ref, Ref 1, Ref 2, Ref 3, Ref 4,
Expedidor, Tipo Serviço, Tipo Serviço nome, Nome, CP, Localidade, Entrega,
Flag Cobrança, Valor Cobrança, Peso, Volume, Tx Combustivel, Valor Tx Combustivel,
Valor, Total
```
Historical file `Análisis envíos VASP.xlsx` sheet `Detalhe` is the same data plus 6 derived columns at the front: `Año, Mes, Tipo Bulto, Delegación, Zona, Tipo Exp., Q Expediciones`.

### 3.3 Dachser — `Facturas/<year>/MM-YYYY IN <invoice#>.xlsx`
**Painful**: the file is a saved SAP "Salida dinámica de lista". Its only sheet has the title in row 1, so pandas reads garbage column names; the real header sits ~3 rows down. ~56 columns including: `Doc.vtas, OfVta, Solic., Nombre 1, Cod. Traf., Factura, Fecha factura, Sal./Lleg., N Exp., Pedido, Peso, Volumen, Bultos, Plz. Orig, Origen, Pais Ori, Plz. Dest., Destino, Pais Dest., Portes, Reexp+Des, Seguro, Reembolso, Suplidos, Servicios, Otros, Manipulac., Administ, Distrib., Almacenaje, Importe neto, Ref1, FR LUMP-S, BACK BILL., TRANSIT FR, ADMINISTR., WAREHOUSE, UNLOAD&DIS, SERV.CHARG, Incot, Incoterms2, ID Consol.`.
The historical workbook (`Expediciones Dachser.xlsx`) has both `Old Datos` (72 cols, legacy schema) and `New Datos` (60 cols, current). The user has already mapped Old↔New on a `New & old Fields` sheet.

### 3.4 Seitrans — `Facturas/<year>/YYYY_MM_DD_<invoice#>.xlsx` → sheet `Risultato`
Cleanest non-Seur source. **21 columns**, all caps:
```
CLIENTE_RAGIONE_SOCIALE, DOCUMENTO_NUMERO, SPEDIZIONE_NUMERO,
MITTENTE_RAGIONE_SOCIALE, MITTENTE_NAZIONE_DESCRIZIONE, MITTENTE_CAP,
DESTINATARIO_RAGIONE_SOCIALE, DESTINATARIO_LOCALITA, DESTINATARIO_CAP,
DESTINATARIO_NAZIONE_DESCRIZIONE, IMBALLI, PESO_LORDO, VOLUME, PESO_TASSABILE,
METRI_LINEARI, VOCE_DESCRIZIONE, IMPORTO_TOTALE_VALUTA, RIFERIMENTO_COMMITTENTE,
RESA_DESCRIZIONE, SETTORE_DESCRIZIONE, DOCUMENTO_DATA
```
Historical `Análisis envíos Seitrans.xlsx` sheet `Datos` adds 4 derived columns at the front (`Tipo expedición, Q Expediciones, Año, Mes`) and renames `_` → space in the column names.

### 3.5 Correos Express — `Facturas/<year>/FAC_UNICO_FYYMM_NNNNN.xlsx`
Single sheet, **51 wide columns, but with a header band**: row 0 = invoice totals (`Nº FACTURA`, `F.FACTURA`, `TOTAL (€)` …); row 1 = the **actual** column titles for the shipment lines (`Nº ENVIO, F.ALBARAN, F.ADMISION, REFERENCIA, …, F.ENTREGA, HORA ENTREGA`); rows 2+ = data. So row 1 needs to be promoted to header before parsing.
Historical `Análisis Envíos Correos Express V2.xlsx` sheet `Datos` has 58 cols (same data + 6 derived `Año, Mes, Tipo Bulto, Tipo Exp., Q Expediciones, País`).

### 3.6 UPS (UK) — `Invoices/<year>/EYYNNNNN_ES_Details of Invoice_O_110003790_<ts>.XLSX`
**UPS standard billing extract** — a flat table with **250 columns** and many `Place Holder N` empties. Useful columns: `Invoice Date, Invoice Number, Invoice Currency Code, Invoice Amount, Tracking Number, Service / Charge Description, Sender/Receiver Name+Address+Postal+Country, Entered Weight, Billed Weight, Zone, Net Amount, Shipment Date, Shipment Delivery Date, Charge Description Code, Tax Indicator…`.
Each invoice file is one week. Historical `UPS Shippings Report.xlsx` sheet `Data` keeps the full 250-col schema as-is.

### 3.7 Lynda's Transport — `<year>/YYYY_MM_DD IN <inv#> Lynda's.xlsx`
The file is the result of a Tabula/PDF→Excel extraction. Sheet `Table 1` is the only useful one and has **8 columns**: `Date, Our Ref., Your Ref., Collection Address, Delivery Address, Packs, Weight, Price`. Other sheets (`Table 2..6`) carry footer rows (surcharge, totals, IBAN). The historical `Lynda's Shipment Report.xlsx` sheet `Data` enriches this with `Invoice Date, Invoice nr., Fuel surcharge 9%, Total cost, £/KG, POSTAL CODE` (the postcode is parsed out of the delivery address).

### 3.8 DPD France — `<year>/YYYY_MM_DD <accountcode> YYMM NNNNN complement_facture.xlsx`
**Worst raw schema**: the sheet has **no header row**; pandas auto-uses the first data row as headers. ~95 columns, mostly numeric, with codes like `C2000 / TVA 20 %` at the right. A column dictionary already exists in the historical file (`DPD FR-Shipping report-v0.0.xlsx` sheet `Data`, 94 named cols including `Invoice number, Invoice Date, From/To name+address+PC+country, Delivery number, BP code, Delivery date, Parcels number, Weight, Calculated Weight, Parcel Size L/W/H, Transport Cost (Base), Insurance Cost, Gasoil Cost, Amount management fees, VAT Invoice, TVA Code, TVA %, Total Transport Cost, Frais de dédouanement, Ajustement tarifaire: 10%`). **The mapping from raw→named is implicit and must be reverse-engineered.**

### 3.9 Express Catalan (FR) — `<year>/YYYY_MM_DD FA <invoice#>.xls` (binary `.xls`)
Single sheet `Worksheet`, 33 columns, but the **first ~10 rows are an invoice cover** (`Facture transport / Emetteur / Destinataire …`); the actual line-item table starts ~row 11. Historical `Express Catalan FR-Shipping report-v0.0.xlsx` sheet `Data` has 32 named cols (`Delivery number, Delivery ref, Delivery date, To PC/City, Nb UM, Nb colis, Nb palette, MPL, Volume, Poids, Distri, Taxe Fixe, Traction, PAQ, Divers, Montant HT, Senders name, From PC/City/Country, Ref. expéditeur, To Name/Country, Trafic, Taxe gasoil, Libellé, Bilan Carbone, Exonéré, Unité Taxée, Type Unité Taxée, Kilométrage (KM)`).

### 3.10 Wwex (US) — `<year>/YYYY_MM_DD shipment_detail_report.<ext>`
Sheet name varies (`shipmentDetailsUPS_W130089866_2`). 42 columns on the raw side: `CUSTOMER_NO, COMPANY_NAME, MARKET_NAME, ACCOUNT_NO, BILL_TO_ACCOUNT_NUMBER, CREATION_DATE, SHIPMENT_DATE, SENDER, ORIGIN_*, CONSIGNEE_*, DESTINATION_*, STATUS, TRACKING_NO, SERVICE_TYPE, ZONE, ACTUAL_PICKUP_DATE, ACTUAL_DELIVERY_DATE, REFERENCE_NUMBER, PACKAGE_COUNT, TOTAL_WEIGHT, TOTAL_RATED_WEIGHT, PACKAGE_WEIGHT, PACKAGE_RATED_WEIGHT, SHIPPED DIMENSIONS, BILLED DIMENSIONS, IS_INSURED, INSURED_AMOUNT, COST OF INSURANCE, ESTIMATED_TOTAL_PRICE, LOGINID, ACCESSORIAL_CHARGES, TRACKING INFO`.
The historical `Wwex USA Shippings Report.xlsx` sheet `Data` uses a **different schema** (44 cols, "Source System / SpeedShip" naming: `Source System, Tracking#, Ship Date, Ship Ref1/2, Ship From/To Company+Addr1-3+City+State+Postal Code+Country+Phone, Bill To Acct#, Package Weight, Billed Weight, Package Dimensions, Service, Insured Value, Est Transportation Charges, Est Other Charges, Insurance, Package Count, Weight per package`). **Schema drifted at some point** — likely a Wwex platform change. File extension also drifts (`.xls` ↔ `.xlsx` ↔ `.csv`).

### 3.11 Spring (FR) — `<year>/<Month>/EYYNNNNN_ES_Details of Invoice_O_110003790_<ts>.XLSX`
Most data-rich raw file (114 cols on the `REPORT` sheet of the historical workbook): `client, client_number, user_country, source, shipper_item_id, invoice_number, order_ref, order_date, requested_service, service, carrier, carrier_tracking_number, status, error_message, creation_time, accept_time, delivery_date, delivery_days, weight, dim_*, value, currency, total_tax, total_duty, shipper_*, consignee_*, group_id, is_dangerous, item_*_1, last/first_tracking_event_*, Parcel Cost, Energetic Suplement cost, Undeliverable cost, Total Parcel cost, Transit time*`.
The historical workbook ALSO carries a separate sheet `INVOICES` (24 cols) which is the per-line invoice charges (`Invoice Number, Invoice Date, CONNOTE, Product, Shipment Date, Country, Format, Items, Item Charge, Actual Kilos, Volumetric Kilos, Weight Charge, Amount, Amount Incl. VAT, MONTH, YEAR`). So **two parallel data streams** per Spring invoice: an operations report and a billing detail.

### 3.12 Royal Mail / Amazon
- Royal Mail: only `Tariffs 2025.pdf`. No automation target yet.
- Amazon: only the Amazon-side VAT scheme report. Schema is the standard 95-col Amazon VAT format (`UNIQUE_ACCOUNT_IDENTIFIER, ACTIVITY_PERIOD, …, TAX_REPORTING_SCHEME`). It is sourced from Seller Central, not from a courier — flag for the user to confirm scope.

---

## 4. The historical "big files" — common pattern

The user's manual workflow paste-and-format produces a **per-courier workbook** with the same structure each time:

| Sheet (typical) | Role |
|---|---|
| `Datos` / `Data` / `Detalhe` / `REPORT` | The append-only line-level fact table |
| `Tabla` / `TD` / `New Tablas` | One or more pivot tables for the .pbix |
| `Origen`, `Destino`, `Countries`, `Codes`, etc. | Reference dimensions (country code → name, postal code → region) |
| Courier-specific extras | `ClaveFactura`, `Códigos IC`, `Iskaypet`, `Slides` … |

Two key derivations the user systematically adds on top of the raw data:

1. **Date enrichment** — `Año`, `Mes` (and sometimes `Fecha factura` parsed).
2. **Weight bucket** — `Tipo Bulto` (`001 KG`, `005 KG`, `015 KG`, `MÁS 200 KG` …) and `Tipo Exp.` (`Bulto` vs `Pallet`).
3. **Geography enrichment** — looking up country / region from the postal code or country code via the reference sheets.
4. **Counts** — `Q Expediciones = 1` per row (used as a measure-friendly constant).

These are exactly the columns that need to be **computed by the automation** rather than copy-pasted.

---

## 5. Cross-courier roll-up

`Global Transport Report.xlsx` (root) is a **manual P&L** by year/lane (`Domestic / UE+Export / Total`). It is not a fact-level conglomerate — just summary numbers typed in from the SAP general-ledger extract (sheets `2021`…`2025` carry the GL accounts). It is the closest thing today to the user's "future objective" of a global view, but it is **disconnected from the per-courier line data**.

There is **no shared/normalized schema** between couriers. The simplest path to a global fact table is to define a target schema and map each courier into it — see plan document.

---

## 6. Email & web sources (not yet sampled)

The user states that raw data arrives by email to a company inbox or is downloaded from the courier's portal. None of that source layer is visible in the working directory; only the saved attachments are.
- **By email (most carriers):** Seur, VASP, Dachser, Seitrans, Correos Express, Lynda's, DPD France, Express Catalan, Spring.
- **From web/portal:** UPS (UPS Billing Center; .xlsx + .pdf pair), Wwex (SpeedShip portal report), possibly Spring (XBSBack / Spring portal).
- **Unknown:** Royal Mail, Amazon (Amazon VAT report comes from Seller Central, monthly).

The exact sender addresses, subject patterns, and the invoice-portal credentials are not in the directory and need to be captured from the user before automating ingestion.

---

## 7. Risks / open questions

1. **Inconsistent file naming.** Each courier uses a different date / invoice convention. A courier-specific filename parser is unavoidable.
2. **Header position varies.** Several files (Correos, VASP, Express Catalan, Dachser, DPD) require finding the real header row before parsing.
3. **Schema drift.** Wwex changed its schema at least once. Dachser has Old/New schemas. Spring has two parallel streams. Schema changes must be detected, not assumed.
4. **Free text in shared cells.** Lynda's collection/delivery address mixes business + town; the historical file has a derived `POSTAL CODE` parsed out of the address — that parsing logic is currently only in the user's head.
5. **Encoding.** Multiple files contain non-UTF-8 mojibake (`Sumário` → `Sum�rio`, `País` → `Pa�s`). The pipeline must read them in their native encoding (cp1252 / latin-1) and persist UTF-8.
6. **PDF-only invoices.** Royal Mail today and Lynda's "Table N" footers are PDF-derived. We can defer Royal Mail until the user actually starts using it.
7. **Two invoices in one month** for some couriers (DPD France: France + complement; Dachser: Artero + Artero France). Idempotency by invoice number, not by month.
8. **Amazon "06"** is likely not a courier invoice — confirm scope with the user before including.
9. **Royal Mail** has no historical operational file yet — out of scope for v1.
10. **Reference data drift.** The `Códigos IC` / `Origen` / `Destino` sheets are maintained manually inside each courier's historical workbook. They must be lifted out into versioned reference data.

---

## 8. Inventory snapshot (file counts at exploration time)

```
Seur / Facturas / 2025      ~120 invoice .xlsx (+ matching .pdf for many)
VASP / Facturas / 2025      5 monthly .xlsx + 2 signed .pdf
Dachser / Facturas / 2025   13 .xlsx (some carry the "ARTERO FRANCE" entity twice)
Seitrans / Facturas / 2025  ~12 monthly .xlsx + occasional VE_10 .pdf
Correos / Facturas / 2025   12 monthly .xlsx + matching .pdf
UPS / Invoices / 2026       50+ weekly .XLSX + matching .pdf (year just started)
Lynda's / 2025              7 monthly .xlsx (Jan–Jul)
DPD FR / 2025               10 monthly .xlsx (often 2/month: France + Artero France)
Express Catalan / 2025      6 monthly .xls (Jan–Jun)
Wwex / 2025                 12 monthly files, mix of .xls/.xlsx/.csv
Spring / 2025-2026          one folder per month, each with weekly .XLSX + .pdf pairs
Royal Mail                  none (only tariff PDF)
Amazon                      one historical 2022 file only
```

These counts are the **input volume v1 of the automation has to handle each year** — a useful sizing for whatever ingestion mechanism is chosen.
