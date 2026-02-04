#!/usr/bin/env python3
"""
Extract Excel schema (structure only, no data) for safe sharing.
Run: python extract_schema.py "your_file.xlsx" > schema.txt
"""

import sys
import pandas as pd

def extract_schema(file_path):
    print("=" * 70)
    print("EXCEL SCHEMA EXTRACTION (NO ACTUAL DATA)")
    print("=" * 70)

    xlsx = pd.ExcelFile(file_path, engine='openpyxl')

    print(f"\nFile: {file_path}")
    print(f"Total sheets: {len(xlsx.sheet_names)}")

    for sheet in xlsx.sheet_names:
        print(f"\n{'=' * 70}")
        print(f"SHEET: \"{sheet}\"")
        print("=" * 70)

        # Read just enough to get structure
        df = pd.read_excel(xlsx, sheet_name=sheet, nrows=5, header=None)

        print(f"\nDimensions: ~{df.shape[1]} columns")
        print(f"\nFirst 5 rows (to identify header location):")

        for idx, row in df.iterrows():
            print(f"\n  Row {idx}:")
            for col_idx, val in enumerate(row[:15]):  # First 15 columns
                if pd.notna(val):
                    val_type = type(val).__name__
                    # Mask actual values, show structure only
                    if isinstance(val, str) and len(val) > 30:
                        val_preview = f"[STRING: {len(val)} chars]"
                    elif isinstance(val, (int, float)):
                        val_preview = f"[{val_type.upper()}]"
                    else:
                        val_preview = repr(val)[:50]
                    print(f"    Col {col_idx}: {val_preview}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python extract_schema.py <excel_file>")
        sys.exit(1)
    extract_schema(sys.argv[1])
