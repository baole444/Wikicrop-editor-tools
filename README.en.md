# Wikicrop tools

[Tiếng Việt](README.md) | **English**

Tools for maintaining [Wikicrop](http://wikicrop.lab.io.vn/wikicrop/index.php/Main_Page), a MediaWiki
encyclopedia of crops and crop varieties for Vietnamese growers, and the Google Sheet that holds its
variety catalogue. They bulk-edit wiki pages to the writing standard, audit the result, move variety
images from the source workbooks to the sheet and the wiki, and help find sources for the data.

Section numbers in comments and edit summaries (`standard section 6.6`, `(9.11)`) refer to the writing
standard on the wiki: [Wikicrop:Tiêu chuẩn viết bài](http://wikicrop.lab.io.vn/wikicrop/index.php/Wikicrop:Tiêu_chuẩn_viết_bài).

## Setup

### Python and Node.js

- **Python 3.11 or newer**: download from [python.org/downloads](https://www.python.org/downloads/). On
  Windows, tick "Add python.exe to PATH" on the installer's first screen, or install with
  `winget install Python.Python.3.12`. Check with `python --version`.
- **Node.js** is needed only by `sheet-varieties/mock/`. Install it through a Node version manager, so
  versions are easy to switch:
  - Windows: [nvm-windows](https://github.com/coreybutler/nvm-windows/releases) (download `nvm-setup.exe`),
    then `nvm install lts` and `nvm use lts`.
  - macOS, Linux: [nvm](https://github.com/nvm-sh/nvm#installing-and-updating), then `nvm install --lts`.

  Check with `node --version`.

### Python environment

The setup script makes a virtual environment in `.venv` beside `scripts/` and
installs the packages in `scripts/requirements.txt` (openpyxl, Pillow, pypdf). It can be run again at
any time.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_env.ps1
.\.venv\Scripts\Activate.ps1
```

```bash
bash scripts/setup_env.sh
source .venv/bin/activate          # Git Bash on Windows: source .venv/Scripts/activate
```

Options: `-Video` / `--video` adds faster-whisper for the transcript tool, `-Gpu` / `--gpu` with it adds
the CUDA libraries for an NVIDIA GPU. The script also reports the optional programs it found:

| Program      | Needed by                                                                        |
|--------------|----------------------------------------------------------------------------------|
| [LibreOffice](https://www.libreoffice.org/download/download-libreoffice/) | `document-export/odt_finish.py`, run with LibreOffice's own Python (it has `uno`) |
| pdftotext    | `source-finder/find_sources.py`, for PDF sources. Part of Poppler: Windows [poppler-windows](https://github.com/oschwartz10612/poppler-windows/releases), macOS `brew install poppler`, Linux `apt install poppler-utils` |
| curl         | `fetch_live_sheet.py`, fallback download. Built into Windows 10 and later, macOS and most Linux |
| Node.js      | `sheet-varieties/mock/`, to test Apps Scripts locally. Install as above          |

The Apps Scripts (`.gs`) run in the Google Sheet itself (Extensions → Apps Script), not on your machine.

## Settings

All paths and addresses are in `scripts/project_config.py`. Set them without editing any tool, in a
local settings file or as environment variables (which win):

```bash
cp scripts/settings.example.toml scripts/settings.local.toml     # then fill it in
```

`settings.local.toml` holds the sheet ID and your own folder layout: keep it out of version control
and out of anything you share. The ones to set first:

| Variable            | What                                                                       |
|---------------------|----------------------------------------------------------------------------|
| `WIKICROP_OPERATOR` | Your wiki username. Sent in the User-Agent, so the wiki's admins can reach whoever runs a bot |
| `WIKICROP_SHEET_ID` | ID of the project's Google Sheet, from its URL. Needed by `fetch_live_sheet.py` |
| `WIKICROP_API`      | MediaWiki API endpoint, if the wiki moves                                  |

The rest (`WIKICROP_ROOT`, `_CATALOGUE`, `_SOURCE_BOOKS`, `_IMAGES`, `_LOGS`, `_CROPS`, `_CATEGORIES`)
place the local data, by default under `data/`, and are described in `project_config.py`. The category
scheme that `create_categories.py` creates is data too, in `scripts/categories.toml`. API keys for the search tools are read from the
environment as each tool's docstring says. No credential is stored anywhere: wiki logins are prompted for.

## Safety model of the wiki tools

Every tool that writes to the wiki follows the same rules:

- **Dry run by default.** Nothing is written without `--run`. The dry run needs no login and shows what
  would change. `--validate` renders the changed pages through the wiki's parser first, where offered.
- **BotPassword login**, prompted at run time and held only in memory. Create one at
  `Special:BotPasswords` with the single grant the tool's docstring names.
- **No lost edits.** Each edit carries the revision it was computed from (`baserevid`), so an edit made
  meanwhile by someone else stops it. `nocreate` or `createonly` keeps it from making or overwriting pages
  by mistake. `maxlag` and a fixed delay keep the load on the shared server low.
- **A CSV log of every page**, and a separate read-only audit script that checks the live wiki
  against the log afterwards.

## Tools

Each tool's docstring is its manual: run it with `--help`, or read the top of the file.

**Wiki pages** (bulk edits, each with its audit)

| Tool | Does |
|------|------|
| `create_categories.py`, `validate_categories.py`, `audit_categories.py` | Create the category scheme, check it before, audit it after |
| `reformat_infoboxes.py`, `audit_infobox_reformat.py` | Infobox calls to one parameter per line |
| `retrofit_pass.py`, `audit_retrofit_pass.py` | One pass of every mechanical fix a page needs: infobox layout and place, taxonomy, dashes, entities, links, categories |
| `convert_citations.py`, `audit_citations.py` | Web citations to `{{ChuthichWeb}}` and named `<ref>`s |
| `fetch_crop_pages.py`, `audit_crop_pages.py` | Copy a crop's pages from the wiki (read-only), and check them against the standard's mechanical rules |

**Variety images**

| Tool | Does |
|------|------|
| `export_variety_images.py` | Export the pictures embedded in the source workbooks, named by catalogue name |
| `find_image_sources.py` | Find the web page each picture came from, from the row's reference |
| `reverse_search_images.py` | Reverse image search (Google Cloud Vision) for the rest |
| `sheet-images/fetch_commons_images.py` | Pictures for rows that had none: Wikimedia Commons first, then cited web pages |
| `upload_variety_images.py` | Upload them to the wiki, never overwriting or duplicating, with their source cited |
| `sheet-images/insert_images.gs` | Apps Script: put the pictures into the sheet's image column |

**Google Sheet**

| Tool | Does |
|------|------|
| `fetch_live_sheet.py` | Download the live sheet as .xlsx, checked before it replaces the local copy |
| `sheet-common/sheet_config.gs` | Apps Script: the sheet's layout and run switches, shared by the scripts that edit it |
| `sheet-varieties/build_table3_block.py` | Check a batch of drafted texts against a snapshot, then write a review file and the Apps Script that uploads it. Example batch: `table3_block_example_drafts.py` |
| `sheet-varieties/mock/` | Test an Apps Script against a local copy of the sheet before running it on the real one |

**Sources and documents**

| Tool | Does |
|------|------|
| `source-finder/find_sources.py` | Gather, fetch and rank candidate sources for rows without one, for a person to review |
| `video-transcript/transcribe_video.py` | Private, timestamped transcript of a recorded programme, for research (see its README) |
| `document-export/` | Markdown to ODT and PDF, and to wikitext for a wiki page (see its README) |
