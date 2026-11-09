"""Download the project's live Google Sheet as .xlsx (to WIKICROP_CATALOGUE, see project_config.py).

Replaces the manual "File → Download → .xlsx" step. The sheet is link-shared, so its
export URL needs no sign-in. The download is checked (real xlsx, expected tabs) before it
replaces the local copy, so a failed or partial fetch never overwrites a good file.

    python scripts/fetch_live_sheet.py            # refresh the local copy
    python scripts/fetch_live_sheet.py --keep     # also keep a dated copy beside it

The local copy stays read-only by convention: all edits go on the live sheet.
"""
import argparse
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import project_config as cfg  # noqa: E402

SHEET_ID = cfg.SHEET_ID
URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=xlsx"
TARGET = cfg.CATALOGUE
EXPECTED_TABS = ["Danh mục giống cây", "Thông tin loài", "Thông tin giống & Canh tác"]
RETRY_WAITS = (0, 10, 30)   # seconds before each urllib attempt; curl is tried after the last


class FetchError(Exception):
    pass


def fetch_urllib() -> bytes:
    req = urllib.request.Request(URL, headers={"User-Agent": "wikicrop-sheet-fetch/1.0", "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            ctype = r.headers.get("Content-Type", "")
            data = r.read()
    except urllib.error.HTTPError as e:
        # The export URL answers 307 to a one-off googleusercontent.com link; say which hop failed.
        body = e.read()[:200].decode("utf-8", "replace").strip().replace("\n", " ")
        if body.startswith("<"):   # Google's HTML error page says nothing useful
            body = ""
        raise FetchError(f"HTTP {e.code} from {urllib.parse.urlparse(e.url).netloc}" + (f": {body}" if body else ""))
    except (urllib.error.URLError, OSError) as e:
        raise FetchError(f"network error: {getattr(e, 'reason', e)}")
    if "spreadsheetml" not in ctype:
        raise FetchError(f"not an xlsx ({ctype or 'no content type'}), is the sheet still shared by link?")
    return data


def fetch_curl(curl: str) -> bytes:
    fd, tmp = tempfile.mkstemp(suffix=".xlsx.part")
    os.close(fd)
    try:
        r = subprocess.run([curl, "-sSL", "--fail", "--max-time", "300", "-o", tmp, URL],
                           capture_output=True, text=True)
        if r.returncode:
            raise FetchError(f"curl: {r.stderr.strip() or f'exit {r.returncode}'}")
        return Path(tmp).read_bytes()
    finally:
        os.remove(tmp)


def fetch() -> bytes:
    """Google's export server fails now and then (HTTP 400 from googleusercontent.com on 27/10/2026, while curl got
    the file in the same minute), so a failure is retried after a pause, and curl is tried last."""
    errors = []
    for wait in RETRY_WAITS:
        if wait:
            print(f"  retrying in {wait} s", file=sys.stderr)
            time.sleep(wait)
        try:
            return fetch_urllib()
        except FetchError as e:
            errors.append(str(e))
            print(f"fetch failed: {e}", file=sys.stderr)
    curl = shutil.which("curl")
    if curl:
        print("  trying curl", file=sys.stderr)
        try:
            return fetch_curl(curl)
        except FetchError as e:
            errors.append(str(e))
    sys.exit("Download failed, local copy left unchanged:\n  " + "\n  ".join(errors))


def tabs(data: bytes) -> list[str]:
    import re
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        wb = z.read("xl/workbook.xml").decode("utf-8")
    return [t.replace("&amp;", "&") for t in re.findall(r'<sheet [^>]*name="([^"]+)"', wb)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--keep", action="store_true", help="also save a timestamped copy next to the target")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # Windows console defaults to cp1252
    if not SHEET_ID:
        sys.exit("No sheet ID: set WIKICROP_SHEET_ID to the ID in the sheet's URL (.../spreadsheets/d/<ID>/edit).")

    data = fetch()
    try:
        names = tabs(data)
    except (zipfile.BadZipFile, KeyError):
        sys.exit("Download is not a valid xlsx; local copy left unchanged.")
    missing = [t for t in EXPECTED_TABS if t not in names]
    if missing:
        sys.exit(f"Tabs missing from the download: {missing}; local copy left unchanged.")

    fd, tmp = tempfile.mkstemp(dir=TARGET.parent, suffix=".xlsx.part")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, TARGET)

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"{stamp}  {len(data) // 1024} KB  ->  {TARGET.name}")
    print("tabs:", " | ".join(names))
    if args.keep:
        dated = TARGET.with_name(f"{TARGET.stem} {datetime.now():%Y%m%d-%H%M}.xlsx")
        dated.write_bytes(data)
        print("kept:", dated.name)


if __name__ == "__main__":
    main()
