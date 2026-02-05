# DEPT Delivery Finance Tool - Architecture

## Overview

A web-based tool for analyzing historical project estimates, querying financial data using natural language (Gemini), and tracking project financials.

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              WEB INTERFACE                                   │
│                         (Next.js / React / Streamlit)                       │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐             │
│  │  Upload Excel   │  │  Chat Interface │  │   Dashboards    │             │
│  │  Sheet Preview  │  │  (Gemini NL)    │  │  & Reports      │             │
│  │  Select Tabs    │  │  Ask Questions  │  │  Burn Rates     │             │
│  │  Add Metadata   │  │  Get Insights   │  │  Variances      │             │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘             │
└───────────┼────────────────────┼────────────────────┼───────────────────────┘
            │                    │                    │
            ▼                    ▼                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            API LAYER (Cloud Run)                            │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐             │
│  │  ETL Service    │  │  Query Service  │  │  Analytics API  │             │
│  │  - Parse Excel  │  │  - NL to SQL    │  │  - Burn rates   │             │
│  │  - Detect tabs  │  │  - RAG search   │  │  - Forecasts    │             │
│  │  - Transform    │  │  - Gemini API   │  │  - Variances    │             │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘             │
└───────────┼────────────────────┼────────────────────┼───────────────────────┘
            │                    │                    │
            ▼                    ▼                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              DATA LAYER                                      │
├──────────────────────────┬──────────────────────────────────────────────────┤
│      BigQuery            │           Vertex AI                              │
│  ┌──────────────────┐    │    ┌──────────────────┐                         │
│  │   rate_cards     │    │    │  Embeddings      │                         │
│  │   projects       │    │    │  (scope desc)    │                         │
│  │   allocations    │    │    │                  │                         │
│  │   actuals        │    │    │  Vector Search   │                         │
│  │   ingestion_log  │    │    │  (similarity)    │                         │
│  └──────────────────┘    │    └──────────────────┘                         │
└──────────────────────────┴──────────────────────────────────────────────────┘
```

## Data Model

### BigQuery Tables

#### 1. `rate_cards` - Master Rate Data
| Column | Type | Description |
|--------|------|-------------|
| rate_card_id | STRING | Unique identifier |
| rate_card_name | STRING | Name (e.g., "2023 DEPT") |
| market_region | STRING | AMER, EMEA, APAC |
| department | STRING | CX & Design, Engineering, etc. |
| level | STRING | Junior, Senior, Manager, Lead, Director |
| role | STRING | Job title |
| cost_rate | FLOAT | Internal cost per hour |
| bill_rate | FLOAT | Client billing rate |

#### 2. `projects` - Project Metadata
| Column | Type | Description |
|--------|------|-------------|
| project_id | STRING | Unique identifier |
| client_name | STRING | Client company name |
| project_title | STRING | Project name |
| scope_description | STRING | Free-text scope (for RAG) |
| scope_tags | ARRAY<STRING> | Categorization tags |
| total_estimated_fees | FLOAT | Total estimated revenue |
| source_sheet | STRING | Original sheet name |
| sheet_metadata | STRING | User-provided context |

#### 3. `allocations` - Resource Plans
| Column | Type | Description |
|--------|------|-------------|
| allocation_id | STRING | Unique identifier |
| project_id | STRING | FK to projects |
| role | STRING | Job title |
| week_number | INT | Week 1-52+ |
| hours | FLOAT | Planned hours |
| bill_rate | FLOAT | Rate applied |
| estimated_fees | FLOAT | hours × rate |

#### 4. `actuals` - Actual Time (Future)
| Column | Type | Description |
|--------|------|-------------|
| actual_id | STRING | Unique identifier |
| project_id | STRING | FK to projects |
| week_number | INT | Week number |
| actual_hours | FLOAT | Hours worked |
| variance_hours | FLOAT | actual - planned |

#### 5. `project_scope_docs` - RAG Content
| Column | Type | Description |
|--------|------|-------------|
| doc_id | STRING | Unique identifier |
| project_id | STRING | FK to projects |
| doc_type | STRING | 'pricing_qa', 'user_input', 'pdf_upload', 'markdown_upload' |
| content | STRING | Full text for RAG indexing |
| content_summary | STRING | Gemini-generated summary |
| section_tags | ARRAY | 'scope', 'challenge', 'deliverables', etc. |

### Flexible Schema Design

Every table includes `extra_fields JSON` for sheet-specific data that doesn't
fit the standard schema. The Rate Card also has `all_rates JSON` to capture
all rate card columns (e.g., `{"2023 DEPT": 250, "Moody's 2024": 275}`).

This means the ETL captures **everything** without data loss, even when
different PMs add custom columns.

### Project Identity Key

The `project_id` is the critical key that ties everything together and
prevents hallucinations. It is generated deterministically:

```
project_id = hash(client_name + project_title + source_file + source_sheet)
```

**Rules:**
1. Same file + same sheet = same project_id (idempotent re-ingestion)
2. Different sheets in same file = different project_ids (correctly separates
   "2025 Plan" vs "2026 Plan" for the same client)
3. User can override and link related sheets to a parent project_id
4. Every query to Gemini includes project_id context to prevent cross-project
   hallucination

**Anti-Hallucination Strategy:**
- Gemini system prompt always includes: "Only answer using data from the
  specified project_id. If data is not available, say so."
- Every RAG chunk includes project_id metadata
- SQL queries always filter by project_id
- UI always shows which project context is active

## Query Examples

### 1. Simple SQL Queries
```sql
-- Total estimated fees by client
SELECT client_name, SUM(estimated_fees) as total_fees
FROM allocations a
JOIN projects p ON a.project_id = p.project_id
GROUP BY client_name
ORDER BY total_fees DESC;
```

### 2. Natural Language → SQL (Gemini)
**User:** "What was the estimate for web redesign projects in 2025?"

**Gemini generates:**
```sql
SELECT p.client_name, p.project_title, SUM(a.estimated_fees) as total
FROM projects p
JOIN allocations a ON p.project_id = a.project_id
WHERE LOWER(p.project_title) LIKE '%web%redesign%'
  AND EXTRACT(YEAR FROM p.start_date) = 2025
GROUP BY p.client_name, p.project_title;
```

### 3. RAG for Scope Search
**User:** "Have we done any creative optimization projects?"

**Flow:**
1. Embed user query using Vertex AI
2. Search project scope_description embeddings
3. Return semantically similar projects
4. Gemini summarizes findings

## Technology Stack

| Component | Technology | Purpose |
|-----------|------------|---------|
| Frontend | Next.js or Streamlit | Web UI |
| API | Cloud Run + FastAPI | Backend services |
| Database | BigQuery | Structured data |
| Vector Search | Vertex AI Vector Search | Semantic search |
| LLM | Gemini Pro | NL queries, summaries |
| ETL | Python + Pandas | Data processing |
| IaC | Terraform | Infrastructure |
| Auth | Cloud IAM | Access control |

## ETL Flow

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Upload     │────▶│   Detect     │────▶│   User       │────▶│   Process    │
│   Excel      │     │   Sheets     │     │   Confirms   │     │   & Load     │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
                                                │
                                                ▼
                                         ┌──────────────┐
                                         │  Add Meta:   │
                                         │  - Year      │
                                         │  - Status    │
                                         │  - Scope     │
                                         └──────────────┘
```

## Scope Ingestion & RAG Architecture

### How Scope Gets Into the System

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    SCOPE INGESTION FLOW                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  STEP 1: Auto-Extract from Excel                                       │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ "Pricing Panel Q&A" tab → Extract all Q&A pairs:                │   │
│  │   • Who's the client?                                            │   │
│  │   • What's their marketing challenge?                            │   │
│  │   • What's the total projected revenue?                          │   │
│  │   • What's the delivery model? (Fixed Fee, T&M, etc.)           │   │
│  │   • What's your pricing strategy?                                │   │
│  │                                                                  │   │
│  │ Sheet metadata zone → Extract key-value pairs:                   │   │
│  │   • Client name, project title, start date                       │   │
│  │   • Market, billing type, cadence                                │   │
│  │   • Total fees, gross margin, hours                              │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                           │                                             │
│                           ▼                                             │
│  STEP 2: Show User What Was Found                                      │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ "Here's what we extracted from your pricing sheet:"              │   │
│  │                                                                  │   │
│  │  Client: Carlyle Group                                           │   │
│  │  Project: Global Web Redesign                                    │   │
│  │  Start: July 28, 2025                                            │   │
│  │  Billing: Fixed Fee                                              │   │
│  │  Market: Experience                                              │   │
│  │  Challenge: [from Q&A tab]                                       │   │
│  │  Delivery model: [from Q&A tab]                                  │   │
│  │                                                                  │   │
│  │  ✅ Does this look correct? [Yes / Edit]                         │   │
│  │  💡 The more detail you add, the better the AI can help.         │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                           │                                             │
│                           ▼                                             │
│  STEP 3: Conversation Starters (Guided Input)                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ "Want to add more project context? Pick a starter:"              │   │
│  │                                                                  │   │
│  │  📋 "Describe the project scope and key deliverables"            │   │
│  │  🎯 "What problem is this solving for the client?"               │   │
│  │  👥 "What teams or disciplines are involved?"                    │   │
│  │  📅 "What are the key phases or milestones?"                     │   │
│  │  🔄 "Is this similar to any past projects?"                      │   │
│  │  💰 "Any special pricing considerations?"                        │   │
│  │                                                                  │   │
│  │  Or type freely...                                               │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                           │                                             │
│                           ▼                                             │
│  STEP 4: Upload Additional Docs (Optional)                             │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ "Upload scope documents for richer AI context:"                  │   │
│  │                                                                  │   │
│  │  📄 Upload Markdown (.md) — RECOMMENDED (pre-summarized)         │   │
│  │  📄 Upload PDF (proposals, SOWs)                                 │   │
│  │  📄 Upload Word Doc (.docx)                                      │   │
│  │  📄 Upload Google Doc (via link or export)                       │   │
│  │  📊 Upload Google Sheets (via link or export)                    │   │
│  │  📄 Paste text directly                                          │   │
│  │                                                                  │   │
│  │  💡 TIP: For best results, summarize your slides, PDFs, and     │   │
│  │  tables into a single Markdown doc before uploading.             │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### Gemini Prompt for Users to Summarize Scope

Give this prompt to users so they can use Gemini to summarize their project
materials into a clean Markdown doc for upload:

```
You are a project scope summarizer for a financial planning tool.

I'm going to share project materials (slides, documents, tables, etc.).
Please summarize them into a structured Markdown document with these sections:

## Project Overview
- Client name and industry
- Project name and one-sentence description

## Business Challenge
- What problem is the client trying to solve?
- Why is this project needed?

## Scope & Deliverables
- Key deliverables (bulleted list)
- What is in scope vs out of scope

## Team & Disciplines
- Which departments/disciplines are involved?
  (e.g., Strategy, CX & Design, Engineering, Creative, Data, Paid Media)
- Key roles needed

## Timeline & Phases
- Project start and end dates
- Major phases or milestones

## Budget & Pricing
- Billing model (Fixed Fee / T&M / Retainer)
- Any special pricing notes

## Tags
- Add 3-5 keyword tags for this project
  (e.g., web-redesign, cms-migration, creative-optimization, analytics)

Keep it concise. Focus on facts that would help someone estimate
a similar project in the future.
```

### Supported Document Formats

| Format | Library | Google Workspace | MS 365 |
|--------|---------|-----------------|--------|
| .xlsx / .xls | openpyxl / pandas | Google Sheets export | Excel |
| .pdf | pdfplumber / PyPDF2 | - | - |
| .docx | python-docx | Google Docs export | Word |
| .pptx | python-pptx | Google Slides export | PowerPoint |
| .md | Built-in | - | - |
| .txt | Built-in | - | - |
| Google Sheets | gspread + google-auth | Native | - |
| Google Docs | Google Docs API | Native | - |

**No Document AI needed** for clean text documents. Reserve Document AI
only if users start uploading scanned/image PDFs.

### RAG Query Flow

1. **Ingestion Time:**
   - Extract text from all scope documents
   - Auto-extract Q&A from Pricing Panel tab
   - Generate embeddings using Vertex AI `text-embedding-004`
   - Store in `project_scope_docs` table + vector index

2. **Query Time:**
   - User asks: "Have we done any creative optimization projects?"
   - Generate embedding for query
   - Vector search across `project_scope_docs.content`
   - Retrieve matching project_ids + their scope docs
   - Gemini synthesizes answer WITH project_id context
   - Return: project names, estimates, timelines from structured data

### Anti-Hallucination: Grounded Responses

```python
SYSTEM_PROMPT = """
You are a financial planning assistant for DEPT.
You ONLY answer based on data from the BigQuery tables provided.

Rules:
1. Always cite the project_id and source for every claim.
2. If you don't have data to answer, say "I don't have data for that."
3. Never invent numbers, dates, or project details.
4. When comparing projects, show the data side by side.
5. Distinguish between estimates (from allocations) and actuals (from actuals table).
"""
```

## Future Extensions

1. **Actuals Tracking** - Import actual hours/costs, calculate variances
2. **Burn Rate Analysis** - Weekly/monthly spend vs. budget
3. **Forecasting** - Project remaining budget, ETC, EAC
4. **Alerts** - Notify when projects exceed thresholds
5. **Multi-tenant** - Support multiple organizations
6. **Google Drive Auto-Ingest** - Connect a folder, standardize naming, auto-process
7. **Document AI** - Only if scanned PDFs become common

## Deployment

```bash
# 1. Deploy infrastructure
cd terraform
terraform init
terraform apply

# 2. Build and deploy API
gcloud run deploy etl-service --source .

# 3. Deploy frontend
npm run build
gcloud run deploy frontend --source .
```
