/**
 * Insert variety images into column G (Hình ảnh) of `Thông tin loài`, matched by name.
 *
 * Source: the `upload/` folder made by scripts/export_variety_images.py - one JPEG per
 * catalogue name, ≤ 1000 px, ≤ ~250 KB. Images go *into* the cell (CellImage), built from
 * a base64 copy of the Drive file, so the Drive folder does not need to be shared.
 *
 * Needs: scripts/sheet-common/sheet_config.gs (SheetConfig, sheetRows_, sheetKey_).
 *
 * Setup (once):
 *   1. Drag the `upload` folder of the variety-image folder (WIKICROP_IMAGES) into Google
 *      Drive. Open it and copy the folder ID from the URL (…/folders/<ID>).
 *   2. In the sheet: Extensions → Apps Script. Add script files and paste `sheet_config.gs` and
 *      this file into them (sheet_config.gs once per project). Set `folderId` in insertImages, save.
 *   3. Run `insertImages` with DRY_RUN = true first. Read the log (View → Logs / Execution log):
 *      it lists what would go where, and which files and rows found no match.
 *   4. Set DRY_RUN = false and run again. Approve the Drive + Sheets permissions the first time.
 *      If it stops at the time limit, just run it again — filled cells are skipped.
 *
 * Matching: file name (without extension) vs column D (variety) over the whole sheet, both halves;
 * for rows with no variety, vs column C (species-only rows such as Rau đắng biển). sheetKey_
 * ignores case, spaces, hyphens, dots, a leading "Giống", and characters not allowed in file
 * names, so `ĐX-044` matches `ĐX044` and `Sả hoa hồng (Sả hồng / Palmarosa)` matches its file
 * name. Rename D to the catalogue name first for the best hit rate.
 */

// File name (catalogue name) -> other names the same plant has in this sheet's column D.
// For rows the sheet's editors named differently from the catalogue; tried after the file name itself.
const IMAGE_ALIASES = {
  'Nếp Phú Tân': ['Lúa nếp Phú Tân'],
  'OMĐN 25-20': ['OMDN 25-20'],
  'Thầu dầu (Ricinus communis)': ['Thầu dầu'],
};

function insertImages() {
  // Run settings: set them here, then run.
  SheetConfig.DRY_RUN   = true;    // true = only log the plan
  SheetConfig.OVERWRITE = false;   // true = replace images already in G
  const folderId       = 'PASTE_DRIVE_FOLDER_ID_HERE';
  const LIMIT          = 0;        // >0 = stop after this many inserts (for a first test)
  const ROW_HEIGHT     = 0;        // >0 = set the height (px) of rows that receive an image
  const TIME_BUDGET_MS = 5 * 60 * 1000;   // Apps Script stops at 6 min; leave a margin

  const started = Date.now();
  const { DRY_RUN, OVERWRITE } = SheetConfig, IMAGE_COL = SheetConfig.COL.IMAGE;
  const { sheet, rowsByKey, value } = sheetRows_({ speciesFallback: true, withValues: true });
  const placed = {};   // rows that received an image in this run
  const hasImage = row => {
    if (placed[row]) return true;
    const v = value(row, IMAGE_COL);
    return v !== '' && v !== null;
  };

  const files = DriveApp.getFolderById(folderId).getFiles();
  const done = [], skipped = [], unmatched = [], failed = [];
  let inserted = 0, stoppedEarly = false;
  while (files.hasNext()) {
    if (Date.now() - started > TIME_BUDGET_MS || (LIMIT && inserted >= LIMIT)) { stoppedEarly = true; break; }
    const file = files.next();
    const mime = file.getMimeType();
    if (!/^image\//.test(mime)) continue;
    const base = file.getName().replace(/\.[^.]+$/, '');
    const rows = [base].concat(IMAGE_ALIASES[base.normalize('NFC')] || [])
      .map(n => rowsByKey[sheetKey_(n)]).find(r => r);
    if (!rows) { unmatched.push(base); continue; }
    for (const row of rows) {
      if (hasImage(row) && !OVERWRITE) { skipped.push(`${base} → R${row} (G already filled)`); continue; }
      if (DRY_RUN) { done.push(`${base} → R${row}`); continue; }
      try {
        const dataUrl = `data:${mime};base64,${Utilities.base64Encode(file.getBlob().getBytes())}`;
        const img = SpreadsheetApp.newCellImage().setSourceUrl(dataUrl).setAltTextTitle(base).build();
        sheet.getRange(row, IMAGE_COL).setValue(img);
        if (ROW_HEIGHT) sheet.setRowHeight(row, ROW_HEIGHT);
        placed[row] = true;
        done.push(`${base} → R${row}`);
        inserted++;
      } catch (e) {
        failed.push(`${base} → R${row}: ${e.message}`);
      }
    }
  }

  const label = DRY_RUN ? 'would insert' : 'inserted';
  Logger.log(`${label}: ${done.length}\n  ` + done.join('\n  '));
  Logger.log(`skipped (G already filled): ${skipped.length}\n  ` + skipped.join('\n  '));
  Logger.log(`files with no matching row: ${unmatched.length}\n  ` + unmatched.join('\n  '));
  if (failed.length) Logger.log(`failed: ${failed.length}\n  ` + failed.join('\n  '));
  if (stoppedEarly) Logger.log('Stopped early (time budget or LIMIT). Run again to continue.');
  SpreadsheetApp.getActive().toast(
    `${label} ${done.length} · skipped ${skipped.length} · no row ${unmatched.length}` +
    (failed.length ? ` · failed ${failed.length}` : '') + (stoppedEarly ? ' · run again' : ''),
    'Insert images', 10);
}
