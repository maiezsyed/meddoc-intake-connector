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

## RAG Architecture

For scope/description understanding:

1. **Ingestion Time:**
   - Extract scope descriptions from Plan sheets
   - Generate embeddings using Vertex AI
   - Store in BigQuery with vector column OR Vertex AI Vector Search

2. **Query Time:**
   - User asks semantic question
   - Generate embedding for query
   - Find similar project scopes
   - Use Gemini to synthesize answer from matched documents

## Future Extensions

1. **Actuals Tracking** - Import actual hours/costs, calculate variances
2. **Burn Rate Analysis** - Weekly/monthly spend vs. budget
3. **Forecasting** - Project remaining budget, ETC, EAC
4. **Alerts** - Notify when projects exceed thresholds
5. **Multi-tenant** - Support multiple organizations

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
