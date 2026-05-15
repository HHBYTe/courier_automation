# Graph storage backend

How to set up the cloud variant of the courier pipeline (run on GitHub
Actions, talks to OneDrive/SharePoint via Microsoft Graph). The Python
stays the same; only the Storage backend changes.

## When you want this

You don't yet — the local Task Scheduler runner works fine for current
volume. Stand up the Graph backend when one of:

- PC uptime becomes a constraint (boss travels, weekend ingest needed).
- You want one canonical authority for who writes the master workbooks.
- You want versioned, observable runs (GH Actions logs vs. the local
  `logs/collector/` directory).

See [continuation.md](continuation.md) for the headline open work — n8n
must be live and the local runner proven for at least a few weeks of
real invoices before flipping to cloud.

## One-time setup

### 1. Register an Azure AD app

In the Azure portal → **Entra ID → App registrations → New registration**:

- Name: `Courier Pipeline (CI)`.
- Supported account types: **Single tenant**.
- Redirect URI: leave blank (this is a service-principal app, no
  interactive sign-in).

Record the **Application (client) ID** and the **Directory (tenant) ID**.

### 2. Create a client secret

Inside the new app → **Certificates & secrets → New client secret**:

- Description: `gh-actions`.
- Expires: 24 months (the longest sensible window).

Record the **Value** immediately — Azure shows it once and never again.

### 3. Grant the `Sites.Selected` permission

Inside the app → **API permissions → Add a permission → Microsoft Graph →
Application permissions** → check **`Sites.Selected`** → **Add**.

Then **Grant admin consent for artero.com** (only an admin can do this).
The `Sites.Selected` permission by itself does nothing — it just opts
the app into the "this app gets per-site grants" model. The next step
adds the actual access.

### 4. Authorise the app on the target SharePoint site

This is the step that turns `Sites.Selected` from "no access" into
"write access on one specific site". From an admin-authenticated shell
(e.g. Graph Explorer signed in as a Global Admin), hit:

```
POST https://graph.microsoft.com/v1.0/sites/{site-id}/permissions
Content-Type: application/json

{
  "roles": ["write"],
  "grantedToIdentities": [
    {
      "application": {
        "id": "{client-id}",
        "displayName": "Courier Pipeline (CI)"
      }
    }
  ]
}
```

Where:

- `{site-id}` is the SharePoint site holding `Operations - Couriers/`.
  Find it via `GET https://graph.microsoft.com/v1.0/sites/{tenant}.sharepoint.com:/sites/{site-name}`.
- `{client-id}` is the app's Application (client) ID from step 1.

If `Operations - Couriers/` lives on the boss's **personal OneDrive**
(not a SharePoint site), the equivalent route is to make the service
principal a member of the OneDrive folder share. Service-principal
access to personal OneDrives is more restrictive — for the
production setup, prefer hosting on a SharePoint document library.

### 5. Run the bootstrap helper

Locally, with the four secrets exported as env vars:

```powershell
$env:GRAPH_TENANT_ID     = "<tenant-guid>"
$env:GRAPH_CLIENT_ID     = "<app-client-id>"
$env:GRAPH_CLIENT_SECRET = "<client-secret-value>"
$env:GRAPH_SITE_ID       = "<sharepoint-site-id>"  # or GRAPH_USER_PRINCIPAL

.venv\Scripts\python.exe scripts/graph_bootstrap.py
```

Expected output: the resolved drive ID, a directory listing of the
drive root that **includes `Operations - Couriers`**, and the four
secret values formatted for pasting into GitHub.

If the listing doesn't include `Operations - Couriers`:

- Drive id wrong → re-check the SharePoint site / OneDrive user.
- `Sites.Selected` not granted on the site → re-run step 4.
- Admin consent missing on the application permission → re-run step 3.

### 6. Add the four secrets to GitHub

GitHub repo → **Settings → Secrets and variables → Actions → New
repository secret**:

| Name | Value |
|---|---|
| `GRAPH_TENANT_ID`     | tenant GUID from step 1 |
| `GRAPH_CLIENT_ID`     | app client ID from step 1 |
| `GRAPH_CLIENT_SECRET` | secret value from step 2 |
| `GRAPH_DRIVE_ID`      | drive ID from step 5 |

### 7. Enable the workflow

The workflow at [.github/workflows/courier-pipeline.yml](../.github/workflows/courier-pipeline.yml)
is `workflow_dispatch` + a daily cron at 06:00 UTC. Start with
`workflow_dispatch` only (comment out the `schedule:`), trigger one
manual run, verify the log artifact looks right, then uncomment the
cron.

## Shadow week before cutover

Don't disable the local Task Scheduler immediately. Run both for ~1
week:

- Cloud runs daily at 06:00 UTC.
- Local Task Scheduler keeps its existing cadence.

Each run takes the etag of the master, downloads, mutates, uploads. If
both ran simultaneously, one would lose the etag race and retry — the
retry path is the production behaviour, not an error. After a clean
week with no surprises in Power BI, disable the local Task Scheduler.

## Rollback

Disable the workflow's schedule (`workflow_dispatch`-only is fine to
leave on). Re-enable Task Scheduler on the PC. **No code change
required** — the backends are interchangeable; the only difference is
the `COURIER_BACKEND` env var, which Task Scheduler doesn't set so it
defaults to `local`.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `MSAL token acquisition failed: invalid_client` | Wrong tenant ID, wrong client ID, or the secret has expired. Generate a new secret. |
| 403 on every Graph call after auth | `Sites.Selected` granted but not authorised on the site → re-run step 4. |
| 412 retry loop exhausts on every workbook write | An Excel Online session is holding the file open. Close it (or wait for it to time out). |
| Power BI dashboard not refreshing | The parquet outputs uploaded to `Operations - Couriers/_pipeline_outputs/data/` but OneDrive desktop sync hasn't pulled them down yet. Give it a minute, then refresh in Power BI Desktop. |
| `ImportError: msal is required` | Backend selected as `graph` but msal not installed. `pip install -r requirements.txt`. |
