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
# Outputs
# -----------------------------------------------------------------------------

output "dataset_id" {
  value       = google_bigquery_dataset.delivery_finance.dataset_id
  description = "BigQuery Dataset ID"
}

output "table_ids" {
  value = {
    rate_cards    = google_bigquery_table.rate_cards.table_id
    projects      = google_bigquery_table.projects.table_id
    allocations   = google_bigquery_table.allocations.table_id
    actuals       = google_bigquery_table.actuals.table_id
    ingestion_log = google_bigquery_table.ingestion_log.table_id
  }
  description = "BigQuery Table IDs"
}
