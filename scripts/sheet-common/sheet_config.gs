/**
 * Shared settings and row lookup for the Apps Scripts that edit the project's Google Sheet, e.g.
 *   sheet-images/insert_images.gs               insertImages   Thông tin loài, column G
 *   sheet-varieties/fill_table3_block*.gs       (generated)    Thông tin giống & Canh tác
 * Any new script that edits the sheet should read its tab names, columns and run switches from here.
 *
 * The sheet has three tabs: `Danh mục giống cây` (catalogue), `Thông tin loài` (species) and
 * `Thông tin giống & Canh tác` (varieties and cultivation). Each lists crop groups 1-6 and 7-13 in
 * two parts. The scripts here edit groups 7-13 only: the part below the last section header, and in
 * the third tab the rows between the blue header band and the label row that starts groups 1-6.
 *
 * The top-level SHEET_NAME and COL are `Thông tin loài`. CATALOGUE and VARIETY_INFO hold the layout
 * of `Danh mục giống cây` and `Thông tin giống & Canh tác`.
 *
 * Paste this file into the Apps Script project once, beside whichever scripts you use. Apps Script
 * loads every file of a project into one global scope before a function runs, so the scripts can
 * all read SheetConfig, whatever order the files are in.
 *
 * SheetConfig works like a class with public static fields: one shared object, readable and
 * settable from any script. The sheet layout below is set here, once. The run switches (DRY_RUN,
 * OVERWRITE) are set by each script at the top of its entry function, before it does anything:
 *
 *     function fillReasons() {
 *       SheetConfig.DRY_RUN   = true;
 *       SheetConfig.OVERWRITE = false;
 *       ...
 *
 * Every run starts from a fresh load of the files, so a value one script sets never carries over
 * into the next run of another. The object is sealed: a misspelt field name is not added, so a
 * typo leaves the safe default in place rather than creating a new setting.
 */
const SheetConfig = Object.seal({
  SHEET_NAME:     'Thông tin loài',
  SECTION_HEADER: 'Danh mục giống',   // column A of the header row; groups 7-13 start below the last one
  FIRST_ROW:      2,                  // first data row of the whole sheet
  SKIP_NAMES:     ['Danh sách giống', 'Bảng phân loại loài'],   // header text in C/D, never a plant
  COL: Object.freeze({
    GROUP: 1,        // A  Danh mục giống
    INTRO: 2,        // B  Giới thiệu
    SPECIES: 3,      // C  Bảng phân loại loài
    VARIETY: 4,      // D  Danh sách giống
    DESCRIPTION: 5,  // E  Chú thích
    REFERENCE: 6,    // F  Tài liệu tham khảo
    IMAGE: 7,        // G  Hình ảnh
    EDITOR: 8,       // H  Người sửa
    REASON: 9,       // I  Lý do sửa
  }),

  // The other two tabs. The fields above stay the `Thông tin loài` layout, so older scripts keep working.
  CATALOGUE: Object.freeze({
    SHEET_NAME: 'Danh mục giống cây',
    COL: Object.freeze({
      TAXON_ID: 1,     // A  Botany taxonomy ID (BT01 ... on the group rows)
      GROUP: 2,        // B  Botany taxonomy
      SPECIES: 3,      // C  Species Name (species header rows only)
      COUNT: 4,        // D  No of varieties/crops
      VARIETY: 5,      // E  Varieties (>=300)
      SOURCE: 6,       // F  Nguồn phụ
      EDITOR: 7,       // G  Người sửa
      REASON: 8,       // H  Lý do sửa
    }),
  }),
  VARIETY_INFO: Object.freeze({
    SHEET_NAME: 'Thông tin giống & Canh tác',
    HEADER: Object.freeze(['Loài', 'Giống']),   // A and B of the blue band row; groups 7-13 start below it
    SCOPE_END_LABEL: 'Hàng thuộc nhóm 1-6',      // start of A on the label row that ends groups 7-13
    COL: Object.freeze({
      SPECIES: 1,      // A  Loài (first row of each species block)
      VARIETY: 2,      // B  Giống
      INFO: 3,         // C  Thông tin giống
      CULTIVATION: 4,  // D  Thông tin kỹ thuật canh tác
      SOURCE: 5,       // E  Nguồn phụ
      EDITOR: 6,       // F  Người sửa
      REASON: 7,       // G  Lý do sửa
    }),
  }),

  // Run switches: safe defaults, set by each script before it runs.
  DRY_RUN:   true,    // true = only log the plan
  OVERWRITE: false,   // true = replace what is already in the target cells
  EDITOR:    '',      // name written to the Người sửa column of changed rows, '' leaves it alone
  WRITE_REASONS: true,   // append the reason for each change to the Lý do sửa column
});

// Name key for matching: case, spaces, hyphens, dots, a leading "Giống", and characters not allowed
// in file names (the image exporter turns them into "-") are ignored. Both the sheet names and the
// data names go through it. Mirrored by key() in build_description_data.py.
function sheetKey_(s) {
  return String(s || '')
    .normalize('NFC')
    .toLowerCase()
    .replace(/[\\/:*?"<>|]/g, '-')
    .replace(/^[\s\-–•]+/, '')
    .replace(/^giống\s+/, '')
    .replace(/[\s\-.]/g, '');
}

/**
 * Reads the sheet and maps each name key to the sheet rows it sits on now. No row numbers are
 * stored anywhere; they are found here on every run.
 *   ourHalf          true = only the rows below the last SECTION_HEADER row (groups 7–13);
 *                    false = the whole sheet from FIRST_ROW
 *   speciesFallback  true = a row with no variety is keyed by its species (column C)
 *   mergedCols       columns whose merged blocks should read as filled on every row they cover
 *   withValues       true = also read raw values (needed to see in-cell images)
 * Returns { sheet, rowsByKey, cell(row, col) → trimmed display text, value(row, col) → raw value }.
 */
function sheetRows_(opts) {
  opts = opts || {};
  const C = SheetConfig.COL;
  const sheet = SpreadsheetApp.getActive().getSheetByName(SheetConfig.SHEET_NAME);
  if (!sheet) throw new Error(`No sheet named "${SheetConfig.SHEET_NAME}".`);
  const last = sheet.getLastRow(), width = C.REASON;
  const range = sheet.getRange(1, 1, last, width);
  const shown = range.getDisplayValues();
  const values = opts.withValues ? range.getValues() : null;

  // A merged block holds its text in the top cell only; give every row in it that text.
  (opts.mergedCols || []).forEach(col => {
    sheet.getRange(1, col, last, 1).getMergedRanges().forEach(m => {
      const top = m.getRow() - 1;
      for (let i = top + 1; i < top + m.getNumRows(); i++) shown[i][col - 1] = shown[top][col - 1];
    });
  });

  let start = SheetConfig.FIRST_ROW - 1;
  if (opts.ourHalf) {
    // The sheet repeats the header row for each half; ours starts below the last one.
    start = -1;
    shown.forEach((r, i) => { if (String(r[C.GROUP - 1]).trim() === SheetConfig.SECTION_HEADER) start = i + 1; });
    if (start < 0) throw new Error(`No "${SheetConfig.SECTION_HEADER}" header in column A; nothing written.`);
  }

  const rowsByKey = {};
  for (let i = start; i < shown.length; i++) {
    const variety = String(shown[i][C.VARIETY - 1]).trim();
    const name = variety || (opts.speciesFallback ? String(shown[i][C.SPECIES - 1]).trim() : '');
    if (!name || SheetConfig.SKIP_NAMES.indexOf(name) >= 0) continue;
    const k = sheetKey_(name);
    (rowsByKey[k] = rowsByKey[k] || []).push(i + 1);
  }
  return {
    sheet, rowsByKey,
    cell:  (row, col) => String(shown[row - 1][col - 1]).trim(),
    value: (row, col) => values[row - 1][col - 1],
  };
}

// Writes the log lines shared by the fill scripts: one header line, then each non-empty list.
function logLists_(head, lists) {
  Logger.log(`\n${SheetConfig.DRY_RUN ? 'DRY RUN. ' : ''}${head}`);
  Object.keys(lists).forEach(label => {
    const items = lists[label];
    if (items.length) Logger.log(`${label} (${items.length}):\n  ${items.join('\n  ')}`);
  });
}
