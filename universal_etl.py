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
  python3 universal_etl.py <excel_file> [options]

Options:
  --interactive     Interactive mode: confirm sheets and metadata before processing
  --dry-run         Process without uploading to BigQuery
  --output-csv      Output processed data to CSV files
  --client-name     Client name for project identification
  --project-title   Project title for identification
  --year            Fiscal year for the data
  --verbose         Show detailed processing info

Examples:
  # Interactive mode (recommended for new files)
  python3 universal_etl.py "client_file.xlsx" --interactive --output-csv

  # Batch mode with known metadata
  python3 universal_etl.py "client_file.xlsx" my-gcp-project dataset_id \\
    --client-name "Acme Corp" --project-title "2025 Platform"
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
        r'^plan$',
        r'plan\s*\(',
        r'allocation',
        r'forecast',
        r'staffing',
        r'20\d{2}.*plan',  # Year-prefixed plans like "2025 Plan"
        r'plan.*20\d{2}',  # Plan with year suffix
    ],
    'rate_card': [
        r'rate\s*card',
        r'ratecard',
        r'custom.*rate',
        r'deptapps.*rate',
    ],
    'actuals': [
        r'actual',
        r'timesheet',
        r'hours.*log',
        r'pivot',  # Often actuals pivots
    ],
    'costs': [
        r'^costs?$',
        r'expense',
        r'vendor.*cost',
        r'^extras?$',
    ],
    'investment_log': [
        r'invest.*log',
        r'investment\s+log',
        r'overrun',
    ],
    'external_estimate': [
        r'ext.*estimate',
        r'client.*estimate',
        r'external.*summary',
        r'^ext\s+',
    ],
    'media': [
        r'^media$',
        r'media.*plan',
        r'media.*buy',
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
    'investment_log': ['date identified', 'investment summary', 'investment amount', 'resource impact'],
    'external_estimate': ['department', 'role', 'total hours', 'total fee', 'dedication'],
    'media': ['channel', 'platform', 'budget', 'spend', 'impressions', 'vendor'],
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
    Returns list of (column_name, week_number, type) tuples.
    """
    week_cols = []

    for col in columns:
        if pd.isna(col):
            continue

        col_str = str(col).strip()

        # Match patterns: "01", "1", "01-Hours", integers
        if re.match(r'^(0?[1-9]|[1-8][0-9]|90)$', col_str):
            week_num = int(col_str.lstrip('0') or '0')
            week_cols.append((col, week_num, 'fee'))
        elif re.match(r'^(0?[1-9]|[1-8][0-9]|90)-[Hh]ours$', col_str):
            match = re.match(r'^(\d+)', col_str)
            if match:
                week_num = int(match.group(1).lstrip('0') or '0')
                week_cols.append((col, week_num, 'hours'))
        elif isinstance(col, (int, float)) and not pd.isna(col) and 1 <= col <= 90:
            week_cols.append((col, int(col), 'fee'))

    return week_cols


def generate_project_id(client_name: str, project_title: str, source_file: str, source_sheet: str) -> str:
    """Generate a deterministic project ID from identifying information."""
    key = f"{client_name}|{project_title}|{source_file}|{source_sheet}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def safe_json_serialize(obj):
    """Convert an object to JSON-safe format, handling datetime and other special types."""
    if isinstance(obj, dict):
        return {k: safe_json_serialize(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [safe_json_serialize(v) for v in obj]
    elif isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    elif pd.isna(obj):
        return None
    elif isinstance(obj, (int, float, str, bool, type(None))):
        return obj
    else:
        return str(obj)


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
    raw_columns = df.iloc[header_row].values

    # Track original column names before normalization for week column detection
    original_columns = list(raw_columns)

    # Normalize column names, then make unique to handle duplicates
    normalized_columns = [normalize_column_name(col) for col in raw_columns]
    unique_columns = make_columns_unique(normalized_columns)
    df_data.columns = unique_columns

    # Build a mapping from unique normalized columns to their original values
    # This helps us identify which columns are week columns
    col_original_map = dict(zip(unique_columns, original_columns))

    # Core dimension columns we want to keep
    dimension_cols = [
        'category', 'market', 'department', 'role', 'employee_name',
        'notes', 'business_team', 'ic_type',
        'final_bill_rate', 'effective_bill_rate', 'cost_rate', 'final_cost_rate',
        'total_fees', 'total_cost', 'total_hours', 'margin_pct', 'discount_pct',
    ]

    # Keep only columns that exist (handle duplicates with suffixes)
    existing_dims = []
    for dim in dimension_cols:
        if dim in df_data.columns:
            existing_dims.append(dim)
        # Also check for suffixed versions (dim_1, dim_2, etc.) but only add the first
        elif any(col.startswith(dim + '_') and col[len(dim)+1:].isdigit() for col in df_data.columns):
            for col in df_data.columns:
                if col.startswith(dim + '_') and col[len(dim)+1:].isdigit():
                    existing_dims.append(col)
                    break

    # Filter out empty rows (no market/department/role)
    key_cols = ['market', 'department', 'role']
    existing_keys = [c for c in key_cols if c in df_data.columns]
    if existing_keys:
        df_data = df_data.dropna(subset=existing_keys, how='all')

    # Extract metadata from rows above header
    sheet_metadata = extract_sheet_metadata(df, header_row)

    # Identify week columns from the CURRENT dataframe columns
    # A week column is one whose original value was a week number (1-90)
    week_col_in_data = []
    for col in df_data.columns:
        original = col_original_map.get(col)
        if original is None:
            continue

        # Check original value for week number patterns
        original_str = str(original).strip() if pd.notna(original) else ''

        # Integer columns (1, 2, 3...)
        if isinstance(original, (int, float)) and pd.notna(original):
            try:
                if 1 <= int(original) <= 90:
                    week_col_in_data.append((col, int(original)))
                    continue
            except (ValueError, TypeError):
                pass

        # String columns ("01", "02", "1", "2"...)
        if re.match(r'^(0?[1-9]|[1-8][0-9]|90)$', original_str):
            week_num = int(original_str.lstrip('0') or '0')
            if 1 <= week_num <= 90:
                week_col_in_data.append((col, week_num))

    if verbose:
        print(f"    Found {len(df_data)} data rows")
        print(f"    Dimension columns: {existing_dims}")
        print(f"    Week columns: {len(week_col_in_data)}")

    if week_col_in_data:
        # Get just the column names for melting
        week_col_names = [col for col, wk in week_col_in_data]

        # Ensure id_vars and value_vars don't overlap
        id_vars = [c for c in existing_dims if c in df_data.columns and c not in week_col_names]

        try:
            df_melted = pd.melt(
                df_data,
                id_vars=id_vars,
                value_vars=week_col_names,
                var_name='week_col',
                value_name='hours'
            )

            # Map week_col back to week number using our tracked mapping
            week_col_to_num = {col: wk for col, wk in week_col_in_data}
            df_melted['week_number'] = df_melted['week_col'].map(week_col_to_num)
            df_melted = df_melted.drop(columns=['week_col'])

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
        except Exception as e:
            if verbose:
                print(f"    Warning: Could not melt week columns: {str(e)}")
            # Fall through to return dimension data only

    # If no week columns or melting failed, return dimension data only
    df_data['source_sheet'] = sheet_name
    df_data['project_id'] = metadata.get('project_id', '')

    cols_to_return = [c for c in existing_dims if c in df_data.columns] + ['source_sheet', 'project_id']
    return {
        'allocations': df_data[cols_to_return] if cols_to_return else df_data,
        'metadata': sheet_metadata,
        'row_count': len(df_data),
    }


def make_columns_unique(columns: list) -> list:
    """
    Make a list of column names unique by appending suffixes to duplicates.
    Handles None/NaN values by converting to 'unnamed'.
    """
    seen = {}
    unique_columns = []
    for col in columns:
        col_str = str(col) if pd.notna(col) else 'unnamed'
        if col_str in seen:
            seen[col_str] += 1
            unique_columns.append(f"{col_str}_{seen[col_str]}")
        else:
            seen[col_str] = 0
            unique_columns.append(col_str)
    return unique_columns


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
    raw_columns = df.iloc[header_row].values

    # First normalize, then make unique (to handle post-normalization duplicates)
    normalized_columns = [normalize_column_name(col) for col in raw_columns]
    unique_columns = make_columns_unique(normalized_columns)

    df_data.columns = unique_columns

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
    raw_columns = df.iloc[header_row].values

    # Normalize then make unique
    normalized_columns = [normalize_column_name(col) for col in raw_columns]
    unique_columns = make_columns_unique(normalized_columns)
    df_data.columns = unique_columns

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
    raw_columns = df.iloc[header_row].values

    # Normalize then make unique
    normalized_columns = [normalize_column_name(col) for col in raw_columns]
    unique_columns = make_columns_unique(normalized_columns)
    df_data.columns = unique_columns

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


def process_investment_log_sheet(
    df: pd.DataFrame,
    sheet_name: str,
    header_row: int,
    metadata: dict,
    verbose: bool = False
) -> dict:
    """Process an Investment Log sheet."""
    df_data = df.iloc[header_row + 1:].copy()
    raw_columns = df.iloc[header_row].values

    # Normalize then make unique
    normalized_columns = [normalize_column_name(col) for col in raw_columns]
    unique_columns = make_columns_unique(normalized_columns)
    df_data.columns = unique_columns

    # Remove empty rows - check for any non-null value in key columns
    key_cols = ['investment_summary', 'investment_amount', 'date_identified']
    existing_keys = [c for c in key_cols if c in df_data.columns]
    if existing_keys:
        df_data = df_data.dropna(subset=existing_keys, how='all')

    df_data['source_sheet'] = sheet_name
    df_data['project_id'] = metadata.get('project_id', '')

    if verbose:
        print(f"    Found {len(df_data)} investment log entries")

    return {
        'investment_log': df_data,
        'row_count': len(df_data),
    }


def process_external_estimate_sheet(
    df: pd.DataFrame,
    sheet_name: str,
    header_row: int,
    metadata: dict,
    verbose: bool = False
) -> dict:
    """Process an External Estimate sheet (client-facing summary)."""
    df_data = df.iloc[header_row + 1:].copy()
    raw_columns = df.iloc[header_row].values

    # Normalize then make unique
    normalized_columns = [normalize_column_name(col) for col in raw_columns]
    unique_columns = make_columns_unique(normalized_columns)
    df_data.columns = unique_columns

    # Remove empty rows
    key_cols = ['department', 'role', 'total_fee', 'est_total_hours']
    existing_keys = [c for c in key_cols if c in df_data.columns]
    if existing_keys:
        df_data = df_data.dropna(subset=existing_keys, how='all')

    df_data['source_sheet'] = sheet_name
    df_data['project_id'] = metadata.get('project_id', '')

    if verbose:
        print(f"    Found {len(df_data)} external estimate entries")

    return {
        'external_estimate': df_data,
        'row_count': len(df_data),
    }


def process_media_sheet(
    df: pd.DataFrame,
    sheet_name: str,
    header_row: int,
    metadata: dict,
    verbose: bool = False
) -> dict:
    """Process a Media plan/buy sheet."""
    df_data = df.iloc[header_row + 1:].copy()
    raw_columns = df.iloc[header_row].values

    # Normalize then make unique
    normalized_columns = [normalize_column_name(col) for col in raw_columns]
    unique_columns = make_columns_unique(normalized_columns)
    df_data.columns = unique_columns

    # Remove completely empty rows
    df_data = df_data.dropna(how='all')

    df_data['source_sheet'] = sheet_name
    df_data['project_id'] = metadata.get('project_id', '')

    if verbose:
        print(f"    Found {len(df_data)} media entries")

    return {
        'media': df_data,
        'row_count': len(df_data),
    }


def extract_sheet_metadata(df: pd.DataFrame, header_row: int) -> dict:
    """
    Extract comprehensive metadata from rows above the data header.
    Captures project info, financial summaries, and configuration.
    """
    metadata = {
        'project_info': {},
        'financial_summary': {},
        'configuration': {},
        'raw_metadata': [],  # Store all label-value pairs found
    }

    # Valid market codes (only accept these specific values)
    VALID_MARKET_CODES = {
        'DPUS', 'CXUS', 'EXUS', 'MTUS', 'AMER', 'EMEA', 'APAC', 'LATAM',
        'NA', 'EU', 'UK', 'US', 'CA', 'AU', 'GLOBAL', 'CORP',
    }

    # Values that should NOT be accepted as market codes
    INVALID_MARKET_VALUES = {
        'total hours', 'total fees', 'total cost', 'gross margin',
        'category', 'department', 'role', 'notes', 'employee',
        'bill rate', 'cost rate', 'hours', 'fees', 'costs',
    }

    def is_valid_market_value(val):
        """Check if a value is a valid market code."""
        if pd.isna(val):
            return False
        val_str = str(val).strip().upper()
        val_lower = str(val).strip().lower()
        # Must be a known code or a short uppercase string (not a common label)
        if val_str in VALID_MARKET_CODES:
            return True
        if val_lower in INVALID_MARKET_VALUES:
            return False
        # Accept short uppercase codes that look like market codes (2-6 chars)
        if len(val_str) <= 6 and val_str.isalpha() and val_str.isupper():
            return True
        return False

    # Known labels and their canonical keys
    # Format: 'label pattern' -> ('category', 'key_name')
    known_labels = {
        # Project identification
        'client': ('project_info', 'client'),
        'client (info)': ('project_info', 'client'),
        'project title': ('project_info', 'project_title'),
        'project title (info)': ('project_info', 'project_title'),
        'project number': ('project_info', 'project_number'),
        'project number (info)': ('project_info', 'project_number'),

        # Dates
        'start date': ('project_info', 'start_date'),
        'start date (required)': ('project_info', 'start_date'),
        'end date': ('project_info', 'end_date'),

        # Configuration - market requires special validation
        'market': ('configuration', 'market'),
        'market (required)': ('configuration', 'market'),
        'company': ('configuration', 'company'),
        'company (required)': ('configuration', 'company'),
        'rate card': ('configuration', 'rate_card'),
        'rate card (required)': ('configuration', 'rate_card'),
        'billing type': ('configuration', 'billing_type'),
        'billing type (info)': ('configuration', 'billing_type'),
        'hour mode': ('configuration', 'hour_mode'),

        # Financial summaries
        'total project fee': ('financial_summary', 'total_project_fee'),
        'estimated gross margin': ('financial_summary', 'estimated_gross_margin'),
        'target gm%': ('financial_summary', 'target_gm_pct'),
        'target gm% / fee': ('financial_summary', 'target_gm_pct'),
        'billable labor fees': ('financial_summary', 'billable_labor_fees'),
        'additional billable fees': ('financial_summary', 'additional_billable_fees'),
        'passthrough': ('financial_summary', 'passthrough'),
        'labor costs': ('financial_summary', 'labor_costs'),
        'investment costs': ('financial_summary', 'investment_costs'),
        'total hours': ('financial_summary', 'total_hours'),
        'total cost': ('financial_summary', 'total_cost'),
        'gross margin': ('financial_summary', 'gross_margin'),

        # Overrides/options
        'fixed fee': ('configuration', 'fixed_fee'),
        'fixed fee (optional)': ('configuration', 'fixed_fee'),
        'fixed % discount': ('configuration', 'fixed_discount_pct'),
        'blended rate': ('configuration', 'blended_rate'),
    }

    # Labels that require special value validation
    LABELS_REQUIRING_VALIDATION = {'market'}

    # Also look for these patterns that might have values in adjacent cells
    value_patterns = [
        'weekly (fixed 40)', 'weekly (fixed 35)', 'monthly (fixed 150)',
        'fixed fee', 't&m', 'retainer', 'hybrid',
    ]

    # Scan metadata rows (only rows BEFORE the header, not the header itself)
    for idx in range(min(header_row, 50)):
        row = df.iloc[idx]

        for col_idx in range(min(len(row), 30)):  # Check first 30 columns
            val = row.iloc[col_idx]

            if pd.isna(val):
                continue

            # Check if it's a string label
            if isinstance(val, str):
                val_lower = val.lower().strip()

                # Check against known labels (exact match preferred)
                for label_pattern, (category, key_name) in known_labels.items():
                    # Use exact match or contained match for labels
                    if val_lower == label_pattern or (label_pattern in val_lower and len(val_lower) < 50):
                        # Try to get value from next column
                        if col_idx + 1 < len(row):
                            next_val = row.iloc[col_idx + 1]
                            if pd.notna(next_val):
                                # Special validation for market field
                                if key_name == 'market':
                                    if not is_valid_market_value(next_val):
                                        continue  # Skip invalid market values

                                metadata[category][key_name] = next_val
                                metadata['raw_metadata'].append({
                                    'row': idx + 1,
                                    'label': val,
                                    'value': next_val,
                                    'category': category,
                                    'key': key_name,
                                })
                        break

                # Check for value patterns (these ARE the value, not a label)
                for pattern in value_patterns:
                    if pattern in val_lower:
                        # This is likely a configuration value
                        if 'weekly' in val_lower or 'monthly' in val_lower:
                            metadata['configuration']['hour_mode'] = val
                        elif val_lower in ['fixed fee', 't&m', 'retainer', 'hybrid']:
                            metadata['configuration']['billing_type'] = val
                        metadata['raw_metadata'].append({
                            'row': idx + 1,
                            'label': 'detected_value',
                            'value': val,
                        })
                        break

            # Check for standalone market codes (short uppercase strings)
            if isinstance(val, str) and len(val) <= 10:
                val_upper = val.strip().upper()
                if val_upper in VALID_MARKET_CODES:
                    if 'market' not in metadata['configuration']:
                        metadata['configuration']['market'] = val_upper
                    metadata['raw_metadata'].append({
                        'row': idx + 1,
                        'label': 'market_code',
                        'value': val_upper,
                    })

            # Capture financial numbers that appear with labels
            # Look for patterns where col 0 has a label and col 1 has a number
            if col_idx == 0 and isinstance(val, str):
                if col_idx + 1 < len(row):
                    next_val = row.iloc[col_idx + 1]
                    if isinstance(next_val, (int, float)) and pd.notna(next_val):
                        metadata['raw_metadata'].append({
                            'row': idx + 1,
                            'label': val,
                            'value': next_val,
                            'type': 'number',
                        })

    # Also check column 3-4 pattern (common in these sheets for fee summaries)
    for idx in range(min(header_row, 50)):
        row = df.iloc[idx]
        if len(row) > 4:
            label_val = row.iloc[3] if pd.notna(row.iloc[3]) else None
            num_val = row.iloc[4] if len(row) > 4 and pd.notna(row.iloc[4]) else None

            if isinstance(label_val, str) and isinstance(num_val, (int, float)):
                label_lower = label_val.lower().strip()

                # Map to financial summary
                if 'billable' in label_lower and 'fee' in label_lower:
                    metadata['financial_summary']['billable_fees'] = num_val
                elif 'passthrough' in label_lower:
                    metadata['financial_summary']['passthrough'] = num_val
                elif 'investment' in label_lower:
                    metadata['financial_summary']['investment_costs'] = num_val
                elif 'total hours' in label_lower:
                    metadata['financial_summary']['total_hours'] = num_val
                elif 'total cost' in label_lower:
                    metadata['financial_summary']['total_cost'] = num_val
                elif 'labor cost' in label_lower:
                    metadata['financial_summary']['labor_costs'] = num_val

                metadata['raw_metadata'].append({
                    'row': idx + 1,
                    'col': 3,
                    'label': label_val,
                    'value': num_val,
                })

    return metadata


# =============================================================================
# INTERACTIVE MODE FUNCTIONS
# =============================================================================

def print_sheet_summary(xlsx: pd.ExcelFile) -> list:
    """Print a summary of all sheets with auto-detected types."""
    print("\n" + "=" * 70)
    print("SHEET DETECTION SUMMARY")
    print("=" * 70)
    print(f"{'#':<4} {'Sheet Name':<40} {'Detected Type':<15} {'Rows':<8}")
    print("-" * 70)

    sheet_info = []
    for idx, sheet_name in enumerate(xlsx.sheet_names, 1):
        detected_type = detect_sheet_type(sheet_name)

        # Quick row count
        try:
            df = pd.read_excel(xlsx, sheet_name=sheet_name, header=None, nrows=5)
            row_count = len(pd.read_excel(xlsx, sheet_name=sheet_name, header=None))
        except:
            row_count = 0

        sheet_info.append({
            'index': idx,
            'name': sheet_name,
            'detected_type': detected_type,
            'row_count': row_count,
            'selected': detected_type not in ['skip', 'unknown', 'mapping', 'info'],
        })

        # Color coding for terminal
        type_display = detected_type.upper() if detected_type not in ['skip', 'unknown'] else f"({detected_type})"
        name_display = sheet_name[:38] + '..' if len(sheet_name) > 40 else sheet_name

        print(f"{idx:<4} {name_display:<40} {type_display:<15} {row_count:<8}")

    print("-" * 70)
    return sheet_info


def interactive_sheet_selection(sheet_info: list) -> list:
    """Let user confirm or modify sheet selections and types."""
    print("\n" + "=" * 70)
    print("SHEET SELECTION")
    print("=" * 70)
    print("Review the detected sheet types above. You can:")
    print("  - Press ENTER to accept all detected types")
    print("  - Enter sheet numbers to toggle selection (e.g., '1,3,5' or '1-5')")
    print("  - Enter 'c' to change a sheet's type")
    print("  - Enter 's' to skip all and select manually")
    print("-" * 70)

    # Show currently selected
    selected = [s for s in sheet_info if s['selected']]
    print(f"\nCurrently selected for processing ({len(selected)} sheets):")
    for s in selected:
        print(f"  [{s['index']}] {s['name']} -> {s['detected_type']}")

    while True:
        response = input("\nAction (ENTER=accept, numbers=toggle, c=change type, s=manual): ").strip().lower()

        if response == '':
            # Accept current selection
            break

        elif response == 's':
            # Deselect all, let user pick
            for s in sheet_info:
                s['selected'] = False
            nums = input("Enter sheet numbers to select (e.g., 1,3,5 or 1-5): ").strip()
            indices = parse_number_input(nums)
            for s in sheet_info:
                if s['index'] in indices:
                    s['selected'] = True
            break

        elif response == 'c':
            # Change type
            num = input("Enter sheet number to change type: ").strip()
            try:
                idx = int(num)
                sheet = next((s for s in sheet_info if s['index'] == idx), None)
                if sheet:
                    print(f"Current type for '{sheet['name']}': {sheet['detected_type']}")
                    print("Available types: plan, rate_card, actuals, costs, investment_log, external_estimate, skip")
                    new_type = input("New type: ").strip().lower()
                    if new_type in ['plan', 'rate_card', 'actuals', 'costs', 'investment_log', 'external_estimate', 'skip']:
                        sheet['detected_type'] = new_type
                        sheet['selected'] = new_type != 'skip'
                        print(f"  Updated to: {new_type}")
            except ValueError:
                print("Invalid input")

        else:
            # Toggle selection by numbers
            indices = parse_number_input(response)
            for s in sheet_info:
                if s['index'] in indices:
                    s['selected'] = not s['selected']

            # Show updated selection
            selected = [s for s in sheet_info if s['selected']]
            print(f"\nUpdated selection ({len(selected)} sheets):")
            for s in selected:
                print(f"  [{s['index']}] {s['name']} -> {s['detected_type']}")

    return sheet_info


def parse_number_input(s: str) -> list:
    """Parse input like '1,3,5' or '1-5' or '1,3-5,7' into list of integers."""
    indices = []
    for part in s.replace(' ', '').split(','):
        if '-' in part:
            try:
                start, end = part.split('-')
                indices.extend(range(int(start), int(end) + 1))
            except:
                pass
        else:
            try:
                indices.append(int(part))
            except:
                pass
    return indices


def interactive_metadata_input(file_name: str) -> dict:
    """Prompt user for project metadata."""
    print("\n" + "=" * 70)
    print("PROJECT METADATA")
    print("=" * 70)
    print("Enter project information (press ENTER to skip optional fields):")
    print("-" * 70)

    # Try to extract hints from filename
    year_match = re.search(r'20\d{2}', file_name)
    suggested_year = year_match.group() if year_match else str(datetime.now().year)

    client_name = input(f"Client name: ").strip()
    project_title = input(f"Project title: ").strip()
    year = input(f"Fiscal year [{suggested_year}]: ").strip() or suggested_year

    try:
        year = int(year)
    except:
        year = datetime.now().year

    return {
        'client_name': client_name,
        'project_title': project_title,
        'year': year,
    }


def show_processing_preview(sheet_info: list, metadata: dict, xlsx: pd.ExcelFile) -> bool:
    """Show preview of what will be processed and confirm."""
    print("\n" + "=" * 70)
    print("PROCESSING PREVIEW")
    print("=" * 70)

    print(f"\nProject: {metadata.get('client_name', 'Unknown')} - {metadata.get('project_title', 'Unknown')}")
    print(f"Year: {metadata.get('year', 'Unknown')}")

    selected = [s for s in sheet_info if s['selected']]
    print(f"\nSheets to process ({len(selected)}):")
    print("-" * 50)

    for s in selected:
        sheet_name = s['name']
        sheet_type = s['detected_type']

        # Quick header detection preview
        try:
            df = pd.read_excel(xlsx, sheet_name=sheet_name, header=None)
            header_row = find_header_row(df, sheet_type)
            if header_row >= 0:
                headers = df.iloc[header_row].dropna().tolist()[:8]
                headers_str = ', '.join(str(h)[:20] for h in headers)
                print(f"\n  [{s['index']}] {sheet_name}")
                print(f"      Type: {sheet_type.upper()}")
                print(f"      Header row: {header_row + 1}")
                print(f"      Columns: {headers_str}...")
                print(f"      Data rows: ~{s['row_count'] - header_row - 1}")
            else:
                print(f"\n  [{s['index']}] {sheet_name}")
                print(f"      Type: {sheet_type.upper()}")
                print(f"      WARNING: Could not detect header row")
        except Exception as e:
            print(f"\n  [{s['index']}] {sheet_name}")
            print(f"      ERROR reading sheet: {str(e)}")

    print("\n" + "-" * 70)
    confirm = input("Proceed with processing? (y/n): ").strip().lower()
    return confirm in ['y', 'yes', '']


def run_interactive_mode(file_path: str) -> Optional[dict]:
    """Run the ETL in interactive mode with user confirmation."""
    print("\n" + "=" * 70)
    print("UNIVERSAL FINANCIAL ETL - INTERACTIVE MODE")
    print("=" * 70)

    file_name = Path(file_path).name
    print(f"\nFile: {file_name}")

    # Load workbook
    try:
        xlsx = pd.ExcelFile(file_path, engine='openpyxl')
    except Exception as e:
        print(f"ERROR: Could not open file: {str(e)}")
        return None

    # Step 1: Show sheet summary
    sheet_info = print_sheet_summary(xlsx)

    # Step 2: Let user confirm/modify selection
    sheet_info = interactive_sheet_selection(sheet_info)

    selected = [s for s in sheet_info if s['selected']]
    if not selected:
        print("\nNo sheets selected. Exiting.")
        return None

    # Step 3: Get metadata
    metadata = interactive_metadata_input(file_name)

    # Step 4: Show preview and confirm
    if not show_processing_preview(sheet_info, metadata, xlsx):
        print("\nCancelled.")
        return None

    # Step 5: Process selected sheets
    return process_workbook_with_selections(
        xlsx,
        sheet_info,
        metadata,
        file_name,
        verbose=True
    )


def process_workbook_with_selections(
    xlsx: pd.ExcelFile,
    sheet_info: list,
    metadata: dict,
    file_name: str,
    verbose: bool = False
) -> dict:
    """Process workbook with user-confirmed sheet selections."""
    results = {
        'allocations': [],
        'rate_cards': [],
        'actuals': [],
        'costs': [],
        'projects': [],
        'processing_log': [],
    }

    print("\n" + "=" * 70)
    print("PROCESSING")
    print("=" * 70)

    base_metadata = {
        'client_name': metadata.get('client_name', ''),
        'project_title': metadata.get('project_title', ''),
        'source_file': file_name,
        'year': metadata.get('year', datetime.now().year),
    }

    selected = [s for s in sheet_info if s['selected']]

    for s in selected:
        sheet_name = s['name']
        sheet_type = s['detected_type']

        print(f"\n[{sheet_type.upper()}] {sheet_name}")

        df = pd.read_excel(xlsx, sheet_name=sheet_name, header=None)

        if len(df) == 0:
            print("    Empty sheet, skipping")
            continue

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

        sheet_metadata = base_metadata.copy()
        sheet_metadata['project_id'] = generate_project_id(
            base_metadata['client_name'],
            base_metadata['project_title'],
            file_name,
            sheet_name
        )
        sheet_metadata['source_sheet'] = sheet_name

        try:
            if sheet_type == 'plan':
                result = process_plan_sheet(df, sheet_name, header_row, sheet_metadata, verbose)
                if 'allocations' in result and len(result['allocations']) > 0:
                    results['allocations'].append(result['allocations'])
                    print(f"    Processed {result['row_count']} allocation records")

                # Build rich project record with extracted metadata
                sheet_meta = result.get('metadata', {})
                project_info = sheet_meta.get('project_info', {})
                financial_summary = sheet_meta.get('financial_summary', {})
                config = sheet_meta.get('configuration', {})

                project_record = {
                    'project_id': sheet_metadata['project_id'],
                    'client_name': project_info.get('client') or base_metadata['client_name'],
                    'project_title': project_info.get('project_title') or base_metadata['project_title'],
                    'project_number': project_info.get('project_number'),
                    'source_file': file_name,
                    'source_sheet': sheet_name,
                    'year': base_metadata['year'],
                    # Configuration from sheet
                    'market': config.get('market'),
                    'billing_type': config.get('billing_type'),
                    'hour_mode': config.get('hour_mode'),
                    'rate_card_name': config.get('rate_card'),
                    'start_date': str(project_info.get('start_date', '')) if project_info.get('start_date') else None,
                    'end_date': str(project_info.get('end_date', '')) if project_info.get('end_date') else None,
                    # Financial summary
                    'total_project_fee': financial_summary.get('total_project_fee'),
                    'billable_labor_fees': financial_summary.get('billable_labor_fees') or financial_summary.get('billable_fees'),
                    'additional_billable_fees': financial_summary.get('additional_billable_fees'),
                    'passthrough': financial_summary.get('passthrough'),
                    'labor_costs': financial_summary.get('labor_costs'),
                    'investment_costs': financial_summary.get('investment_costs'),
                    'total_hours': financial_summary.get('total_hours'),
                    'estimated_gross_margin': financial_summary.get('estimated_gross_margin'),
                    # Full metadata JSON for anything else
                    'sheet_metadata_json': json.dumps(safe_json_serialize(sheet_meta)),
                    'processed_at': datetime.now().isoformat(),
                }
                results['projects'].append(project_record)

                if verbose:
                    if config:
                        print(f"    Config: market={config.get('market')}, billing={config.get('billing_type')}, hours={config.get('hour_mode')}")
                    if financial_summary:
                        print(f"    Financials: fee={financial_summary.get('total_project_fee')}, hours={financial_summary.get('total_hours')}")

            elif sheet_type == 'rate_card':
                result = process_rate_card_sheet(df, sheet_name, header_row, sheet_metadata, verbose)
                if 'rate_card' in result and len(result['rate_card']) > 0:
                    results['rate_cards'].append(result['rate_card'])
                    print(f"    Processed {result['row_count']} rate card entries")

            elif sheet_type == 'actuals':
                result = process_actuals_sheet(df, sheet_name, header_row, sheet_metadata, verbose)
                if 'actuals' in result and len(result['actuals']) > 0:
                    results['actuals'].append(result['actuals'])
                    print(f"    Processed {result['row_count']} actuals records")

            elif sheet_type == 'costs':
                result = process_costs_sheet(df, sheet_name, header_row, sheet_metadata, verbose)
                if 'costs' in result and len(result['costs']) > 0:
                    results['costs'].append(result['costs'])
                    print(f"    Processed {result['row_count']} cost entries")

            elif sheet_type == 'investment_log':
                result = process_investment_log_sheet(df, sheet_name, header_row, sheet_metadata, verbose)
                print(f"    Processed {result['row_count']} investment log entries")

            elif sheet_type == 'external_estimate':
                result = process_external_estimate_sheet(df, sheet_name, header_row, sheet_metadata, verbose)
                print(f"    Processed {result['row_count']} external estimate entries")

            elif sheet_type == 'media':
                result = process_media_sheet(df, sheet_name, header_row, sheet_metadata, verbose)
                print(f"    Processed {result['row_count']} media entries")

            else:
                print(f"    Skipping unknown sheet type: {sheet_type}")
                result = {'row_count': 0}

            results['processing_log'].append({
                'sheet': sheet_name,
                'type': sheet_type,
                'status': 'success',
                'row_count': result.get('row_count', 0),
            })

        except Exception as e:
            print(f"    ERROR: {str(e)}")
            import traceback
            if verbose:
                traceback.print_exc()
            results['processing_log'].append({
                'sheet': sheet_name,
                'type': sheet_type,
                'status': 'error',
                'message': str(e),
            })

    # Combine results
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    if results['allocations']:
        results['allocations_combined'] = pd.concat(results['allocations'], ignore_index=True)
        print(f"Total allocations: {len(results['allocations_combined'])} rows")
    else:
        print("Total allocations: 0 rows")

    if results['rate_cards']:
        results['rate_cards_combined'] = pd.concat(results['rate_cards'], ignore_index=True)
        print(f"Total rate card entries: {len(results['rate_cards_combined'])} rows")
    else:
        print("Total rate card entries: 0 rows")

    if results['actuals']:
        results['actuals_combined'] = pd.concat(results['actuals'], ignore_index=True)
        print(f"Total actuals: {len(results['actuals_combined'])} rows")
    else:
        print("Total actuals: 0 rows")

    if results['costs']:
        results['costs_combined'] = pd.concat(results['costs'], ignore_index=True)
        print(f"Total costs: {len(results['costs_combined'])} rows")
    else:
        print("Total costs: 0 rows")

    return results


# =============================================================================
# MAIN ETL ORCHESTRATOR (BATCH MODE)
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
                # Build rich project record with extracted metadata
                sheet_meta = result.get('metadata', {})
                project_info = sheet_meta.get('project_info', {})
                financial_summary = sheet_meta.get('financial_summary', {})
                config = sheet_meta.get('configuration', {})

                project_record = {
                    'project_id': metadata['project_id'],
                    'client_name': project_info.get('client') or client_name,
                    'project_title': project_info.get('project_title') or project_title,
                    'project_number': project_info.get('project_number'),
                    'source_file': file_name,
                    'source_sheet': sheet_name,
                    'year': year,
                    # Configuration from sheet
                    'market': config.get('market'),
                    'billing_type': config.get('billing_type'),
                    'hour_mode': config.get('hour_mode'),
                    'rate_card_name': config.get('rate_card'),
                    'start_date': str(project_info.get('start_date', '')) if project_info.get('start_date') else None,
                    'end_date': str(project_info.get('end_date', '')) if project_info.get('end_date') else None,
                    # Financial summary
                    'total_project_fee': financial_summary.get('total_project_fee'),
                    'billable_labor_fees': financial_summary.get('billable_labor_fees') or financial_summary.get('billable_fees'),
                    'additional_billable_fees': financial_summary.get('additional_billable_fees'),
                    'passthrough': financial_summary.get('passthrough'),
                    'labor_costs': financial_summary.get('labor_costs'),
                    'investment_costs': financial_summary.get('investment_costs'),
                    'total_hours': financial_summary.get('total_hours'),
                    'estimated_gross_margin': financial_summary.get('estimated_gross_margin'),
                    # Full metadata JSON for anything else
                    'sheet_metadata_json': json.dumps(safe_json_serialize(sheet_meta)),
                    'processed_at': datetime.now().isoformat(),
                }
                results['projects'].append(project_record)

                if verbose:
                    if config:
                        print(f"    Config: market={config.get('market')}, billing={config.get('billing_type')}, hours={config.get('hour_mode')}")
                    if financial_summary:
                        print(f"    Financials: fee={financial_summary.get('total_project_fee')}, hours={financial_summary.get('total_hours')}")

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

            elif sheet_type == 'investment_log':
                result = process_investment_log_sheet(df, sheet_name, header_row, metadata, verbose)
                print(f"    Processed {result['row_count']} investment log entries")

            elif sheet_type == 'external_estimate':
                result = process_external_estimate_sheet(df, sheet_name, header_row, metadata, verbose)
                print(f"    Processed {result['row_count']} external estimate entries")

            elif sheet_type == 'media':
                result = process_media_sheet(df, sheet_name, header_row, metadata, verbose)
                print(f"    Processed {result['row_count']} media entries")

            else:
                print(f"    Skipping unhandled sheet type: {sheet_type}")
                result = {'row_count': 0}

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
    parser.add_argument('--interactive', '-i', action='store_true',
                        help='Interactive mode: confirm sheets and metadata before processing')
    parser.add_argument('--dry-run', action='store_true', help='Process without uploading')
    parser.add_argument('--output-csv', action='store_true', help='Output to CSV files')
    parser.add_argument('--client-name', default='', help='Client name')
    parser.add_argument('--project-title', default='', help='Project title')
    parser.add_argument('--year', type=int, help='Fiscal year')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')

    args = parser.parse_args()

    # Interactive mode
    if args.interactive:
        results = run_interactive_mode(args.excel_file)
        if results is None:
            sys.exit(1)
    else:
        # Batch mode
        results = process_workbook(
            args.excel_file,
            client_name=args.client_name,
            project_title=args.project_title,
            year=args.year,
            verbose=args.verbose,
        )

    # Output
    if args.output_csv:
        print("\n" + "=" * 70)
        print("OUTPUTTING TO CSV")
        print("=" * 70)
        output_to_csv(results)

    if not args.dry_run and args.gcp_project and args.dataset_id:
        print("\n" + "=" * 70)
        print("UPLOADING TO BIGQUERY")
        print("=" * 70)
        upload_to_bigquery(results, args.gcp_project, args.dataset_id, args.verbose)
    elif not args.dry_run and not args.output_csv:
        print("\nNo output specified. Use --dry-run, --output-csv, or provide GCP project/dataset.")

    print("\nDone!")


if __name__ == '__main__':
    main()
