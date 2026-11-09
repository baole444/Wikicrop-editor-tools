#!/usr/bin/env python3
"""
Upload the exported variety images to Wikicrop as `Tập tin:<catalogue name>.jpg`.

Input: the variety-image folder (WIKICROP_IMAGES, see project_config.py), made by
`scripts/export_variety_images.py`. `upload/` holds one JPEG per catalogue name (≤ 1000 px);
`manifest.csv` maps each to its catalogue row, group and species.

Plus `variety-images/gapfill/`, made by `scripts/sheet-images/fetch_commons_images.py`: pictures for
rows the snapshots had none for, or whose picture was blocked. Its web and local pictures go up
with their source page cited, and replace the snapshot picture of the same name. Its Commons pictures
are not uploaded (the wiki shows them through InstantCommons) and appear in the plan as `commons`.

Design notes (same conventions as create_categories.py)
------------------------------------------------------
* DRY RUN BY DEFAULT. Uploading requires --run. The dry run needs no login and writes the plan
  to `upload-plan.csv` beside the images.
* Never overwrites and never duplicates. A file is skipped when:
    - its target title already exists on the wiki (`exists`), or
    - the wiki already holds the identical bytes under any name (`duplicate`, SHA-1), or
    - the wiki holds the same picture resized or recompressed (`near-duplicate`, perceptual
      hash within NEAR_DUP_BITS of 64 bits).
  On top of that, uploads are sent *without* ignorewarnings, so the server itself refuses
  anything it sees as a duplicate or an existing name.
* Deliberately slow: a fixed delay between uploads (default 20 s), maxlag on every request,
  backoff on rate limits.
* Credentials are prompted for at run time (getpass) and held only in memory.

Usage
-----
    python scripts/upload_variety_images.py                    # dry run: plan + report
    python scripts/upload_variety_images.py --groups BT07-BT13 # only these groups
    python scripts/upload_variety_images.py --run              # upload (prompts login)
    python scripts/upload_variety_images.py --run --limit 3    # first test with 3 files
    python scripts/upload_variety_images.py --add-citations    # sources found after upload: preview
    python scripts/upload_variety_images.py --add-citations --run   # … and edit those file pages
                                                               # (needs the "Sửa các trang hiện có" grant)

Login
-----
A BotPassword with the grant "Tải lên tập tin mới" (uploadfile). The page-creation grant used by
create_categories.py does not include uploading; add this grant to the same bot at
Special:BotPasswords, or make a second one.
"""

import argparse
import csv
import getpass
import hashlib
import http.cookiejar
import io
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import project_config as cfg  # noqa: E402

API = cfg.API
COMMONS_API = "https://commons.wikimedia.org/w/api.php"   # the wiki's shared repository (InstantCommons)
UA = cfg.user_agent("WikicropImageBot", "variety images from the catalogue")
IMG_DIR = cfg.IMAGES
SUMMARY = "Tải ảnh giống {name} (bộ dữ liệu Danh mục giống cây trồng)"
# Organiser, 05/10: the source goes in as a citation (numbered list under == Tham khảo ==, as on
# `Lúa`), not as a link in the summary. Web sources use {{ChuthichWeb}}; the dataset is offline.
DESCRIPTION = """== Tóm tắt ==
Ảnh giống '''{page}''' ({species}).
{refs}
"""
WEB_REF = "# {{{{ChuthichWeb\n| url         = {url}\n| NhaXuatBan  = {site}\n| TieuDe      = {title}\n| NgayTruyCap = {date}\n}}}}"
CITE_SUMMARY = "Thêm nguồn hình ảnh (Tham khảo), bỏ thể loại Bài thiếu nguồn"
# No source found yet: upload anyway, tagged for replacement by a sourced picture (sortkey = file name).
NO_SOURCE_CAT = "[[Thể loại:Bài thiếu nguồn"
SOURCES_CSV = IMG_DIR / "image-sources.csv"
HASH_CACHE = IMG_DIR / "wiki-hash-cache.json"    # wiki file name -> {sha1, dhash of its thumbnail}
GAPFILL = IMG_DIR / "gapfill"
GAPFILL_UPLOAD = {"upload", "upload-replace"}   # gapfill/hold/ waits on a check: never uploaded
REVERSE_CSV = IMG_DIR / "reverse-search.csv"
CITABLE = {"verified", "confirmed"}   # statuses in image-sources.csv that become a web citation
# Checked by eye on 2026-10-05: the picture shows another variety, so it never goes up under this name.
WRONG_PICTURE = {
    "Khoai tây Eben": "picture shows Marabel (sign in the photo), not Eben",
}

MAXLAG = 5
READ_DELAY = 1.0
DEFAULT_WRITE_DELAY = 20.0
MAX_RETRIES = 5
# Same picture as an existing wiki file but cropped/resized, which the perceptual hash misses.
# Checked by eye on 2026-10-05 (catalogue name -> existing wiki file).
VERIFIED_DUPLICATES = {
    "OM4218": "Om-4218.jpg",
    "OM6976": "Lua-om6976.jpg",
    "RVT": "RVT.jpg",
}
NEAR_DUP_BITS = 3         # dHash distance at or below which two pictures count as the same
SIMILAR_REPORT_BITS = 12   # reported for a human look, not skipped. Rice-field photos of different
                           # varieties can land at 5 (OMCS2000 vs OM4900), so the skip line stays tight.


def norm(s):
    return unicodedata.normalize("NFC", s or "").strip()


def dhash(data: bytes) -> int:
    img = Image.open(io.BytesIO(data)).convert("L").resize((9, 8), Image.LANCZOS)
    px = list(img.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return bits


def cite_safe(s):
    """Text that sits inside a template parameter and an external-link label."""
    return re.sub(r"\s+", " ", s).replace("|", "{{!}}").replace("]", "&#93;").replace("[", "&#91;").strip()


def safe(s):
    """Characters a file name cannot hold become '-' (standard §6.4)."""
    return re.sub(r'[\\/:*?"<>|#\[\]{}]', "-", s).strip(" .")


def ascii_slug(s):
    s = unicodedata.normalize("NFD", s.replace("đ", "d").replace("Đ", "D"))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "", re.sub(r"\.[a-z]+$", "", s))


class Wiki:
    def __init__(self):
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))
        self.opener.addheaders = [("User-Agent", UA)]
        self.csrf = None

    def _send(self, data, headers=None, url=API):
        req = urllib.request.Request(url, data=data, headers=headers or {})
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.opener.open(req, timeout=120)
                raw, retry_after = resp.read(), resp.headers.get("Retry-After")
            except urllib.error.HTTPError as e:
                raw, retry_after = e.read(), e.headers.get("Retry-After")
            except urllib.error.URLError as e:
                wait = min(60, 5 * attempt)
                print(f"    network error ({e.reason}); waiting {wait}s [attempt {attempt}/{MAX_RETRIES}]")
                time.sleep(wait)
                continue
            try:
                out = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return raw            # binary download (thumbnails)
            code = out.get("error", {}).get("code")
            if code in ("maxlag", "readonly"):
                wait = int(retry_after or 10)
                print(f"    server busy ({code}); waiting {wait}s")
                time.sleep(wait)
                continue
            if code == "ratelimited":
                wait = min(300, 30 * attempt)
                print(f"    rate limited; backing off {wait}s [attempt {attempt}/{MAX_RETRIES}]")
                time.sleep(wait)
                continue
            return out
        raise RuntimeError("gave up after retries")

    def get(self, params):
        params = dict(params, format="json", formatversion="2")
        return self._send(None, url=API + "?" + urllib.parse.urlencode(params))

    def post(self, params):
        params = dict(params, format="json", formatversion="2")
        return self._send(urllib.parse.urlencode(params).encode("utf-8"),
                          {"Content-Type": "application/x-www-form-urlencoded"})

    def fetch(self, url):
        return self._send(None, url=url)

    def token(self, kind):
        return self.get({"action": "query", "meta": "tokens", "type": kind})["query"]["tokens"][kind + "token"]

    def login(self, user, password):
        r = self.post({"action": "login", "lgname": user, "lgpassword": password, "lgtoken": self.token("login")})
        if r.get("login", {}).get("result") != "Success":
            raise SystemExit(f"Login failed: {r.get('login', {}).get('reason', r)}")
        self.csrf = self.token("csrf")
        return r["login"].get("lgusername", user)

    def all_images(self):
        out, cont = [], {}
        while True:
            r = self.get(dict({"action": "query", "list": "allimages", "ailimit": "500",
                               "aiprop": "sha1|mime|url|size"}, **cont))
            out += r["query"]["allimages"]
            if "continue" not in r:
                return out
            cont = r["continue"]
            time.sleep(READ_DELAY)

    def all_pages(self):
        out, cont = [], {}
        while True:
            r = self.get(dict({"action": "query", "list": "allpages", "aplimit": "500", "apnamespace": "0",
                               "apfilterredir": "nonredirects"}, **cont))
            out += [p["title"] for p in r["query"]["allpages"]]
            if "continue" not in r:
                return out
            cont = r["continue"]
            time.sleep(READ_DELAY)

    def thumbs(self, titles, width=256):
        urls = {}
        for i in range(0, len(titles), 50):
            r = self.get({"action": "query", "prop": "imageinfo", "iiprop": "url",
                          "iiurlwidth": str(width), "titles": "|".join(titles[i:i + 50])})
            for p in r["query"]["pages"]:
                ii = (p.get("imageinfo") or [{}])[0]
                urls[p["title"]] = ii.get("thumburl") or ii.get("url")
            time.sleep(READ_DELAY)
        return urls

    def upload(self, filename, data, text, summary):
        boundary = uuid.uuid4().hex
        fields = {"action": "upload", "format": "json", "formatversion": "2", "filename": filename,
                  "comment": summary, "text": text, "token": self.csrf, "assert": "user", "maxlag": str(MAXLAG)}
        body = io.BytesIO()
        for k, v in fields.items():
            body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n".encode())
            body.write(str(v).encode("utf-8") + b"\r\n")
        body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                   f"filename=\"upload.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".encode())
        body.write(data + b"\r\n" + f"--{boundary}--\r\n".encode())
        r = self._send(body.getvalue(), {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        if "error" in r:
            return False, f"{r['error'].get('code')}: {r['error'].get('info', '')[:100]}"
        up = r.get("upload", {})
        if up.get("result") == "Success":
            return True, "uploaded"
        return False, "warning: " + ", ".join(f"{k}={v}" for k, v in (up.get("warnings") or {}).items())

    def page_texts(self, titles):
        """-> {title: (wikitext, revid)} for the pages that exist."""
        out = {}
        for i in range(0, len(titles), 50):
            r = self.get({"action": "query", "prop": "revisions", "rvprop": "content|ids", "rvslots": "main",
                          "titles": "|".join(titles[i:i + 50])})
            for p in r["query"]["pages"]:
                if not p.get("missing") and p.get("revisions"):
                    rev = p["revisions"][0]
                    out[p["title"]] = (rev["slots"]["main"]["content"], rev["revid"])
            time.sleep(READ_DELAY)
        return out

    def edit(self, title, text, summary, baserevid):
        r = self.post({"action": "edit", "title": title, "text": text, "summary": summary, "nocreate": "1",
                       "baserevid": str(baserevid), "bot": "1", "token": self.csrf, "assert": "user",
                       "maxlag": str(MAXLAG)})
        if "error" in r:
            return False, f"{r['error'].get('code')}: {r['error'].get('info', '')[:100]}"
        e = r.get("edit", {})
        if e.get("result") == "Success":
            return True, "edited"
        # e.g. {"result": "Failure", "captcha": {...}}: ConfirmEdit wants a CAPTCHA for the new link
        why = "CAPTCHA required (new external link)" if "captcha" in e else ""
        return False, f"{why} {json.dumps(e, ensure_ascii=False)[:200]}".strip()


def _k(s):
    return re.sub(r"[\s\-._]", "", norm(s).lower())


def resolve_title(name, species, level, page_keys):
    """Standard §6.4: file name = page title. Use the existing page's title when there is one;
    otherwise predict it the way the page would be titled (`<Crop> <Variety>`, no alias)."""
    crops = [c.strip() for c in norm(species).split(" (")[0].split("/") if c.strip()]   # "Hương nhu / Húng quế"
    crop = crops[0]
    base = re.sub(r"\s*\(.*?\)\s*$", "", name).strip()
    aliases = [a.strip() for grp in re.findall(r"\((.*?)\)", name) for a in grp.split("/") if a.strip()]
    cands = [name, base] if level == "species" else \
        [name, base, f"{crop} {name}", f"{crop} {base}"] + [x for a in aliases for x in (a, f"{crop} {a}")]
    for c in cands:
        if _k(c) in page_keys:
            return page_keys[_k(c)], "page"
    first = base.split()[0].lower() if base else ""
    if level == "species" or any(_k(base).startswith(_k(c)) or first == c.split()[0].lower() for c in crops):
        return base, "predicted"        # already carries its crop: "Húng quế …", "Dâu Hạ Châu"
    return f"{crop} {base}", "predicted"


def gapfill_rows():
    """gapfill/sources.csv as manifest-shaped rows, with group, species and level from the catalogue."""
    if not (GAPFILL / "sources.csv").exists():
        return []
    from export_variety_images import catalogue_targets, safe_name   # same folder; reads the live catalogue
    cat = {norm(t["name"]).lower(): t for t in catalogue_targets()}
    originals = {p.stem: p for p in (GAPFILL / "originals").iterdir()}
    out = []
    for s in csv.DictReader(open(GAPFILL / "sources.csv", encoding="utf-8-sig")):
        name = norm(s["catalogue name"])
        t = cat.get(name.lower())
        if t is None and s["kind"] != "commons":
            raise SystemExit(f"gapfill/sources.csv: '{name}' is not in the catalogue")
        t = t or {"grp": "", "species": "", "level": "", "row": ""}
        stem = safe_name(name)                                   # as fetch_commons_images.py saved it
        out.append({"file": originals[stem].relative_to(IMG_DIR).as_posix(),
                    "upload file": f"gapfill/{s['folder']}/{stem}.jpg", "role": "primary", "group": t["grp"],
                    "species": t["species"], "catalogue name": t.get("name", name), "level": t["level"],
                    "catalogue row": t["row"], "gapfill": s})
    return out


def in_groups(group, spec):
    if not spec:
        return True
    lo, _, hi = spec.upper().partition("-")
    return lo <= group <= (hi or lo)


def commons_taken(names):
    """Lower-cased file names (no prefix) that exist on Wikimedia Commons."""
    out = set()
    for i in range(0, len(names), 50):
        body = urllib.parse.urlencode({"action": "query", "titles": "|".join("File:" + n for n in names[i:i + 50]),
                                       "format": "json", "formatversion": "2", "maxlag": "5"}).encode()
        for attempt in range(1, MAX_RETRIES + 1):
            req = urllib.request.Request(COMMONS_API, data=body, headers={"User-Agent": UA})
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    pages = json.load(resp)["query"]["pages"]
                break
            except urllib.error.HTTPError as e:
                if e.code != 429 or attempt == MAX_RETRIES:
                    print(f"    Commons check failed ({e}); names clashing with Commons will FAIL on upload")
                    return out
                wait = int(e.headers.get("Retry-After") or 10 * attempt)
                print(f"    Commons rate limit; waiting {wait}s")
                time.sleep(wait)
        for p in pages:
            if not p.get("missing"):
                out.add(p["title"].split(":", 1)[1].lower())
        time.sleep(2)
    return out


def main():
    ap = argparse.ArgumentParser(description="Upload variety images to Wikicrop (dry run by default).")
    ap.add_argument("--run", action="store_true", help="actually upload (prompts for a BotPassword)")
    ap.add_argument("--groups", help="only these catalogue groups, e.g. BT07-BT13 or BT13")
    ap.add_argument("--add-citations", action="store_true",
                    help="no uploads: add the citation to already-uploaded 'no source' files whose source is now known")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many uploads")
    ap.add_argument("--delay", type=float, default=DEFAULT_WRITE_DELAY, help="seconds between uploads")
    ap.add_argument("--skip-same-subject", action="store_true",
                    help="also skip a variety when the wiki already has a differently-pictured file "
                         "with the same name in ASCII (e.g. Buoi-nam-roi.jpg for Bưởi Năm Roi)")
    args = ap.parse_args()
    cfg.require(IMG_DIR)
    sys.stdout.reconfigure(encoding="utf-8")

    rows = [r for r in csv.DictReader(open(IMG_DIR / "manifest.csv", encoding="utf-8-sig"))
            if r["role"] == "primary" and in_groups(r["group"], args.groups)]
    # A gapfill picture takes the place of the snapshot picture of the same name. Commons ones are
    # listed, not uploaded. Pictures in hold/ replace nothing yet.
    gap = [g for g in gapfill_rows() if in_groups(g["group"], args.groups)]
    replaced = {g["catalogue name"] for g in gap if g["gapfill"]["folder"] in GAPFILL_UPLOAD}
    commons = [g for g in gap if g["gapfill"]["kind"] == "commons" or g["gapfill"]["folder"] not in GAPFILL_UPLOAD]
    gap = [g for g in gap if g not in commons]
    rows = [r for r in rows if r["catalogue name"] not in replaced] + gap
    wiki = Wiki()

    print("Reading the wiki's existing files …")
    existing = [f for f in wiki.all_images() if f["mime"].startswith("image/")]
    by_sha = {f["sha1"]: f["name"] for f in existing}
    taken = {norm(f["name"]).replace("_", " ").lower(): f for f in existing}
    # Thumbnail hashes are cached by file name + SHA-1: only files new or re-uploaded since the last
    # run are downloaded again.
    cache = json.loads(HASH_CACHE.read_text(encoding="utf-8")) if HASH_CACHE.exists() else {}
    ex_hash = {f["name"]: cache[f["name"]]["dhash"] for f in existing
               if cache.get(f["name"], {}).get("sha1") == f["sha1"]}
    todo_hash = [f for f in existing if f["name"] not in ex_hash]
    print(f"    thumbnail hashes: {len(ex_hash)} cached, {len(todo_hash)} to fetch")
    turls = wiki.thumbs(["Tập tin:" + f["name"] for f in todo_hash]) if todo_hash else {}
    for f in todo_hash:
        try:
            ex_hash[f["name"]] = dhash(wiki.fetch(turls["Tập tin:" + f["name"].replace("_", " ")] or f["url"]))
            cache[f["name"]] = {"sha1": f["sha1"], "dhash": ex_hash[f["name"]]}
        except Exception as e:  # unreadable thumbnail: exact checks still apply, retried next run
            print(f"    could not hash {f['name']}: {e}")
        time.sleep(READ_DELAY / 2)
    live = {f["name"] for f in existing}
    HASH_CACHE.write_text(json.dumps({k: v for k, v in cache.items() if k in live}, ensure_ascii=False, indent=0),
                          encoding="utf-8")
    ex_slug = {ascii_slug(f["name"]): f["name"] for f in existing}
    page_keys = {_k(p): p for p in wiki.all_pages()}

    # The wiki also shows Wikimedia Commons files (InstantCommons) and refuses a local file under a
    # name Commons already has (fileexists-shared-forbidden). Those names count as taken: ours
    # becomes "<title> 2.jpg", and the Commons photo can be used under the plain name.
    pages = [safe(resolve_title(r["catalogue name"], r["species"], r["level"], page_keys)[0]) for r in rows]
    shared = commons_taken([f"{p}{s}.jpg" for p in pages for s in ("", " 2", " 3")])

    plan, used = [], set()
    for r in rows:
        stem = Path(r["file"]).stem
        path = IMG_DIR / r.get("upload file", f"upload/{stem}.jpg")
        data = path.read_bytes()
        orig = (IMG_DIR / r["file"]).read_bytes()
        page, how = resolve_title(r["catalogue name"], r["species"], r["level"], page_keys)
        title, n = f"{safe(page)}.jpg", 2
        while title.lower() in used or title.lower() in shared:   # second photo: "<title> 2.jpg"
            title, n = f"{safe(page)} {n}.jpg", n + 1
        used.add(title.lower())
        sha = {hashlib.sha1(data).hexdigest(), hashlib.sha1(orig).hexdigest()}
        h = dhash(data)
        near = min(((bin(h ^ eh).count("1"), n) for n, eh in ex_hash.items()), default=(99, ""))
        same_slug = ex_slug.get(ascii_slug(page), "") or ex_slug.get(ascii_slug(stem), "")
        if title.lower() in taken:
            status, note = "skip", "exists: a file with this name is already on the wiki"
        elif sha & by_sha.keys():
            status, note = "skip", f"duplicate of {by_sha[(sha & by_sha.keys()).pop()]}"
        elif r["catalogue name"] in VERIFIED_DUPLICATES:
            status, note = "skip", f"same picture as {VERIFIED_DUPLICATES[r['catalogue name']]} (cropped; checked by eye)"
        elif near[0] <= NEAR_DUP_BITS:
            status, note = "skip", f"near-duplicate of {near[1]} (distance {near[0]})"
        elif same_slug and args.skip_same_subject:
            status, note = "skip", f"same subject: wiki already has {same_slug}"
        else:
            status = "upload"
            note = (f"similar to {near[1]} (distance {near[0]}): check" if near[0] <= SIMILAR_REPORT_BITS else "")
            if same_slug:
                note = (note + "; " if note else "") + f"wiki has a same-named file {same_slug} (different picture)"
        plan.append(dict(r, title=title, page=page, how=how, status=status, note=note, data=data))

    # citation: the verified web source (image-sources.csv). A file with no source found yet goes up
    # tagged with the missing-source maintenance category, to be replaced by a sourced picture later.
    # Pictures that must not go up at all: seen on a stock-photo site, or showing another variety.
    sources = {}
    if SOURCES_CSV.exists():
        sources = {s["catalogue name"]: s for s in csv.DictReader(open(SOURCES_CSV, encoding="utf-8-sig"))
                   if s["status"] in CITABLE}
    blocked = dict(WRONG_PICTURE)
    if REVERSE_CSV.exists():
        blocked.update({r["catalogue name"]: r["why"] for r in csv.DictReader(open(REVERSE_CSV, encoding="utf-8-sig"))
                        if r["why"].startswith("also on a stock-photo site")})
    t = time.localtime()
    access = f"{t.tm_mday} tháng {t.tm_mon} năm {t.tm_year}"
    for p in plan:
        s = sources.get(p["catalogue name"])
        if "gapfill" in p:         # a new picture: the blocks were about the old one, its source is its own
            g = p["gapfill"]
            s = {"page url": g["source page"], "site name": g["author / publisher"], "page title": g["page title"]}
        elif p["catalogue name"] in blocked and p["status"] == "upload":
            p["status"], p["note"] = "blocked", blocked[p["catalogue name"]]
        if s:
            host = urllib.parse.urlsplit(s["page url"]).hostname or ""
            refs = "\n== Tham khảo ==\n" + WEB_REF.format(
                url=s["page url"], site=cite_safe(s["site name"] or host.removeprefix("www.")),
                title=cite_safe(s["page title"] or host), date=access)
        else:
            refs = f"\n{NO_SOURCE_CAT}|{p['title'][:-4]}]]"
        p["web_source"] = s["page url"] if s else ""
        p["text"] = DESCRIPTION.format(page=p["title"][:-4], species=p["species"], refs=refs)

    with open(IMG_DIR / "upload-plan.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["status", "wiki title", "named after", "group", "catalogue name", "cited web source", "note"])
        for p in plan:
            src = "existing page" if p["how"] == "page" else "predicted page title (no page yet)"
            w.writerow([p["status"], "Tập tin:" + p["title"], src, p["group"], p["catalogue name"],
                        p["web_source"] or "—", p["note"]])
        for c in commons:   # not uploaded: articles use the Commons file name as it is
            g = c["gapfill"]
            w.writerow(["commons", "Tập tin:" + g["commons file / local file"], "Wikimedia Commons", c["group"],
                        c["catalogue name"], g["source page"],
                        f"{g['licence']}, {g['author / publisher']}" + (f". On hold: {g['hold reason']}" if g["hold reason"] else "")])
    print(f"to upload with a cited source: {sum(1 for p in plan if p['status'] == 'upload' and p['web_source'])} · "
          f"tagged no source: {sum(1 for p in plan if p['status'] == 'upload' and not p['web_source'])} · "
          f"blocked: {sum(1 for p in plan if p['status'] == 'blocked')} · "
          f"from gapfill/: {sum(1 for p in plan if 'gapfill' in p)} · on Commons, not uploaded: {len(commons)}")
    print(f"named after an existing page: {sum(p['how'] == 'page' for p in plan)} · "
          f"predicted title (no page yet): {sum(p['how'] == 'predicted' for p in plan)}")
    todo = [p for p in plan if p["status"] == "upload"]
    print(f"{len(plan)} images · upload {len(todo)} · skip {len(plan) - len(todo)} "
          f"(exists {sum('exists' in p['note'] for p in plan)}, duplicate {sum(p['note'].startswith('duplicate') for p in plan)}, "
          f"near-duplicate {sum(p['note'].startswith('near') for p in plan)}) · flagged to check "
          f"{sum(1 for p in todo if p['note'])}")
    print("plan:", IMG_DIR / "upload-plan.csv")

    if args.add_citations:
        # Files uploaded as "no source" whose source has been found since: swap the maintenance
        # category for the citation. Only pages still exactly as this script wrote them are touched.
        cited = [p for p in plan if p["web_source"]]
        live = wiki.page_texts(["Tập tin:" + p["title"] for p in cited])
        fix, other = [], 0
        for p in cited:
            text, rev = live.get("Tập tin:" + p["title"], (None, None))
            if text is None:
                continue
            uncited = DESCRIPTION.format(page=p["title"][:-4], species=p["species"],
                                         refs=f"\n{NO_SOURCE_CAT}|{p['title'][:-4]}]]")
            if text.strip() == uncited.strip():
                fix.append((p, rev))
            elif NO_SOURCE_CAT in text:
                other += 1
                print(f"  edited by someone since upload, left alone: Tập tin:{p['title']}")
        print(f"citations to add: {len(fix)}" + (f" · changed pages left alone: {other}" if other else ""))
        for p, _ in fix:
            print(f"  Tập tin:{p['title']}  ←  {p['web_source'][:80]}")
        if not args.run:
            print("Dry run: nothing edited. Re-run with --add-citations --run to edit.")
            return
        user = input(cfg.bot_login_hint()).strip()
        print("Logged in as", wiki.login(user, getpass.getpass("BotPassword: ")))
        done = 0
        for p, rev in fix:
            if args.limit and done >= args.limit:
                break
            ok, msg = wiki.edit("Tập tin:" + p["title"], p["text"], CITE_SUMMARY, rev)
            print(f"  {'OK  ' if ok else 'FAIL'} Tập tin:{p['title']} — {msg}")
            done += ok
            time.sleep(args.delay / 2)
        print(f"edited {done}")
        return

    if not args.run:
        print("Dry run: nothing uploaded. Re-run with --run to upload.")
        return

    user = input(cfg.bot_login_hint()).strip()
    print("Logged in as", wiki.login(user, getpass.getpass("BotPassword: ")))
    done = 0
    for p in todo:
        if args.limit and done >= args.limit:
            break
        ok, msg = wiki.upload(p["title"], p["data"], p["text"], SUMMARY.format(name=p["title"][:-4]))
        print(f"  {'OK  ' if ok else 'FAIL'} Tập tin:{p['title']} — {msg}")
        done += ok
        time.sleep(args.delay)
    print(f"uploaded {done}")


if __name__ == "__main__":
    main()
