# QA Triage Dashboard

**Purpose:** A real-time QA quality triaging tool for Tax Rule Change (TRC) review. It enables QA leads and tax experts to rapidly triage defects by reviewing system-generated TRCs against reviewer comments, tracking acceptance rates, and creating Azure DevOps bugs — all from a single dashboard.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Technology Stack](#technology-stack)
- [Integration Touch Points](#integration-touch-points)
- [Data Model](#data-model)
- [Key Features](#key-features)
- [Project Structure](#project-structure)
- [Setup & Configuration](#setup--configuration)
- [Running Locally](#running-locally)
- [Deployment (Azure Static Web Apps)](#deployment-azure-static-web-apps)
- [Environment Variables](#environment-variables)

---

## Overview

The TCA (Tax Content Automation) system automatically compares year-over-year tax document revisions and generates **Tax Rule Changes (TRCs)** — machine-inferred changes that may require regulatory action. Tax experts review these TRCs and provide feedback across four quality dimensions:

| Quality Dimension | What It Measures |
|---|---|
| **Context** | Is the surrounding context of the change relevant? |
| **System Inference** | Is the AI's reasoning about the change correct? |
| **Tax Rule Change** | Is the TRC description accurate and complete? |
| **Category** | Is the change correctly categorized (bucket)? |

The **QA Triage Dashboard** aggregates this data to surface quality trends, identify recurring defect patterns, and allow one-click ADO bug creation — replacing manual Excel-based triage with a real-time, interactive workflow.

### What Problem Does It Solve?

1. **Manual triage is slow** — Reviewers previously exported feedback to Excel, cross-referenced TRCs, and manually classified issues. This dashboard automates that pipeline.
2. **No visibility into quality trends** — Without aggregation, it was impossible to see which types of TRC errors repeat (e.g., "Form Identification Error" appearing in 70%+ of reviews).
3. **Disconnected bug tracking** — Issues found during review had to be manually re-typed into Azure DevOps. The dashboard creates ADO bugs with pre-populated fields directly from the review context.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     QA Triage Dashboard                      │
│              (Single-page HTML + Vanilla JS)                 │
├──────────────────────────────────────────────────────────────┤
│  KPIs │ Field Approval Bars │ Tag Analysis │ Document Cards  │
│  Filter Bar (Entity, Tax Year, Reviewer, Date Range)         │
│  ADO Bug Creation Modal                                      │
└───────────┬────────────────────────────┬─────────────────────┘
            │ /api/refresh (POST)        │ ADO REST API
            ▼                            ▼
┌───────────────────────┐    ┌──────────────────────────┐
│   server.py (5500)    │    │  Azure DevOps REST API   │
│  Python HTTP Server   │    │  dev.azure.com           │
│  + /api/refresh       │    │  Create Bug work items   │
│  + Static file serve  │    └──────────────────────────┘
└───────────┬───────────┘
            │ subprocess
            ▼
┌───────────────────────┐
│  export_qa_triage.py  │
│  ETL from Cosmos DB   │
│  → qa_triage_data.json│
└───────────┬───────────┘
            │ azure-cosmos SDK
            ▼
┌───────────────────────┐
│   Azure Cosmos DB     │
│   (QA environment)    │
│   tcaDB.reg_documents │
│                       │
│ Entity Types:         │
│  • Document           │
│  • TaxRuleChange      │
│  • TaxRuleChangeFB    │
│  • ComparisonJob      │
│  • TaxRuleChangeJob   │
└───────────────────────┘
```

### Deployment Architecture (Azure Static Web Apps)

```
┌─────────────────────────────────────────────────────┐
│         Azure Static Web Apps (SWA)                 │
├──────────────────────┬──────────────────────────────┤
│  Static Content      │  Azure Functions API (Python)│
│  index.html          │  /api/refresh-data           │
│  qa_triage_data.json │  /api/create-ado-bug         │
└──────────────────────┴──────────────────────────────┘
        │                        │
        │                  ┌─────┴───────────────┐
        │                  │ App Settings (Env)   │
        │                  │ COSMOS_ENDPOINT      │
        │                  │ COSMOS_KEY           │
        │                  │ ADO_PAT              │
        │                  └─────────────────────┘
```

---

## Technology Stack

| Layer | Technology | Purpose |
|---|---|---|
| **Frontend** | Vanilla HTML5 + CSS3 + JavaScript (ES6+) | Single-file dashboard, zero build tooling |
| **Local Server** | Python `http.server` + custom handler | Serves static files + `/api/refresh` endpoint |
| **ETL Script** | Python 3.11 (`azure-cosmos` SDK) | Queries Cosmos DB, enriches TRC data, exports JSON |
| **Data Store** | Azure Cosmos DB (SQL API) | Source of truth for documents, TRCs, and feedback |
| **Bug Tracking** | Azure DevOps REST API (v7.1) | Creates Bug work items from dashboard |
| **Cloud Hosting** | Azure Static Web Apps | Production deployment with serverless Python Functions |
| **CI/CD** | Azure Pipelines (YAML) | Automated deploy on push to `main` |
| **NLP Classification** | Keyword-based tag classifier | Classifies reviewer comments into 10 tax expert issue categories |

### Python Dependencies

```
azure-cosmos>=4.5.0    # Cosmos DB SQL API client
```

---

## Integration Touch Points

### 1. Azure Cosmos DB (Data Source)

- **Endpoint:** Configured via `COSMOS_ENDPOINT` environment variable
- **Database:** `***`
- **Container:** `reg_documents` (partitioned by `entity_type`)
- **Queries:** 5 entity types fetched in sequence:
  1. `Document` — Tax document metadata (entity, year, revision, forms)
  2. `TaxRuleChange` — System-generated TRCs with classification, description, reasoning
  3. `TaxRuleChangeCustomFeedback` — Reviewer feedback per TRC field (Approved/NotApproved + comments)
  4. `ComparisonJob` — Job metadata linking documents to ADO work items
  5. `TaxRuleChangeJob` — Links TRCs to their parent ComparisonJob

### 2. Azure DevOps (Bug Creation)

- **API:** `https://dev.azure.com/{org}/{project}/_apis/wit/workitems/$Bug?api-version=7.1-preview.3`
- **Auth:** Personal Access Token (PAT) via Basic auth
- **Fields populated:**
  - `System.Title` — Auto-generated from TRC context
  - `System.Description` — TRC description + reviewer comment
  - `Microsoft.VSTS.TCM.ReproSteps` — System-generated vs reviewer comparison
  - `Microsoft.VSTS.Common.Severity` / `Priority` — User-selected
  - `EDMAgile.Application`, `EDMAgile.Impact` — Custom org fields
  - `Custom.DEF_DefectInProd`, `Custom.DEF_EnvironmentFoundIn` — Defect tracking fields

### 3. Local Python Server (server.py)

- **Port:** 5500 (configurable)
- **Static serving:** All files under `Mock Screens/` root
- **API endpoint:** `POST /api/refresh` → runs `export_qa_triage.py` → returns fresh JSON inline
- **Error handling:** Exit code 0 = success, exit code 2 = no TRC data (returns cached), other = error

### 4. Azure Static Web Apps (Production)

- **Serverless Functions:** Python Azure Functions in `swa-deploy/api/`
  - `refresh-data` — Same Cosmos ETL logic, credentials from App Settings
  - `create-ado-bug` — Server-side ADO proxy (PAT never exposed to browser)
- **Config:** `staticwebapp.config.json` handles routing and CORS

---

## Data Model

### TRC Classification (Critical Concept)

The system classifies each detected change into three categories:

| Classification | Meaning | Dashboard Treatment |
|---|---|---|
| `TRC_REQUIRED` | A real tax rule change requiring action | **Counted as TRC** |
| `POTENTIAL_TRC` | Likely a TRC, needs expert confirmation | **Counted as TRC** |
| `NOT_TRC` | Year-over-year document diff, not a real change | **Excluded from counts** |

> Only `TRC_REQUIRED` and `POTENTIAL_TRC` are counted in KPIs and displayed in document cards. `NOT_TRC` items are year-over-year formatting/numbering differences and are excluded from quality metrics.

### Review Status Flow

```
NotStarted → FirstReviewCompleted → (future: SecondReview, Approved)
```

### TRC Enrichment Pipeline (export_qa_triage.py)

```
Cosmos query → Group TRCs by document → Join feedback by trc_id
  → Classify reviewer comments (10 tax expert tags)
  → Compute per-document & global metrics
  → Filter NOT_TRC from metric calculations
  → Write qa_triage_data.json
```

### Tax Expert Comment Classification

Reviewer comments are auto-tagged into 10 categories with severity levels:

| Tag | Severity | Keyword Examples |
|---|---|---|
| Form Identification Error | High | form name, wrong form, schedule |
| Line Reference Inaccuracy | High | line, reference, incorrect line |
| Calculation Change Missing | High | calc, formula, computation |
| Change Type Misclassification | High | renumber, actually, wrong bucket |
| Change Completeness | High | missing, incomplete, additional |
| Context Irrelevance | Medium | irrelevant, wrong context |
| Clarity & Precision | Medium | unclear, vague, confusing |
| Section Specificity | Medium | section, area, location |
| Content Verbosity | Low | wordy, too long, verbose |
| Positive Feedback | Low | good, accurate, well done |

Tags appearing in >70% of reviewed TRCs are flagged as **Critical Issues** with pulsing visual indicators.

---

## Key Features

1. **Real-time Cosmos refresh** — Auto-refreshes from Cosmos DB on dashboard open; manual refresh button available
2. **Multi-dimensional filtering** — Filter by Entity, Tax Year, Reviewer, Document, and Date Range
3. **KPI cards** — Documents, Actual TRCs (vs total changes), First Review Completed, TRC Description Acceptance %, All-field Approval %
4. **Per-field approval bars** — Visual breakdown of Approved vs Rejected for each quality dimension
5. **Tax Expert Issue Tags** — Auto-classified reviewer comments with severity coloring and critical issue alerts
6. **Document drill-down** — Expandable document cards showing per-TRC review details with side-by-side System Generated vs. Reviewer Comment views
7. **One-click ADO bug creation** — Pre-populated bug modal with severity, priority, and all relevant TRC context
8. **NOT_TRC filtering** — Excludes document-level formatting differences from quality metrics, showing only genuine tax rule changes
9. **Offline fallback** — Serves cached `qa_triage_data.json` when Cosmos is unavailable or has no data

---

## Project Structure

```
Triaging-Dashboard/
├── README.md                          # This file
├── .env.example                       # Required environment variables template
├── .gitignore                         # Git ignore rules
│
├── qa_triage_dashboard.html           # Main live dashboard (single-file)
├── qa_triage_dashboard_demo.html      # Self-contained demo with embedded static data
├── qa_triage_data.json                # Cached data from last Cosmos export
│
├── server.py                          # Local HTTP server (static files + /api/refresh)
├── export_qa_triage.py                # ETL: Cosmos DB → qa_triage_data.json
│
├── swa-deploy/                        # Azure Static Web Apps deployment package
│   ├── index.html                     # Dashboard (PAT removed, uses API proxy)
│   ├── qa_triage_data.json            # Cached data for initial load
│   ├── staticwebapp.config.json       # SWA routing config
│   ├── azure-pipelines.yml            # CI/CD pipeline definition
│   └── api/                           # Python Azure Functions
│       ├── refresh-data/              # Cosmos refresh endpoint
│       │   └── __init__.py
│       └── create-ado-bug/            # ADO bug creation proxy
│           └── __init__.py
│
├── explore_*.py                       # Cosmos DB exploration/debugging scripts
├── check_*.py                         # Cosmos DB schema/data validation scripts
├── document_metrics*.py               # Document-level metrics computation
├── triage_clustering.py               # TRC clustering analysis
└── trc_deep_dive.py                   # Deep-dive analysis of individual TRCs
```

---

## Setup & Configuration

### Prerequisites

- Python 3.11+
- `azure-cosmos` package (`pip install azure-cosmos`)

### Environment Variables

Create a `.env` file (or set system environment variables) based on `.env.example`:

```bash
# Cosmos DB
COSMOS_ENDPOINT=https://your-cosmos-account.documents.azure.com:443/
COSMOS_KEY=your-cosmos-primary-key-here
COSMOS_DATABASE=tcaDB
COSMOS_CONTAINER=reg_documents

# Azure DevOps (for bug creation)
ADO_PAT=your-ado-personal-access-token
ADO_ORGANIZATION=your-org
ADO_PROJECT=your-project
```

---

## Running Locally

### 1. Start the dashboard server

```bash
cd "Mock Screens"
python Triaging-Dashboard/server.py
```

The server starts on port 5500. Open: `http://localhost:5500/Triaging-Dashboard/qa_triage_dashboard.html`

### 2. Manual data export (without server)

```bash
cd Triaging-Dashboard
python export_qa_triage.py
```

This queries Cosmos DB and writes `qa_triage_data.json`.

### 3. VS Code Task

A pre-configured VS Code task is available:
- **Task name:** `Serve Mock Screens (Python http.server)`
- Run via: `Terminal → Run Task → Serve Mock Screens`

---

## Deployment (Azure Static Web Apps)

The `swa-deploy/` folder contains a production-ready deployment package:

1. **Secrets** are stored in Azure App Settings (never in code)
2. **ADO PAT** is proxied through a server-side Azure Function
3. **CI/CD** via Azure Pipelines (`azure-pipelines.yml`)

### Required App Settings

| Setting | Description |
|---|---|
| `COSMOS_ENDPOINT` | Cosmos DB account endpoint URL |
| `COSMOS_KEY` | Cosmos DB primary key |
| `ADO_PAT` | Azure DevOps Personal Access Token |
| `ADO_ORGANIZATION` | ADO organization name |
| `ADO_PROJECT` | ADO project name |

---

## Environment Variables

All credentials are read from environment variables. **No secrets are stored in source code.**

| Variable | Used By | Description |
|---|---|---|
| `COSMOS_ENDPOINT` | `export_qa_triage.py`, SWA API | Cosmos DB endpoint URL |
| `COSMOS_KEY` | `export_qa_triage.py`, SWA API | Cosmos DB primary key |
| `COSMOS_DATABASE` | `export_qa_triage.py`, SWA API | Database name (default: `tcaDB`) |
| `COSMOS_CONTAINER` | `export_qa_triage.py`, SWA API | Container name (default: `reg_documents`) |
| `ADO_PAT` | `qa_triage_dashboard.html`, SWA API | Azure DevOps PAT for bug creation |
| `ADO_ORGANIZATION` | SWA API | ADO org (default: read from env) |
| `ADO_PROJECT` | SWA API | ADO project (default: read from env) |
