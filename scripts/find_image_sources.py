#!/usr/bin/env python3
"""
Find the web page each variety image came from, so its file page can cite it.

The snapshot workbooks keep no source for the pictures themselves. They do keep, on the same row,
the reference the sheet's authors used for the variety (column F). This script opens that page,
downloads the pictures on it, and compares them with ours (perceptual hash). A citation is only
proposed when the picture is actually found there: a reference URL alone proves nothing about
where an image came from.

    python scripts/find_image_sources.py            # writes variety-images/image-sources.csv
    python scripts/find_image_sources.py --retry    # re-check only the rows that were unreadable

Status per image:
    verified   the picture is on that page (dHash distance ≤ VERIFIED_BITS)
    probable   close match (≤ PROBABLE_BITS) — look before citing
    not found  the page was read but the picture is not on it
    no url     the row has no web reference
    unreadable page or PDF could not be checked

Polite: one request at a time, a pause between requests, pages and image hashes cached per URL.
"""
import csv
import html
import io
import re
import argparse
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import project_config as cfg  # noqa: E402

IMG = cfg.IMAGES
BOOKS = cfg.SOURCE_WORKBOOKS
UA = "Mozilla/5.0 (" + cfg.user_agent("WikicropImageSourceCheck", "image source check") + ")"
DELAY = 0.6
MAX_IMAGES_PER_PAGE = 60
VERIFIED_BITS = 5
PROBABLE_BITS = 10
SKIP_IMG = re.compile(r"logo|icon|avatar|banner|sprite|button|flag|emoji|loading|placeholder|ads?[_/-]|qr", re.I)

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
RID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def row_references(path):
    """Snapshot `Thông tin loài` column F per row: (text, [urls]) with cell hyperlinks first."""
    z = zipfile.ZipFile(path)
    sst = ["".join(t.text or "" for t in si.iter(NS + "t")) for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(NS + "si")]
    sh = ET.fromstring(z.read("xl/worksheets/sheet2.xml"))
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/worksheets/_rels/sheet2.xml.rels"))}
    links = {h.get("ref"): rels.get(h.get(RID)) for h in sh.iter(NS + "hyperlink") if h.get(RID)}
    out = {}
    for row in sh.iter(NS + "row"):
        for c in row.iter(NS + "c"):
            if re.fullmatch(r"F\d+", c.get("r")):
                v = c.find(NS + "v")
                val = sst[int(v.text)] if c.get("t") == "s" and v is not None else (v.text if v is not None else "")
                urls = ([links[c.get("r")]] if c.get("r") in links else []) + re.findall(r"https?://[^\s)\]>,;\"']+", val or "")
                out[int(row.get("r"))] = (val or "", list(dict.fromkeys(u for x in urls for u in clean_urls(x))))
    return out


def clean_urls(url):
    """Split URLs glued together in the cell ("...bap-myhttps:/vi.wikipedia...", "...html%20http...")
    and percent-encode accented paths, which urllib cannot send as they are."""
    out = []
    for part in re.split(r"(?:%20)?(?=https?:/)", url):
        part = re.sub(r"^(https?):/(?!/)", r"\1://", part.strip().rstrip("."))
        if part:
            out.append(urllib.parse.quote(part, safe=":/?&=%#+,;@!$'()*~[]"))
    return out


def dhash(img):
    g = img.convert("L").resize((9, 8), Image.LANCZOS)
    p = list(g.getdata())
    return sum(1 << i for i, (a, b) in enumerate((p[r * 9 + c], p[r * 9 + c + 1]) for r in range(8) for c in range(8)) if a > b)


def get(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "vi,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=45) as r:
        ctype = r.headers.get("Content-Type", "")
        data = r.read(15_000_000)
    time.sleep(DELAY)
    return data, ctype


def page_info(url, cache):
    """-> dict(title, site, hashes=[(dhash, img_url)], error)"""
    if url in cache:
        return cache[url]
    info = dict(title="", site="", hashes=[], error="")
    try:
        data, ctype = get(url)
        if "pdf" in ctype.lower() or url.lower().endswith(".pdf"):
            info["error"] = "pdf"
        else:
            text = data.decode("utf-8", "replace")
            meta = lambda prop: (re.search(rf'<meta[^>]+(?:property|name)=["\']{prop}["\'][^>]+content=["\']([^"\']+)', text, re.I) or
                                 re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{prop}["\']', text, re.I))
            t = meta("og:title") or re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
            s = meta("og:site_name")
            info["title"] = html.unescape(re.sub(r"\s+", " ", t.group(1))).strip() if t else ""
            info["site"] = html.unescape(s.group(1)).strip() if s else ""
            srcs = []
            og = meta("og:image")
            if og:
                srcs.append(og.group(1))
            for m in re.finditer(r"<img\b[^>]*>", text, re.I):
                tag = m.group(0)
                for attr in ("data-lazy-src", "data-src", "data-original", "src"):
                    a = re.search(rf'\s{attr}=["\']([^"\']+)', tag, re.I)
                    if a and not a.group(1).startswith("data:"):
                        srcs.append(a.group(1))
                        break
                ss = re.search(r'\ssrcset=["\']([^"\']+)', tag, re.I)
                if ss:   # largest candidate from srcset
                    srcs.append(ss.group(1).split(",")[-1].strip().split(" ")[0])
            seen = []
            for s in srcs:
                u = urllib.parse.urljoin(url, html.unescape(s))
                if u not in seen and not SKIP_IMG.search(u) and not u.lower().endswith((".svg", ".gif")):
                    seen.append(u)
            for u in seen[:MAX_IMAGES_PER_PAGE]:
                try:
                    d, _ = get(u, binary=True)
                    im = Image.open(io.BytesIO(d))
                    if min(im.size) >= 120:
                        info["hashes"].append((dhash(im), u))
                except Exception:
                    continue
    except Exception as e:
        info["error"] = type(e).__name__
    cache[url] = info
    return info


def main():
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    retry = "--retry" in sys.argv   # re-check only the rows marked unreadable last time
    cfg.require(IMG / "manifest.csv", *BOOKS.values())
    refs = {tag: row_references(p) for tag, p in BOOKS.items()}
    rows = [r for r in csv.DictReader(open(IMG / "manifest.csv", encoding="utf-8-sig")) if r["role"] == "primary"]
    previous = {}
    if retry:
        with open(IMG / "image-sources.csv", encoding="utf-8-sig") as f:
            previous = {r[1]: r for r in list(csv.reader(f))[1:]}
    cache, out = {}, []
    for i, r in enumerate(rows, 1):
        if retry and previous.get(r["catalogue name"], ["unreadable"])[0] != "unreadable":
            out.append(previous[r["catalogue name"]])
            continue
        text, urls = refs[r["source"]].get(int(r["source row (Thông tin loài)"]), ("", []))
        ours = dhash(Image.open(IMG / r["file"]))
        best = (99, "", "", "", "")
        errors = []
        for u in urls:
            info = page_info(u, cache)
            if info["error"]:
                errors.append(f"{info['error']}: {u}")
                continue
            for h, img_url in info["hashes"]:
                d = bin(h ^ ours).count("1")
                if d < best[0]:
                    best = (d, u, img_url, info["title"], info["site"])
        if not urls:
            status = "no url"
        elif best[0] <= VERIFIED_BITS:
            status = "verified"
        elif best[0] <= PROBABLE_BITS:
            status = "probable"
        elif errors and best[0] == 99:
            status = "unreadable"
        else:
            status = "not found"
        out.append([status, r["catalogue name"], r["group"], best[1] if status in ("verified", "probable") else (urls[0] if urls else ""),
                    best[3] if status in ("verified", "probable") else "", best[4] if status in ("verified", "probable") else "",
                    best[2] if status in ("verified", "probable") else "", best[0] if best[0] < 99 else "",
                    "; ".join(errors), text[:200]])
        print(f"[{i}/{len(rows)}] {status:10} {r['catalogue name'][:40]}")
    with open(IMG / "image-sources.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["status", "catalogue name", "group", "page url", "page title", "site name", "image url on page",
                    "distance", "errors", "row reference (F)"])
        w.writerows(out)
    from collections import Counter
    print(Counter(o[0] for o in out))


if __name__ == "__main__":
    _ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _ap.add_argument("--retry", action="store_true", help="re-check only the rows marked unreadable last time")
    _ap.parse_args()
    main()
