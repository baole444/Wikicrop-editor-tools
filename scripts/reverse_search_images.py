#!/usr/bin/env python3
"""
Reverse image search for the variety images whose source was not found on their row's reference
page (find_image_sources.py), using Google Cloud Vision "web detection".

Vision returns the pages that carry the same picture (full match) or a crop/edit of it (partial
match), with their titles, in no useful order: a horse-health blog can come before the seed
company's variety page. So every answer is kept (variety-images/vision-cache/) and the pages are
ranked here:
    title or URL names the variety · then the crop · full match before partial · Vietnamese
    site (.gov.vn / .edu.vn first) · marketplaces and social media never chosen.
A picture that Vision also finds on a stock-photo site (iStock, Shutterstock …) is flagged: it is
probably a licensed photo and must not be uploaded, whatever page cites it.

Setup (once): in Google Cloud Console enable "Cloud Vision API", link billing to the project and
create an API key (APIs & Services → Credentials). Free tier: the first 1,000 images a month.
Put the key in an environment variable; it is never stored by this script:

    PowerShell:  $env:GOOGLE_VISION_API_KEY = "..."
    python scripts/reverse_search_images.py            # only images not yet verified
    python scripts/reverse_search_images.py --all      # every image
    python scripts/reverse_search_images.py --rerank   # no API calls: re-rank the cached answers
    python scripts/reverse_search_images.py --rerank --read-pages   # + open `check` pages, look for the variety in the text

Writes variety-images/reverse-search.csv. `proposal` says what to do with each row:
    accept   full match on a page that names the variety
    check    a match whose page names only the crop, or a partial match naming the variety — look first
    reject   stock photo, or nothing usable
Rows you accept: copy page url / title / site into image-sources.csv for that variety and set its
status to `confirmed`; the upload script cites `verified` and `confirmed` rows.
"""
import argparse
import base64
import csv
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import project_config as cfg  # noqa: E402

IMG = cfg.IMAGES
CACHE = IMG / "vision-cache"
ENDPOINT = "https://vision.googleapis.com/v1/images:annotate?key={key}"
DELAY = 0.5
# Pages that are never a citable original: marketplaces, social media, image hosts, Wikicrop itself.
SKIP_DOMAINS = re.compile(r"(facebook|fbcdn|instagram|pinterest|shopee|lazada|tiki|tiktok|youtube|reddit|"
                          r"alibaba|aliexpress|amazon|ebay|flipkart|meesho|tradeindia|indiamart|accio|"
                          r"wikicrop\.lab\.io\.vn|googleusercontent)\.", re.I)
# Seeing the picture here means it is most likely a licensed stock photo.
STOCK_DOMAINS = re.compile(r"(istockphoto|gettyimages|shutterstock|alamy|dreamstime|123rf|depositphotos|"
                           r"stock\.adobe|adobestock|pond5|bigstockphoto|vecteezy|freepik|canstockphoto|"
                           r"agefotostock|superstock|alphacoders)\.", re.I)


def detect(key, data):
    body = json.dumps({"requests": [{"image": {"content": base64.b64encode(data).decode()},
                                     "features": [{"type": "WEB_DETECTION", "maxResults": 50}]}]}).encode()
    req = urllib.request.Request(ENDPOINT.format(key=key), data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out = json.load(r)["responses"][0]
    except urllib.error.HTTPError as e:
        # Google explains the refusal in the body (billing off, API not enabled, key restricted …).
        try:
            msg = json.load(e).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        raise SystemExit(f"Vision API refused the request (HTTP {e.code}): {msg or e.reason}")
    if "error" in out:
        raise RuntimeError(out["error"].get("message"))
    return out.get("webDetection", {})


def flat(s):
    """Lower case, no accents, letters and digits only: 'Xoài Cát Hòa Lộc' -> 'xoaicathoaloc'."""
    s = unicodedata.normalize("NFD", s.replace("đ", "d").replace("Đ", "D"))
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in s if unicodedata.category(c) != "Mn").lower())


def names_for(catalogue_name, species):
    """-> (variety spellings, crop spellings), flattened."""
    parts = [catalogue_name.split("(")[0]] + re.findall(r"\(([^)]*)\)", catalogue_name)
    variety = {flat(x) for p in parts for x in p.split("/") if len(flat(x)) >= 4}
    crops = {flat(x) for x in species.split("(")[0].split("/") if len(flat(x)) >= 3}
    # The variety without its crop word too ('xoaicathoaloc' -> 'cathoaloc'), if still distinctive.
    for v in list(variety):
        for c in crops:
            if v.startswith(c) and len(v) - len(c) >= 4:
                variety.add(v[len(c):])
    return variety, crops


def rank(wd, catalogue_name, species):
    """-> (best page or None, other candidates, stock sites seen)"""
    variety, crops = names_for(catalogue_name, species)
    pages, stock = [], set()
    for p in wd.get("pagesWithMatchingImages", []):
        url = p.get("url", "")
        host = (urllib.parse.urlsplit(url).hostname or "").removeprefix("www.")
        kind = "full" if p.get("fullMatchingImages") else ("partial" if p.get("partialMatchingImages") else "similar")
        if STOCK_DOMAINS.search(url) and kind != "similar":
            stock.add(host)
        if not url or SKIP_DOMAINS.search(url) or STOCK_DOMAINS.search(url) or kind == "similar":
            continue
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", p.get("pageTitle", ""))).strip()
        text = flat(title + " " + urllib.parse.unquote(url))
        names = "variety" if any(v in text for v in variety) else ("crop" if any(c in text for c in crops) else "")
        # A page naming the variety beats an exact copy on an unrelated page; an exact copy breaks ties.
        score = ((kind == "full") * 50 + {"variety": 60, "crop": 25, "": 0}[names]
                 + host.endswith((".gov.vn", ".edu.vn")) * 10 + host.endswith(".vn") * 5)
        pages.append(dict(kind=kind, url=url, title=title, host=host, names=names, score=score))
    pages.sort(key=lambda x: -x["score"])
    return (pages[0] if pages else None), pages[1:], sorted(stock)


def page_text_names(url, variety, seen):
    """Open the page and look for the variety in its text (cached in vision-cache/pages.json)."""
    if url not in seen:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (WikicropImageSourceCheck/1.0)",
                                                       "Accept-Language": "vi,en;q=0.8"})
            with urllib.request.urlopen(req, timeout=30) as r:
                text = r.read(5_000_000).decode("utf-8", "replace")
            text = re.sub(r"<(script|style)\b.*?</\1>", " ", text, flags=re.S | re.I)
            seen[url] = flat(re.sub(r"<[^>]+>", " ", text))[:400_000]
        except Exception as e:
            seen[url] = "!" + type(e).__name__
        time.sleep(0.5)
    return not seen[url].startswith("!") and any(v in seen[url] for v in variety)


def read_pages(rows_by_name, answers, manifest):
    """For `check` rows, upgrade to `accept` when a matching page's text names the variety."""
    store = CACHE / "pages.json"
    seen = json.loads(store.read_text(encoding="utf-8")) if store.exists() else {}
    for name, row in rows_by_name.items():
        if row[0] != "check":
            continue
        variety, _ = names_for(name, manifest[name]["species"])
        best, others, _ = rank(answers[name], name, manifest[name]["species"])
        for p in sorted([best] + others, key=lambda x: x["kind"] != "full"):   # exact copies first
            if p["names"] != "variety" and page_text_names(p["url"], variety, seen):
                p["names"] = "variety (in text)"
            if p["names"].startswith("variety"):
                row[:2] = ["accept" if p["kind"] == "full" else "check",
                           "" if p["kind"] == "full" else "partial match (cropped or edited copy)"]
                if p["names"] == "variety (in text)":
                    row[1] = (row[1] + "; " if row[1] else "") + "variety named in the page text"
                row[4:9] = [p["kind"], p["names"], p["url"], p["title"], p["host"]]
                break
        print(f"  {row[0]:6} {name[:36]:36}  {row[8]}")
        store.write_text(json.dumps(seen, ensure_ascii=False), encoding="utf-8")


def proposal(best, stock):
    if stock:
        return "reject", "also on a stock-photo site: " + ", ".join(stock)
    if not best:
        return "reject", "no usable page"
    if best["kind"] == "full" and best["names"] == "variety":
        return "accept", ""
    if best["names"] == "variety":
        return "check", "partial match (cropped or edited copy)"
    if best["names"] == "crop":
        return "check", "page names the crop, not the variety"
    return "check" if best["kind"] == "full" else "reject", "page does not name the crop"


def main():
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="search every image, not only the unverified ones")
    ap.add_argument("--rerank", action="store_true", help="no API calls; rank the cached answers again")
    ap.add_argument("--read-pages", action="store_true", help="open the pages of `check` rows and look for the variety in the text")
    args = ap.parse_args()
    cfg.require(IMG)
    key = os.environ.get("GOOGLE_VISION_API_KEY")
    if not key and not args.rerank:
        sys.exit("Set GOOGLE_VISION_API_KEY first (see the docstring).")

    manifest = {r["catalogue name"]: r for r in csv.DictReader(open(IMG / "manifest.csv", encoding="utf-8-sig"))
                if r["role"] == "primary"}
    status = {}
    if (IMG / "image-sources.csv").exists():
        status = {r["catalogue name"]: r["status"] for r in csv.DictReader(open(IMG / "image-sources.csv", encoding="utf-8-sig"))}
    todo = [n for n in manifest if args.all or status.get(n) not in ("verified", "confirmed")]
    CACHE.mkdir(exist_ok=True)
    print(f"{'ranking' if args.rerank else 'searching'} {len(todo)} images")

    rows, answers = [], {}
    for i, name in enumerate(todo, 1):
        m = manifest[name]
        cached = CACHE / (Path(m["file"]).stem + ".json")
        if cached.exists():
            wd = json.loads(cached.read_text(encoding="utf-8"))
        elif args.rerank:
            continue
        else:
            try:
                wd = detect(key, (IMG / "upload" / (Path(m["file"]).stem + ".jpg")).read_bytes())
            except RuntimeError as e:
                rows.append(["reject", str(e)[:120], name, m["group"], "error", "", "", "", "", ""])
                print(f"[{i}/{len(todo)}] error  {name}: {e}")
                continue
            cached.write_text(json.dumps(wd, ensure_ascii=False), encoding="utf-8")
            time.sleep(DELAY)
        answers[name] = wd
        best, others, stock = rank(wd, name, m["species"])
        verdict, why = proposal(best, stock)
        b = best or dict(kind="none", url="", title="", host="", names="")
        rows.append([verdict, why, name, m["group"], b["kind"], b["names"], b["url"], b["title"], b["host"],
                     " | ".join(f"{o['kind']}{'+' + o['names'] if o['names'] else ''}: {o['title'][:50]} <{o['url']}>"
                                for o in others[:4])])
        print(f"[{i}/{len(todo)}] {verdict:6} {name[:36]:36}  {b['host']}")

    if args.read_pages:
        print("reading the pages of the check rows …")
        read_pages({r[2]: r for r in rows if r[2] in answers}, answers, manifest)

    with open(IMG / "reverse-search.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["proposal", "why", "catalogue name", "group", "match", "page names", "page url", "page title",
                    "site", "other candidates"])
        w.writerows(rows)
    from collections import Counter
    print(Counter(r[0] for r in rows))


if __name__ == "__main__":
    main()
