# =============================================================================
# DEPT Delivery Finance - BigQuery Infrastructure
# =============================================================================
# This Terraform creates the BigQuery dataset and tables for the
# financial planning and estimation tool.
# =============================================================================

terraform {
  required_version = ">= 1.0.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

# -----------------------------------------------------------------------------
# Variables
# -----------------------------------------------------------------------------

variable "project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "region" {
  description = "GCP Region for BigQuery dataset"
  type        = string
  default     = "US"
}

variable "dataset_id" {
  description = "BigQuery Dataset ID"
  type        = string
  default     = "delivery_finance"
}

variable "environment" {
  description = "Environment (dev, staging, prod)"
  type        = string
  default     = "dev"
}

# -----------------------------------------------------------------------------
# Provider Configuration
# -----------------------------------------------------------------------------

provider "google" {
  project = var.project_id
  region  = var.region
}

# -----------------------------------------------------------------------------
# BigQuery Dataset
# -----------------------------------------------------------------------------

resource "google_bigquery_dataset" "delivery_finance" {
  dataset_id    = var.dataset_id
  friendly_name = "Delivery Finance Data"
  description   = "Financial planning, estimates, and actuals for client projects"
  location      = var.region

  labels = {
    environment = var.environment
    managed_by  = "terraform"
  }
}

# -----------------------------------------------------------------------------
# Table: rate_cards
# Master rate card data - source of truth for billing and cost rates
# -----------------------------------------------------------------------------

resource "google_bigquery_table" "rate_cards" {
  dataset_id          = google_bigquery_dataset.delivery_finance.dataset_id
  table_id            = "rate_cards"
  deletion_protection = false

  schema = jsonencode([
    {
      name        = "rate_card_id"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Unique identifier for rate card entry"
    },
    {
      name        = "rate_card_name"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Name of the rate card (e.g., '2023 DEPT', 'BASIC Google Retained')"
    },
    {
      name        = "market_region"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Market/Region (e.g., AMER, EMEA, APAC)"
    },
    {
      name        = "department"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Global Department (e.g., CX & Design, Engineering)"
    },
    {
      name        = "level"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Seniority level (Junior, Senior, Manager, Lead, Director)"
    },
    {
      name        = "role"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Job title/role"
    },
    {
      name        = "cost_rate"
      type        = "FLOAT64"
      mode        = "NULLABLE"
      description = "Internal cost rate per hour"
    },
    {
      name        = "bill_rate"
      type        = "FLOAT64"
      mode        = "NULLABLE"
      description = "Client billing rate per hour"
    },
    {
      name        = "effective_date"
      type        = "DATE"
      mode        = "NULLABLE"
      description = "Date this rate becomes effective"
    },
    {
      name        = "all_rates"
      type        = "JSON"
      mode        = "NULLABLE"
      description = "All rate card columns as JSON (e.g., {'2023 DEPT': 250, 'Moody\\'s 2024': 275})"
    },
    {
      name        = "extra_fields"
      type        = "JSON"
      mode        = "NULLABLE"
      description = "Overflow for any non-standard columns"
    },
    {
      name        = "source_file"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Source Excel file name"
    },
    {
      name        = "ingested_at"
      type        = "TIMESTAMP"
      mode        = "REQUIRED"
      description = "Timestamp when record was ingested"
    }
  ])

  labels = {
    data_type = "master_data"
  }
}

# -----------------------------------------------------------------------------
# Table: projects
# Project metadata and scope information
# -----------------------------------------------------------------------------

resource "google_bigquery_table" "projects" {
  dataset_id          = google_bigquery_dataset.delivery_finance.dataset_id
  table_id            = "projects"
  deletion_protection = false

  schema = jsonencode([
    {
      name        = "project_id"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Unique project identifier"
    },
    {
      name        = "client_name"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Client/Company name"
    },
    {
      name        = "project_title"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Project title/name"
    },
    {
      name        = "project_number"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Internal project number"
    },
    {
      name        = "company_code"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Company code (e.g., CXUS, DPUS)"
    },
    {
      name        = "market_region"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Primary market region"
    },
    {
      name        = "rate_card_used"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Rate card applied to this project"
    },
    {
      name        = "billing_type"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Billing type (Fixed Fee, T&M, Retainer)"
    },
    {
      name        = "start_date"
      type        = "DATE"
      mode        = "NULLABLE"
      description = "Project start date"
    },
    {
      name        = "end_date"
      type        = "DATE"
      mode        = "NULLABLE"
      description = "Project end date"
    },
    {
      name        = "scope_description"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Free-text project scope description for RAG search"
    },
    {
      name        = "scope_tags"
      type        = "STRING"
      mode        = "REPEATED"
      description = "Tags for categorizing project scope"
    },
    {
      name        = "total_estimated_fees"
      type        = "FLOAT64"
      mode        = "NULLABLE"
      description = "Total estimated fees"
    },
    {
      name        = "total_estimated_hours"
      type        = "FLOAT64"
      mode        = "NULLABLE"
      description = "Total estimated hours"
    },
    {
      name        = "total_estimated_cost"
      type        = "FLOAT64"
      mode        = "NULLABLE"
      description = "Total estimated internal cost"
    },
    {
      name        = "target_gross_margin"
      type        = "FLOAT64"
      mode        = "NULLABLE"
      description = "Target gross margin percentage"
    },
    {
      name        = "status"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Project status (Draft, Active, Completed, On Hold)"
    },
    {
      name        = "source_file"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Source Excel file"
    },
    {
      name        = "source_sheet"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Source sheet/tab name"
    },
    {
      name        = "sheet_metadata"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "User-provided metadata about the sheet (e.g., '2025 plan', 'WIP')"
    },
    {
      name        = "sheet_metadata_zone"
      type        = "JSON"
      mode        = "NULLABLE"
      description = "All key-value pairs extracted from the metadata zone (rows above data header)"
    },
    {
      name        = "pricing_panel_qa"
      type        = "JSON"
      mode        = "NULLABLE"
      description = "Extracted Q&A from Pricing Panel tab as JSON object"
    },
    {
      name        = "extra_fields"
      type        = "JSON"
      mode        = "NULLABLE"
      description = "Overflow for any sheet-specific fields not in the standard schema"
    },
    {
      name        = "ingested_at"
      type        = "TIMESTAMP"
      mode        = "REQUIRED"
      description = "Timestamp when record was ingested"
    }
  ])

  labels = {
    data_type = "project_metadata"
  }
}

# -----------------------------------------------------------------------------
# Table: project_scope_docs
# Scope documents and descriptions for RAG (from uploads, Q&A, user input)
# -----------------------------------------------------------------------------

resource "google_bigquery_table" "project_scope_docs" {
  dataset_id          = google_bigquery_dataset.delivery_finance.dataset_id
  table_id            = "project_scope_docs"
  deletion_protection = false

  schema = jsonencode([
    {
      name        = "doc_id"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Unique document identifier"
    },
    {
      name        = "project_id"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "FK to projects table"
    },
    {
      name        = "doc_type"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Source type: 'pricing_qa', 'user_input', 'pdf_upload', 'doc_upload', 'slides_upload', 'markdown_upload', 'sheet_metadata'"
    },
    {
      name        = "source_name"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Original filename or source identifier"
    },
    {
      name        = "content"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Full text content for RAG indexing"
    },
    {
      name        = "content_summary"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Gemini-generated summary of the content"
    },
    {
      name        = "section_tags"
      type        = "STRING"
      mode        = "REPEATED"
      description = "Tags: 'scope', 'challenge', 'deliverables', 'timeline', 'team', 'budget'"
    },
    {
      name        = "extra_fields"
      type        = "JSON"
      mode        = "NULLABLE"
      description = "Any additional metadata from the document"
    },
    {
      name        = "uploaded_by"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "User who uploaded/created this record"
    },
    {
      name        = "ingested_at"
      type        = "TIMESTAMP"
      mode        = "REQUIRED"
      description = "Timestamp when record was ingested"
    }
  ])

  labels = {
    data_type = "rag_content"
  }
}

# -----------------------------------------------------------------------------
# Table: allocations
# Resource allocations (hours per role per period)
# -----------------------------------------------------------------------------

resource "google_bigquery_table" "allocations" {
  dataset_id          = google_bigquery_dataset.delivery_finance.dataset_id
  table_id            = "allocations"
  deletion_protection = false

  schema = jsonencode([
    {
      name        = "allocation_id"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Unique allocation record identifier"
    },
    {
      name        = "project_id"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Foreign key to projects table"
    },
    {
      name        = "category"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Category/workstream (e.g., PMO, Strategy, Design)"
    },
    {
      name        = "role"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Role/Title"
    },
    {
      name        = "market_region"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Market region for this allocation"
    },
    {
      name        = "department"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Department"
    },
    {
      name        = "resource_name"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Named resource (if assigned)"
    },
    {
      name        = "week_number"
      type        = "INT64"
      mode        = "REQUIRED"
      description = "Week number (1-52+)"
    },
    {
      name        = "week_start_date"
      type        = "DATE"
      mode        = "NULLABLE"
      description = "Start date of the week"
    },
    {
      name        = "hours"
      type        = "FLOAT64"
      mode        = "REQUIRED"
      description = "Hours allocated"
    },
    {
      name        = "bill_rate"
      type        = "FLOAT64"
      mode        = "NULLABLE"
      description = "Billing rate applied"
    },
    {
      name        = "cost_rate"
      type        = "FLOAT64"
      mode        = "NULLABLE"
      description = "Cost rate applied"
    },
    {
      name        = "estimated_fees"
      type        = "FLOAT64"
      mode        = "NULLABLE"
      description = "Calculated: hours * bill_rate"
    },
    {
      name        = "estimated_cost"
      type        = "FLOAT64"
      mode        = "NULLABLE"
      description = "Calculated: hours * cost_rate"
    },
    {
      name        = "extra_fields"
      type        = "JSON"
      mode        = "NULLABLE"
      description = "Overflow for sheet-specific columns (e.g., specialization, team, overrides)"
    },
    {
      name        = "source_file"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Source Excel file"
    },
    {
      name        = "source_sheet"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Source sheet/tab name"
    },
    {
      name        = "ingested_at"
      type        = "TIMESTAMP"
      mode        = "REQUIRED"
      description = "Timestamp when record was ingested"
    }
  ])

  time_partitioning {
    type  = "DAY"
    field = "week_start_date"
  }

  clustering = ["project_id", "department", "role"]

  labels = {
    data_type = "transactional"
  }
}

# -----------------------------------------------------------------------------
# Table: actuals (for future use)
# Actual hours worked and costs incurred
# -----------------------------------------------------------------------------

resource "google_bigquery_table" "actuals" {
  dataset_id          = google_bigquery_dataset.delivery_finance.dataset_id
  table_id            = "actuals"
  deletion_protection = false

  schema = jsonencode([
    {
      name        = "actual_id"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Unique actual record identifier"
    },
    {
      name        = "project_id"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Foreign key to projects table"
    },
    {
      name        = "category"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Category/workstream"
    },
    {
      name        = "role"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Role/Title"
    },
    {
      name        = "resource_name"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Resource name"
    },
    {
      name        = "week_number"
      type        = "INT64"
      mode        = "REQUIRED"
      description = "Week number"
    },
    {
      name        = "week_start_date"
      type        = "DATE"
      mode        = "NULLABLE"
      description = "Start date of the week"
    },
    {
      name        = "actual_hours"
      type        = "FLOAT64"
      mode        = "REQUIRED"
      description = "Actual hours worked"
    },
    {
      name        = "actual_cost"
      type        = "FLOAT64"
      mode        = "NULLABLE"
      description = "Actual cost incurred"
    },
    {
      name        = "actual_fees"
      type        = "FLOAT64"
      mode        = "NULLABLE"
      description = "Actual fees billed"
    },
    {
      name        = "variance_hours"
      type        = "FLOAT64"
      mode        = "NULLABLE"
      description = "Variance from estimated hours"
    },
    {
      name        = "variance_cost"
      type        = "FLOAT64"
      mode        = "NULLABLE"
      description = "Variance from estimated cost"
    },
    {
      name        = "source_file"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Source Excel file"
    },
    {
      name        = "ingested_at"
      type        = "TIMESTAMP"
      mode        = "REQUIRED"
      description = "Timestamp when record was ingested"
    }
  ])

  time_partitioning {
    type  = "DAY"
    field = "week_start_date"
  }

  clustering = ["project_id", "role"]

  labels = {
    data_type = "transactional"
  }
}

# -----------------------------------------------------------------------------
# Table: ingestion_log
# Track what files/sheets have been processed
# -----------------------------------------------------------------------------

resource "google_bigquery_table" "ingestion_log" {
  dataset_id          = google_bigquery_dataset.delivery_finance.dataset_id
  table_id            = "ingestion_log"
  deletion_protection = false

  schema = jsonencode([
    {
      name        = "ingestion_id"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Unique ingestion job identifier"
    },
    {
      name        = "source_file"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Source file name"
    },
    {
      name        = "source_sheet"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Source sheet name"
    },
    {
      name        = "sheet_type"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Type of sheet (rate_card, plan, actuals)"
    },
    {
      name        = "user_metadata"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "User-provided description of the sheet"
    },
    {
      name        = "rows_processed"
      type        = "INT64"
      mode        = "NULLABLE"
      description = "Number of rows processed"
    },
    {
      name        = "status"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Status (success, failed, partial)"
    },
    {
      name        = "error_message"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Error details if failed"
    },
    {
      name        = "ingested_by"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "User who ran the ingestion"
    },
    {
      name        = "ingested_at"
      type        = "TIMESTAMP"
      mode        = "REQUIRED"
      description = "Timestamp of ingestion"
    }
  ])

  labels = {
    data_type = "audit"
  }
}

# -----------------------------------------------------------------------------
# Table: costs
# Non-labor costs and expenses
# -----------------------------------------------------------------------------

resource "google_bigquery_table" "costs" {
  dataset_id          = google_bigquery_dataset.delivery_finance.dataset_id
  table_id            = "costs"
  deletion_protection = false

  schema = jsonencode([
    {
      name        = "cost_id"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Unique cost record identifier"
    },
    {
      name        = "project_id"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Foreign key to projects table"
    },
    {
      name        = "item"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Cost item name"
    },
    {
      name        = "category"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Cost category"
    },
    {
      name        = "vendor"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Vendor name"
    },
    {
      name        = "cost_type"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Type: Passthrough, Investment, Billable"
    },
    {
      name        = "estimate_actual"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Whether this is an Estimate or Actual"
    },
    {
      name        = "amount"
      type        = "FLOAT64"
      mode        = "NULLABLE"
      description = "Cost amount"
    },
    {
      name        = "cost_date"
      type        = "DATE"
      mode        = "NULLABLE"
      description = "Date of cost"
    },
    {
      name        = "notes"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Notes/description"
    },
    {
      name        = "source_file"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Source Excel file"
    },
    {
      name        = "source_sheet"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Source sheet/tab name"
    },
    {
      name        = "ingested_at"
      type        = "TIMESTAMP"
      mode        = "REQUIRED"
      description = "Timestamp when record was ingested"
    }
  ])

  labels = {
    data_type = "transactional"
  }
}

# -----------------------------------------------------------------------------
# Table: chat_history
# Store conversation history for context
# -----------------------------------------------------------------------------

resource "google_bigquery_table" "chat_history" {
  dataset_id          = google_bigquery_dataset.delivery_finance.dataset_id
  table_id            = "chat_history"
  deletion_protection = false

  schema = jsonencode([
    {
      name        = "message_id"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Unique message identifier"
    },
    {
      name        = "session_id"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Chat session identifier"
    },
    {
      name        = "user_id"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "User identifier"
    },
    {
      name        = "role"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Message role: user or assistant"
    },
    {
      name        = "content"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Message content"
    },
    {
      name        = "context_used"
      type        = "JSON"
      mode        = "NULLABLE"
      description = "Projects/data referenced in response"
    },
    {
      name        = "created_at"
      type        = "TIMESTAMP"
      mode        = "REQUIRED"
      description = "Timestamp when message was created"
    }
  ])

  labels = {
    data_type = "audit"
  }
}

# -----------------------------------------------------------------------------
# Enable Additional APIs for Cloud Run and Vertex AI
# -----------------------------------------------------------------------------

resource "google_project_service" "required_apis" {
  for_each = toset([
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "aiplatform.googleapis.com",
    "secretmanager.googleapis.com",
  ])

  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

# -----------------------------------------------------------------------------
# Service Account for the App
# -----------------------------------------------------------------------------

resource "google_service_account" "app_sa" {
  account_id   = "delivery-finance-app"
  display_name = "Delivery Finance App Service Account"
}

resource "google_project_iam_member" "app_sa_roles" {
  for_each = toset([
    "roles/bigquery.dataEditor",
    "roles/bigquery.jobUser",
    "roles/aiplatform.user",
    "roles/storage.objectViewer",
    "roles/logging.logWriter",
  ])

  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.app_sa.email}"
}

# -----------------------------------------------------------------------------
# Artifact Registry for Docker Images
# -----------------------------------------------------------------------------

resource "google_artifact_registry_repository" "app_repo" {
  location      = var.region == "US" ? "us-central1" : var.region
  repository_id = "delivery-finance-app"
  description   = "Docker images for Delivery Finance App"
  format        = "DOCKER"

  depends_on = [google_project_service.required_apis]
}

# -----------------------------------------------------------------------------
# Cloud Run Service
# -----------------------------------------------------------------------------

variable "cloud_run_region" {
  description = "Region for Cloud Run service"
  type        = string
  default     = "us-central1"
}

resource "google_cloud_run_v2_service" "app" {
  name     = "delivery-finance-app"
  location = var.cloud_run_region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.app_sa.email

    containers {
      image = "${var.cloud_run_region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.app_repo.repository_id}/app:latest"

      ports {
        container_port = 8080
      }

      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }

      env {
        name  = "BQ_DATASET_ID"
        value = google_bigquery_dataset.delivery_finance.dataset_id
      }

      env {
        name  = "GEMINI_MODEL"
        value = "gemini-1.5-pro"
      }

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
      }

      startup_probe {
        http_get {
          path = "/"
        }
        initial_delay_seconds = 10
        period_seconds        = 10
        failure_threshold     = 3
      }
    }

    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }
  }

  depends_on = [
    google_project_service.required_apis,
    google_artifact_registry_repository.app_repo,
  ]

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
    ]
  }
}

# Allow unauthenticated access (or remove this for authenticated only)
resource "google_cloud_run_v2_service_iam_member" "public_access" {
  project  = var.project_id
  location = google_cloud_run_v2_service.app.location
  name     = google_cloud_run_v2_service.app.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# -----------------------------------------------------------------------------
# Outputs
# -----------------------------------------------------------------------------

output "dataset_id" {
  value       = google_bigquery_dataset.delivery_finance.dataset_id
  description = "BigQuery Dataset ID"
}

output "table_ids" {
  value = {
    rate_cards         = google_bigquery_table.rate_cards.table_id
    projects           = google_bigquery_table.projects.table_id
    project_scope_docs = google_bigquery_table.project_scope_docs.table_id
    allocations        = google_bigquery_table.allocations.table_id
    actuals            = google_bigquery_table.actuals.table_id
    costs              = google_bigquery_table.costs.table_id
    ingestion_log      = google_bigquery_table.ingestion_log.table_id
    chat_history       = google_bigquery_table.chat_history.table_id
  }
  description = "BigQuery Table IDs"
}

output "service_account_email" {
  value       = google_service_account.app_sa.email
  description = "Service Account Email"
}

output "artifact_registry_url" {
  value       = "${var.cloud_run_region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.app_repo.repository_id}"
  description = "Artifact Registry URL for Docker images"
}

output "cloud_run_url" {
  value       = google_cloud_run_v2_service.app.uri
  description = "Cloud Run Service URL"
}
