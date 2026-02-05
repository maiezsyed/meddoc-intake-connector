#!/usr/bin/env python3
"""
Universal Financial ETL Script
==============================
Processes financial Excel workbooks with varying structures across different
projects and PMs. Auto-detects sheet types, header rows, and column mappings.

Supports:
- Plan/Allocation sheets (multiple per file)
- Rate Card sheets (standard, custom, external)
- Actuals sheets
- Costs sheets
- Investment Log sheets
- External Estimate sheets

Usage:
  python3 universal_etl.py <excel_file> <project_id> <dataset_id> [options]

Options:
  --dry-run         Process without uploading to BigQuery
  --output-csv      Output processed data to CSV files
  --client-name     Client name for project identification
  --project-title   Project title for identification
  --year            Fiscal year for the data
  --verbose         Show detailed processing info
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd

# =============================================================================
# CONFIGURATION - Sheet Detection Patterns
# =============================================================================

SHEET_TYPE_PATTERNS = {
    'plan': [
        r'plan',
        r'allocation',
        r'forecast',
        r'staffing',
        r'20\d{2}.*plan',  # Year-prefixed plans like "2025 Plan"
    ],
    'rate_card': [
        r'^rate\s*card$',
        r'rate\s*card\s*\(',
        r'_rate\s*card',
        r'custom.*rate',
        r'ext.*rate',
    ],
    'actuals': [
        r'actual',
        r'timesheet',
        r'hours.*log',
    ],
    'costs': [
        r'^costs?$',
        r'expense',
        r'vendor.*cost',
    ],
    'investment_log': [
        r'invest.*log',
        r'overrun',
    ],
    'external_estimate': [
        r'ext.*estimate',
        r'client.*estimate',
        r'external.*summary',
    ],
    'info': [
        r'^info$',
        r'change.*log',
        r'version',
    ],
    'mapping': [
        r'^_mapping',
        r'^_custom',
        r'helper',
    ],
}

# Sheets to skip (internal/helper sheets)
SKIP_SHEET_PATTERNS = [
    r'^_',  # Sheets starting with underscore
    r'helper',
    r'mapping',
    r'^info$',
]

# =============================================================================
# COLUMN NAME MAPPINGS - Normalize varying column names
# =============================================================================

COLUMN_MAPPINGS = {
    # Market variations
    'market': 'market',
    'market_region': 'market',
    'dept market': 'market',

    # Department variations
    'department': 'department',
    'global department': 'department',
    'dept department': 'department',
    'craft': 'department',  # Sometimes craft = department

    # Role variations
    'role': 'role',
    'job role': 'role',
    'title': 'title',
    'dept title': 'title',
    'level name': 'level',
    'level': 'level',

    # Rate variations
    'cost rate': 'cost_rate',
    'bill rate': 'bill_rate',
    'bill rate, usd': 'bill_rate',
    'final bill rate': 'final_bill_rate',
    'effective bill rate': 'effective_bill_rate',
    'rate card bill rate': 'rate_card_bill_rate',
    'final cost rate': 'final_cost_rate',
    'primary rate': 'primary_rate',
    'standard bill rate': 'standard_bill_rate',

    # Override columns
    'bill rate override': 'bill_rate_override',
    'cost rate override': 'cost_rate_override',
    'total fees override': 'total_fees_override',
    'total cost override': 'total_cost_override',
    'total hours override': 'total_hours_override',

    # Calculated columns
    'total fees': 'total_fees',
    'effective fees': 'effective_fees',
    'total cost': 'total_cost',
    'total hours': 'total_hours',
    'gross margin': 'gross_margin',
    'margin %': 'margin_pct',
    'discount %': 'discount_pct',

    # Employee/resource columns
    'employee name': 'employee_name',
    'employee': 'employee_name',
    'name': 'employee_name',
    'employee currrent title': 'employee_title',  # Note: typo in source
    'business team': 'business_team',
    'ic type': 'ic_type',

    # Other common columns
    'category': 'category',
    'notes': 'notes',
    'notes/description': 'notes',
    'standard role name, \nif non-default': 'standard_role_override',
    'alt. custom rate card': 'alt_rate_card',
    'deptapps budget': 'deptapps_budget',

    # Costs sheet columns
    'item': 'item',
    'vendor': 'vendor',
    'estimate/actual': 'estimate_actual',
    'type': 'cost_type',

    # External estimate columns
    '% dedication': 'dedication_pct',
    'est. # of total hours': 'est_total_hours',
    'est. # of weekly hours': 'est_weekly_hours',
    'total fee': 'total_fee',
    'weekly fee': 'weekly_fee',
}

# =============================================================================
# HEADER ROW DETECTION
# =============================================================================

# Keywords that indicate a header row
HEADER_KEYWORDS = {
    'plan': ['category', 'market', 'department', 'role', 'total hours', 'total fees'],
    'rate_card': ['market', 'craft', 'role', 'title', 'cost rate', 'bill rate', 'level'],
    'actuals': ['market', 'employee', 'role', 'total hours'],
    'costs': ['item', 'category', 'date', 'vendor', 'total cost'],
    'investment_log': ['date identified', 'investment summary', 'investment amount'],
    'external_estimate': ['department', 'role', 'total hours', 'total fee'],
}


def detect_sheet_type(sheet_name: str) -> Optional[str]:
    """Detect the type of sheet based on its name."""
    name_lower = sheet_name.lower().strip()

    # Check skip patterns first
    for pattern in SKIP_SHEET_PATTERNS:
        if re.search(pattern, name_lower):
            return 'skip'

    # Check each sheet type
    for sheet_type, patterns in SHEET_TYPE_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, name_lower):
                return sheet_type

    # Default: if it has numbers that look like years + common words, probably a plan
    if re.search(r'20\d{2}', name_lower):
        return 'plan'

    return 'unknown'


def find_header_row(df: pd.DataFrame, sheet_type: str, max_rows: int = 60) -> int:
    """
    Find the header row by scoring each row based on keyword matches.
    Returns -1 if no suitable header found.
    """
    keywords = HEADER_KEYWORDS.get(sheet_type, HEADER_KEYWORDS['plan'])
    best_row = -1
    best_score = 0

    for idx in range(min(max_rows, len(df))):
        row = df.iloc[idx]
        non_null = row.dropna()

        if len(non_null) < 3:
            continue

        score = 0
        string_count = 0

        for val in non_null:
            if isinstance(val, str):
                val_lower = val.lower().strip()
                string_count += 1

                # Check for keyword matches
                for kw in keywords:
                    if kw in val_lower:
                        score += 5

                # Check for common header patterns
                if re.match(r'^(0[1-9]|[1-9][0-9])$', val_lower):  # Week numbers
                    score += 1
                if re.match(r'^(0[1-9]|[1-9][0-9])-hours$', val_lower):
                    score += 1

        # Bonus for having multiple string columns (headers are usually strings)
        if string_count >= 4:
            score += string_count

        if score > best_score:
            best_score = score
            best_row = idx

    return best_row


def normalize_column_name(col_name: str) -> str:
    """Normalize a column name to a canonical form."""
    if pd.isna(col_name):
        return ''

    col_lower = str(col_name).lower().strip()
    col_lower = re.sub(r'\s+', ' ', col_lower)  # Normalize whitespace

    # Check direct mapping
    if col_lower in COLUMN_MAPPINGS:
        return COLUMN_MAPPINGS[col_lower]

    # Check partial matches for common patterns
    for pattern, canonical in COLUMN_MAPPINGS.items():
        if pattern in col_lower:
            return canonical

    # Return cleaned version if no mapping found
    return re.sub(r'[^\w]', '_', col_lower).strip('_')


def identify_week_columns(columns: list) -> list:
    """
    Identify columns that represent week/period numbers.
    Returns list of (column_name, week_number) tuples.
    """
    week_cols = []

    for col in columns:
        col_str = str(col).strip()

        # Match patterns: "01", "1", "01-Hours", integers
        if re.match(r'^(0?[1-9]|[1-8][0-9]|90)$', col_str):
            week_num = int(col_str)
            week_cols.append((col, week_num, 'fee'))
        elif re.match(r'^(0?[1-9]|[1-8][0-9]|90)-[Hh]ours$', col_str):
            week_num = int(re.match(r'^(\d+)', col_str).group(1))
            week_cols.append((col, week_num, 'hours'))
        elif isinstance(col, (int, float)) and 1 <= col <= 90:
            week_cols.append((col, int(col), 'fee'))

    return week_cols


def generate_project_id(client_name: str, project_title: str, source_file: str, source_sheet: str) -> str:
    """Generate a deterministic project ID from identifying information."""
    key = f"{client_name}|{project_title}|{source_file}|{source_sheet}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# =============================================================================
# SHEET PROCESSORS
# =============================================================================

def process_plan_sheet(
    df: pd.DataFrame,
    sheet_name: str,
    header_row: int,
    metadata: dict,
    verbose: bool = False
) -> dict:
    """
    Process a Plan/Allocation sheet.
    Returns dict with 'allocations' DataFrame and 'metadata' dict.
    """
    # Set header and get data
    df_data = df.iloc[header_row + 1:].copy()
    df_data.columns = df.iloc[header_row].values

    # Normalize column names
    col_map = {col: normalize_column_name(col) for col in df_data.columns}
    df_data = df_data.rename(columns=col_map)

    # Identify dimension vs week columns
    week_cols = identify_week_columns(df.iloc[header_row].values)

    # Core dimension columns we want to keep
    dimension_cols = [
        'category', 'market', 'department', 'role', 'employee_name',
        'notes', 'business_team', 'ic_type',
        'final_bill_rate', 'effective_bill_rate', 'cost_rate', 'final_cost_rate',
        'total_fees', 'total_cost', 'total_hours', 'margin_pct', 'discount_pct',
    ]

    # Keep only columns that exist
    existing_dims = [c for c in dimension_cols if c in df_data.columns]

    # Filter out empty rows (no market/department/role)
    key_cols = ['market', 'department', 'role']
    existing_keys = [c for c in key_cols if c in df_data.columns]
    if existing_keys:
        df_data = df_data.dropna(subset=existing_keys, how='all')

    if verbose:
        print(f"    Found {len(df_data)} data rows")
        print(f"    Dimension columns: {existing_dims}")
        print(f"    Week columns: {len(week_cols)}")

    # Extract metadata from rows above header
    sheet_metadata = extract_sheet_metadata(df, header_row)

    # Melt week columns into rows for allocations table
    fee_week_cols = [(col, wk) for col, wk, typ in week_cols if typ == 'fee']

    if fee_week_cols:
        # Get the actual column names after normalization
        fee_col_names = []
        for orig_col, wk, _ in week_cols:
            if _ == 'fee':
                norm_name = normalize_column_name(str(orig_col))
                if norm_name in df_data.columns:
                    fee_col_names.append(norm_name)
                elif str(orig_col) in df_data.columns:
                    fee_col_names.append(str(orig_col))

        # Find original week column names in df_data
        week_col_in_data = []
        for col in df_data.columns:
            col_str = str(col)
            if re.match(r'^(0?[1-9]|[1-8][0-9]|90)$', col_str):
                week_col_in_data.append(col)
            elif isinstance(col, (int, float)) and 1 <= col <= 90:
                week_col_in_data.append(col)

        if week_col_in_data:
            # Melt the dataframe
            id_vars = [c for c in existing_dims if c in df_data.columns]

            df_melted = pd.melt(
                df_data,
                id_vars=id_vars,
                value_vars=week_col_in_data,
                var_name='week_number',
                value_name='hours'
            )

            # Convert week_number to int
            df_melted['week_number'] = df_melted['week_number'].apply(
                lambda x: int(re.match(r'^(\d+)', str(x)).group(1)) if re.match(r'^(\d+)', str(x)) else x
            )

            # Filter out zero/null hours
            df_melted = df_melted[df_melted['hours'].notna() & (df_melted['hours'] != 0)]

            # Add metadata columns
            df_melted['source_sheet'] = sheet_name
            df_melted['project_id'] = metadata.get('project_id', '')

            return {
                'allocations': df_melted,
                'metadata': sheet_metadata,
                'row_count': len(df_melted),
            }

    # If no week columns, return dimension data only
    df_data['source_sheet'] = sheet_name
    df_data['project_id'] = metadata.get('project_id', '')

    return {
        'allocations': df_data[existing_dims + ['source_sheet', 'project_id']] if existing_dims else df_data,
        'metadata': sheet_metadata,
        'row_count': len(df_data),
    }


def process_rate_card_sheet(
    df: pd.DataFrame,
    sheet_name: str,
    header_row: int,
    metadata: dict,
    verbose: bool = False
) -> dict:
    """
    Process a Rate Card sheet.
    Returns dict with 'rate_card' DataFrame.
    """
    # Set header and get data
    df_data = df.iloc[header_row + 1:].copy()
    df_data.columns = df.iloc[header_row].values

    # Normalize column names
    col_map = {col: normalize_column_name(col) for col in df_data.columns}
    df_data = df_data.rename(columns=col_map)

    # Remove empty rows
    if 'market' in df_data.columns:
        df_data = df_data.dropna(subset=['market'])
    elif 'title' in df_data.columns:
        df_data = df_data.dropna(subset=['title'])

    # Determine rate card type
    rate_card_type = 'standard'
    if 'custom' in sheet_name.lower():
        rate_card_type = 'custom'
    elif 'ext' in sheet_name.lower():
        rate_card_type = 'external'

    df_data['rate_card_type'] = rate_card_type
    df_data['source_sheet'] = sheet_name

    if verbose:
        print(f"    Rate card type: {rate_card_type}")
        print(f"    Found {len(df_data)} rate entries")

    return {
        'rate_card': df_data,
        'rate_card_type': rate_card_type,
        'row_count': len(df_data),
    }


def process_actuals_sheet(
    df: pd.DataFrame,
    sheet_name: str,
    header_row: int,
    metadata: dict,
    verbose: bool = False
) -> dict:
    """Process an Actuals/Timesheet sheet."""
    df_data = df.iloc[header_row + 1:].copy()
    df_data.columns = df.iloc[header_row].values

    col_map = {col: normalize_column_name(col) for col in df_data.columns}
    df_data = df_data.rename(columns=col_map)

    # Remove empty rows
    if 'employee_name' in df_data.columns:
        df_data = df_data.dropna(subset=['employee_name'])
    elif 'market' in df_data.columns:
        df_data = df_data.dropna(subset=['market'])

    df_data['source_sheet'] = sheet_name
    df_data['project_id'] = metadata.get('project_id', '')

    if verbose:
        print(f"    Found {len(df_data)} actuals rows")

    return {
        'actuals': df_data,
        'row_count': len(df_data),
    }


def process_costs_sheet(
    df: pd.DataFrame,
    sheet_name: str,
    header_row: int,
    metadata: dict,
    verbose: bool = False
) -> dict:
    """Process a Costs/Expenses sheet."""
    df_data = df.iloc[header_row + 1:].copy()
    df_data.columns = df.iloc[header_row].values

    col_map = {col: normalize_column_name(col) for col in df_data.columns}
    df_data = df_data.rename(columns=col_map)

    # Remove empty rows
    if 'item' in df_data.columns:
        df_data = df_data.dropna(subset=['item'])

    df_data['source_sheet'] = sheet_name
    df_data['project_id'] = metadata.get('project_id', '')

    if verbose:
        print(f"    Found {len(df_data)} cost entries")

    return {
        'costs': df_data,
        'row_count': len(df_data),
    }


def extract_sheet_metadata(df: pd.DataFrame, header_row: int) -> dict:
    """Extract metadata from rows above the data header."""
    metadata = {}

    # Common metadata labels to look for
    metadata_labels = {
        'client': ['client', 'client (info)'],
        'project_title': ['project title', 'project title (info)', 'project'],
        'project_number': ['project number', 'project number (info)'],
        'start_date': ['start date', 'start date (required)'],
        'billing_type': ['billing type', 'billing type (info)'],
        'market': ['market (required)', 'market'],
        'rate_card': ['rate card', 'rate card (required)'],
        'total_project_fee': ['total project fee'],
        'gross_margin': ['estimated gross margin'],
    }

    for idx in range(min(header_row, 40)):
        row = df.iloc[idx]
        for col_idx, val in enumerate(row):
            if pd.isna(val) or not isinstance(val, str):
                continue

            val_lower = val.lower().strip()

            for meta_key, labels in metadata_labels.items():
                if val_lower in labels:
                    # Try to get value from next column
                    if col_idx + 1 < len(row):
                        next_val = row.iloc[col_idx + 1]
                        if pd.notna(next_val):
                            metadata[meta_key] = next_val

    return metadata


# =============================================================================
# MAIN ETL ORCHESTRATOR
# =============================================================================

def process_workbook(
    file_path: str,
    client_name: str = '',
    project_title: str = '',
    year: int = None,
    verbose: bool = False
) -> dict:
    """
    Process an entire Excel workbook.
    Returns dict with processed data for each table type.
    """
    results = {
        'allocations': [],
        'rate_cards': [],
        'actuals': [],
        'costs': [],
        'projects': [],
        'processing_log': [],
    }

    file_name = Path(file_path).name
    xlsx = pd.ExcelFile(file_path, engine='openpyxl')

    print(f"\nProcessing: {file_name}")
    print(f"Total sheets: {len(xlsx.sheet_names)}")
    print("=" * 60)

    # Generate base project ID
    base_metadata = {
        'client_name': client_name,
        'project_title': project_title,
        'source_file': file_name,
        'year': year or datetime.now().year,
    }

    for sheet_name in xlsx.sheet_names:
        sheet_type = detect_sheet_type(sheet_name)

        if sheet_type == 'skip' or sheet_type == 'unknown':
            if verbose:
                print(f"\n[SKIP] {sheet_name} (type: {sheet_type})")
            continue

        print(f"\n[{sheet_type.upper()}] {sheet_name}")

        # Read sheet without header
        df = pd.read_excel(xlsx, sheet_name=sheet_name, header=None)

        if len(df) == 0:
            print("    Empty sheet, skipping")
            continue

        # Find header row
        header_row = find_header_row(df, sheet_type)

        if header_row < 0:
            print(f"    Could not find header row, skipping")
            results['processing_log'].append({
                'sheet': sheet_name,
                'type': sheet_type,
                'status': 'error',
                'message': 'Could not find header row',
            })
            continue

        print(f"    Header at row {header_row + 1}")

        # Generate project ID for this sheet
        metadata = base_metadata.copy()
        metadata['project_id'] = generate_project_id(
            client_name, project_title, file_name, sheet_name
        )
        metadata['source_sheet'] = sheet_name

        # Process based on sheet type
        try:
            if sheet_type == 'plan':
                result = process_plan_sheet(df, sheet_name, header_row, metadata, verbose)
                if 'allocations' in result and len(result['allocations']) > 0:
                    results['allocations'].append(result['allocations'])
                    print(f"    Processed {result['row_count']} allocation records")

                # Create project record
                project_record = {
                    'project_id': metadata['project_id'],
                    'client_name': client_name,
                    'project_title': project_title,
                    'source_file': file_name,
                    'source_sheet': sheet_name,
                    'year': year,
                    'sheet_metadata': json.dumps(result.get('metadata', {})),
                    'processed_at': datetime.now().isoformat(),
                }
                results['projects'].append(project_record)

            elif sheet_type == 'rate_card':
                result = process_rate_card_sheet(df, sheet_name, header_row, metadata, verbose)
                if 'rate_card' in result and len(result['rate_card']) > 0:
                    results['rate_cards'].append(result['rate_card'])
                    print(f"    Processed {result['row_count']} rate card entries")

            elif sheet_type == 'actuals':
                result = process_actuals_sheet(df, sheet_name, header_row, metadata, verbose)
                if 'actuals' in result and len(result['actuals']) > 0:
                    results['actuals'].append(result['actuals'])
                    print(f"    Processed {result['row_count']} actuals records")

            elif sheet_type == 'costs':
                result = process_costs_sheet(df, sheet_name, header_row, metadata, verbose)
                if 'costs' in result and len(result['costs']) > 0:
                    results['costs'].append(result['costs'])
                    print(f"    Processed {result['row_count']} cost entries")

            results['processing_log'].append({
                'sheet': sheet_name,
                'type': sheet_type,
                'status': 'success',
                'row_count': result.get('row_count', 0),
            })

        except Exception as e:
            print(f"    ERROR: {str(e)}")
            results['processing_log'].append({
                'sheet': sheet_name,
                'type': sheet_type,
                'status': 'error',
                'message': str(e),
            })

    # Combine dataframes
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    if results['allocations']:
        results['allocations_combined'] = pd.concat(results['allocations'], ignore_index=True)
        print(f"Total allocations: {len(results['allocations_combined'])} rows")

    if results['rate_cards']:
        results['rate_cards_combined'] = pd.concat(results['rate_cards'], ignore_index=True)
        print(f"Total rate card entries: {len(results['rate_cards_combined'])} rows")

    if results['actuals']:
        results['actuals_combined'] = pd.concat(results['actuals'], ignore_index=True)
        print(f"Total actuals: {len(results['actuals_combined'])} rows")

    if results['costs']:
        results['costs_combined'] = pd.concat(results['costs'], ignore_index=True)
        print(f"Total costs: {len(results['costs_combined'])} rows")

    return results


def upload_to_bigquery(results: dict, gcp_project: str, dataset_id: str, verbose: bool = False):
    """Upload processed data to BigQuery."""
    from google.cloud import bigquery

    client = bigquery.Client(project=gcp_project)

    tables = [
        ('allocations_combined', 'allocations'),
        ('rate_cards_combined', 'rate_cards'),
        ('actuals_combined', 'actuals'),
        ('costs_combined', 'costs'),
    ]

    for result_key, table_name in tables:
        if result_key in results and results[result_key] is not None:
            df = results[result_key]
            table_id = f"{gcp_project}.{dataset_id}.{table_name}"

            print(f"\nUploading to {table_id}...")

            # Configure job
            job_config = bigquery.LoadJobConfig(
                write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
                schema_update_options=[
                    bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION,
                ],
            )

            try:
                job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
                job.result()
                print(f"  Uploaded {len(df)} rows to {table_name}")
            except Exception as e:
                print(f"  ERROR uploading to {table_name}: {str(e)}")

    # Upload projects
    if results.get('projects'):
        df_projects = pd.DataFrame(results['projects'])
        table_id = f"{gcp_project}.{dataset_id}.projects"

        print(f"\nUploading to {table_id}...")

        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )

        try:
            job = client.load_table_from_dataframe(df_projects, table_id, job_config=job_config)
            job.result()
            print(f"  Uploaded {len(df_projects)} project records")
        except Exception as e:
            print(f"  ERROR uploading projects: {str(e)}")


def output_to_csv(results: dict, output_dir: str = '.'):
    """Output processed data to CSV files."""
    output_path = Path(output_dir)

    tables = [
        ('allocations_combined', 'allocations.csv'),
        ('rate_cards_combined', 'rate_cards.csv'),
        ('actuals_combined', 'actuals.csv'),
        ('costs_combined', 'costs.csv'),
    ]

    for result_key, filename in tables:
        if result_key in results and results[result_key] is not None:
            df = results[result_key]
            filepath = output_path / filename
            df.to_csv(filepath, index=False)
            print(f"Wrote {len(df)} rows to {filepath}")

    # Projects
    if results.get('projects'):
        df_projects = pd.DataFrame(results['projects'])
        filepath = output_path / 'projects.csv'
        df_projects.to_csv(filepath, index=False)
        print(f"Wrote {len(df_projects)} rows to {filepath}")


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Universal Financial ETL - Process Excel workbooks to BigQuery'
    )
    parser.add_argument('excel_file', help='Path to Excel file')
    parser.add_argument('gcp_project', nargs='?', help='GCP project ID')
    parser.add_argument('dataset_id', nargs='?', help='BigQuery dataset ID')
    parser.add_argument('--dry-run', action='store_true', help='Process without uploading')
    parser.add_argument('--output-csv', action='store_true', help='Output to CSV files')
    parser.add_argument('--client-name', default='', help='Client name')
    parser.add_argument('--project-title', default='', help='Project title')
    parser.add_argument('--year', type=int, help='Fiscal year')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')

    args = parser.parse_args()

    # Process workbook
    results = process_workbook(
        args.excel_file,
        client_name=args.client_name,
        project_title=args.project_title,
        year=args.year,
        verbose=args.verbose,
    )

    # Output
    if args.output_csv:
        print("\n" + "=" * 60)
        print("OUTPUTTING TO CSV")
        print("=" * 60)
        output_to_csv(results)

    if not args.dry_run and args.gcp_project and args.dataset_id:
        print("\n" + "=" * 60)
        print("UPLOADING TO BIGQUERY")
        print("=" * 60)
        upload_to_bigquery(results, args.gcp_project, args.dataset_id, args.verbose)
    elif not args.dry_run and not args.output_csv:
        print("\nNo output specified. Use --dry-run, --output-csv, or provide GCP project/dataset.")

    print("\nDone!")


if __name__ == '__main__':
    main()
