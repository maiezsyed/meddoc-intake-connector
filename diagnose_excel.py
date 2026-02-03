#!/usr/bin/env python3
"""
Diagnostic script to inspect Excel file structure.
Run: python diagnose_excel.py "your_file.xlsx"
"""

import sys
import pandas as pd

def diagnose(file_path):
    print("=" * 60)
    print("EXCEL FILE DIAGNOSTIC")
    print("=" * 60)

    xlsx = pd.ExcelFile(file_path, engine='openpyxl')

    print(f"\nFile: {file_path}")
    print(f"\nSheets found ({len(xlsx.sheet_names)}):")
    for i, name in enumerate(xlsx.sheet_names):
        print(f"  {i+1}. \"{name}\"")

    for sheet in xlsx.sheet_names:
        print("\n" + "=" * 60)
        print(f"SHEET: \"{sheet}\"")
        print("=" * 60)

        # Read first 35 rows to see structure
        df = pd.read_excel(xlsx, sheet_name=sheet, nrows=35, header=None)
        print(f"\nFirst 35 rows (raw):")
        print(df.to_string())

        print(f"\nShape: {df.shape}")
        print(f"Columns types: {[type(c).__name__ for c in df.columns]}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python diagnose_excel.py <excel_file>")
        sys.exit(1)
    diagnose(sys.argv[1])
