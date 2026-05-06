import sys
import os
import pandas as pd
from openpyxl import load_workbook

def inspect(path, max_rows=3):
    print(f"\n=== {path} ===")
    if not os.path.exists(path):
        print("MISSING")
        return
    try:
        ext = os.path.splitext(path)[1].lower()
        if ext == ".xls":
            xl = pd.ExcelFile(path, engine="xlrd")
        else:
            xl = pd.ExcelFile(path, engine="openpyxl")
        for s in xl.sheet_names:
            try:
                df = xl.parse(s, nrows=max_rows)
                print(f"--- Sheet: {s!r} | shape head: {df.shape} ---")
                print("Columns:", list(df.columns))
                print(df.head(max_rows).to_string(max_cols=12, max_colwidth=30))
            except Exception as e:
                print(f"Sheet {s!r} read error: {e}")
    except Exception as e:
        print(f"ERR: {e}")

paths = sys.argv[1:]
for p in paths:
    inspect(p)
