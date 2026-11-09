// Mock of the SpreadsheetApp calls used by scripts that edit several tabs at once (values, merges,
// row insert and delete). Loads the JSON written by mkgrid_multi.py.
//
//   node mock_multi.js <script.gs>[,<script.gs>...] grid_multi.json dry|real <functionName>
//
// Several scripts are loaded into one scope in the order given, as Apps Script does with the files of a
// project. Put scripts/sheet-common/sheet_config.gs first for scripts that use SheetConfig. `real` turns
// off the dry run, whether it is a local `const DRY_RUN = true;` or `SheetConfig.DRY_RUN = true;`.
//
// Writes mock_log.txt and mock_after_multi.json (same shape as the input) to the current folder.
// insertRowsAfter and deleteRows behave as Google Sheets does: rows shift, a merge that spans an
// inserted row grows, a merge that loses a row shrinks. `merge` throws if a cell other than the
// top-left one holds a value, as Sheets would drop it. A value written to a cell inside a merge, other
// than its top-left cell, is dropped as Sheets drops it, with a `MOCK: dropped` line in the log and on
// the console.
const fs = require('fs'), vm = require('vm');
const [,, scriptFile, gridFile, dry, fn] = process.argv;
const data = JSON.parse(fs.readFileSync(gridFile, 'utf8'));
const logs = [];
const sheets = Object.entries(data).map(([name, t]) => ({
  name, width: t.width, rows: t.grid.map(r => r.slice()),
  merges: t.merges.map(([r, c, nr, nc]) => ({ r, c, nr, nc })),
}));
const blank = s => Array.from({ length: s.width }, () => '');
function get(s, r, c) { return r <= s.rows.length ? s.rows[r - 1][c - 1] : ''; }
const L = x => String.fromCharCode(64 + x);
const a1 = (r, c, nr, nc) => `${L(c)}${r}:${L(c + nc - 1)}${r + nr - 1}`;
const dropped = [];
function put(s, r, c, v) {
  const m = s.merges.find(m => r >= m.r && r < m.r + m.nr && c >= m.c && c < m.c + m.nc && (r !== m.r || c !== m.c));
  if (m) {
    if (String(v) !== '') dropped.push(`MOCK: dropped write to ${s.name} ${L(c)}${r}, inside merge ${a1(m.r, m.c, m.nr, m.nc)}`);
    return;
  }
  while (s.rows.length < r) s.rows.push(blank(s));
  s.rows[r - 1][c - 1] = v;
}
const overlap = (m, r, c, nr, nc) => !(m.r + m.nr - 1 < r || m.r > r + nr - 1 || m.c + m.nc - 1 < c || m.c > c + nc - 1);
function Range(s, r, c, nr = 1, nc = 1) {
  return {
    getRow: () => r, getColumn: () => c, getNumRows: () => nr, getNumColumns: () => nc,
    getA1Notation: () => a1(r, c, nr, nc),
    getValue: () => get(s, r, c),
    getValues: () => Array.from({ length: nr }, (_, i) => Array.from({ length: nc }, (_, j) => get(s, r + i, c + j))),
    getDisplayValues: () => Array.from({ length: nr }, (_, i) => Array.from({ length: nc }, (_, j) => String(get(s, r + i, c + j)))),
    setValue: v => { put(s, r, c, v); return Range(s, r, c, nr, nc); },
    setValues: vals => { vals.forEach((row, i) => row.forEach((v, j) => put(s, r + i, c + j, v))); return Range(s, r, c, nr, nc); },
    getMergedRanges: () => s.merges.filter(m => overlap(m, r, c, nr, nc)).map(m => Range(s, m.r, m.c, m.nr, m.nc)),
    breakApart: () => { s.merges = s.merges.filter(m => !overlap(m, r, c, nr, nc)); return Range(s, r, c, nr, nc); },
    merge: () => {
      if (s.merges.some(m => overlap(m, r, c, nr, nc))) throw new Error(`${s.name}: merge overlap ${a1(r, c, nr, nc)}`);
      for (let i = 0; i < nr; i++) for (let j = 0; j < nc; j++)
        if ((i || j) && String(get(s, r + i, c + j)) !== '') throw new Error(`${s.name}: merge would drop value at ${a1(r + i, c + j, 1, 1)}`);
      s.merges.push({ r, c, nr, nc }); return Range(s, r, c, nr, nc);
    },
    setVerticalAlignment: () => Range(s, r, c, nr, nc),
  };
}
function Sheet(s) {
  return {
    getName: () => s.name,
    getLastRow: () => { for (let r = s.rows.length; r >= 1; r--) if (s.rows[r - 1].some(v => String(v).trim() !== '')) return r; return 0; },
    getRange: (r, c, nr, nc) => Range(s, r, c, nr, nc),
    insertRowsAfter: (after, n) => {
      while (s.rows.length < after) s.rows.push(blank(s));
      s.rows.splice(after, 0, ...Array.from({ length: n }, () => blank(s)));
      s.merges = s.merges.map(m => {
        if (m.r > after) return Object.assign({}, m, { r: m.r + n });
        if (m.r + m.nr - 1 > after) return Object.assign({}, m, { nr: m.nr + n });
        return m;
      });
    },
    deleteRows: (row, n) => {
      s.rows.splice(row - 1, n);
      const end = row + n - 1;
      s.merges = s.merges.map(m => {
        const mEnd = m.r + m.nr - 1;
        if (mEnd < row) return m;
        if (m.r > end) return Object.assign({}, m, { r: m.r - n });
        const keep = m.nr - (Math.min(mEnd, end) - Math.max(m.r, row) + 1);
        return keep > 0 ? Object.assign({}, m, { r: Math.min(m.r, row), nr: keep }) : null;
      }).filter(Boolean);
    },
    // Rows move above row `dest` (numbered before the move). Throws if a merge touches the rows that
    // shift, where Sheets would refuse or split it: break merges in that span first.
    moveRows: (spec, dest) => {
      const row = spec.getRow(), n = spec.getNumRows();
      const lo = Math.min(row, dest), hi = Math.max(row + n - 1, dest - 1);
      const hit = s.merges.find(m => overlap(m, lo, 1, hi - lo + 1, s.width));
      if (hit) throw new Error(`${s.name}: moveRows over merge ${a1(hit.r, hit.c, hit.nr, hit.nc)}`);
      const moved = s.rows.splice(row - 1, n);
      s.rows.splice(dest > row ? dest - 1 - n : dest - 1, 0, ...moved);
    },
  };
}
const ss = { getSheetByName: n => { const s = sheets.find(x => x.name === n); return s ? Sheet(s) : null; } };
const ctx = { SpreadsheetApp: { getActive: () => ss, flush: () => {} }, Logger: { log: m => logs.push(String(m)) }, console };
vm.createContext(ctx);
let code = scriptFile.split(',').map(f => fs.readFileSync(f, 'utf8')).join('\n');
if (dry === 'real') code = code.replace('const DRY_RUN = true;', 'const DRY_RUN = false;')
  .replace(/^(\s*)SheetConfig\.DRY_RUN = true;/m, '$1SheetConfig.DRY_RUN = false;');
vm.runInContext(code + '\n' + fn + '();', ctx);
dropped.forEach(d => console.log(d));
fs.writeFileSync('mock_log.txt', logs.concat(dropped).join('\n'));
const out = {};
sheets.forEach(s => { out[s.name] = { width: s.width, grid: s.rows, merges: s.merges.map(m => [m.r, m.c, m.nr, m.nc]) }; });
fs.writeFileSync('mock_after_multi.json', JSON.stringify(out));
console.log('ok');
