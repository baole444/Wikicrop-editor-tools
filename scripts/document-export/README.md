# Document export — Markdown → ODT → PDF

For documents going to people outside this folder (the article standard, the analysis).

## Build everything

```bash
"C:/Program Files/LibreOffice/program/python.exe" scripts/document-export/odt_finish.py
```

PowerShell, from the project root — the `&` is needed because the path is quoted:

```powershell
& "C:\Program Files\LibreOffice\program\python.exe" .\scripts\document-export\odt_finish.py
```

Builds every `[[document]]` listed in `export-config.toml`: Markdown → `.odt`, fills in the contents page numbers, saves the `.odt`, exports the `.pdf`. It must run under LibreOffice's own Python, which carries the `uno` module.

Other PowerShell forms:

```powershell
& "C:\Program Files\LibreOffice\program\python.exe" .\scripts\document-export\odt_finish.py --config .\my-look.toml
```

```powershell
python .\scripts\document-export\md2odt.py
```

## Change the look — `export-config.toml`

| Section        | Controls                                                                                                                                                      |
|----------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `[page]`       | Page size and margins (cm)                                                                                                                                    |
| `[fonts]`      | Body font and code font — the body font must cover Vietnamese                                                                                                 |
| `[sizes]`      | Every font size (pt): body, title, headings, tables, code, contents entries, footer                                                                           |
| `[colors]`     | Text, muted text, rules, table header and zebra rows, code, links, quotes (`#rrggbb`)                                                                         |
| `[style]`      | Body line spacing, line under Heading 1 on/off, zebra rows on/off                                                                                             |
| `[cover]`      | Contents on/off, its label, its depth (1–3), leader character between entry and page number                                                                   |
| `[footer]`     | Page number on/off, left / center / right, starting number                                                                                                    |
| `[pdf]`        | Bookmarks, tagged PDF                                                                                                                                         |
| `[wiki]`       | `md2wiki.py`: the wiki's address (links to it become internal links), `---` separators on/off, checkbox character, own contents list on/off               |
| `[[document]]` | One block per document: `source`, `odt`, `pdf_dir`, `title`; optional `cut` (headings to drop), `contents_label` (this document's label, e.g. `"Mục lục"`) and `wiki` (where `md2wiki.py` writes the wikitext) |

Delete a line to fall back to the built-in default. Unknown keys are reported and ignored; bad values (a colour that is not `#rrggbb`, an alignment that does not exist) stop the build with a message naming the setting.

To try a different look without touching the main file, copy it and pass `--config other.toml` to either script.

## Single steps

```bash
python scripts/document-export/md2odt.py
```
Markdown → `.odt` only, for every listed document. Any Python 3.11+, standard library only. Or one file: `md2odt.py <src.md> <dst.odt> "<Title>" ["<cut regex>||<cut regex>"]`.

```bash
"C:/Program Files/LibreOffice/program/python.exe" scripts/document-export/odt_finish.py "working documents/odt/Wikicrop - Article Standard.odt" "working documents/pdf"
```
Finish existing `.odt` files only: contents page numbers, save, PDF. The trailing folder is where the PDFs go.

## What you get

- Cover: title and a live contents list, with no header, footer or page number
- Body: page number only
- `##` / `###` / `####` → Heading 1 / 2 / 3, so the Navigator, the contents and PDF bookmarks all work
- PDF: contents entries are clickable, headings appear as bookmarks
- Links to headings, `[§1A](#1a-phạm-vi-giới-hạn-viết-bài-cho-thị-trường-việt-nam)`, jump to that heading in the ODT and PDF. The anchor is the one a Markdown preview (VS Code, GitHub) gives the heading — copy it from there. A link whose heading doesn't exist is reported during the build and left as plain text

## Wikitext for the wiki — `md2wiki.py`

```powershell
python .\scripts\document-export\md2wiki.py
```

Writes the wikitext version of every `[[document]]` that has a `wiki = ` path, into `working documents/wikitext/`. Any Python 3.11+, standard library only. Or one file: `md2wiki.py <src.md> <dst.wiki> ["<cut regex>||<cut regex>"]`. It only writes the file. Putting it on the wiki is a separate step: open the page, paste, preview, save.

| Markdown                     | Wikitext                                                                           |
|------------------------------|------------------------------------------------------------------------------------|
| `##` / `###` / `####`        | `==` / `===` / `====`. The first `#` line is the page title and is dropped           |
| `**b**` `*i*` `~~s~~`        | `'''b'''` `''i''` `<s>s</s>`                                                       |
| `<sup>x</sup>` `<sub>x</sub>` | Kept as they are. The ODT and PDF show them raised or lowered too                 |
| `` `code` ``                 | `<code><nowiki>code</nowiki></code>`, shown exactly as typed                       |
| ```` ``` ```` block          | `<pre>`, shown exactly as typed, so `<ref>` and `{{...}}` examples stay examples   |
| Table                        | `{\| class="wikitable"`                                                            |
| `-` / `1.` / `- [ ]`         | `*` / `#` / `*` with a `☐`                                                         |
| `> quote`                    | `<blockquote>`                                                                     |
| `[§3](#3-citations)`         | `[[#3. Citations\|§3]]`. A link whose heading doesn't exist is reported and left as text |
| A link to this wiki          | `[[Page\|text]]`, an internal link                                                 |
| Any other web link           | `[https://... text]`                                                               |
| A link to a local file       | Its text only                                                                      |

Contents are left to MediaWiki. Wikicrop's skin (Vector 2022) lists the headings in the sidebar without numbering them again, so the standard's own `3.1` shows once. `contents = true` under `[wiki]` puts a list at the top instead (label and depth from `[cover]`) and turns MediaWiki's off. That is only worth it on a skin that numbers its contents.

Editing the `.odt` by hand afterwards is fine: in LibreOffice, **Tools → Update → Update All** refreshes the contents, then **File → Export as PDF**. Rebuilding from Markdown overwrites hand edits, so change the Markdown if the edit should last.
