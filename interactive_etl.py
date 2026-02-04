#!/usr/bin/env python3
"""
Interactive Financial ETL Script
=================================
Auto-detects sheets, prompts user for confirmation, and ingests to BigQuery.

Features:
- Auto-detects Plan sheets and Rate Card sheets
- Prompts user to confirm/select which sheets to import
- Allows user to add metadata (year, status, notes)
- Handles multiple rate card columns
- Tracks ingestion history

Usage:
    python interactive_etl.py "pricing_file.xlsx"
"""

import sys
import re
import uuid
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field

import pandas as pd

# Try to import BigQuery (optional for dry-run)
try:
    from google.cloud import bigquery
    from google.oauth2 import service_account
    HAS_BIGQUERY = True
except ImportError:
    HAS_BIGQUERY = False


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class SheetConfig:
    """Configuration for a detected sheet."""
    name: str
    sheet_type: str  # 'plan', 'rate_card', 'costs', 'unknown'
    header_row: int
    columns: List[str]
    row_count: int
    metadata: Dict = field(default_factory=dict)
    selected: bool = False
    user_description: str = ""


@dataclass
class ColumnMapping:
    """Mapping between source and target columns."""
    source: str
    target: str
    transform: Optional[str] = None


# =============================================================================
# Auto-Detection Patterns
# =============================================================================

# Patterns to identify sheet types
PLAN_SHEET_PATTERNS = [
    r'plan',
    r'allocation',
    r'forecast',
    r'estimate',
    r'retainer',
    r'\d{4}',  # Year like 2025, 2026
]

RATE_CARD_PATTERNS = [
    r'rate\s*card',
    r'rates',
    r'pricing',
]

COSTS_PATTERNS = [
    r'cost',
    r'expense',
]

EXCLUDE_PATTERNS = [
    r'example',
    r'template',
    r'info',
    r'q&a',
    r'mapping',
    r'log',
    r'change',
    r'_old',
]

# Column patterns for auto-detection
PLAN_HEADER_PATTERNS = [
    'role', 'market', 'department', 'category', 'name', 'bill rate'
]

RATE_CARD_HEADER_PATTERNS = [
    'market', 'department', 'level', 'title', 'cost rate', 'rate'
]


# =============================================================================
# Sheet Detection
# =============================================================================

def detect_sheet_type(sheet_name: str, df_preview: pd.DataFrame) -> str:
    """Detect the type of sheet based on name and content."""
    name_lower = sheet_name.lower()

    # Check exclusion patterns first
    for pattern in EXCLUDE_PATTERNS:
        if re.search(pattern, name_lower):
            return 'excluded'

    # Check for rate card
    for pattern in RATE_CARD_PATTERNS:
        if re.search(pattern, name_lower):
            return 'rate_card'

    # Check for costs
    for pattern in COSTS_PATTERNS:
        if re.search(pattern, name_lower):
            return 'costs'

    # Check for plan sheets
    for pattern in PLAN_SHEET_PATTERNS:
        if re.search(pattern, name_lower):
            return 'plan'

    # If no pattern matches, check content
    return detect_type_from_content(df_preview)


def detect_type_from_content(df: pd.DataFrame) -> str:
    """Detect sheet type from its content."""
    # Flatten all values to search for patterns
    all_values = ' '.join(str(v).lower() for v in df.values.flatten() if pd.notna(v))

    # Check for plan indicators
    plan_indicators = ['project title', 'client', 'start date', 'billing type']
    plan_score = sum(1 for ind in plan_indicators if ind in all_values)

    # Check for rate card indicators
    rate_indicators = ['market', 'department', 'level', 'title', 'cost rate']
    rate_score = sum(1 for ind in rate_indicators if ind in all_values)

    if rate_score >= 4:
        return 'rate_card'
    elif plan_score >= 3:
        return 'plan'

    return 'unknown'


def find_header_row(df: pd.DataFrame, patterns: List[str]) -> int:
    """Find the row that contains header columns."""
    for idx in range(min(30, len(df))):
        row_values = [str(v).lower() for v in df.iloc[idx] if pd.notna(v)]
        row_text = ' '.join(row_values)

        matches = sum(1 for p in patterns if p in row_text)
        if matches >= 3:
            return idx

    return -1


def analyze_sheets(excel_path: str) -> List[SheetConfig]:
    """Analyze all sheets in an Excel file."""
    print("\n" + "=" * 70)
    print("  ANALYZING EXCEL FILE")
    print("=" * 70)

    xlsx = pd.ExcelFile(excel_path, engine='openpyxl')
    configs = []

    print(f"\nFile: {excel_path}")
    print(f"Total sheets: {len(xlsx.sheet_names)}\n")

    for sheet_name in xlsx.sheet_names:
        print(f"Analyzing: {sheet_name}...", end=" ")

        # Read preview (first 35 rows)
        df_preview = pd.read_excel(xlsx, sheet_name=sheet_name, nrows=35, header=None)

        # Detect sheet type
        sheet_type = detect_sheet_type(sheet_name, df_preview)

        # Find header row based on type
        if sheet_type == 'plan':
            header_row = find_header_row(df_preview, PLAN_HEADER_PATTERNS)
        elif sheet_type == 'rate_card':
            header_row = find_header_row(df_preview, RATE_CARD_HEADER_PATTERNS)
        else:
            header_row = -1

        # Get columns if header found
        columns = []
        if header_row >= 0:
            df_with_header = pd.read_excel(
                xlsx, sheet_name=sheet_name,
                skiprows=header_row, nrows=1, header=None
            )
            columns = [str(c) for c in df_with_header.iloc[0] if pd.notna(c)][:20]

        # Get row count
        df_full = pd.read_excel(xlsx, sheet_name=sheet_name, header=None)
        row_count = len(df_full)

        config = SheetConfig(
            name=sheet_name,
            sheet_type=sheet_type,
            header_row=header_row,
            columns=columns,
            row_count=row_count,
            selected=(sheet_type in ['plan', 'rate_card'])
        )

        configs.append(config)
        print(f"[{sheet_type.upper()}]")

    return configs


# =============================================================================
# User Interaction
# =============================================================================

def print_sheet_summary(configs: List[SheetConfig]) -> None:
    """Print summary of detected sheets."""
    print("\n" + "=" * 70)
    print("  DETECTED SHEETS")
    print("=" * 70)

    # Group by type
    by_type = {}
    for c in configs:
        by_type.setdefault(c.sheet_type, []).append(c)

    for sheet_type in ['rate_card', 'plan', 'costs', 'unknown', 'excluded']:
        sheets = by_type.get(sheet_type, [])
        if not sheets:
            continue

        print(f"\n{sheet_type.upper().replace('_', ' ')} SHEETS ({len(sheets)}):")
        for i, c in enumerate(sheets):
            marker = "[*]" if c.selected else "[ ]"
            print(f"  {marker} {c.name}")
            if c.header_row >= 0:
                print(f"      Header row: {c.header_row + 1}, Columns: {len(c.columns)}, Rows: ~{c.row_count}")


def get_user_selection(configs: List[SheetConfig]) -> List[SheetConfig]:
    """Interactive prompt for user to select sheets."""
    print("\n" + "=" * 70)
    print("  SELECT SHEETS TO IMPORT")
    print("=" * 70)

    # List selectable sheets
    selectable = [c for c in configs if c.sheet_type != 'excluded']

    print("\nEnter sheet numbers to toggle selection (comma-separated).")
    print("Enter 'a' to select all Plan sheets, 'r' for Rate Card.")
    print("Enter 'd' when done.\n")

    for i, c in enumerate(selectable):
        marker = "[X]" if c.selected else "[ ]"
        print(f"  {i + 1}. {marker} [{c.sheet_type.upper():10}] {c.name}")

    while True:
        choice = input("\nYour choice: ").strip().lower()

        if choice == 'd':
            break
        elif choice == 'a':
            for c in selectable:
                if c.sheet_type == 'plan':
                    c.selected = True
        elif choice == 'r':
            for c in selectable:
                if c.sheet_type == 'rate_card':
                    c.selected = True
        else:
            try:
                indices = [int(x.strip()) - 1 for x in choice.split(',')]
                for idx in indices:
                    if 0 <= idx < len(selectable):
                        selectable[idx].selected = not selectable[idx].selected
            except ValueError:
                print("Invalid input. Enter numbers separated by commas.")
                continue

        # Reprint list
        print("\nCurrent selection:")
        for i, c in enumerate(selectable):
            marker = "[X]" if c.selected else "[ ]"
            print(f"  {i + 1}. {marker} [{c.sheet_type.upper():10}] {c.name}")

    return [c for c in configs if c.selected]


def get_sheet_metadata(config: SheetConfig) -> SheetConfig:
    """Prompt user for metadata about a sheet."""
    print(f"\n--- Metadata for: {config.name} ---")

    if config.sheet_type == 'plan':
        print("Add a description for this plan sheet.")
        print("Examples: '2025 plan - approved', '2026 estimate - WIP', 'AI team extension'")
        config.user_description = input("Description (or press Enter to skip): ").strip()

        # Try to extract year from sheet name
        year_match = re.search(r'20\d{2}', config.name)
        if year_match:
            config.metadata['year'] = year_match.group()
            print(f"  Auto-detected year: {config.metadata['year']}")

    elif config.sheet_type == 'rate_card':
        print("Which rate card column should be used for billing rates?")
        print(f"Available columns: {config.columns[:15]}")
        rate_col = input("Rate column name (or press Enter for default): ").strip()
        if rate_col:
            config.metadata['rate_column'] = rate_col

    return config


# =============================================================================
# Processing Functions
# =============================================================================

def generate_project_id(client: str, project: str, sheet: str) -> str:
    """Generate a unique project ID."""
    source = f"{client}|{project}|{sheet}"
    return hashlib.md5(source.encode()).hexdigest()[:12]


def process_rate_card(
    excel_path: str,
    config: SheetConfig,
    rate_column: str = None
) -> pd.DataFrame:
    """Process a rate card sheet."""
    print(f"\n[INFO] Processing Rate Card: {config.name}")

    df = pd.read_excel(
        excel_path,
        sheet_name=config.name,
        skiprows=config.header_row,
        engine='openpyxl'
    )

    # Clean column names
    df.columns = [str(c).strip().lower() for c in df.columns]

    # Standard column mapping
    column_map = {
        'market': 'market_region',
        'global department': 'department',
        'title': 'role',
    }

    df = df.rename(columns=column_map)

    # Determine which rate column to use
    if rate_column:
        rate_col = rate_column.lower()
    else:
        # Try to find a rate column
        for col in df.columns:
            if 'rate' in col.lower() and 'cost' not in col.lower():
                rate_col = col
                break
        else:
            rate_col = None

    # Prepare output dataframe
    required_cols = ['market_region', 'department', 'level', 'role']
    available_cols = [c for c in required_cols if c in df.columns]

    result = df[available_cols].copy()

    if 'cost rate' in df.columns:
        result['cost_rate'] = pd.to_numeric(df['cost rate'], errors='coerce')

    if rate_col and rate_col in df.columns:
        result['bill_rate'] = pd.to_numeric(df[rate_col], errors='coerce')
        result['rate_card_name'] = rate_col

    result['rate_card_id'] = [str(uuid.uuid4())[:8] for _ in range(len(result))]
    result['source_file'] = Path(excel_path).name
    result['ingested_at'] = datetime.utcnow()

    # Drop empty rows
    result = result.dropna(subset=['role'], how='all')

    print(f"[OK] Processed {len(result)} rate card entries")
    return result


def process_plan_sheet(
    excel_path: str,
    config: SheetConfig,
    rate_card_df: pd.DataFrame = None
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Process a plan sheet and return (project_df, allocations_df).
    """
    print(f"\n[INFO] Processing Plan: {config.name}")

    # Extract metadata from first rows
    df_meta = pd.read_excel(
        excel_path,
        sheet_name=config.name,
        nrows=10,
        header=None,
        engine='openpyxl'
    )

    # Find client name and project title
    client_name = "Unknown Client"
    project_title = "Unknown Project"
    project_number = None
    start_date = None
    company_code = None
    market_region = None
    billing_type = None

    for idx, row in df_meta.iterrows():
        row_values = [str(v).lower() if pd.notna(v) else '' for v in row]
        row_str = ' '.join(row_values)

        if 'client' in row_str or 'company' in row_str:
            for i, v in enumerate(row):
                if pd.notna(v) and i > 0 and str(v).strip():
                    val = str(row.iloc[i]).strip()
                    if val and val.lower() not in ['client (info)', 'company (required)']:
                        client_name = val
                        break

        if 'project title' in row_str:
            for i, v in enumerate(row):
                if pd.notna(v) and i > 0:
                    val = str(row.iloc[i]).strip()
                    if val and 'project title' not in val.lower():
                        project_title = val
                        break

        if 'project number' in row_str:
            for i, v in enumerate(row):
                if pd.notna(v) and i > 0:
                    val = str(row.iloc[i]).strip()
                    if val and 'project number' not in val.lower():
                        project_number = val
                        break

        if 'start date' in row_str:
            for i, v in enumerate(row):
                if isinstance(v, datetime):
                    start_date = v.date()
                    break

        if 'market' in row_str:
            for i, v in enumerate(row):
                if pd.notna(v) and i > 0:
                    val = str(row.iloc[i]).strip()
                    if val and 'market' not in val.lower() and val != 'Please choose company above':
                        market_region = val
                        break

        if 'billing type' in row_str:
            for i, v in enumerate(row):
                if pd.notna(v) and i > 0:
                    val = str(row.iloc[i]).strip()
                    if val and 'billing' not in val.lower():
                        billing_type = val
                        break

    print(f"  Client: {client_name}")
    print(f"  Project: {project_title}")

    # Generate project ID
    project_id = generate_project_id(client_name, project_title, config.name)

    # Read data section
    df = pd.read_excel(
        excel_path,
        sheet_name=config.name,
        skiprows=config.header_row,
        engine='openpyxl'
    )

    # Clean column names
    original_cols = df.columns.tolist()
    df.columns = [
        str(c).strip().lower() if isinstance(c, str) else str(c)
        for c in df.columns
    ]

    # Identify period columns (week numbers)
    period_cols = []
    dimension_cols = []

    for col in df.columns:
        try:
            if isinstance(col, (int, float)) and pd.notna(col):
                period_cols.append(col)
            elif str(col).isdigit():
                period_cols.append(col)
            else:
                dimension_cols.append(col)
        except:
            dimension_cols.append(col)

    print(f"  Found {len(period_cols)} period columns, {len(dimension_cols)} dimension columns")

    if not period_cols:
        print(f"  [WARN] No period columns found, skipping melt")
        return None, None

    # Melt the data
    df_melted = pd.melt(
        df,
        id_vars=dimension_cols,
        value_vars=period_cols,
        var_name='week_number',
        value_name='hours'
    )

    # Clean hours
    df_melted['hours'] = pd.to_numeric(df_melted['hours'], errors='coerce')
    df_melted = df_melted[df_melted['hours'].notna() & (df_melted['hours'] != 0)]

    if len(df_melted) == 0:
        print(f"  [WARN] No data rows after filtering zero hours")
        return None, None

    # Build allocations dataframe
    allocations = pd.DataFrame({
        'allocation_id': [str(uuid.uuid4())[:8] for _ in range(len(df_melted))],
        'project_id': project_id,
        'week_number': df_melted['week_number'].astype(int),
        'hours': df_melted['hours'],
        'source_file': Path(excel_path).name,
        'source_sheet': config.name,
        'ingested_at': datetime.utcnow()
    })

    # Map standard columns
    col_mappings = {
        'role': 'role',
        'market_region': 'market_region',
        'market': 'market_region',
        'department': 'department',
        'category': 'category',
        'category\n(optional)': 'category',
        'name': 'resource_name',
    }

    for src, tgt in col_mappings.items():
        if src in df_melted.columns:
            allocations[tgt] = df_melted[src].values

    print(f"  [OK] Generated {len(allocations)} allocation records")

    # Build project record
    project_record = pd.DataFrame([{
        'project_id': project_id,
        'client_name': client_name,
        'project_title': project_title,
        'project_number': project_number,
        'market_region': market_region,
        'billing_type': billing_type,
        'start_date': start_date,
        'total_estimated_hours': allocations['hours'].sum(),
        'status': 'Active',
        'source_file': Path(excel_path).name,
        'source_sheet': config.name,
        'sheet_metadata': config.user_description,
        'ingested_at': datetime.utcnow()
    }])

    return project_record, allocations


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    """Main interactive ETL workflow."""
    print("=" * 70)
    print("  DEPT DELIVERY FINANCE - INTERACTIVE ETL")
    print("=" * 70)

    if len(sys.argv) < 2:
        print("\nUsage: python interactive_etl.py <excel_file> [--dry-run]")
        sys.exit(1)

    excel_path = sys.argv[1]
    dry_run = '--dry-run' in sys.argv

    if not Path(excel_path).exists():
        print(f"[ERROR] File not found: {excel_path}")
        sys.exit(1)

    # Step 1: Analyze sheets
    configs = analyze_sheets(excel_path)

    # Step 2: Show summary
    print_sheet_summary(configs)

    # Step 3: Get user selection
    selected = get_user_selection(configs)

    if not selected:
        print("\n[INFO] No sheets selected. Exiting.")
        sys.exit(0)

    # Step 4: Get metadata for each selected sheet
    print("\n" + "=" * 70)
    print("  ADD METADATA")
    print("=" * 70)

    for config in selected:
        get_sheet_metadata(config)

    # Step 5: Process sheets
    print("\n" + "=" * 70)
    print("  PROCESSING SHEETS")
    print("=" * 70)

    all_rate_cards = []
    all_projects = []
    all_allocations = []

    # Process rate cards first
    rate_card_df = None
    for config in selected:
        if config.sheet_type == 'rate_card':
            rate_col = config.metadata.get('rate_column')
            df = process_rate_card(excel_path, config, rate_col)
            all_rate_cards.append(df)
            rate_card_df = df

    # Process plan sheets
    for config in selected:
        if config.sheet_type == 'plan':
            project_df, alloc_df = process_plan_sheet(excel_path, config, rate_card_df)
            if project_df is not None:
                all_projects.append(project_df)
            if alloc_df is not None:
                all_allocations.append(alloc_df)

    # Step 6: Summary
    print("\n" + "=" * 70)
    print("  PROCESSING SUMMARY")
    print("=" * 70)

    if all_rate_cards:
        combined_rates = pd.concat(all_rate_cards, ignore_index=True)
        print(f"\nRate Cards: {len(combined_rates)} entries")

    if all_projects:
        combined_projects = pd.concat(all_projects, ignore_index=True)
        print(f"Projects: {len(combined_projects)} records")

    if all_allocations:
        combined_allocations = pd.concat(all_allocations, ignore_index=True)
        print(f"Allocations: {len(combined_allocations)} records")
        print(f"Total Hours: {combined_allocations['hours'].sum():,.1f}")

    if dry_run:
        print("\n[DRY RUN] No data uploaded to BigQuery.")
        print("Run without --dry-run to upload data.")

        # Save to CSV for review
        if all_allocations:
            output_file = "etl_output_preview.csv"
            combined_allocations.to_csv(output_file, index=False)
            print(f"\n[INFO] Preview saved to: {output_file}")
    else:
        print("\n[INFO] BigQuery upload not implemented in this version.")
        print("Use the original financial_etl.py for BigQuery upload.")

    print("\n" + "=" * 70)
    print("  DONE")
    print("=" * 70)


if __name__ == "__main__":
    main()
