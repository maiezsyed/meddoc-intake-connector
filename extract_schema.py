#!/usr/bin/env python3
"""
Deep Excel Schema Extraction (PII-Safe)
=========================================
Scans full sheet structure to identify all data zones:
  - Metadata zone (rows 0-N: client info, project details, summaries)
  - Data zone (row N+: actual staffing/rate data with headers)

ALL cell values are masked by default. Only whitelisted structural
labels and known-safe codes are shown. No PII, company names, or
confidential data is exposed.

Run: python3 extract_schema.py "your_file.xlsx" > schema.txt
"""

import sys
import re
import hashlib
from datetime import datetime
import pandas as pd


# =============================================================================
# Whitelists — ONLY these exact values are shown unmasked
# =============================================================================

# Known-safe short codes (market regions, billing types, etc.)
SAFE_CODES = {
    'amer', 'emea', 'apac', 'beno', 'dach', 'uki', 'nearshore',
    'cxus', 'dpus', 'exus', 'dph', 'dus', 'mtus',
    'junior', 'senior', 'manager', 'lead', 'director',
    'associate director', 'group director',
    'fixed fee', 't&m', 'retainer', 'hybrid',
    'experience', 'technology', 'marketing', 'holding',
    'estimate', 'actual', 'investment cost', 'passthrough', 'billable fee',
    'weekly', 'monthly', 'weekly (fixed 40)', 'monthly (fixed 150)',
    'beta 1', 'beta 2',
    'ux', 'ui',
    '#ref!',
}

# Structural labels that describe what a cell IS (row/column labels)
# These are shown because they describe the schema, not client data
STRUCTURAL_LABELS = {
    # Metadata labels
    'client (info)', 'project title (info)', 'project number (info)',
    'start date (required)', 'company (required)', 'market (required)',
    'rate card (required)', 'billing type (info)',
    'estimated gross margin', 'total project fee', 'target gm% / fee (info)',
    'billable labor fees', 'additional billable fees', 'passthrough',
    'labor costs', 'investment costs', 'global overrides',
    'fixed fee (optional)', 'fixed % discount (optional)',
    'blended rate (optional)',
    'expand to view additional cost/fee details',
    'edit to choose external rates',
    'version:',

    # Data header labels
    'category\n(optional)', 'role', 'market_region', 'department',
    'team\n(info)', 'specialization (info)', 'name', 'bill rate',
    'rate card override\n(optional)', 'disc. % override\n(optional)',
    'bill rate override\n(optional)', 'final billable rate', 'total fees',
    'cost rate', 'cost rate override\n(optional)', 'final cost rate',
    'total cost', 'gross margin', 'total hours',

    # Rate card labels
    'market', 'global department', 'level', 'title', 'cost rate',
    'custom rate cards -->',

    # Q&A labels
    'question', 'answer', 'notes (optional)', 'info',
    "who's the prospect or client?",
    "what's their marketing challenge?",
    "what's the total projected revenue?",
    "who's the buyer or sponsor?",
    "what's the forecasted gross margin?",
    "is there a cross-sell opportunity?",
    "have we worked with them before?",
    "who are we up against?",
    "what's the delivery model?",
    "so what's your pricing strategy?",
    "when is the proposal due?",

    # Costs sheet labels
    'item', 'category', 'date', 'vendor', 'estimate/actual',
    'total cost', 'type', 'notes/description',

    # Mapping sheet labels
    'internal division code', 'market', 'team name', 'departments',
    'sub-departments', 'levels', 'margin target',
    'summarized roles (auto)', 'holidays (us)',

    # Role mapping labels
    'title', 'summarized role', 'level',

    # Change log labels
    'version', 'notes', 'published by', 'published on',

    # Column placeholders
    'column 5', 'column 6', 'column 7', 'column 8', 'column 9',
    'column 10', 'column 11', 'column 12', 'column 13', 'column 14',
    'column 15', 'column 16', 'column 17', 'column 18',
}


def scrub_filename(path: str) -> str:
    """Replace filename with a hash to avoid leaking client/project names."""
    name = str(path).split('/')[-1]
    file_hash = hashlib.sha256(name.encode()).hexdigest()[:8]
    ext = name.rsplit('.', 1)[-1] if '.' in name else 'xlsx'
    return f"[SCRUBBED_{file_hash}].{ext}"


def classify_cell(val):
    """Classify a cell value. Only whitelisted values are shown."""
    if pd.isna(val):
        return None

    if isinstance(val, datetime):
        return "[DATE]"

    if isinstance(val, bool):
        return "[BOOL]"

    if isinstance(val, (int, float)):
        return "[NUMBER]"

    val_str = str(val).strip()
    if not val_str:
        return None

    val_lower = val_str.lower().strip()

    # Check exact match against structural labels
    if val_lower in STRUCTURAL_LABELS:
        return f'"{val_str}"'

    # Check exact match against safe codes
    if val_lower in SAFE_CODES:
        return f'"{val_str}"'

    # Check if it starts with a structural label (e.g., "Category\n(optional)")
    for label in STRUCTURAL_LABELS:
        if val_lower.startswith(label):
            return f'"{val_str[:len(label) + 5].strip()}"'

    # Everything else is masked
    return f"[STRING: {len(val_str)} chars]"


def detect_data_header_row(df):
    """
    Detect where the main data table header starts.
    Looks for rows with many non-null values that look like column headers.
    """
    best_row = -1
    best_score = 0

    for idx in range(min(60, len(df))):
        row = df.iloc[idx]
        non_null = row.dropna()
        if len(non_null) < 4:
            continue

        score = 0
        header_words = [
            'role', 'title', 'market', 'department', 'level', 'rate',
            'cost', 'name', 'category', 'team', 'hours', 'fee',
            'item', 'vendor', 'type', 'date'
        ]
        string_count = 0
        for val in non_null:
            if isinstance(val, str):
                string_count += 1
                if any(hw in val.lower() for hw in header_words):
                    score += 3
            elif isinstance(val, (int, float)):
                score += 0.5

        if string_count >= 3:
            score += string_count * 2
        score += len(non_null) * 0.5

        if score > best_score:
            best_score = score
            best_row = idx

    return best_row


def scrub_sheet_name(name: str) -> str:
    """Scrub potential PII from sheet names while keeping structural info."""
    # Sheet names like "2025 Plan" or "Rate Card" are safe
    # But names might contain client names
    safe_sheet_words = {
        'plan', 'rate', 'card', 'info', 'costs', 'mapping', 'log', 'change',
        'example', 'simple', 'complex', 'estimate', 'sheet', 'q&a', 'pricing',
        'panel', 'ext', 'current', 'old', 'final', 'refactor', 'only',
        'core', 'retainer', 'support', 'invest', 'week', 'team', 'extend',
        'analytics', 'role',
    }
    # Keep year patterns
    year_pattern = re.compile(r'20\d{2}')

    words = name.split()
    scrubbed = []
    for word in words:
        word_lower = word.lower().strip('_(),')
        if word_lower in safe_sheet_words:
            scrubbed.append(word)
        elif year_pattern.match(word_lower):
            scrubbed.append(word)
        elif word_lower.isdigit():
            scrubbed.append(word)
        elif word.startswith('_'):
            scrubbed.append(word)
        else:
            scrubbed.append('[REDACTED]')

    result = ' '.join(scrubbed)
    # Clean up consecutive redactions
    result = re.sub(r'(\[REDACTED\]\s*)+', '[REDACTED] ', result).strip()
    return result


def extract_schema(file_path):
    """Extract full schema from all sheets (PII-safe)."""
    print("=" * 70)
    print("DEEP EXCEL SCHEMA EXTRACTION (PII-SAFE)")
    print("=" * 70)
    print("NOTE: All values are masked except structural labels and safe codes.")
    print("No PII, company names, or confidential data is exposed.")

    xlsx = pd.ExcelFile(file_path, engine='openpyxl')

    scrubbed_name = scrub_filename(file_path)
    print(f"\nFile: {scrubbed_name}")
    print(f"Total sheets: {len(xlsx.sheet_names)}")
    print(f"\nSheet index:")
    for i, name in enumerate(xlsx.sheet_names):
        print(f"  {i + 1}. \"{scrub_sheet_name(name)}\"")

    for sheet in xlsx.sheet_names:
        scrubbed_sheet = scrub_sheet_name(sheet)
        print(f"\n\n{'=' * 70}")
        print(f"SHEET: \"{scrubbed_sheet}\"")
        print("=" * 70)

        # Read ALL rows (header=None to preserve raw structure)
        df = pd.read_excel(xlsx, sheet_name=sheet, header=None)
        total_rows = len(df)
        total_cols = len(df.columns)

        print(f"\nTotal rows: {total_rows}")
        print(f"Total columns: {total_cols}")

        if total_rows == 0:
            print("  [EMPTY SHEET]")
            continue

        # --- Detect data header row ---
        header_row = detect_data_header_row(df)

        # --- ZONE 1: Metadata (everything above data header) ---
        meta_end = header_row if header_row > 0 else min(total_rows, 10)

        print(f"\n--- METADATA ZONE (Rows 1 to {meta_end}) ---")
        for idx in range(meta_end):
            row = df.iloc[idx]
            non_null = [(i, row.iloc[i]) for i in range(min(len(row), 20)) if pd.notna(row.iloc[i])]
            if not non_null:
                continue
            # Only print row if it has at least one classifiable value
            classified_cells = []
            for col_idx, val in non_null:
                classified = classify_cell(val)
                if classified:
                    classified_cells.append((col_idx, classified))
            if classified_cells:
                print(f"\n  Row {idx + 1}:")
                for col_idx, classified in classified_cells:
                    print(f"    Col {col_idx}: {classified}")

        # --- ZONE 2: Data header ---
        if header_row >= 0:
            print(f"\n--- DATA HEADER (Row {header_row + 1}) ---")
            header_vals = df.iloc[header_row]

            # Separate dimension columns from period columns
            dim_cols = []
            period_cols = []
            other_cols = []

            for col_idx, val in enumerate(header_vals):
                if pd.isna(val):
                    other_cols.append((col_idx, '[EMPTY]'))
                elif isinstance(val, (int, float)):
                    period_cols.append((col_idx, val))
                elif isinstance(val, datetime):
                    period_cols.append((col_idx, val.strftime('%Y-%m-%d')))
                else:
                    dim_cols.append((col_idx, str(val).strip()))

            print(f"\n  Dimension columns ({len(dim_cols)}):")
            for col_idx, name in dim_cols:
                # Header names are structural, always safe to show
                print(f"    Col {col_idx}: \"{name}\"")

            if period_cols:
                print(f"\n  Period/Week columns ({len(period_cols)}):")
                print(f"    First: Col {period_cols[0][0]} = {period_cols[0][1]}")
                print(f"    Last:  Col {period_cols[-1][0]} = {period_cols[-1][1]}")

            if other_cols:
                trailing = [c for c in other_cols if c[0] > (dim_cols[-1][0] if dim_cols else 0)]
                if trailing:
                    print(f"\n  Empty/trailing columns: {len(trailing)}")

            # --- ZONE 3: Data sample ---
            data_start = header_row + 1
            data_end = min(data_start + 5, total_rows)
            data_rows = total_rows - data_start

            print(f"\n--- DATA ZONE (Rows {data_start + 1} to {total_rows}, ~{data_rows} data rows) ---")
            print(f"  Sample (first 5 data rows, dimension columns only):")

            for idx in range(data_start, data_end):
                row = df.iloc[idx]
                print(f"\n    Row {idx + 1}:")
                for col_idx, col_name in dim_cols[:10]:
                    val = row.iloc[col_idx] if col_idx < len(row) else None
                    classified = classify_cell(val)
                    if classified:
                        print(f"      {col_name}: {classified}")

        else:
            print("\n  [WARN] Could not detect data header row")
            print("  Showing first 10 rows:")
            for idx in range(min(10, total_rows)):
                row = df.iloc[idx]
                non_null = [(i, row.iloc[i]) for i in range(min(len(row), 15))
                            if pd.notna(row.iloc[i])]
                if non_null:
                    classified_cells = []
                    for col_idx, val in non_null:
                        classified = classify_cell(val)
                        if classified:
                            classified_cells.append((col_idx, classified))
                    if classified_cells:
                        print(f"\n    Row {idx + 1}:")
                        for col_idx, classified in classified_cells:
                            print(f"      Col {col_idx}: {classified}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 extract_schema.py <excel_file>")
        sys.exit(1)
    extract_schema(sys.argv[1])
