#!/usr/bin/env python3
"""
Deep Excel Schema Extraction
=============================
Scans full sheet structure to identify all data zones:
  - Metadata zone (rows 0-N: client info, project details, summaries)
  - Data zone (row N+: actual staffing/rate data with headers)

Masks sensitive values but preserves structure for safe sharing.

Run: python3 extract_schema.py "your_file.xlsx" > schema.txt
"""

import sys
import re
from datetime import datetime
import pandas as pd


def classify_cell(val):
    """Classify a cell value without exposing sensitive data."""
    if pd.isna(val):
        return None
    if isinstance(val, datetime):
        return f"[DATE: {val.strftime('%Y-%m-%d')}]"
    if isinstance(val, bool):
        return f"[BOOL]"
    if isinstance(val, (int, float)):
        return f"[NUMBER]"
    val_str = str(val).strip()
    if not val_str:
        return None
    # Keep structural labels (they describe the sheet, not client data)
    structural_keywords = [
        'client', 'project', 'company', 'market', 'department', 'rate',
        'title', 'role', 'level', 'cost', 'fee', 'hour', 'bill', 'name',
        'category', 'start date', 'billing', 'cadence', 'margin', 'total',
        'required', 'optional', 'info', 'override', 'date', 'version',
        'team', 'specialization', 'number', 'question', 'answer', 'notes',
        'item', 'vendor', 'type', 'estimate', 'actual', 'investment',
        'passthrough', 'discount', 'blended', 'fixed', 'column', 'custom',
        'mapping', 'sub-department', 'holiday', 'log', 'published',
    ]
    val_lower = val_str.lower()
    # If it looks like a label/header, show it
    if any(kw in val_lower for kw in structural_keywords):
        return repr(val_str)[:80]
    # If it's short and looks like a code/enum, show it
    if len(val_str) <= 6 and val_str.isalpha():
        return repr(val_str)
    # Otherwise mask it
    return f"[STRING: {len(val_str)} chars]"


def detect_data_header_row(df):
    """
    Detect where the main data table header starts.
    Looks for rows with many non-null values that look like column headers.
    """
    best_row = -1
    best_score = 0

    for idx in range(min(40, len(df))):
        row = df.iloc[idx]
        non_null = row.dropna()
        if len(non_null) < 4:
            continue

        # Score based on: many columns + string values + header-like words
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
                score += 0.5  # Period columns are numbers

        # Prefer rows with many strings (headers) followed by numbers (weeks)
        if string_count >= 3:
            score += string_count * 2
        score += len(non_null) * 0.5

        if score > best_score:
            best_score = score
            best_row = idx

    return best_row


def extract_schema(file_path):
    """Extract full schema from all sheets."""
    print("=" * 70)
    print("DEEP EXCEL SCHEMA EXTRACTION (NO SENSITIVE DATA)")
    print("=" * 70)

    xlsx = pd.ExcelFile(file_path, engine='openpyxl')

    print(f"\nFile: {file_path}")
    print(f"Total sheets: {len(xlsx.sheet_names)}")
    print(f"\nSheet index:")
    for i, name in enumerate(xlsx.sheet_names):
        print(f"  {i + 1}. \"{name}\"")

    for sheet in xlsx.sheet_names:
        print(f"\n\n{'=' * 70}")
        print(f"SHEET: \"{sheet}\"")
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
            print(f"\n  Row {idx + 1}:")
            for col_idx, val in non_null:
                classified = classify_cell(val)
                if classified:
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
                    print(f"\n    Row {idx + 1}:")
                    for col_idx, val in non_null:
                        classified = classify_cell(val)
                        if classified:
                            print(f"      Col {col_idx}: {classified}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 extract_schema.py <excel_file>")
        sys.exit(1)
    extract_schema(sys.argv[1])
