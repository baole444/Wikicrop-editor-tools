# Mock test for the sheet Apps Scripts

Runs an Apps Script against a JSON copy of the live sheet on your own machine, so a script can be
checked before it touches the real sheet. A stand-in replaces the parts of `SpreadsheetApp` the scripts
use. Needs Node.js (any current version) and the Python environment from `scripts/setup_env.*`.

The stand-in behaves like Google Sheets where it matters:

- `merge` throws if a cell other than the top one holds a value, as Sheets would drop it.
- A value written inside a merged range, below or right of its top-left cell, is dropped as Sheets
  drops it, and listed as a `MOCK: dropped write ...` line on the console and at the end of `mock_log.txt`.
- In `mock_multi.js`, rows inserted or deleted shift the rows below, and merges grow or shrink with them.
  `moveRows` throws if a merge touches the rows that shift, so a script must break merges in that span first.

## Which mock

| Mock            | Tabs                          | Dry-run switch flipped by `real`  | Use for                                   |
|-----------------|-------------------------------|-----------------------------------|-------------------------------------------|
| `mock_multi.js` | all three                     | `SheetConfig.DRY_RUN` or `DRY_RUN`| scripts that use `sheet_config.gs`, including the generated `fill_table3_block*.gs` |
| `mock_sheet.js` | `Thông tin giống & Canh tác`  | `const DRY_RUN = true;` only      | stand-alone scripts with their own `DRY_RUN` |

## Steps

Run from a scratch folder: the mocks write `mock_log.txt` (the script's log) and `mock_after*.json` into
the current directory. Paths below are relative to the project root; adjust them to where you run.

```bash
python scripts/fetch_live_sheet.py --keep
```

Three-tab mock, for scripts that use `SheetConfig` (load `sheet_config.gs` first, then the script):

```bash
python scripts/sheet-varieties/mock/mkgrid_multi.py "<snapshot.xlsx>" grid_multi.json
node scripts/sheet-varieties/mock/mock_multi.js scripts/sheet-common/sheet_config.gs,<script.gs> grid_multi.json dry <functionName>
node scripts/sheet-varieties/mock/mock_multi.js scripts/sheet-common/sheet_config.gs,<script.gs> grid_multi.json real <functionName>
```

One-tab mock (`empty_plan.js` is an empty prelude, for scripts that need no extra globals):

```bash
python scripts/sheet-varieties/mock/mkgrid.py "<snapshot.xlsx>" grid.json
node scripts/sheet-varieties/mock/mock_sheet.js scripts/sheet-varieties/mock/empty_plan.js <script.gs> grid.json dry <functionName>
node scripts/sheet-varieties/mock/mock_sheet.js scripts/sheet-varieties/mock/empty_plan.js <script.gs> grid.json real <functionName>
```

## Checking a run

Compare `mock_after.json` with the grid to see every changed cell. Then run the script again on the
result: a well-behaved script stops at its own checks on the second run, because the cells are no longer
in the state it expects.

`mock_multi.js` writes `mock_after_multi.json` in the same shape as its input, so it can be passed
straight back in. `mock_sheet.js` writes `cells` and merges as `{r, c, nr, nc}`, while its input wants
`grid` and merges as `[r, c, nr, nc]`, so convert it first:

```bash
python -c "import json;a=json.load(open('mock_after.json',encoding='utf-8'));json.dump({'grid':a['cells'],'merges':[[m['r'],m['c'],m['nr'],m['nc']] for m in a['merges']]},open('grid2.json','w',encoding='utf-8'),ensure_ascii=False)"
```

## Also here

`tcvn3_to_unicode.py` converts text extracted from PDFs in the legacy TCVN3 encoding (such as the
Cục Sở hữu trí tuệ geographical-indication records) to Unicode. See its docstring for the limits.
