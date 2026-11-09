"""Dump the `Thông tin giống & Canh tác` tab of a snapshot (values and merges) to JSON for mock_sheet.js.

    python scripts/sheet-varieties/mock/mkgrid.py "<snapshot.xlsx>" grid.json

Output: {"grid": [[A..G] per row], "merges": [[row, col, n_rows, n_cols], ...]}, rows and columns 1-based.
Merges are read from the workbook XML, as openpyxl's read-only mode does not expose them. The tab is
expected to be the third sheet of the workbook (xl/worksheets/sheet3.xml), as in the live sheet's export.
"""
import json
import re
import sys
import zipfile

import openpyxl

if len(sys.argv) != 3:
    sys.exit(__doc__)
f = sys.argv[1]
wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
ws = wb["Thông tin giống & Canh tác"]
grid = [[("" if v is None else v) for v in (list(r) + [None]*7)[:7]] for r in ws.iter_rows(values_only=True)]
xml = zipfile.ZipFile(f).read("xl/worksheets/sheet3.xml").decode()
merges = re.findall(r'<mergeCell ref="([A-Z]+)(\d+):([A-Z]+)(\d+)"', xml)
m = [[int(r1), ord(c1)-64, int(r2)-int(r1)+1, ord(c2)-ord(c1)+1] for c1, r1, c2, r2 in merges]
json.dump({"grid": grid, "merges": m}, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, default=str)
print(len(grid), len(m))
