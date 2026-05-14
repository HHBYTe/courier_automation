# Continuation — picking the project up cold

A handoff note for resuming work in a fresh session with no prior
context. Read this first, then [status.md](status.md) for the full
state. The headline open task is **finishing the n8n workflow** — that
section below is the detailed one.

## Orient yourself first

Read, in order: [README.md](../README.md) → [architecture.md](architecture.md)
→ [pipeline.md](pipeline.md) → [automation.md](automation.md). Then skim
[status.md](status.md) ("Known gaps" + "Roadmap").

Key files:

- `courier_automation/pipeline.py` — the per-carrier orchestrator.
- `courier_automation/intake.py` — classify a dropped file → file it into `Facturas/`.
- `scripts/run_collector.py` — the scheduled local runner.
- `courier_automation/carriers.py` — the per-carrier config registry.
- `n8n/courier-collector.workflow.json` — the n8n workflow (the open task).

Sanity check the checkout: `.venv\Scripts\pytest` should show
**145 passing, 5 pre-existing failures** (2 from the disabled manifest,
3 golden/schema drift — all documented in status.md, none introduced by
recent work). `.venv\Scripts\ruff check courier_automation/ scripts/ unified/`
should be clean.

Recent commits (most recent last): the per-carrier `pipeline` command →
the collector (n8n workflow + intake + runner) → the documentation pass.

## Current state in one paragraph

The Python side is **done and tested**: eight carrier parsers, the
`pipeline` command (parse → duplicate guard → master + parquet →
unified), the `unified` build, and the collector's local half
(`intake.py` + `scripts/run_collector.py`) — all verified end-to-end
against a temp inbox. What's *not* done is the off-machine half: the
**n8n Cloud workflow has never run**. Everything downstream of "a file
appears in `Operations - Couriers/_inbox/`" works; getting files *into*
that folder automatically is the open task.

---

## The main task: finish the n8n workflow

### The contract (what n8n must achieve)

n8n Cloud's only job: watch one Outlook folder, and for every invoice
email, upload each `.xlsx/.xls/.csv` attachment — **original filename
preserved** — into `Operations - Couriers/_inbox/` on the user's
"OneDrive - Artero". No carrier logic, no parsing, no month routing —
that all happens in Python on the PC. n8n is a dumb pipe. Once a file
lands in `_inbox/`, OneDrive syncs it to the PC and the rest is done.

Filename preservation matters: the Python classifier matches on
filename regexes and Royal Mail's rebuild keys on filenames. If n8n
renames attachments (e.g. `file (1).xlsx` on a re-upload), they'll fall
through to `_inbox/_unclassified/`.

### Where it stands

`n8n/courier-collector.workflow.json` is a **5-node skeleton**, written
without a live n8n instance to test against — so node `typeVersion`s,
parameter names, and the credential block are best-guess and **must be
verified on import**. Nodes: Outlook Trigger → Get message +
attachments → Code (filter + fan out attachments) → OneDrive upload →
Move message to `Collected`. It has five placeholders:
`REPLACE_WITH_OUTLOOK_CREDENTIAL_ID`, `REPLACE_WITH_ONEDRIVE_CREDENTIAL_ID`,
`PASTE_COURIER_INVOICES_FOLDER_ID`, `PASTE_COLLECTED_FOLDER_ID`,
`PASTE_ONEDRIVE_INBOX_FOLDER_ID`.

### Step 1 — clear the prerequisites (these are the real blockers)

These are likely IT / tenant-admin tasks, not coding. Do them first;
the rest is quick.

1. **An n8n Cloud account.** Confirm one exists / can be created.
2. **Azure AD app registration** in the artero.com tenant for the
   Microsoft OAuth2 credentials. This is the big external dependency.
   It needs: an app registration, n8n Cloud's redirect URI added,
   delegated API permissions — `Mail.Read` *and* `Mail.ReadWrite` (the
   workflow *moves* messages), `offline_access`, and `Files.ReadWrite.All`
   (OneDrive upload) — plus admin consent. If IT won't grant this, see
   "Fallbacks" below — the whole n8n path is blocked without it.
3. **Outlook folders + rule.** Create `Courier Invoices` and
   `Courier Invoices/Collected`; add one broad Outlook rule routing the
   five email carriers (Seur, Seitrans, Correos, Dachser, Spring) into
   `Courier Invoices`. One rule is enough — carrier classification is in
   Python.
4. **OneDrive `_inbox/` folder.** Create
   `Operations - Couriers/_inbox/` (the local runner also creates it,
   but n8n's upload node needs it to exist to target it).

### Step 2 — import and wire the workflow

1. Import `n8n/courier-collector.workflow.json` into n8n Cloud.
2. Create the two OAuth2 credentials (Microsoft Outlook, Microsoft
   OneDrive) against the Azure AD app from Step 1; attach them to the
   nodes (replace the `REPLACE_WITH_*` placeholders).
3. Resolve the three `PASTE_*_FOLDER_ID` placeholders — in n8n the
   Microsoft node dropdowns let you pick the folder by name instead of
   pasting an ID.
4. Activate.

### Step 3 — verify each node (the JSON is a skeleton)

The skeleton's structure is right; its parameter details may not be.
Open each node in the n8n UI and confirm against the n8n version's
actual schema. The things most likely to need fixing or attention:

- **Outlook Trigger** — polling the `Courier Invoices` folder for the
  `messageReceived` event. Confirm the poll cadence and that it only
  sees that one folder.
- **Get message + attachments** — the trigger alone may not download
  attachments; this node re-fetches the message with
  `downloadAttachments: true`. **Verify the attachment binary actually
  lands on the item** — n8n names binary properties like `attachment_0`,
  `attachment_1`, …; the next node depends on that.
- **Code node (extract attachments)** — iterates `item.binary`, keeps
  `.xlsx/.xls/.csv`, emits one output item per kept attachment. Verify
  it against: an email with several attachments; an email whose
  attachment is a signature image / PDF (must be dropped); an email
  with the invoice inline vs attached. The binary-property key names it
  reads must match what the previous node produced.
- **OneDrive upload** — confirm it targets the **"OneDrive - Artero"
  business drive** (the repo lives under it), not a personal OneDrive,
  and the `Operations - Couriers/_inbox/` folder specifically. Confirm
  it **keeps the original filename** and does not append `(1)` on a
  collision (a collision shouldn't happen if "Move to Collected" works,
  but check the node's conflict behaviour).
- **Move message to Collected** — runs *after* a successful upload, so a
  failed upload leaves the message in `Courier Invoices` to be retried.
  This is the primary idempotency guard — verify the trigger does not
  re-fire on messages already moved out.
- **Error handling** — add an n8n error workflow (or an error branch)
  that emails on failure. A failed upload must NOT move the message.

### Step 4 — test the two halves, separately then together

The collector is two decoupled halves; test each alone first.

1. **n8n half** — send a test email with a real `.xlsx` invoice
   attachment to the `Courier Invoices` folder. Confirm: n8n fires, the
   file appears in `Operations - Couriers/_inbox/` (check on the PC
   after OneDrive syncs), and the source message moved to `Collected`.
2. **Python half** — already verified, but to re-confirm: drop a file
   into `_inbox/` by hand and run `python scripts/run_collector.py`
   (point `COURIER_INBOX` at a temp dir first if you don't want a real
   ingest — see [automation.md](automation.md)).
3. **End to end** — let n8n deliver a file, then run the collector;
   confirm the invoice is classified, filed into
   `Facturas/<YYYY>/<NN> - <Mes>/`, ingested, and reflected in the
   summary log.

### Fallbacks if n8n is blocked

- **Azure AD registration refused / delayed.** The Python runner does
  not care how files arrive in `_inbox/` — the operator can drop them
  there by hand (this is already how UPS/WWEX/Royal Mail work). The
  collector still classifies, files, ingests, and emails. n8n only
  removes the manual drop step for the five email carriers.
- **The workflow JSON won't import cleanly.** Rebuild the five nodes by
  hand in the n8n UI — [automation.md](automation.md) "Part 2" describes
  each node's job. The JSON is a convenience, not load-bearing.
- **Org runs Power Automate, not n8n.** The same five-step flow (watch
  folder → get attachments → filter → upload to OneDrive → move message)
  maps directly onto Power Automate connectors. The Python contract is
  identical: files in `_inbox/`, filename preserved.

---

## Other pending work (not n8n)

Brief — full detail and the roadmap order are in [status.md](status.md).

- **Dachser 2026-02 schema drift** — `pipeline --carrier dachser` exits 2
  until `_RENAME_CLEAN` in `parsers/dachser.py` gets a key for the
  `Nº de pedido` raw header (a `º` character-variant mismatch; verify
  the exact character against the live file).
- **FX rates** — `unified/fx_rates.py` ships placeholder GBP/USD rates
  marked `>>> REVIEW THESE RATES <<<`; the `*_eur` columns aren't
  trustworthy until the business sets them.
- **M365 SMTP AUTH** — needed for the collector's summary email; off by
  default in M365 (tenant-admin task). The runner degrades gracefully
  without it.
- **First real production `pipeline` run** — the master-append path is
  unit-tested and the Royal Mail rebuild has run for real, but no
  genuine net-new append to a production master has happened yet.
- **Re-enable the manifest**, **Power BI repoint**, **portal automation
  for UPS/WWEX/Royal Mail** — later roadmap items.

## Don't clobber

The working tree has **uncommitted changes that are not part of this
work** — `powerbi/build_dims.py`, `powerbi/measures.dax`,
`unified/couriers.pbix`, `unified/normalizers/seur.py`. They're the
user's separate in-progress work. Leave them alone; don't stage them in
any commit.
