"""Example drafts module for build_table3_block.py: every field a block must define, with placeholder content.

Copy this file, rename it (e.g. table3_block8_drafts.py), and replace the placeholder rows with real ones. A block
is one batch of edits to the `Thông tin giống & Canh tác` tab, groups 7-13:

    C  Thông tin giống                 the variety's description, strengths and weaknesses
    D  Thông tin kỹ thuật canh tác     cultivation, per variety or shared by the whole species (a merge)
    E  Nguồn phụ                       the sources, one per line, written by the build from SOURCES
    G  Lý do sửa                       the reason for each change, written by the build

Then:

    python scripts/fetch_live_sheet.py --keep
    python scripts/sheet-varieties/build_table3_block.py table3_block8_drafts "<snapshot.xlsx>"

The build checks the drafts against the snapshot and stops with a list of problems if anything is off: a variety
not found exactly once, a `fill` cell that is no longer empty, a `replace` cell that no longer holds `was`, text that
breaks the house style (no semicolons, plain hyphens, closed ranges `85-90`, no double spaces). With the placeholder
names below it stops at the first check, which is the expected result for this example.

When it passes, it writes the review file REVIEW (read it before uploading) and the Apps Script GS. Test GS with
mock/mock_multi.js, then paste it into the sheet's Apps Script project beside sheet_config.gs, run FN with
SheetConfig.DRY_RUN = true, read the log, and run again with DRY_RUN = false.

All Vietnamese text follows the writing standard on the wiki (`Wikicrop:Tiêu chuẩn viết bài`): plain hyphen for
every dash, no semicolons, decimal comma and thousands point, yields in kg/ha or tấn/ha, a source for every figure,
and a weakness for every variety where a source gives one.
"""

# Shown as the review file's heading and in the generated script's header.
TITLE = 'Table 3, example block: two fills, one replace, one append, one shared D'
# Name of the generated Apps Script function, and of its file (written beside build_table3_block.py).
FN = 'fillTable3BlockExample'
GS = 'fill_table3_block_example.gs'
# Review file name, written to WIKICROP_SOURCE_BOOKS (see project_config.py).
REVIEW = 'table3-fill-block-example.md'

# Source key -> the line written to E. One source per line in E, so each key is one full citation:
# publisher (date). Title. URL. Keys are used in CELLS[...]['src'] and MERGES[...]['src'].
SOURCES = {
    'vien_a': 'Viện Ví dụ A (01/01/2026). Giới thiệu giống Ví dụ 1. https://example.org/giong-vi-du-1',
    'khuyennong_b': 'Trung tâm Khuyến nông Ví dụ B (15/03/2025). Quy trình kỹ thuật trồng cây ví dụ. https://example.org/quy-trinh',
}

# One dict per cell to write.
#   B       the variety exactly as in column B (matched after Unicode NFC and trimming)
#   col     'C' or 'D'
#   mode    'fill'     the cell must be empty
#           'replace'  the cell must still contain `was`, the whole text is replaced by `text`
#           'append'   `text` is added after the current text as a new paragraph (the build records the
#                      current text, and the upload stops if the cell changed since)
#   src     source keys for E, [] for a format-only fix that leaves E alone
#   text    the new text. Use '\n' between paragraphs
# Optional:
#   was       for 'replace': the text the cell must still hold
#   was_of    for 'replace' without a reason: the variety the misplaced old text belonged to (used in the default reason)
#   show_was  True to print the old text in the review file too
#   reason    the G text for this cell, instead of the default reason for its mode
CELLS = [
    {
        'B': 'Giống ví dụ 1',
        'col': 'C',
        'mode': 'fill',
        'src': ['vien_a'],
        'text': 'Giống ví dụ 1 có thời gian sinh trưởng 95-100 ngày, năng suất 6-7 tấn/ha. '
                'Nhược điểm: nhiễm nhẹ bệnh đạo ôn trong vụ Đông Xuân.',
    },
    {
        'B': 'Giống ví dụ 2',
        'col': 'C',
        'mode': 'replace',
        'src': ['vien_a'],
        'was': 'Nội dung cũ thuộc về giống khác do lệch hàng.',
        'was_of': 'Giống ví dụ 3',
        'show_was': True,
        'text': 'Giống ví dụ 2 cho trái nặng 1,2-1,5 kg, thịt vàng, ít xơ.',
    },
    {
        'B': 'Giống ví dụ 3',
        'col': 'C',
        'mode': 'append',
        'src': ['vien_a'],
        'text': 'Nhược điểm: dễ nứt trái khi mưa nhiều vào giai đoạn chín.',
    },
]

# Species whose varieties share one cultivation text in D. The build writes `text` into the D cell of `first`,
# clears D on the rows below, and merges D from `first` to `last` (all rows of the species block).
#   species  the species as at the start of column A on its first row
#   first, last   the first and last variety (column B) of the block
#   filled   the varieties whose D holds text now, in row order ([] if all D are empty). The build checks this
#   src, text     as in CELLS
MERGES = [
    {
        'species': 'Loài ví dụ',
        'first': 'Giống ví dụ 4',
        'last': 'Giống ví dụ 5',
        'filled': [],
        'src': ['khuyennong_b'],
        'text': 'Thời vụ: trồng đầu mùa mưa, tháng 5-6.\n'
                'Mật độ: 400-500 cây/ha, khoảng cách 5 x 5 m.\n'
                'Phân bón: theo quy trình của nguồn ghi ở cột E.',
    },
]

# Optional. Rows whose E already holds sources: the new ones are added after them (the upload checks that the
# existing E is unchanged). '*' means every row with a non-empty E. Without it, a non-empty E stops the build.
E_APPEND = []

# Optional. Varieties whose sources give no weakness, listed in the review file for a later pass.
NO_WEAKNESS = ['Giống ví dụ 2']

# Review file layout: an introduction (Markdown lines), then one section per heading with its varieties in order.
# Merges get their own sections after these, and the G reasons are listed at the end.
INTRO = [
    '## What this covers',
    '',
    '- Two C cells filled from Viện Ví dụ A, one C cell rewritten (it held another variety\'s text), one weakness added.',
    '- One species with a shared D, merged over its varieties.',
]
SECTIONS = [
    ('Thông tin giống', ['Giống ví dụ 1', 'Giống ví dụ 2', 'Giống ví dụ 3']),
]
