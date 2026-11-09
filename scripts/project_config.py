"""Shared settings for the Wikicrop tools: file locations, the wiki, the operator.

Every tool takes its paths and wiki address from here, so the project can be laid out differently
without editing the tools. Each setting is looked up in this order:

  1. an environment variable of the same name,
  2. the local settings file, scripts/settings.local.toml (or the file named by WIKICROP_SETTINGS).
     Copy settings.example.toml to start one. It holds values for one machine and one team, such as the
     sheet ID, so keep it out of version control and out of anything you share,
  3. the default below.

Relative paths are taken from WIKICROP_ROOT.

Settings
--------
  WIKICROP_ROOT           Project root. Default: the folder that holds `scripts/`.
  WIKICROP_API            MediaWiki API endpoint of the wiki.
  WIKICROP_OPERATOR       Wiki username of the person running the tools. It goes into the User-Agent
                          of every request, as MediaWiki's API etiquette asks, so the wiki's admins can
                          contact whoever runs a bot.
  WIKICROP_SHEET_ID       ID of the project's Google Sheet (the variety catalogue): the part of its URL
                          between /d/ and /edit. Ask the project for it. No default.
  WIKICROP_CATALOGUE      Local .xlsx copy of that sheet, written by fetch_live_sheet.py and read by the
                          wiki tools. Default: data/catalogue.xlsx.
  WIKICROP_SOURCE_BOOKS   Folder of the source workbooks the catalogue was built from, and of the review
                          files the sheet builders write. Default: data/source-workbooks.
  WIKICROP_IMAGES         Working folder for variety images (export, sources, upload).
                          Default: data/variety-images.
  WIKICROP_LOGS           Run logs and review tables written by the bulk-edit tools. Default: data/logs.
  WIKICROP_CROPS          Local copies of wiki pages, one folder per crop. Default: Crops.
  WIKICROP_CATEGORIES     The category scheme for create_categories.py. Default: scripts/categories.toml.

The two source workbooks that carry embedded variety images are set in the local settings file under
[source_workbooks], by tag: CG (primary, the newer one) and CT (fallback). File names there are relative to
WIKICROP_SOURCE_BOOKS. Default: source-CG.xlsx and source-CT.xlsx.

Example (PowerShell):
    $env:WIKICROP_OPERATOR = "ExampleUser"
    $env:WIKICROP_LOGS = "D:\\wikicrop-logs"
Example (bash):
    export WIKICROP_OPERATOR=ExampleUser
"""

import os
import tomllib
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SETTINGS_FILE = Path(os.environ.get("WIKICROP_SETTINGS") or _HERE / "settings.local.toml")
try:
    with open(_SETTINGS_FILE, "rb") as _fh:
        _LOCAL = tomllib.load(_fh)
except FileNotFoundError:
    _LOCAL = {}


def _get(var: str, default: str = "") -> str:
    return os.environ.get(var) or str(_LOCAL.get(var) or "") or default


ROOT = Path(_get("WIKICROP_ROOT") or _HERE.parent)


def _path(var: str, default: Path) -> Path:
    value = _get(var)
    if not value:
        return default
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


API = _get("WIKICROP_API", "http://wikicrop.lab.io.vn/wikicrop/api.php")
OPERATOR = _get("WIKICROP_OPERATOR", "unknown (WIKICROP_OPERATOR not set)")
SHEET_ID = _get("WIKICROP_SHEET_ID")

DATA = ROOT / "data"
CATALOGUE = _path("WIKICROP_CATALOGUE", DATA / "catalogue.xlsx")
SOURCE_BOOKS = _path("WIKICROP_SOURCE_BOOKS", DATA / "source-workbooks")
IMAGES = _path("WIKICROP_IMAGES", DATA / "variety-images")
LOGS = _path("WIKICROP_LOGS", DATA / "logs")
CROPS = _path("WIKICROP_CROPS", ROOT / "Crops")
CATEGORIES = _path("WIKICROP_CATEGORIES", _HERE / "categories.toml")

# The two source workbooks that carry the embedded variety images, by short tag.
_books = _LOCAL.get("source_workbooks", {})
SOURCE_WORKBOOKS = {tag: SOURCE_BOOKS / _books.get(tag, f"source-{tag}.xlsx") for tag in ("CG", "CT")}


def require(*paths) -> None:
    """Stop with a clear message, naming the settings, when an input file or folder is missing."""
    missing = [str(p) for p in paths if not Path(p).exists()]
    if missing:
        raise SystemExit(
            "Missing input:\n  " + "\n  ".join(missing) + "\n"
            "Set the paths in scripts/settings.local.toml or as WIKICROP_* environment variables "
            "(see scripts/project_config.py).")


def user_agent(tool: str, purpose: str) -> str:
    """User-Agent for wiki requests: tool name, what it does, and who runs it."""
    return f"{tool}/1.0 ({purpose}; operated by User:{OPERATOR})"


def bot_login_hint() -> str:
    """Prompt text for the BotPassword username."""
    return "Bot username (e.g. ExampleUser@wikicrop-bot): "
