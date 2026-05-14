# Automated invoice collection

How new invoices get from the carriers' emails into the pipeline without
anyone touching a file by hand — the **collector** layer. For the
per-carrier `pipeline` it feeds, see [pipeline.md](pipeline.md); for the
design rationale, see [architecture.md](architecture.md).

## The shape of it

n8n Cloud can't see the local disk or run Python, so the work is split
in two and decoupled through the OneDrive folder:

```
  carrier email (Outlook)                          this PC
  ───────────────────────                          ───────
   ┌──────────────────┐   attachment   ┌──────────────────────────┐
   │  n8n Cloud        │ ────────────▶ │ OneDrive: .../_inbox/     │
   │  (dumb pipe)      │  via OneDrive  │   syncs down to the PC   │
   └──────────────────┘   API          └────────────┬─────────────┘
                                                     │ Task Scheduler
                                                     ▼
                                       ┌──────────────────────────┐
                                       │ scripts/run_collector.py  │
                                       │  classify → place →       │
                                       │  pipeline sweep → unified │
                                       │  → log + email summary    │
                                       └──────────────────────────┘
```

- **n8n Cloud** watches one Outlook folder and uploads every invoice
  attachment into `Operations - Couriers/_inbox/`. It has **no carrier
  logic** — it doesn't know or care which carrier a file is.
- **`scripts/run_collector.py`** runs on the PC (Windows Task Scheduler).
  It classifies each inbox file to a carrier *in Python*, files it into
  `Facturas/<YYYY>/<NN> - <Mes>/`, runs the `pipeline` for all carriers,
  rebuilds the unified table once, logs, and emails a summary.
- The two halves never talk directly. n8n drops a file; the runner picks
  it up on its next scheduled pass. The pipeline's duplicate guard makes
  re-runs safe, so the cadence doesn't have to be precise.

**Carriers by route.** Seur, Seitrans, Correos, Dachser, Spring arrive by
email → n8n. UPS, WWEX, Royal Mail stay **manual** — download them from
the portals and drop the files straight into `Operations - Couriers/_inbox/`
(or into the carrier's `Facturas/` folder). The runner handles both the
same way.

## Part 1 — Outlook

Create one folder and one rule:

1. In Outlook, create a folder `Courier Invoices` (and a subfolder
   `Courier Invoices/Collected`).
2. Add **one** rule: any email from the carrier senders (Seur, Seitrans,
   Correos, Dachser, Spring) → move to `Courier Invoices`. One broad rule
   is enough — n8n doesn't need per-carrier folders, because carrier
   classification happens in Python.

## Part 2 — n8n Cloud

1. **Import** `n8n/courier-collector.workflow.json` into n8n Cloud.
2. **Credentials** — create two OAuth2 credentials (each needs an Azure AD
   app registration in the artero.com tenant; ask IT if you can't self-serve):
   - *Microsoft Outlook account* — scope to read mail + move messages.
   - *Microsoft OneDrive account* — scope to write files.
   Attach them to the nodes (the JSON has `REPLACE_WITH_..._CREDENTIAL_ID`
   placeholders).
3. **Folder IDs** — replace the three `PASTE_..._FOLDER_ID` placeholders:
   - `PASTE_COURIER_INVOICES_FOLDER_ID` — the `Courier Invoices` folder.
   - `PASTE_COLLECTED_FOLDER_ID` — the `Courier Invoices/Collected` subfolder.
   - `PASTE_ONEDRIVE_INBOX_FOLDER_ID` — the OneDrive folder
     `Operations - Couriers/_inbox/` (create it first; the runner also
     creates it on its first local run if missing).
   In n8n, the dropdowns on each Microsoft node let you pick the folder by
   name instead of pasting an ID.
4. **Activate** the workflow.

The workflow is deliberately 5 nodes: trigger → get message + attachments
→ extract `.xlsx/.xls/.csv` attachments (a Code node, one output item per
attachment) → upload to OneDrive → move the message to `Collected`. Node
parameter names can vary slightly between n8n versions — if a field looks
off on import, the node's own UI is authoritative; the JSON is a skeleton.

## Part 3 — the local runner

`scripts/run_collector.py`, run from the repo root. It needs no arguments.

### Environment variables

| Var | Purpose | Default |
|---|---|---|
| `COURIER_INBOX` | Override the inbox folder path | `Operations - Couriers/_inbox/` |
| `COURIER_SMTP_HOST` | SMTP server. **Unset = email disabled** (not an error) | — |
| `COURIER_SMTP_PORT` | | `587` |
| `COURIER_SMTP_USER` | Sending mailbox | — |
| `COURIER_SMTP_PASSWORD` | App password / secret | — |
| `COURIER_SMTP_TO` | Comma-separated recipients | `COURIER_SMTP_USER` |
| `COURIER_SMTP_FROM` | | `COURIER_SMTP_USER` |
| `COURIER_SMTP_STARTTLS` | `1` = STARTTLS (port 587), `0` = SMTPS (465) | `1` |
| `COURIER_SMTP_SKIP_EMPTY` | `1` = don't email when nothing was collected and every carrier was a clean no-op | `0` |

**Microsoft 365 SMTP caveat.** M365 disables SMTP AUTH per-mailbox by
default. A tenant admin must enable *Authenticated SMTP* on the sending
mailbox, and if MFA is on you need an **app password**. Use
`smtp.office365.com` port `587` with STARTTLS. If IT won't allow SMTP
AUTH at all, leave `COURIER_SMTP_HOST` unset (the runner still does all
the ingest work and writes its log) and read the log, or wire a small
"send mail" step into the n8n workflow instead.

### Windows Task Scheduler

Create a task that runs every few hours:

- **Program:** `<repo>\.venv\Scripts\python.exe`
- **Arguments:** `scripts\run_collector.py`
- **Start in:** the repo root (`...\Courier Automation`)
- Set the `COURIER_SMTP_*` vars as system/user environment variables (or
  wrap the call in a `.bat` that `set`s them first).

The runner takes a PID/age lock (`logs/collector/.lock`) so overlapping
schedules don't collide. Each run writes `logs/collector/<timestamp>.log`.

## Operator workflow

Normal runs need no attention — check the summary email. Two folders need
occasional human eyes:

- **`_inbox/_unclassified/`** — a file the classifier couldn't place
  (unknown format, or it matched a carrier but failed to parse). Look at
  it: if it's a real invoice, rename/fix it or drop it directly into the
  right `Facturas/<YYYY>/<NN> - <Mes>/` folder; if it's junk, delete it.
- **`_inbox/_conflicts/`** — a file whose name matches one already in the
  carrier's `Facturas/` folder but with **different content** (a reissued
  invoice, or a name clash). Compare the two, decide which is correct,
  and place it by hand.

Both are surfaced in the summary email's "ATTENTION NEEDED" section, and
they make the run exit non-zero so Task Scheduler's "last result" flags it.

## How classification works

`courier_automation/intake.py`:

1. **Filename fast path** — case-insensitive regexes per carrier
   (`CarrierConfig.classify_patterns` in `carriers.py`): Seur, Royal Mail,
   UPS, Correos, WWEX, Spring all have distinctive filenames.
2. **Header sniff** — Seitrans and Dachser invoices have no reliable
   filename signature, so the file's header row is read and matched
   against the parser's expected schema (`SeitransParser.sniff` /
   `DachserParser.sniff`). First match wins.
3. **No match** → `_unclassified/`.

Then `place_invoice_file` parses the file, takes the month from the
invoice date (never the OS file date), and moves it to
`Facturas/<YYYY>/<NN> - <Mes>/`. Every carrier is normalised to that
month-subfolder layout so the pipeline's `--month` discovery always
resolves it.

## Verifying it works

Without n8n — drop a real Seitrans `.xlsx` and a UPS `.csv` into
`Operations - Couriers/_inbox/`, then:

```
.venv\Scripts\python scripts\run_collector.py
```

Expect: each file moved into its `Facturas/<YYYY>/<NN> - <Mes>/` folder,
the pipeline ran (master + parquet updated, or the guard tripped if the
month was already in), unified rebuilt, a log under `logs/collector/`,
and — if SMTP is configured — a summary email. Drop a `.txt` and confirm
it lands in `_inbox/_unclassified/` and is flagged in the summary.

With n8n — send a test email with an `.xlsx` attachment to the watched
folder; confirm the file appears in `Operations - Couriers/_inbox/` on the
PC after OneDrive syncs, and the source message moved to `Collected`.
