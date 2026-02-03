#!/usr/bin/env python3
"""
Financial Data Ingestion Script
ETL pipeline to parse Pricing Template Excel and load to BigQuery.
"""

import glob
import re
import sys
from pathlib import Path

import pandas as pd
from google.cloud import bigquery

# BigQuery Configuration
BQ_PROJECT_ID = "ddus-maiez-syed-dept-ai"
BQ_DATASET = "delivery_finance_data"
BQ_TABLE = "master_forecasts"

# Sheet names
PLAN_SHEET = "Plan (Allocations)"
RATE_CARD_SHEET = "x_Rate Card (Master Rates)"

# Plan sheet row indices (0-indexed)
DATE_ROW_IDX = 26      # Row 27 contains actual dates
WEEK_ROW_IDX = 28      # Row 29 contains week numbers
DATA_START_ROW = 29    # Row 30 is where data grid starts (0-indexed = 29)

# Plan ID columns to keep
PLAN_ID_COLUMNS = ["Category", "Market", "Department", "Role", "Employee Name"]

# Rate Card configuration
RATE_CARD_HEADER_ROW = 4  # Row 5 (0-indexed = 4)
RATE_CARD_COLUMNS = ["A", "B", "C", "D", "E", "F"]  # Keep only A-F


def find_input_file() -> Path:
    """Find the Working_*.xlsx or Working_*.csv file in current directory."""
    patterns = ["Working_*.xlsx", "Working_*.csv"]

    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            # Return the most recently modified file if multiple matches
            return Path(max(matches, key=lambda x: Path(x).stat().st_mtime))

    raise FileNotFoundError(
        "No input file found. Expected a file matching 'Working_*.xlsx' or 'Working_*.csv'"
    )


def extract_project_id(file_path: Path) -> str:
    """Extract project_id from cell B3 of the Plan sheet."""
    df = pd.read_excel(
        file_path,
        sheet_name=PLAN_SHEET,
        header=None,
        nrows=5,
        usecols=[1]  # Column B
    )
    project_id = df.iloc[2, 0]  # Row 3 (0-indexed = 2), Column B
    return str(project_id).strip() if pd.notna(project_id) else "UNKNOWN"


def parse_plan_sheet(file_path: Path) -> tuple[pd.DataFrame, dict]:
    """
    Parse the Plan (Allocations) sheet using two-pass header mapping.

    Pass 1: Build week-to-date mapping from rows 27 and 29.
    Pass 2: Load data grid and transform.

    Returns:
        Tuple of (melted DataFrame, week-to-date mapping dict)
    """
    # === PASS 1: Build Week-to-Date Mapping ===

    # Read the date row (Row 27, 0-indexed 26)
    date_row_df = pd.read_excel(
        file_path,
        sheet_name=PLAN_SHEET,
        header=None,
        skiprows=DATE_ROW_IDX,
        nrows=1
    )
    date_row = date_row_df.iloc[0]

    # Read the week number row (Row 29, 0-indexed 28)
    week_row_df = pd.read_excel(
        file_path,
        sheet_name=PLAN_SHEET,
        header=None,
        skiprows=WEEK_ROW_IDX,
        nrows=1
    )
    week_row = week_row_df.iloc[0]

    # Create week-to-date mapping
    week_to_date = {}
    for col_idx in range(len(week_row)):
        week_num = week_row.iloc[col_idx]
        date_val = date_row.iloc[col_idx]

        # Only map if week_num looks like a week number (01, 02, etc.)
        if pd.notna(week_num):
            week_str = str(week_num).strip()
            # Check if it's a valid week number pattern (1-2 digits, possibly zero-padded)
            if re.match(r'^\d{1,2}$', week_str):
                if pd.notna(date_val):
                    # Convert date to standard format
                    if isinstance(date_val, pd.Timestamp):
                        week_to_date[week_str] = date_val.strftime('%Y-%m-%d')
                    else:
                        # Try to parse as date
                        try:
                            parsed_date = pd.to_datetime(date_val)
                            week_to_date[week_str] = parsed_date.strftime('%Y-%m-%d')
                        except (ValueError, TypeError):
                            week_to_date[week_str] = str(date_val)

    print(f"  Week-to-Date mapping created: {len(week_to_date)} weeks mapped")

    # === PASS 2: Load Data Grid and Transform ===

    # Read the header row (Row 29 contains column names for the data grid)
    header_df = pd.read_excel(
        file_path,
        sheet_name=PLAN_SHEET,
        header=None,
        skiprows=WEEK_ROW_IDX,
        nrows=1
    )
    headers = header_df.iloc[0].tolist()

    # Read the data starting from Row 30
    data_df = pd.read_excel(
        file_path,
        sheet_name=PLAN_SHEET,
        header=None,
        skiprows=DATA_START_ROW
    )

    # Assign headers
    data_df.columns = headers

    # Identify ID columns and week/date columns
    id_cols = [col for col in PLAN_ID_COLUMNS if col in data_df.columns]
    week_cols = [col for col in data_df.columns
                 if str(col).strip() in week_to_date or re.match(r'^\d{1,2}$', str(col).strip())]

    # Rename week columns to their actual dates
    rename_map = {}
    for col in week_cols:
        week_str = str(col).strip()
        if week_str in week_to_date:
            rename_map[col] = week_to_date[week_str]

    data_df = data_df.rename(columns=rename_map)

    # Get the renamed date columns
    date_cols = list(rename_map.values())

    # Keep only ID columns and date columns
    cols_to_keep = id_cols + date_cols
    cols_available = [c for c in cols_to_keep if c in data_df.columns]
    data_df = data_df[cols_available]

    # Filter out rows where Role is empty or contains "Total"
    if "Role" in data_df.columns:
        data_df = data_df[data_df["Role"].notna()]
        data_df = data_df[~data_df["Role"].astype(str).str.contains("Total", case=False, na=False)]

    # Melt/Unpivot the date columns
    melted_df = data_df.melt(
        id_vars=id_cols,
        value_vars=date_cols,
        var_name="forecast_month",
        value_name="hours_allocated"
    )

    # Convert forecast_month to date type
    melted_df["forecast_month"] = pd.to_datetime(melted_df["forecast_month"]).dt.date

    # Clean up hours_allocated - convert to float, handle NaN
    melted_df["hours_allocated"] = pd.to_numeric(melted_df["hours_allocated"], errors="coerce").fillna(0.0)

    print(f"  Plan data extracted: {len(melted_df)} rows after melting")

    return melted_df, week_to_date


def parse_rate_card_sheet(file_path: Path) -> pd.DataFrame:
    """
    Parse the x_Rate Card (Master Rates) sheet.

    - Keep only columns A through F
    - Header is on Row 5 (0-indexed 4)
    - Drop rows 1-4
    - Rename columns and clean rate values
    """
    # Read with header on row 5 (0-indexed 4), only columns A-F (0-5)
    df = pd.read_excel(
        file_path,
        sheet_name=RATE_CARD_SHEET,
        header=RATE_CARD_HEADER_ROW,
        usecols="A:F"
    )

    # Get the original column names for reference
    original_cols = df.columns.tolist()
    print(f"  Rate Card original columns: {original_cols}")

    # Rename columns to standardized names
    # Col A -> market_region, Col B -> department, Col D -> role_title,
    # Col E -> cost_rate, Col F -> bill_rate
    # Note: Col C is kept but not explicitly renamed (might be unused)

    if len(df.columns) >= 6:
        new_columns = {
            df.columns[0]: "market_region",
            df.columns[1]: "department",
            df.columns[2]: "col_c_unused",  # Placeholder for column C
            df.columns[3]: "role_title",
            df.columns[4]: "cost_rate",
            df.columns[5]: "bill_rate"
        }
        df = df.rename(columns=new_columns)

    # Clean cost_rate - strip '$' and convert to float
    if "cost_rate" in df.columns:
        df["cost_rate"] = (
            df["cost_rate"]
            .astype(str)
            .str.replace("$", "", regex=False)
            .str.replace(",", "", regex=False)
            .str.strip()
        )
        df["cost_rate"] = pd.to_numeric(df["cost_rate"], errors="coerce").fillna(0.0)

    # Clean bill_rate - strip '$' and convert to float
    if "bill_rate" in df.columns:
        df["bill_rate"] = (
            df["bill_rate"]
            .astype(str)
            .str.replace("$", "", regex=False)
            .str.replace(",", "", regex=False)
            .str.strip()
        )
        df["bill_rate"] = pd.to_numeric(df["bill_rate"], errors="coerce").fillna(0.0)

    # Drop the unused column C
    if "col_c_unused" in df.columns:
        df = df.drop(columns=["col_c_unused"])

    # Remove any rows where all key fields are empty
    df = df.dropna(subset=["market_region", "department", "role_title"], how="all")

    print(f"  Rate Card data extracted: {len(df)} rows")

    return df


def merge_and_calculate(
    plan_df: pd.DataFrame,
    rate_card_df: pd.DataFrame,
    project_id: str
) -> pd.DataFrame:
    """
    Merge Plan data with Rate Card and calculate financial metrics.

    Join on: [Market, Department, Role]
    Calculate: forecasted_cost, forecasted_revenue
    """
    # Standardize column names for merging
    plan_df = plan_df.rename(columns={
        "Market": "market_region",
        "Department": "department",
        "Role": "role_title",
        "Employee Name": "resource_name",
        "Category": "category"
    })

    # Perform the merge (left join to keep all plan data)
    merged_df = plan_df.merge(
        rate_card_df[["market_region", "department", "role_title", "cost_rate", "bill_rate"]],
        on=["market_region", "department", "role_title"],
        how="left"
    )

    # Fill missing rates with 0
    merged_df["cost_rate"] = merged_df["cost_rate"].fillna(0.0)
    merged_df["bill_rate"] = merged_df["bill_rate"].fillna(0.0)

    # Calculate financial metrics
    merged_df["forecasted_cost"] = merged_df["hours_allocated"] * merged_df["cost_rate"]
    merged_df["forecasted_revenue"] = merged_df["hours_allocated"] * merged_df["bill_rate"]

    # Add project_id
    merged_df["project_id"] = project_id

    # Select and order final columns per schema
    final_columns = [
        "project_id",
        "forecast_month",
        "category",
        "market_region",
        "department",
        "role_title",
        "resource_name",
        "hours_allocated",
        "cost_rate",
        "bill_rate",
        "forecasted_cost",
        "forecasted_revenue"
    ]

    # Keep only columns that exist
    available_columns = [c for c in final_columns if c in merged_df.columns]
    merged_df = merged_df[available_columns]

    print(f"  Merged data: {len(merged_df)} rows")

    return merged_df


def get_bigquery_schema() -> list:
    """Define the BigQuery schema for the target table."""
    return [
        bigquery.SchemaField("project_id", "STRING"),
        bigquery.SchemaField("forecast_month", "DATE"),
        bigquery.SchemaField("category", "STRING"),
        bigquery.SchemaField("market_region", "STRING"),
        bigquery.SchemaField("department", "STRING"),
        bigquery.SchemaField("role_title", "STRING"),
        bigquery.SchemaField("resource_name", "STRING"),
        bigquery.SchemaField("hours_allocated", "FLOAT64"),
        bigquery.SchemaField("cost_rate", "FLOAT64"),
        bigquery.SchemaField("bill_rate", "FLOAT64"),
        bigquery.SchemaField("forecasted_cost", "FLOAT64"),
        bigquery.SchemaField("forecasted_revenue", "FLOAT64"),
    ]


def load_to_bigquery(df: pd.DataFrame, dry_run: bool = False) -> None:
    """
    Load the DataFrame to BigQuery.

    Args:
        df: The DataFrame to load
        dry_run: If True, only validate and print info without loading
    """
    table_id = f"{BQ_PROJECT_ID}.{BQ_DATASET}.{BQ_TABLE}"

    if dry_run:
        print(f"\n[DRY RUN] Would load {len(df)} rows to {table_id}")
        print(f"[DRY RUN] Schema: {[f.name for f in get_bigquery_schema()]}")
        print(f"\n[DRY RUN] Sample data (first 5 rows):")
        print(df.head().to_string())
        return

    # Initialize BigQuery client
    client = bigquery.Client(project=BQ_PROJECT_ID)

    # Configure the load job
    job_config = bigquery.LoadJobConfig(
        schema=get_bigquery_schema(),
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,  # Replace table
    )

    print(f"\nLoading {len(df)} rows to {table_id}...")

    # Load the DataFrame
    job = client.load_table_from_dataframe(df, table_id, job_config=job_config)

    # Wait for the job to complete
    job.result()

    # Verify the load
    table = client.get_table(table_id)
    print(f"Successfully loaded {table.num_rows} rows to {table_id}")


def main():
    """Main ETL pipeline execution."""
    print("=" * 60)
    print("Financial Data Ingestion Pipeline")
    print("=" * 60)

    # Check for --dry-run flag
    dry_run = "--dry-run" in sys.argv or "-n" in sys.argv
    if dry_run:
        print("[MODE] Dry Run - No data will be loaded to BigQuery")

    # Step 1: Find input file
    print("\n[1/5] Finding input file...")
    try:
        input_file = find_input_file()
        print(f"  Found: {input_file}")
    except FileNotFoundError as e:
        print(f"  ERROR: {e}")
        sys.exit(1)

    # Step 2: Extract project_id
    print("\n[2/5] Extracting project ID...")
    project_id = extract_project_id(input_file)
    print(f"  Project ID: {project_id}")

    # Step 3: Parse Plan sheet
    print("\n[3/5] Parsing Plan (Allocations) sheet...")
    plan_df, week_mapping = parse_plan_sheet(input_file)

    # Step 4: Parse Rate Card sheet
    print("\n[4/5] Parsing x_Rate Card (Master Rates) sheet...")
    rate_card_df = parse_rate_card_sheet(input_file)

    # Step 5: Merge and calculate
    print("\n[5/5] Merging data and calculating financials...")
    final_df = merge_and_calculate(plan_df, rate_card_df, project_id)

    # Load to BigQuery
    print("\n" + "=" * 60)
    print("BigQuery Load")
    print("=" * 60)
    load_to_bigquery(final_df, dry_run=dry_run)

    # Summary
    print("\n" + "=" * 60)
    print("Pipeline Complete")
    print("=" * 60)
    print(f"  Input file: {input_file}")
    print(f"  Project ID: {project_id}")
    print(f"  Total rows processed: {len(final_df)}")
    print(f"  Target table: {BQ_PROJECT_ID}.{BQ_DATASET}.{BQ_TABLE}")

    # Export to CSV for verification
    output_csv = "output_preview.csv"
    final_df.to_csv(output_csv, index=False)
    print(f"  Preview exported to: {output_csv}")


if __name__ == "__main__":
    main()
