// Mock of the SpreadsheetApp calls used by scripts that edit the `Thông tin giống & Canh tác` tab
// (getRange, getValues, setValue(s), merge, getMergedRanges, deleteRows, copyTo). Loads the JSON
// written by mkgrid.py.
//
//   node mock_sheet.js <prelude.js> <script.gs> grid.json dry|real <functionName>
//
// <prelude.js> is run before the script, in the same scope: use it to define globals the script
// expects, or pass empty_plan.js (empty) when there are none. `real` turns off the script's dry run.
// Writes mock_log.txt (the script's log) and mock_after.json (cells and merges) to the current folder.
// A value written inside a merged range below or right of its top cell is dropped, as Google Sheets
// drops it, and reported as a `MOCK: dropped write ...` line.
const fs = require('fs'), vm = require('vm');
const [,, planFile, scriptFile, gridFile, dry, fn] = process.argv;
const data = JSON.parse(fs.readFileSync(gridFile, 'utf8'));
const logs = [];
// Cell = {v, fmt}; fmt tags the origin row, so moved formats can be traced.
function mkSheet(name, rows, cols) {
  const s = { name, cells: [], merges: [], maxRows: rows, maxCols: cols };
  for (let r = 0; r < rows; r++) { s.cells.push([]); for (let c = 0; c < cols; c++) s.cells[r].push({ v: '', fmt: '' }); }
  return s;
}
const main = mkSheet(data.grid.length > 0 ? 'Thông tin giống & Canh tác' : '', data.grid.length, 7);
data.grid.forEach((row, r) => row.forEach((v, c) => { main.cells[r][c] = { v, fmt: v === '' ? '' : `orig${r + 1}` }; }));
main.merges = data.merges.map(([r, c, nr, nc]) => ({ r, c, nr, nc }));
const sheets = [main];
function cell(s, r, c) {
  while (s.cells.length < r) s.cells.push(Array.from({ length: s.maxCols }, () => ({ v: '', fmt: '' })));
  return s.cells[r - 1][c - 1];
}
function a1(r, c, nr, nc) { const L = x => String.fromCharCode(64 + x); return `${L(c)}${r}:${L(c + nc - 1)}${r + nr - 1}`; }
function overlap(m, r, c, nr, nc) { return !(m.r + m.nr - 1 < r || m.r > r + nr - 1 || m.c + m.nc - 1 < c || m.c > c + nc - 1); }
// A value written inside a merge, below or right of its top-left cell, is dropped, as Google Sheets drops it.
const dropped = [];
function put(s, r, c, v) {
  const m = s.merges.find(m => r >= m.r && r < m.r + m.nr && c >= m.c && c < m.c + m.nc && (r !== m.r || c !== m.c));
  if (m) {
    if (String(v) !== '') dropped.push(`MOCK: dropped write to ${String.fromCharCode(64 + c)}${r}, inside merge ${a1(m.r, m.c, m.nr, m.nc)}`);
    return;
  }
  cell(s, r, c).v = v;
}
function Range(s, r, c, nr = 1, nc = 1) {
  return {
    getRow: () => r, getColumn: () => c, getNumRows: () => nr, getNumColumns: () => nc,
    getA1Notation: () => a1(r, c, nr, nc),
    getValues: () => Array.from({ length: nr }, (_, i) => Array.from({ length: nc }, (_, j) => cell(s, r + i, c + j).v)),
    setValues: vals => { vals.forEach((row, i) => row.forEach((v, j) => put(s, r + i, c + j, v))); },
    setValue: v => { put(s, r, c, v); },
    getMergedRanges: () => s.merges.filter(m => overlap(m, r, c, nr, nc)).map(m => Object.assign(Range(s, m.r, m.c, m.nr, m.nc), {})),
    breakApart: () => { s.merges = s.merges.filter(m => !(m.r === r && m.c === c && m.nr === nr && m.nc === nc)); },
    merge: () => {
      if (s.merges.some(m => overlap(m, r, c, nr, nc))) throw new Error('merge overlap ' + a1(r, c, nr, nc));
      for (let i = 0; i < nr; i++) for (let j = 0; j < nc; j++) if ((i || j) && cell(s, r + i, c + j).v !== '') throw new Error('merge would drop value at ' + a1(r + i, c + j, 1, 1));
      s.merges.push({ r, c, nr, nc }); return Range(s, r, c, nr, nc);
    },
    setVerticalAlignment: () => Range(s, r, c, nr, nc),
    setFontWeight: () => { cell(s, r, c).fmt += '+bold'; },
    clear: () => { for (let i = 0; i < nr; i++) for (let j = 0; j < nc; j++) cell(s, r + i, c + j).v = '', cell(s, r + i, c + j).fmt = ''; },
    copyTo: (dest, opts) => {
      const d = dest; const dr = d.getRow(), dc = d.getColumn();
      if (s.merges.some(m => overlap(m, r, c, nr, nc))) throw new Error('copy of merged source ' + a1(r, c, nr, nc));
      const src = Array.from({ length: nr }, (_, i) => Array.from({ length: nc }, (_, j) => Object.assign({}, cell(s, r + i, c + j))));
      const ds = d._s;
      src.forEach((row, i) => row.forEach((x, j) => {
        const t = cell(ds, dr + i, dc + j);
        if (opts && opts.formatOnly) t.fmt = x.fmt; else { t.v = x.v; t.fmt = x.fmt; }
      }));
    },
    _s: s,
  };
}
function Sheet(s) {
  return {
    getLastRow: () => { for (let r = s.cells.length; r >= 1; r--) if (s.cells[r - 1].some(x => String(x.v).trim() !== '')) return r; return 0; },
    getMaxRows: () => s.cells.length, getMaxColumns: () => s.maxCols,
    insertRowsAfter: (_, n) => { for (let i = 0; i < n; i++) s.cells.push(Array.from({ length: s.maxCols }, () => ({ v: '', fmt: '' }))); },
    insertColumnsAfter: () => { throw new Error('cols'); },
    getRange: (r, c, nr, nc) => Range(s, r, c, nr, nc),
    getName: () => s.name,
    deleteRows: (r, n) => {
      s.cells.splice(r - 1, n);
      s.merges = s.merges.filter(m => m.r + m.nr - 1 < r || m.r > r + n - 1).map(m => m.r > r + n - 1 ? Object.assign({}, m, { r: m.r - n }) : m);
      if (s.merges.some(m => m.r < r && m.r + m.nr - 1 >= r)) throw new Error('merge spans deleted rows');
    },
  };
}
const ss = {
  getSheetByName: n => { const s = sheets.find(x => x.name === n); return s ? Sheet(s) : null; },
  insertSheet: n => { const s = mkSheet(n, 1000, 26); sheets.push(s); return Sheet(s); },
  deleteSheet: sh => { const i = sheets.findIndex(x => x.name === sh.getName()); sheets.splice(i, 1); },
};
const ctx = { SpreadsheetApp: { getActive: () => ss, flush: () => {} }, Logger: { log: m => logs.push(m) }, console };
vm.createContext(ctx);
let code = fs.readFileSync(scriptFile, 'utf8');
if (dry === 'real') code = code.replace('const DRY_RUN = true;', 'const DRY_RUN = false;');
vm.runInContext(fs.readFileSync(planFile, 'utf8') + '\n' + code + '\n' + (fn || 'reorganizeTable3') + '();', ctx);
dropped.forEach(d => console.log(d));
fs.writeFileSync('mock_log.txt', logs.concat(dropped).join('\n'));
fs.writeFileSync('mock_after.json', JSON.stringify({ cells: main.cells.map(r => r.map(x => x.v)), fmt: main.cells.map(r => r.map(x => x.fmt)), merges: main.merges, sheets: sheets.map(s => s.name) }));
console.log('ok; sheets left:', sheets.map(s => s.name).join(', '));
