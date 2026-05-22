# SEUR cost-checker — implemented rules

The rules the checker applies, taken from **TARIFA NACIONAL 2026** and
**TARIFA INTERNACIONAL 2026** (`checker/tariffs/`). The rate tables
themselves (the weight × zone numbers) live in `check_seur.py`; only the
*logic* around them is documented here.

## Services

The checker recognises three SEUR services; anything else is left
`UNVERIFIED`.

| Invoice `Servicio` | Tariff used |
|---|---|
| `S-24`  | National — "S-1 (24 H.)" table |
| `*B2C`  | National — "ENTREGA PARTICULARES" table |
| `CLS`   | International — "CLASSIC TERRESTRE" table |

## Weight and the rate band

- A shipment's rate is read from the row whose band (`Hasta X kg`) is the
  first one **≥ the chargeable weight**.
- **Volumetric weight.** The tariff cubes volume into weight at
  **200 kg/m³** (road/air) and **333 kg/m³** (sea); the chargeable weight
  is the greater of real and volumetric. (The international Classic
  contract states 120 kg/m³ for Classic, 200 kg/m³ for Netexpress.)
- **Heavy shipments** above the top band use a per-kg extension, with the
  weight **rounded up to the next whole kg** (verified against invoices):
  - **S-24** — top band 50 kg; per-kg extension applies from 50 kg.
  - **\*B2C** — top band 30 kg; flat at the 30 kg rate between 30–50 kg,
    then per-kg from 50 kg.
  - **International** — top band 32 kg; flat at the 32 kg rate above it,
    except the UK which adds 1.18 €/kg from 32 kg.

## Zones

- **National, peninsular Spain** — four distance tiers (Area BCN / Corto /
  Medio / Largo) that SEUR assigns by the origin–destination plaza pair.
  The PDFs give no postcode map for these, so the checker accepts a
  billed rate that matches **any** of the four tiers for the weight band.
- **Baleares / Canarias / Ceuta / Melilla** — pinned exactly from the
  destination postcode prefix (07 / 35·38 / 51 / 52).
- **Portugal** — its own column in the national tariff (SEUR treats
  Spain + Portugal as "national").
- **International** — one tariff column per country, pinned exactly.

## Fuel surcharge ("tasa de energía")

- Changes **weekly**; each SEUR notice gives an effective date range.
- **Two rates per period**: one for national + Portugal, one for
  international.
- Applied to the base **`Portes` + `Zonas Remotas` + `Reexpedición
  Especial`** — not to the fixed tasas (verified against invoices).
- The Jan–Apr 2026 rates in `FUEL_RATES` are *reconstructed* from the
  invoices (per-week median) and are approximate; only periods entered
  from official SEUR notices are exact.

## Surcharge rules in the PDF that are NOT implemented

The contract's "Cargos" section lists per-shipment surcharges that depend
on package flags the invoice doesn't expose, so the checker does not
recompute them:

- Security tax (Tasa de Seguridad) — 0.30 € / 0.40 € per shipment.
- Remote-origin / remote-destination — 3.00 € per shipment.
- Non-tapeable (`No encintable`) 1.50 € · non-stackable 5.00 € per parcel.
- Out-of-norm A 49.00 € / B 99.00 € per parcel; tyres 1.20 €.
- Maritime IMO eco-tax 0.30 €/kg.
- Peak-season particular-delivery charges (Jun–Sep, Nov–Jan).
- Multi-parcel 0.20 € per parcel from the 2nd onward.
- Missing/invalid email or phone 0.25 € · bad address 0.50 €.
- Extrapeninsular supplement 3 € · non-integrated client 0.50 €/parcel.
- Special-platform delivery 3 € per parcel.
- COD (`Reembolso`) — 5.60 % of declared value, min 7.44 € / max 145.32 €.
- Declared-value insurance — 1.40 % of declared value, min 4 €.
- Saturday delivery / pickup — 11.42 € each.
- International: customs clearance fees, documentation supplements.

These are recorded here so it's clear what the checker covers and what it
deliberately leaves out.
