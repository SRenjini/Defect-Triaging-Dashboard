# QA Triage Dashboard — Azure Static Web App Deployment

## Overview

Self-contained deployment package for hosting the QA Triage Dashboard as a public Azure Static Web App. Anyone with the link can access the dashboard — no local setup required.

**Features:**
- Full QA Triage Dashboard with live data
- Secure ADO bug creation (PAT stored server-side, never exposed to browser)
- Live data refresh from Cosmos DB via server-side proxy
- Scheduled daily data refresh via pipeline
- Professional URL: `https://<your-app>.azurestaticapps.net`

---

## Architecture

```
Browser (anyone with link)
  ├── GET  /                    → index.html (dashboard UI)
  ├── GET  /qa_triage_data.json → pre-generated data snapshot
  ├── POST /api/create-ado-bug  → Azure Function (PAT server-side)
  └── POST /api/refresh-data    → Azure Function (Cosmos key server-side)

Daily Pipeline (6 AM UTC)
  └── export_qa_triage.py → qa_triage_data.json → redeploy to SWA
```

---

## Folder Structure

```
swa-deploy/
├── index.html                  # Dashboard (no secrets)
├── qa_triage_data.json         # Pre-generated data snapshot
├── staticwebapp.config.json    # SWA routing config
├── azure-pipelines.yml         # CI/CD pipeline
├── api/                        # Azure Functions (Python)
│   ├── host.json
│   ├── requirements.txt
│   ├── create-ado-bug/         # ADO proxy function
│   │   ├── function.json
│   │   └── __init__.py
│   └── refresh-data/           # Cosmos refresh function
│       ├── function.json
│       └── __init__.py
└── README.md
```

---

## Deployment Steps

### 1. Create Azure Static Web App

```bash
# Via Azure CLI
az staticwebapp create \
  --name qa-triage-dashboard \
  --resource-group <YOUR_RG> \
  --location eastus2 \
  --sku Standard
```

Or via Azure Portal:
1. Go to **Azure Portal** → **Create a resource** → **Static Web App**
2. Name: `qa-triage-dashboard`
3. Plan: **Standard** (required for Azure Functions API)
4. Source: Connect to your repo or deploy manually

### 2. Configure Application Settings (Secrets)

In the Azure Portal, navigate to your Static Web App → **Configuration** → **Application settings**:

| Setting | Value | Purpose |
|---------|-------|---------|
| `ADO_PAT` | Your ADO Personal Access Token | ADO bug creation |
| `ADO_ORGANIZATION` | Your ADO org name | ADO org name |
| `ADO_PROJECT` | Your ADO project (URL-encoded) | ADO project |
| `COSMOS_ENDPOINT` | Your Cosmos DB endpoint URL | Cosmos DB endpoint |
| `COSMOS_KEY` | Your Cosmos DB primary key | Cosmos DB auth |
| `COSMOS_DATABASE` | `tcaDB` | Database name |
| `COSMOS_CONTAINER` | `reg_documents` | Container name |

```bash
# Or via CLI
az staticwebapp appsettings set \
  --name qa-triage-dashboard \
  --setting-names \
    ADO_PAT="<your-pat>" \
    COSMOS_ENDPOINT="<your-cosmos-endpoint>" \
    COSMOS_KEY="<your-cosmos-key>" \
    COSMOS_DATABASE="tcaDB" \
    COSMOS_CONTAINER="reg_documents"
```

### 3. Deploy

**Option A: Azure CLI (one-time manual deploy)**
```bash
cd Triaging-Dashboard/swa-deploy
swa deploy --deployment-token <YOUR_SWA_TOKEN> --app-location . --api-location api
```

**Option B: Azure DevOps Pipeline (automated)**
1. Create a Variable Group named `tca-dashboard-secrets` with:
   - `AZURE_SWA_DEPLOYMENT_TOKEN` — from SWA → Manage deployment token
   - `COSMOS_ENDPOINT`, `COSMOS_KEY`
2. Create a pipeline from `azure-pipelines.yml`
3. Run the pipeline — it deploys on push and refreshes data daily at 6 AM UTC

### 4. Share the URL

Once deployed, share the URL:
```
https://qa-triage-dashboard.azurestaticapps.net
```

---

## Security

| Item | Status |
|------|--------|
| ADO PAT | ✅ Stored in Azure Function Application Settings (server-side only) |
| Cosmos DB Key | ✅ Stored in Azure Function Application Settings (server-side only) |
| Dashboard HTML | ✅ No secrets — safe to view source |
| API Functions | ✅ Server-side execution only — secrets never sent to browser |
| HTTPS | ✅ Enforced by Azure Static Web Apps |

---

## Local Development

To test locally before deploying:

```bash
# Install SWA CLI
npm install -g @azure/static-web-apps-cli

# Set environment variables for local Functions
export ADO_PAT="<your-pat>"
export COSMOS_ENDPOINT="<your-endpoint>"
export COSMOS_KEY="<your-key>"

# Start local dev server
cd Triaging-Dashboard/swa-deploy
swa start . --api-location api
```

The dashboard will be available at `http://localhost:4280`.

---

## Data Refresh

Data is refreshed in three ways:

1. **Automatic (Pipeline):** Daily at 6 AM UTC via scheduled pipeline
2. **Manual (Pipeline):** Run the pipeline manually with the `RefreshData` stage
3. **Live (Dashboard):** Click "🔄 Refresh Live Data" button → calls `/api/refresh-data` → queries Cosmos DB in real-time
