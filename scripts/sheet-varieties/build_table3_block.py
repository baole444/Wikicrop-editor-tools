"""Build one fill block for `Thông tin giống & Canh tác`: check its drafts against a snapshot of the live
sheet, then write the review file and the upload script.

A block is one drafts module (any importable .py beside this script) holding the texts, sources, merges and
the review notes. table3_block_example_drafts.py shows every field the module must define.

Checks: house style (no semicolons, plain hyphens, closed number ranges, no double spaces), every variety
found exactly once in groups 7-13, every `fill` cell empty, every `replace` cell still holding the text it is
meant to replace, every `append` cell not already holding its text, every E empty (or, for rows listed in
E_APPEND, holding sources the new ones are appended to), every merge block one species with the expected D
cells filled, and no written cell (C, D, E or G) inside a merged range below its top cell, where Sheets would
silently drop the value. Any failure stops the build.

Cell modes: `fill` writes an empty cell, `replace` rewrites a cell that holds `was`, `append` adds the text as
a new paragraph after the current cell (the weakness pass). For `append` the build records the current cell as
`was`, and the upload script stops if the cell changed since. A cell may carry its own `reason` for G.
E_APPEND = '*' appends to E on every row whose E is not empty.

Usage:
    python scripts/sheet-varieties/build_table3_block.py <drafts module> "<snapshot.xlsx>"
    python scripts/sheet-varieties/build_table3_block.py table3_block_example_drafts "<snapshot.xlsx>"

The snapshot is a fresh download of the live sheet (fetch_live_sheet.py). Outputs: the review file
REVIEW (Markdown) in WIKICROP_SOURCE_BOOKS, and the upload script GS beside this script.
"""
import importlib
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

import openpyxl

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
import project_config as cfg  # noqa: E402

REVIEW_DIR = cfg.SOURCE_BOOKS
TEMPLATE = HERE / 'fill_table3_block.template.gs'
SHEET = 'Thông tin giống & Canh tác'

R_C = 'Bổ sung thông tin giống (cột C) theo nguồn ghi ở cột E'
R_D = 'Bổ sung kỹ thuật canh tác (cột D) theo nguồn ghi ở cột E'
R_REPL = ('Sửa thông tin giống (cột C): nội dung cũ là của giống {of} do lệch hàng, '
          'viết lại theo nguồn ghi ở cột E')
R_APPEND = 'Bổ sung nhược điểm (cột C) theo nguồn ghi ở cột E'
R_MERGE_EMPTY = ('Bổ sung kỹ thuật canh tác (cột D) dùng chung cho cả loài theo nguồn ghi ở cột E '
                 'và gộp ô D cho các giống của loài, ô này trước đây trống')
R_MERGE_REPL = ('Thay kỹ thuật canh tác (cột D) bằng quy trình chung cho cả loài theo nguồn ghi ở cột E '
                'và gộp ô D cho các giống của loài')


def nfc(s):
    return unicodedata.normalize('NFC', s).strip()


def style_problems(label, s):
    out = []
    if ';' in s:
        out.append(f'{label}: semicolon')
    if re.search('[–—]', s):
        out.append(f'{label}: long dash')
    m = re.search(r'\d +- *\d|\d *- +\d', s)
    if m:
        out.append(f'{label}: spaced range {m.group()!r}')
    if '  ' in s:
        out.append(f'{label}: double space')
    return out


def quote(lines):
    """Blockquote with each line as its own paragraph."""
    out = []
    for i, line in enumerate(lines):
        out += (['>'] if i else []) + ['> ' + line]
    return out


def main(module, xlsx):
    sys.stderr.reconfigure(encoding='utf-8')
    sys.path.insert(0, str(HERE))
    dr = importlib.import_module(module)
    wb = openpyxl.load_workbook(xlsx, data_only=True)   # not read_only: the merged ranges are needed
    ws = wb[SHEET]
    g = [[('' if v is None else str(v)) for v in (list(r) + [None] * 7)[:7]] for r in ws.iter_rows(values_only=True)]
    head = [i for i, r in enumerate(g) if nfc(r[0]) == 'Loài' and nfc(r[1]) == 'Giống']
    lab = [i for i, r in enumerate(g) if nfc(r[0]).startswith('Hàng thuộc nhóm 1-6')]
    assert len(head) == 1 and len(lab) == 1
    rows = {}
    for i in range(head[0] + 1, lab[0]):
        if nfc(g[i][1]):
            rows.setdefault(nfc(g[i][1]), []).append(i + 1)

    problems, row_of = [], {}
    names = {c['B'] for c in dr.CELLS} | {b for m in dr.MERGES for b in (m['first'], m['last'])}
    for b in sorted(names):
        hits = rows.get(b, [])
        if len(hits) != 1:
            problems.append(f'{b}: found in rows {hits}')
        else:
            row_of[b] = hits[0]
    col = {'C': 2, 'D': 3, 'E': 4}
    for c in dr.CELLS:
        problems += style_problems(f"{c['B']} {c['col']}", c['text'])
        for k in c['src']:
            if k not in dr.SOURCES:
                problems.append(f"{c['B']} {c['col']}: unknown source {k}")
        r = row_of.get(c['B'])
        if not r:
            continue
        cur = g[r - 1][col[c['col']]]
        if c['mode'] == 'fill' and cur.strip():
            problems.append(f"{c['B']} {c['col']}{r}: not empty")
        if c['mode'] == 'replace' and c['was'] not in cur:
            problems.append(f"{c['B']} {c['col']}{r}: no longer holds {c['was']!r}")
        if c['mode'] == 'append':
            if nfc(c['text']) in nfc(cur):
                problems.append(f"{c['B']} {c['col']}{r}: already holds the text to append")
            c['was'] = cur
    merge_rows = {}
    for m in dr.MERGES:
        problems += style_problems(f"{m['species']} D", m['text'])
        top, bot = row_of.get(m['first']), row_of.get(m['last'])
        if not (top and bot):
            continue
        rs = list(range(top, bot + 1))
        if not nfc(g[top - 1][0]).startswith(m['species']):
            problems.append(f"{m['first']} R{top} does not start the {m['species']} block")
        if any(nfc(g[r - 1][0]) for r in rs[1:]):
            problems.append(f"{m['species']}: another species inside rows {top}-{bot}")
        filled = [nfc(g[r - 1][1]) for r in rs if g[r - 1][3].strip()]
        if filled != m['filled']:
            problems.append(f"{m['species']} D filled rows: {filled}, expected {m['filled']}")
        if any(c['col'] == 'D' and row_of.get(c['B']) in rs for c in dr.CELLS):
            problems.append(f"{m['species']}: a drafted D cell sits inside the merge")
        merge_rows[m['species']] = rs
    if problems:
        sys.exit('Nothing written:\n' + '\n'.join(problems))

    # E per row: the merge source first, then the sources of the row's own cells. Reasons likewise.
    e, reasons = {}, {}
    for m in dr.MERGES:
        for r in merge_rows[m['species']]:
            b = nfc(g[r - 1][1])
            e.setdefault(b, []).extend(m['src'])
            reasons.setdefault(b, [])
    for c in dr.CELLS:
        if c['src']:  # a cell with no source (a format fix) leaves E alone
            e.setdefault(c['B'], []).extend(c['src'])
        x = c.get('reason') or {'replace': lambda: R_REPL.format(of=c['was_of']), 'append': lambda: R_APPEND}.get(
            c['mode'], lambda: R_C if c['col'] == 'C' else R_D)()
        reasons.setdefault(c['B'], []).append(x)
    for m in dr.MERGES:
        for r in merge_rows[m['species']]:
            b = nfc(g[r - 1][1])
            reasons[b].append(R_MERGE_REPL if b in m['filled'] else R_MERGE_EMPTY)
    # E must be empty, unless the row is listed in E_APPEND: then the new sources go after the existing ones
    # and the upload script checks that the existing E is unchanged.
    e_all = getattr(dr, 'E_APPEND', []) == '*'
    e_append = set(rows) if e_all else set(getattr(dr, 'E_APPEND', []))
    e_text, e_was = {}, {}
    for b, keys in e.items():
        r = row_of.get(b) or rows[b][0]
        cur = nfc(g[r - 1][4])
        new = list(dict.fromkeys(dr.SOURCES[k] for k in keys))
        if cur and b not in e_append:
            problems.append(f'{b} E{r}: not empty, list it in E_APPEND to append')
        if not cur and b in e_append and not e_all:
            problems.append(f'{b} E{r}: listed in E_APPEND but empty')
        if cur:
            e_was[b] = cur
            old = cur.split('\n')
            new = old + [s for s in new if s not in old]
        e_text[b] = '\n'.join(new)
    for label, s in [(f'E {b}', v) for b, v in e_text.items()] + [(f'G {b}', x) for b, rs in reasons.items() for x in rs]:
        problems += style_problems(label, s)
    # Sheets drops a value written inside a merged range below its top cell (E222 and E236-E237 in block 5),
    # so no write may land there. Write the shared cell's top row instead, or split the merge first.
    hidden = {}
    for m in ws.merged_cells.ranges:
        for r in range(m.min_row, m.max_row + 1):
            for k in range(m.min_col, m.max_col + 1):
                if (r, k) != (m.min_row, m.min_col):
                    hidden[(r, k)] = m.coord
    letter = {'C': 3, 'D': 4, 'E': 5, 'G': 7}
    at = lambda b: row_of.get(b) or rows[b][0]  # noqa: E731
    # A reason whose G sits inside a merged G range goes to the top cell of that range, named by its variety.
    tops = {(r, k): m.min_row for m in ws.merged_cells.ranges
            for r in range(m.min_row + 1, m.max_row + 1) for k in range(m.min_col, m.max_col + 1)}
    for b in list(reasons):
        top = tops.get((at(b), letter['G']))
        if top:
            top_b = nfc(g[top - 1][1])
            if not top_b or len(rows.get(top_b, [])) != 1:
                problems.append(f'{b} G{at(b)}: inside a merged G range whose top row R{top} has no unique variety name')
                continue
            reasons.setdefault(top_b, []).extend(f'{b}: {x}' for x in reasons.pop(b))
    targets = ([(c['B'], c['col']) for c in dr.CELLS] + [(b, 'E') for b in e_text] + [(b, 'G') for b in reasons])
    for b, k in dict.fromkeys(targets):
        if (at(b), letter[k]) in hidden:
            problems.append(f'{b} {k}{at(b)}: inside merged {hidden[(at(b), letter[k])]} below its top cell, '
                            'Sheets would drop the write')
    if problems:
        sys.exit('Nothing written:\n' + '\n'.join(problems))

    # Review file.
    row = lambda b: row_of.get(b) or rows[b][0]  # noqa: E731
    def link(name):  # relative from the review file, or an absolute file: URI across drives
        try:
            return Path(os.path.relpath(HERE / name, REVIEW_DIR)).as_posix()
        except ValueError:
            return (HERE / name).resolve().as_uri()
    L = [f'# {dr.TITLE} (draft for review)', '',
         f'Snapshot: `{Path(xlsx).name}`. Drafts: [`{module}.py`]'
         f'(<{link(module + ".py")}>) (edit there and rerun `build_table3_block.py`, not here). '
         f'Upload script: [`{dr.GS}`](<{link(dr.GS)}>), function `{dr.FN}`, generated by the '
         'same build. Paste it, run with `DRY_RUN = true`, read the log, then run with `DRY_RUN = false`.', '']
    L += dr.INTRO + ['']
    if getattr(dr, 'NO_WEAKNESS', []):
        L += ['## No weakness in the source (for the final pass)', '',
              ', '.join(dr.NO_WEAKNESS) + '.', '']
    for heading, names in dr.SECTIONS:
        L += [f'## {heading}', '']
        for b in names:
            L += [f'### R{row(b)} {b}', '']
            for c in [c for c in dr.CELLS if c['B'] == b]:
                tag = {'fill': 'fill', 'replace': 'REPLACE', 'append': 'append to C'}[c['mode']]
                if c.get('show_was'):
                    L += [f"**{c['col']} before**", ''] + quote(c['was'].split('\n')) + ['']
                L += [f"**{c['col']}** ({tag}, {len(c['text'])} chars)", ''] + quote(c['text'].split('\n')) + ['']
            if b in e_text:
                tag = ' (new sources appended after the existing ones)' if b in e_was else ''
                L += [f'**E**{tag}', ''] + quote(e_text[b].split('\n')) + ['']
    for m in dr.MERGES:
        rs = merge_rows[m['species']]
        L += [f"## {m['species']}: D shared, merged over rows {rs[0]}-{rs[-1]} ({len(m['text'])} chars)", '']
        L += quote(m['text'].split('\n')) + ['']
    L += ['## G reasons written by the upload script', '']
    L += [f'- R{row(b)} {b}: ' + ' / '.join(f'*{x}*' for x in rs) for b, rs in sorted(reasons.items(), key=lambda kv: row(kv[0]))]
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    (REVIEW_DIR / dr.REVIEW).write_text('\n'.join(L) + '\n', encoding='utf-8')

    data = {
        'cells': [{k: c[k] for k in ('B', 'col', 'mode', 'was', 'text') if k in c} for c in dr.CELLS],
        'merges': [{k: m[k] for k in ('species', 'first', 'last', 'filled', 'text')} for m in dr.MERGES],
        'e': e_text,
        'e_was': e_was,
        'reasons': reasons,
        'counts': {'cells': len(dr.CELLS), 'e': len(e_text), 'rows': len(reasons),
                   'merged': sum(len(rs) for rs in merge_rows.values())},
    }
    gs = (TEMPLATE.read_text(encoding='utf-8')
          .replace('@@TITLE@@', dr.TITLE).replace('@@DRAFTS@@', f'{module}.py')
          .replace('@@REVIEW@@', dr.REVIEW).replace('@@FN@@', dr.FN)
          .replace('/*DATA*/{}', json.dumps(data, ensure_ascii=False, indent=1)))
    assert '@@' not in gs
    (HERE / dr.GS).write_text(gs, encoding='utf-8')
    print(f"{len(dr.CELLS)} cells ({sum(c['mode'] == 'replace' for c in dr.CELLS)} replace), "
          f"{len(dr.MERGES)} merges over {data['counts']['merged']} rows, E on {len(e_text)} rows, "
          f"reasons on {len(reasons)} rows -> {dr.REVIEW}, {dr.GS}")


if __name__ == '__main__':
    if len(sys.argv) != 3 or not Path(sys.argv[2]).is_file():
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
