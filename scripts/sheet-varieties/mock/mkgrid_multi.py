"""Dump all three tabs of a snapshot (values and merges) to JSON for mock_multi.js.

    python scripts/sheet-varieties/mock/mkgrid_multi.py "<snapshot.xlsx>" grid_multi.json
"""
import json
import sys

import openpyxl

sys.stdout.reconfigure(encoding='utf-8')

TABS = {'Danh mục giống cây': 8, 'Thông tin loài': 9, 'Thông tin giống & Canh tác': 7}

wb = openpyxl.load_workbook(sys.argv[1])
out = {}
for name, width in TABS.items():
    ws = wb[name]
    last = max(i for i in range(1, ws.max_row + 1)
               if any(ws.cell(i, c).value not in (None, '') for c in range(1, width + 1)))
    grid = [['' if ws.cell(r, c).value is None else ws.cell(r, c).value for c in range(1, width + 1)]
            for r in range(1, last + 1)]
    merges = [[m.min_row, m.min_col, m.max_row - m.min_row + 1, m.max_col - m.min_col + 1]
              for m in ws.merged_cells.ranges if m.min_col <= width]
    out[name] = {'width': width, 'grid': grid, 'merges': merges}
    print(name, len(grid), 'rows', len(merges), 'merges')
json.dump(out, open(sys.argv[2], 'w', encoding='utf-8'), ensure_ascii=False, default=str)
