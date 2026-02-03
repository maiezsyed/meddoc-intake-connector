#!/usr/bin/env python3
"""
Financial ETL Script
====================
Processes financial Excel workbooks with Plan (Allocations) and Rate Card sheets,
performs data transformations, merges, and uploads to BigQuery.

Requirements:
    pip install pandas openpyxl google-cloud-bigquery pyarrow
"""

import sys
import re
from pathlib import Path
from datetime import datetime

import pandas as pd
from google.cloud import bigquery
from google.oauth2 import service_account


# =============================================================================
# Configuration
# =============================================================================

RATE_CARD_SHEET_NAME = "x_Rate Card (Master Rates)"
PLAN_SHEET_NAME = "Plan (Allocations)"

RATE_CARD_SKIP_ROWS = 3  # Header is on row 4 (0-indexed: row 3)
PLAN_DATA_SKIP_ROWS = 29  # Data headers start at row 30 (0-indexed: row 29)

RATE_CARD_REQUIRED_COLUMNS = ['market_region', 'department', 'level', 'role', 'rate', 'cost rate']
MERGE_KEYS = ['market_region', 'department', 'role']

# Column rename mappings
RATE_CARD_RENAME = {'title': 'role'}
PLAN_RENAME = {
    'market': 'market_region',
    'department': 'department',
    'role': 'role',
}

# Date pattern for identifying date columns (YYYY-MM-DD format)
DATE_COLUMN_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')


# =============================================================================
# Helper Functions
# =============================================================================

def print_separator(char: str = "=", length: int = 80) -> None:
    """Print a visual separator line."""
    print(char * length)


def print_section(title: str) -> None:
    """Print a section header."""
    print_separator()
    print(f"  {title}")
    print_separator()


def print_columns(df: pd.DataFrame, label: str) -> None:
    """Print detailed column information for debugging."""
    print(f"\n[DEBUG] {label}")
    print(f"  Total columns: {len(df.columns)}")
    print(f"  Columns: {list(df.columns)}")
    print(f"  Data types:\n{df.dtypes.to_string()}")
    print(f"  Shape: {df.shape}")


def clean_column_headers(df: pd.DataFrame) -> pd.DataFrame:
    """
    Clean column headers by stripping whitespace and converting to lowercase.
    Also handles any column names that might be datetime objects.
    """
    new_columns = []
    for col in df.columns:
        if isinstance(col, datetime):
            # Convert datetime to YYYY-MM-DD string format
            new_columns.append(col.strftime('%Y-%m-%d'))
        elif isinstance(col, str):
            new_columns.append(col.strip().lower())
        else:
            # Convert to string, strip, and lowercase
            new_columns.append(str(col).strip().lower())

    df.columns = new_columns
    return df


def identify_date_columns(columns: list) -> list:
    """Identify columns that match the YYYY-MM-DD date pattern."""
    date_cols = [col for col in columns if DATE_COLUMN_PATTERN.match(str(col))]
    return date_cols


def validate_required_columns(df: pd.DataFrame, required: list, context: str) -> None:
    """Validate that all required columns exist in the dataframe."""
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(
            f"[ERROR] Missing required columns in {context}: {missing}\n"
            f"  Available columns: {list(df.columns)}"
        )
    print(f"[OK] All required columns present in {context}")


# =============================================================================
# Rate Card Processing
# =============================================================================

def process_rate_card(excel_path: str) -> pd.DataFrame:
    """
    Process the Rate Card sheet from the Excel workbook.

    Steps:
        1. Skip first 3 rows (header is on row 4)
        2. Clean column headers (strip whitespace, lowercase)
        3. Rename 'title' column to 'role'
        4. Keep only required columns

    Args:
        excel_path: Path to the Excel workbook

    Returns:
        Processed Rate Card DataFrame
    """
    print_section("PROCESSING RATE CARD SHEET")

    print(f"[INFO] Reading sheet: '{RATE_CARD_SHEET_NAME}'")
    print(f"[INFO] Skipping first {RATE_CARD_SKIP_ROWS} rows")

    # Read the Rate Card sheet
    df = pd.read_excel(
        excel_path,
        sheet_name=RATE_CARD_SHEET_NAME,
        skiprows=RATE_CARD_SKIP_ROWS,
        engine='openpyxl'
    )

    print_columns(df, "Rate Card - Raw data after skiprows")

    # Clean column headers
    df = clean_column_headers(df)
    print_columns(df, "Rate Card - After header cleaning")

    # Rename 'title' to 'role'
    if 'title' in df.columns:
        df = df.rename(columns=RATE_CARD_RENAME)
        print(f"[INFO] Renamed column 'title' -> 'role'")
    else:
        print(f"[WARN] Column 'title' not found for renaming. Available: {list(df.columns)}")

    print_columns(df, "Rate Card - After column rename")

    # Validate required columns exist
    validate_required_columns(df, RATE_CARD_REQUIRED_COLUMNS, "Rate Card")

    # Keep only required columns
    df = df[RATE_CARD_REQUIRED_COLUMNS].copy()
    print(f"[INFO] Kept only required columns: {RATE_CARD_REQUIRED_COLUMNS}")

    # Remove any completely empty rows
    initial_rows = len(df)
    df = df.dropna(how='all')
    dropped = initial_rows - len(df)
    if dropped > 0:
        print(f"[INFO] Dropped {dropped} completely empty rows")

    print(f"\n[RESULT] Rate Card processed successfully")
    print(f"  Final shape: {df.shape}")
    print(f"  Sample data:\n{df.head().to_string()}")

    return df


# =============================================================================
# Plan Sheet Processing
# =============================================================================

def extract_plan_metadata(excel_path: str) -> tuple:
    """
    Extract metadata from the Plan sheet (Client Name and Project Title).

    Reads the first 4 rows to get:
        - Client Name from cell B1
        - Project Title from cell B2

    Args:
        excel_path: Path to the Excel workbook

    Returns:
        Tuple of (client_name, project_title)
    """
    print_section("EXTRACTING PLAN METADATA")

    print(f"[INFO] Reading first 4 rows of sheet: '{PLAN_SHEET_NAME}'")

    # Read first 4 rows with no header to preserve raw cell values
    df_meta = pd.read_excel(
        excel_path,
        sheet_name=PLAN_SHEET_NAME,
        nrows=4,
        header=None,
        engine='openpyxl'
    )

    print(f"[DEBUG] Metadata rows:\n{df_meta.to_string()}")

    # Extract Client Name (B1 = row 0, col 1 in 0-indexed)
    try:
        client_name = df_meta.iloc[0, 1]
        if pd.isna(client_name):
            client_name = "Unknown Client"
            print(f"[WARN] Client Name (B1) is empty, using default: '{client_name}'")
        else:
            client_name = str(client_name).strip()
            print(f"[OK] Client Name (B1): '{client_name}'")
    except IndexError:
        client_name = "Unknown Client"
        print(f"[WARN] Could not read B1, using default: '{client_name}'")

    # Extract Project Title (B2 = row 1, col 1 in 0-indexed)
    try:
        project_title = df_meta.iloc[1, 1]
        if pd.isna(project_title):
            project_title = "Unknown Project"
            print(f"[WARN] Project Title (B2) is empty, using default: '{project_title}'")
        else:
            project_title = str(project_title).strip()
            print(f"[OK] Project Title (B2): '{project_title}'")
    except IndexError:
        project_title = "Unknown Project"
        print(f"[WARN] Could not read B2, using default: '{project_title}'")

    return client_name, project_title


def process_plan_data(excel_path: str, client_name: str, project_title: str) -> pd.DataFrame:
    """
    Process the Plan sheet data section.

    Steps:
        1. Skip first 29 rows (data headers start at row 30)
        2. Clean column headers
        3. Rename dimension columns to standard keys
        4. Melt date columns into Date and Hours
        5. Drop rows where Hours is 0 or NaN
        6. Add Client Name and Project Title columns

    Args:
        excel_path: Path to the Excel workbook
        client_name: Client name from metadata
        project_title: Project title from metadata

    Returns:
        Processed and melted Plan DataFrame
    """
    print_section("PROCESSING PLAN DATA")

    print(f"[INFO] Reading sheet: '{PLAN_SHEET_NAME}'")
    print(f"[INFO] Skipping first {PLAN_DATA_SKIP_ROWS} rows")

    # Read the Plan sheet data section
    df = pd.read_excel(
        excel_path,
        sheet_name=PLAN_SHEET_NAME,
        skiprows=PLAN_DATA_SKIP_ROWS,
        engine='openpyxl'
    )

    print_columns(df, "Plan Data - Raw data after skiprows")

    # Clean column headers
    df = clean_column_headers(df)
    print_columns(df, "Plan Data - After header cleaning")

    # Rename dimension columns
    rename_map = {}
    for old_name, new_name in PLAN_RENAME.items():
        if old_name in df.columns:
            rename_map[old_name] = new_name
            print(f"[INFO] Will rename '{old_name}' -> '{new_name}'")
        elif new_name in df.columns:
            print(f"[INFO] Column '{new_name}' already exists (no rename needed)")
        else:
            print(f"[WARN] Column '{old_name}' not found for renaming")

    if rename_map:
        df = df.rename(columns=rename_map)

    print_columns(df, "Plan Data - After column rename")

    # Identify date columns and dimension columns
    all_columns = list(df.columns)
    date_columns = identify_date_columns(all_columns)

    print(f"\n[DEBUG] Identified {len(date_columns)} date columns:")
    if date_columns:
        print(f"  First 5: {date_columns[:5]}")
        print(f"  Last 5: {date_columns[-5:]}")

    # Dimension columns are everything that's not a date column
    dimension_columns = [col for col in all_columns if col not in date_columns]
    print(f"\n[DEBUG] Dimension columns ({len(dimension_columns)}): {dimension_columns}")

    if not date_columns:
        raise ValueError(
            "[ERROR] No date columns found matching YYYY-MM-DD pattern.\n"
            f"  Available columns: {all_columns}"
        )

    # Validate required dimension columns for merge
    required_dims = ['market_region', 'department', 'role']
    validate_required_columns(df, required_dims, "Plan Data (for merge)")

    # Melt the date columns
    print(f"\n[INFO] Melting {len(date_columns)} date columns into Date and Hours...")

    df_melted = pd.melt(
        df,
        id_vars=dimension_columns,
        value_vars=date_columns,
        var_name='date',
        value_name='hours'
    )

    print(f"[INFO] After melt: {df_melted.shape}")

    # Convert hours to numeric, coercing errors to NaN
    df_melted['hours'] = pd.to_numeric(df_melted['hours'], errors='coerce')

    # Drop rows where Hours is 0 or NaN
    initial_rows = len(df_melted)
    df_melted = df_melted[df_melted['hours'].notna() & (df_melted['hours'] != 0)]
    dropped = initial_rows - len(df_melted)
    print(f"[INFO] Dropped {dropped} rows where Hours was 0 or NaN")
    print(f"[INFO] Remaining rows: {len(df_melted)}")

    # Add Client Name and Project Title
    df_melted['client_name'] = client_name
    df_melted['project_title'] = project_title
    print(f"[INFO] Added 'client_name': '{client_name}'")
    print(f"[INFO] Added 'project_title': '{project_title}'")

    print(f"\n[RESULT] Plan Data processed successfully")
    print(f"  Final shape: {df_melted.shape}")
    print(f"  Final columns: {list(df_melted.columns)}")
    print(f"  Sample data:\n{df_melted.head().to_string()}")

    return df_melted


# =============================================================================
# Merge & Calculation
# =============================================================================

def merge_and_calculate(df_plan: pd.DataFrame, df_rate_card: pd.DataFrame) -> pd.DataFrame:
    """
    Merge Plan data with Rate Card and calculate Total Fees.

    Steps:
        1. Left join on ['market_region', 'department', 'role']
        2. Calculate Total_Fees = Hours * rate

    Args:
        df_plan: Processed Plan DataFrame
        df_rate_card: Processed Rate Card DataFrame

    Returns:
        Merged DataFrame with calculated Total_Fees
    """
    print_section("MERGE & CALCULATION")

    # Pre-merge validation and debugging
    print("[DEBUG] Pre-merge column check:")
    print(f"\n  Plan DataFrame columns ({len(df_plan.columns)}):")
    for col in df_plan.columns:
        print(f"    - {col}")

    print(f"\n  Rate Card DataFrame columns ({len(df_rate_card.columns)}):")
    for col in df_rate_card.columns:
        print(f"    - {col}")

    print(f"\n[INFO] Merge keys: {MERGE_KEYS}")

    # Validate merge keys exist in both dataframes
    for key in MERGE_KEYS:
        if key not in df_plan.columns:
            raise ValueError(f"[ERROR] Merge key '{key}' not found in Plan DataFrame")
        if key not in df_rate_card.columns:
            raise ValueError(f"[ERROR] Merge key '{key}' not found in Rate Card DataFrame")

    print("[OK] All merge keys present in both DataFrames")

    # Show unique values in merge keys for debugging
    print("\n[DEBUG] Unique values in merge keys:")
    for key in MERGE_KEYS:
        plan_unique = df_plan[key].dropna().unique()
        rate_unique = df_rate_card[key].dropna().unique()
        print(f"\n  {key}:")
        print(f"    Plan ({len(plan_unique)} unique): {sorted(plan_unique)[:10]}{'...' if len(plan_unique) > 10 else ''}")
        print(f"    Rate Card ({len(rate_unique)} unique): {sorted(rate_unique)[:10]}{'...' if len(rate_unique) > 10 else ''}")

    # Perform the merge
    print(f"\n[INFO] Performing LEFT JOIN on {MERGE_KEYS}...")

    df_merged = pd.merge(
        df_plan,
        df_rate_card,
        on=MERGE_KEYS,
        how='left',
        indicator=True
    )

    # Report merge results
    merge_stats = df_merged['_merge'].value_counts()
    print(f"\n[DEBUG] Merge results:")
    print(f"  {merge_stats.to_string()}")

    unmatched = (df_merged['_merge'] == 'left_only').sum()
    if unmatched > 0:
        print(f"\n[WARN] {unmatched} rows did not match any Rate Card entry!")
        print("  Sample unmatched rows:")
        unmatched_sample = df_merged[df_merged['_merge'] == 'left_only'][MERGE_KEYS].drop_duplicates().head(10)
        print(f"  {unmatched_sample.to_string()}")

    # Drop the merge indicator column
    df_merged = df_merged.drop(columns=['_merge'])

    # Calculate Total_Fees
    print(f"\n[INFO] Calculating Total_Fees = hours * rate")

    if 'rate' not in df_merged.columns:
        raise ValueError("[ERROR] 'rate' column not found after merge")

    # Ensure rate is numeric
    df_merged['rate'] = pd.to_numeric(df_merged['rate'], errors='coerce')

    df_merged['total_fees'] = df_merged['hours'] * df_merged['rate']

    print(f"[OK] Total_Fees calculated")

    # Summary statistics
    print(f"\n[RESULT] Merge completed successfully")
    print(f"  Final shape: {df_merged.shape}")
    print(f"  Final columns: {list(df_merged.columns)}")
    print(f"\n  Summary statistics:")
    print(f"    Total Hours: {df_merged['hours'].sum():,.2f}")
    print(f"    Total Fees: ${df_merged['total_fees'].sum():,.2f}")
    print(f"    Rows with missing rate: {df_merged['rate'].isna().sum()}")
    print(f"\n  Sample data:\n{df_merged.head(10).to_string()}")

    return df_merged


# =============================================================================
# BigQuery Upload
# =============================================================================

def upload_to_bigquery(
    df: pd.DataFrame,
    project_id: str,
    dataset_id: str,
    table_id: str,
    credentials_path: str = "credentials.json"
) -> None:
    """
    Upload DataFrame to BigQuery.

    Args:
        df: DataFrame to upload
        project_id: GCP project ID
        dataset_id: BigQuery dataset ID
        table_id: BigQuery table ID
        credentials_path: Path to service account credentials JSON
    """
    print_section("BIGQUERY UPLOAD")

    full_table_id = f"{project_id}.{dataset_id}.{table_id}"
    print(f"[INFO] Target table: {full_table_id}")
    print(f"[INFO] Credentials file: {credentials_path}")
    print(f"[INFO] Rows to upload: {len(df)}")

    # Validate credentials file exists
    creds_path = Path(credentials_path)
    if not creds_path.exists():
        raise FileNotFoundError(
            f"[ERROR] Credentials file not found: {credentials_path}\n"
            "  Please ensure the service account JSON file exists."
        )

    print(f"[OK] Credentials file found")

    # Create credentials and client
    print(f"[INFO] Authenticating with BigQuery...")

    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/bigquery"]
    )

    client = bigquery.Client(credentials=credentials, project=project_id)
    print(f"[OK] BigQuery client created for project: {project_id}")

    # Configure the load job
    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        schema_update_options=[
            bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION
        ]
    )

    print(f"[INFO] Write disposition: APPEND")
    print(f"[INFO] Starting upload...")

    # Upload to BigQuery
    job = client.load_table_from_dataframe(
        df,
        full_table_id,
        job_config=job_config
    )

    # Wait for the job to complete
    job.result()

    # Get the updated table info
    table = client.get_table(full_table_id)

    print(f"\n[RESULT] Upload completed successfully!")
    print(f"  Job ID: {job.job_id}")
    print(f"  Rows uploaded: {job.output_rows}")
    print(f"  Table total rows: {table.num_rows}")


# =============================================================================
# Main ETL Pipeline
# =============================================================================

def run_etl(
    excel_path: str,
    bigquery_project: str,
    bigquery_dataset: str,
    bigquery_table: str,
    credentials_path: str = "credentials.json",
    dry_run: bool = False
) -> pd.DataFrame:
    """
    Run the complete ETL pipeline.

    Args:
        excel_path: Path to the Excel workbook
        bigquery_project: GCP project ID
        bigquery_dataset: BigQuery dataset ID
        bigquery_table: BigQuery table ID
        credentials_path: Path to service account credentials JSON
        dry_run: If True, skip BigQuery upload and just return the DataFrame

    Returns:
        Final processed DataFrame
    """
    print_section("STARTING FINANCIAL ETL PIPELINE")
    print(f"[INFO] Excel file: {excel_path}")
    print(f"[INFO] BigQuery target: {bigquery_project}.{bigquery_dataset}.{bigquery_table}")
    print(f"[INFO] Dry run: {dry_run}")
    print(f"[INFO] Timestamp: {datetime.now().isoformat()}")

    # Validate Excel file exists
    excel_path = Path(excel_path)
    if not excel_path.exists():
        raise FileNotFoundError(f"[ERROR] Excel file not found: {excel_path}")

    print(f"[OK] Excel file found: {excel_path}")

    # Step 1: Process Rate Card
    df_rate_card = process_rate_card(excel_path)

    # Step 2A: Extract Plan Metadata
    client_name, project_title = extract_plan_metadata(excel_path)

    # Step 2B: Process Plan Data
    df_plan = process_plan_data(excel_path, client_name, project_title)

    # Step 3: Merge and Calculate
    df_final = merge_and_calculate(df_plan, df_rate_card)

    # Step 4: Upload to BigQuery (unless dry run)
    if dry_run:
        print_section("DRY RUN - SKIPPING BIGQUERY UPLOAD")
        print(f"[INFO] Would have uploaded {len(df_final)} rows to BigQuery")
    else:
        upload_to_bigquery(
            df_final,
            bigquery_project,
            bigquery_dataset,
            bigquery_table,
            credentials_path
        )

    print_section("ETL PIPELINE COMPLETED SUCCESSFULLY")
    print(f"[INFO] Total rows processed: {len(df_final)}")
    print(f"[INFO] Total fees: ${df_final['total_fees'].sum():,.2f}")

    return df_final


# =============================================================================
# CLI Entry Point
# =============================================================================

def main():
    """Main entry point for CLI usage."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Financial ETL Pipeline - Process Excel workbooks and upload to BigQuery",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Run full ETL pipeline
    python financial_etl.py data.xlsx my-project my-dataset my-table

    # Dry run (no BigQuery upload)
    python financial_etl.py data.xlsx my-project my-dataset my-table --dry-run

    # Specify custom credentials file
    python financial_etl.py data.xlsx my-project my-dataset my-table -c /path/to/creds.json
        """
    )

    parser.add_argument(
        "excel_file",
        help="Path to the Excel workbook"
    )
    parser.add_argument(
        "project_id",
        help="Google Cloud project ID"
    )
    parser.add_argument(
        "dataset_id",
        help="BigQuery dataset ID"
    )
    parser.add_argument(
        "table_id",
        help="BigQuery table ID"
    )
    parser.add_argument(
        "-c", "--credentials",
        default="credentials.json",
        help="Path to service account credentials JSON (default: credentials.json)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Process data but skip BigQuery upload"
    )
    parser.add_argument(
        "--output-csv",
        help="Save final DataFrame to CSV file"
    )

    args = parser.parse_args()

    try:
        df_result = run_etl(
            excel_path=args.excel_file,
            bigquery_project=args.project_id,
            bigquery_dataset=args.dataset_id,
            bigquery_table=args.table_id,
            credentials_path=args.credentials,
            dry_run=args.dry_run
        )

        if args.output_csv:
            df_result.to_csv(args.output_csv, index=False)
            print(f"\n[INFO] Results saved to: {args.output_csv}")

        sys.exit(0)

    except FileNotFoundError as e:
        print(f"\n{e}")
        sys.exit(1)
    except ValueError as e:
        print(f"\n{e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
