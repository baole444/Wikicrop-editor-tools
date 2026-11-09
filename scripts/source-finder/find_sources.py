"""Find replacement sources for `Thông tin loài` rows that have no usable reference.

The tool gathers candidates, fetches them, and ranks them for a person to review. It never decides
on its own: every source that goes into the sheet is still read and checked by hand.

Steps (each writes into out/, and each can be rerun on its own):

    python scripts/source-finder/find_sources.py queue            # rows to source -> out/queue.csv
    python scripts/source-finder/find_sources.py search           # candidates     -> out/candidates.csv
    python scripts/source-finder/find_sources.py fetch            # page text      -> .cache/pages/
    python scripts/source-finder/find_sources.py report           # ranking        -> out/report.md, out/ranked.csv
    python scripts/source-finder/find_sources.py book "Đỗ Tất Lợi Những cây thuốc"   # does a book exist?

`queue` reads a CSV given with --input (UTF-8, header row) with at least these columns:
    variety   the catalogue name of the row, aliases in brackets
    E         the row's current description text (the claims to be sourced)
    tier      the row's source status; rows whose tier is in --tiers (default `nolink`) are queued
    python scripts/source-finder/find_sources.py queue --input rows_to_source.csv
Edit
out/queue.csv by hand to add aliases or queries; `queue` will not overwrite it without --force.

Search providers (`search --provider ...`; default: every provider that has its keys):
  cse     Google Custom Search JSON API. Needs GOOGLE_API_KEY and GOOGLE_CSE_ID (a Programmable
          Search Engine set to search the whole web). 100 free queries a day, then paid.
  gemini  Gemini API with Google Search grounding. Needs GEMINI_API_KEY (or GOOGLE_API_KEY with the
          Generative Language API enabled). Returns the pages Gemini's search used. Model:
          GEMINI_MODEL, default gemini-2.5-flash.
  manual  out/manual_candidates.csv (columns variety,url,note): URLs found by hand.
          Always read; needs no key.
`book` uses the Google Books API. Set GOOGLE_API_KEY with the Books API enabled: without a key,
requests share one anonymous daily quota that is usually used up (HTTP 429, seen 09/10/2026).

Keys are read from the environment, or from a KEY=value file outside the project:
%USERPROFILE%\\.config\\wikicrop\\source-finder.env (override with SOURCE_FINDER_ENV). Keep them
out of the project folder, which gets exported and shared. API responses are cached in .cache/,
so a rerun does not spend quota again.
"""
import argparse
import concurrent.futures
import csv
import hashlib
import html.parser
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
OUT = HERE / "out"
CACHE = HERE / ".cache"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"

# Source preference, from the writing standard (section 2.2): institutional, then breeder, then press, then
# commercial last. A domain matches if it equals an entry or ends with "." + entry.
INSTITUTIONAL = [
    "gov.vn", "edu.vn", "vaas.vn", "sofri.org.vn", "fcri.com.vn", "favri.org.vn", "ias.vn",
    "ioop.org.vn", "vjol.info.vn", "vista.gov.vn", "doi.org", "fao.org", "ishs.org", "cabi.org",
    "ncbi.nlm.nih.gov", "sciencedirect.com", "springer.com", "mdpi.com", "researchgate.net",
    "prosea.prota4u.org", "worldagroforestry.org", "wikipedia.org",
]
PRESS = [
    "nhandan.vn", "nongnghiepmoitruong.vn", "nongnghiep.vn", "tuoitre.vn", "thanhnien.vn",
    "vnexpress.net", "vietnamplus.vn", "sggp.org.vn", "tapchicongthuong.vn", "danviet.vn",
    "vietnamnet.vn", "laodong.vn", "qdnd.vn", "daibieunhandan.vn", "plo.vn", "baochinhphu.vn",
    "vtv.vn", "vov.vn", "dantri.com.vn", "nld.com.vn", "baocantho.com.vn", "thesaigontimes.vn",
    "vnbusiness.vn", "vneconomy.vn", "kinhtedothi.vn", "baomoi.com",
]
SALES = [  # nurseries and shops: never the only source for a number
    "welofarm.vn", "agridrone.vn", "vuakienggiare.com", "greenvibes.vn", "caygiongphucan.com",
    "cayantrai.vn", "cayantraidetrong.com", "caygiongcantho.vn", "giongcaytrong.org",
    "maivanglongan.com", "traicayhoabien.com", "saurienggialai.com", "happyagri.com.vn",
    "halan.net", "agrifarmvn.com", "dienmayxanh.com", "nhathuoclongchau.com.vn", "pharmacity.vn",
    "shopee.vn", "lazada.vn", "tiki.vn",
]
TIER_RANK = {"institutional": 0, "press": 1, "other": 2, "sales": 3}


# ---------------------------------------------------------------- keys, cache, http

def load_env():
    path = Path(os.environ.get("SOURCE_FINDER_ENV",
                               Path.home() / ".config" / "wikicrop" / "source-finder.env"))
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Z_]+)\s*=\s*(.*?)\s*$", line)
            if m and not line.lstrip().startswith("#"):
                os.environ.setdefault(m.group(1), m.group(2).strip('"\''))


def cached(kind, key, make):
    """Return make() for this key, cached as JSON under .cache/<kind>/."""
    d = CACHE / kind
    d.mkdir(parents=True, exist_ok=True)
    f = d / (hashlib.sha1(key.encode("utf-8")).hexdigest()[:20] + ".json")
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    value = make()
    f.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return value


def http(url, data=None, headers=None, timeout=40):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.geturl(), r.headers, r.read()


def api_json(url, body=None, key_header=None):
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if key_header:
        headers["x-goog-api-key"] = key_header       # header, so the key never sits in a URL
    data = json.dumps(body).encode() if body is not None else None
    try:
        return json.loads(http(url, data, headers, timeout=120)[3])
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"API error {e.code} for {url.split('?')[0]}: {msg}")


# ---------------------------------------------------------------- names and claims

def norm(s):
    s = unicodedata.normalize("NFC", str(s or "")).lower()
    s = re.sub(r"[‐‑‒–—−]", "-", s)
    return re.sub(r"\s+", " ", s)


CROP_SYNONYMS = {"Dứa": ["Khóm", "Thơm"], "Khóm": ["Dứa"]}


def names_of(variety):
    """Search names for a sheet name, main name first.

    'Mít Thái siêu sớm (Changai)' -> Mít Thái siêu sớm, Changai
    'Húng quế tía / đỏ'           -> Húng quế tía / đỏ, Húng quế tía, Húng quế đỏ
    'Dứa Queen địa phương'        -> Dứa Queen địa phương, Dứa Queen, Khóm Queen, Thơm Queen
    'Sầu riêng Musang King'       -> Sầu riêng Musang King, Musang King
    """
    main = re.sub(r"\(.*?\)", "", variety).strip()
    out = [main]
    m = re.match(r"(.+?)\s+(\S+)\s*/\s*(\S+)$", main)            # 'Húng quế tía / đỏ'
    if m:
        out += [f"{m.group(1)} {m.group(2)}", f"{m.group(1)} {m.group(3)}"]
    out += [x.strip() for e in re.findall(r"\((.*?)\)", variety) for x in e.split("/")]
    short = re.sub(r"\s+địa phương$", "", main)
    out.append(short)
    words = short.split()
    for crop, syns in CROP_SYNONYMS.items():
        if words and words[0] == crop:
            out += [" ".join([s] + words[1:]) for s in syns]
    # Durian cultivar names stand alone in Vietnamese text ('Musang King', 'Kanyao'); other crops'
    # suffixes ('Queen', 'Victoria', 'Thái siêu sớm') are too generic to match on.
    m = re.match(r"Sầu riêng\s+([A-Z0-9].*)$", short)
    if m:
        out.append(m.group(1))
    # Drop Latin binomials ('Artocarpus integer') and very short names.
    return [n for n in dict.fromkeys(o.strip() for o in out)
            if len(n) > 2 and not re.fullmatch(r"[A-Z][a-z]+ [a-z]+", n)]


def flat(s):
    """For comparing figures: no spaces, '2,5' == '2.5', any dash == '-'."""
    return re.sub(r"\s+", "", norm(s)).replace(",", ".")


def claims_of(text):
    """Figures in a description, e.g. '12-15 tháng', '7-7,5%', '30-40g' -> normalised strings."""
    found = re.findall(r"\d+(?:[.,]\d+)?(?:\s*-\s*\d+(?:[.,]\d+)?)?\s*(?:%|kg|g\b|tấn|ml|cm|ngày|tháng|năm)",
                       norm(text))
    return list(dict.fromkeys(re.sub(r"\s+", "", c) for c in found))


# ---------------------------------------------------------------- queue

def cmd_queue(args):
    q = OUT / "queue.csv"
    if q.exists() and not args.force:
        sys.exit(f"{q} exists (it may hold hand edits). Use --force to rebuild it.")
    with open(args.input, encoding="utf-8-sig", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r["tier"] in args.tiers]
    OUT.mkdir(exist_ok=True)
    with open(q, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["variety", "names", "claims", "queries"])
        for r in rows:
            names = names_of(r["variety"])
            plain = [n for n in names if "/" not in n]
            main, alt = plain[0], plain[1] if len(plain) > 1 else None
            # Three queries a row keeps the 22 rows (66 queries) inside CSE's 100 free a day.
            queries = [f'"{main}" giống đặc điểm', f'"{main}" viện OR khuyến nông OR "đại học"',
                       f'"{alt}" giống' if alt else f'"{main}" năng suất vùng trồng']
            w.writerow([r["variety"], " | ".join(names), " | ".join(claims_of(r["E"])), " | ".join(queries)])
    print(f"{len(rows)} rows -> {q}")


def read_queue():
    with open(OUT / "queue.csv", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        for k in ("names", "claims", "queries"):
            r[k] = [x.strip() for x in r[k].split("|") if x.strip()]
    return rows


# ---------------------------------------------------------------- search providers

def search_cse(query):
    key, cx = os.environ["GOOGLE_API_KEY"], os.environ["GOOGLE_CSE_ID"]
    url = "https://customsearch.googleapis.com/customsearch/v1?" + urllib.parse.urlencode(
        {"cx": cx, "q": query, "num": 10, "gl": "vn", "hl": "vi"})
    res = cached("cse", query, lambda: api_json(url, key_header=key))
    return [dict(url=i["link"], title=i.get("title", ""), snippet=i.get("snippet", ""))
            for i in res.get("items", [])]


def search_gemini(variety, names):
    key = os.environ.get("GEMINI_API_KEY") or os.environ["GOOGLE_API_KEY"]
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    prompt = (f"Tìm các nguồn đáng tin cậy mô tả giống cây trồng \"{variety}\" tại Việt Nam: ưu tiên viện "
              f"nghiên cứu (Viện Cây ăn quả miền Nam, VAAS, Viện Nghiên cứu Rau quả), trung tâm khuyến nông, "
              f"trường đại học, quyết định công nhận giống hoặc chỉ dẫn địa lý, rồi đến báo chí chính thống. "
              f"Bỏ qua trang bán cây giống. Tên khác: {', '.join(names)}. Nêu ngắn gọn đặc điểm quả, năng "
              f"suất, vùng trồng, nhược điểm, và nguồn của từng ý.")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}], "tools": [{"google_search": {}}]}
    res = cached("gemini", model + "|" + prompt, lambda: api_json(url, body, key_header=key))
    cand = (res.get("candidates") or [{}])[0]
    answer = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
    chunks = cand.get("groundingMetadata", {}).get("groundingChunks", [])
    # Grounding URIs are redirect links; `fetch` follows them to the real page.
    return [dict(url=c["web"]["uri"], title=c["web"].get("title", ""), snippet=answer[:300])
            for c in chunks if "web" in c]


def cmd_search(args):
    queue = read_queue()
    have = {"cse": bool(os.environ.get("GOOGLE_API_KEY") and os.environ.get("GOOGLE_CSE_ID")),
            "gemini": bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))}
    providers = args.provider or [p for p, ok in have.items() if ok]
    for p in providers:
        if p in have and not have[p]:
            sys.exit(f"provider {p}: keys not set (see the module docstring)")
    only = set(args.only or [])
    rows = []
    for r in queue:
        if only and r["variety"] not in only:
            continue
        if "cse" in providers:
            for q in r["queries"]:
                rows += [dict(variety=r["variety"], provider="cse", query=q, **h) for h in search_cse(q)]
        if "gemini" in providers:
            rows += [dict(variety=r["variety"], provider="gemini", query="", **h)
                     for h in search_gemini(r["variety"], r["names"])]
        if providers:
            print(f"{r['variety']}: {sum(1 for x in rows if x['variety'] == r['variety'])} hits")
    manual = OUT / "manual_candidates.csv"
    if manual.exists():
        with open(manual, encoding="utf-8-sig", newline="") as fh:
            rows += [dict(variety=m["variety"], provider="manual", query="", url=m["url"],
                          title="", snippet=m.get("note", "")) for m in csv.DictReader(fh)
                     if not only or m["variety"] in only]
    if not providers and not manual.exists():
        sys.exit("No provider keys set and no out/manual_candidates.csv; nothing to search with.")
    seen, out = set(), []
    for r in rows:
        if (r["variety"], r["url"]) not in seen:
            seen.add((r["variety"], r["url"]))
            out.append(r)
    with open(OUT / "candidates.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, ["variety", "provider", "query", "url", "title", "snippet"])
        w.writeheader()
        w.writerows(out)
    print(f"{len(out)} candidates -> {OUT / 'candidates.csv'} (providers: {', '.join(providers) or 'manual only'})")


# ---------------------------------------------------------------- fetch

class TextParser(html.parser.HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.depth, self.meta, self.title, self._in_title = [], 0, {}, "", False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.SKIP:
            self.depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            k = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if k and a.get("content"):
                self.meta[k] = a["content"]
        if tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth:
            self.depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.depth:
            self.parts.append(data)


def page_text(raw, ctype):
    if "pdf" in ctype or raw[:5] == b"%PDF-":
        exe = shutil.which("pdftotext")
        if not exe:
            return {"title": "", "date": "", "text": "", "note": "PDF, pdftotext not found"}
        tmp = CACHE / "tmp.pdf"
        tmp.write_bytes(raw)
        # -enc UTF-8: on Windows pdftotext defaults to Latin-1 and drops Vietnamese letters.
        text = subprocess.run([exe, "-enc", "UTF-8", "-layout", str(tmp), "-"],
                              capture_output=True).stdout.decode("utf-8", "replace")
        text = unicodedata.normalize("NFC", text)
        return {"title": text.strip().split("\n")[0][:150], "date": "", "text": text, "note": "pdf"}
    m = re.search(rb'charset=["\']?([\w-]+)', raw[:3000])
    html_ = raw.decode((m.group(1).decode() if m else "utf-8"), "replace")
    p = TextParser()
    p.feed(html_)
    meta = p.meta
    date = next((meta[k] for k in ("article:published_time", "pubdate", "datepublished", "date",
                                   "dc.date.issued", "citation_publication_date") if k in meta), "")
    title = meta.get("og:title") or meta.get("citation_title") or p.title
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.parts))
    text = re.sub(r"\n\s*\n+", "\n", text)
    return {"title": title.strip(), "date": date[:10], "text": text, "note": ""}


def fetch_one(url):
    def make():
        try:
            status, final, headers, raw = http(url)
            info = page_text(raw, headers.get("Content-Type", ""))
            return dict(url=url, final=final, status=status, **info)
        except urllib.error.HTTPError as e:
            return dict(url=url, final=url, status=e.code, title="", date="", text="", note="")
        except Exception as e:                                   # DNS, refused, timeout, TLS
            return dict(url=url, final=url, status=0, title="", date="", text="", note=type(e).__name__)
    return cached("pages", url, make)


def cmd_fetch(args):
    with open(OUT / "candidates.csv", encoding="utf-8-sig", newline="") as fh:
        urls = list(dict.fromkeys(r["url"] for r in csv.DictReader(fh)))
    with concurrent.futures.ThreadPoolExecutor(args.workers) as ex:
        for i, page in enumerate(ex.map(fetch_one, urls), 1):
            if i % 20 == 0 or i == len(urls):
                print(f"fetched {i}/{len(urls)}")
    print("pages cached in", CACHE / "pages")


# ---------------------------------------------------------------- report

def tier_of(url):
    host = urllib.parse.urlparse(url).hostname or ""
    host = host[4:] if host.startswith("www.") else host

    def hit(lst):
        return any(host == d or host.endswith("." + d) for d in lst)
    if hit(SALES):
        return "sales"
    if hit(INSTITUTIONAL):
        return "institutional"
    if hit(PRESS) or re.match(r"bao[\w-]*\.(vn|com\.vn)$", host):
        return "press"
    return "other"


def snippets(text, needle, n=2, width=160):
    t = norm(text)
    out = []
    for m in re.finditer(re.escape(norm(needle)), t):
        s = text[max(0, m.start() - width): m.end() + width]
        out.append(re.sub(r"\s+", " ", s).strip())
        if len(out) >= n:
            break
    return out


def cmd_report(args):
    queue = {r["variety"]: r for r in read_queue()}
    with open(OUT / "candidates.csv", encoding="utf-8-sig", newline="") as fh:
        cands = list(csv.DictReader(fh))
    ranked = []
    for c in cands:
        q = queue.get(c["variety"])
        if not q:
            continue
        p = fetch_one(c["url"])
        t = norm(p["text"])
        name_hits = {n: t.count(norm(n)) for n in q["names"]}
        ft = flat(t)
        claim_hits = [x for x in q["claims"] if flat(x) in ft]
        tier = tier_of(p["final"])
        ok = p["status"] == 200 and bool(p["text"].strip())
        score = (TIER_RANK[tier], -sum(name_hits.values()) if ok else 1, -len(claim_hits))
        ranked.append(dict(variety=c["variety"], tier=tier, status=p["status"], url=p["final"],
                           title=p["title"], date=p["date"], provider=c["provider"],
                           name_hits=sum(name_hits.values()), claims=" | ".join(claim_hits),
                           note=p["note"], usable=ok and sum(name_hits.values()) > 0, _score=score,
                           _snips=[s for n in q["names"] for s in snippets(p["text"], n, 1)][:2]))
    ranked.sort(key=lambda r: (list(queue).index(r["variety"]), not r["usable"], r["_score"]))

    with open(OUT / "ranked.csv", "w", encoding="utf-8-sig", newline="") as fh:
        cols = ["variety", "usable", "tier", "status", "name_hits", "claims", "title", "date", "url",
                "provider", "note"]
        w = csv.DictWriter(fh, cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(ranked)

    md = ["# Source candidates", "",
          "Generated by `scripts/source-finder/find_sources.py report`. A candidate is listed as",
          "**usable** when it loads and names the variety; that is a lead, not a check. Read each",
          "source before citing it, and cite only what it says.", ""]
    for variety in queue:
        rows = [r for r in ranked if r["variety"] == variety]
        good = [r for r in rows if r["usable"]]
        md += [f"## {variety}", "",
               f"{len(rows)} candidates, {len(good)} name the variety. Figures to look for: "
               f"{', '.join(queue[variety]['claims']) or 'none'}.", ""]
        for r in good[:args.top]:
            md.append(f"- **{r['tier']}** [{r['title'][:90] or r['url']}]({r['url']})"
                      f"{' · ' + r['date'] if r['date'] else ''} · name ×{r['name_hits']}"
                      f"{' · figures: ' + r['claims'] if r['claims'] else ''}")
            md += [f"  > {s}" for s in r["_snips"]]
        if not good:
            md.append("- nothing that names the variety")
        md.append("")
    (OUT / "report.md").write_text("\n".join(md), encoding="utf-8")
    print(f"{sum(r['usable'] for r in ranked)} usable of {len(ranked)} -> {OUT / 'report.md'}")


# ---------------------------------------------------------------- books

def cmd_book(args):
    params = {"q": args.query, "maxResults": 10, "printType": "books"}
    key = os.environ.get("GOOGLE_API_KEY")
    url = "https://www.googleapis.com/books/v1/volumes?" + urllib.parse.urlencode(params)
    res = cached("books", args.query, lambda: api_json(url, key_header=key))
    items = res.get("items", [])
    if not items:
        print("no match in Google Books")
    for it in items:
        v = it.get("volumeInfo", {})
        ids = ", ".join(f"{i['type']} {i['identifier']}" for i in v.get("industryIdentifiers", []))
        print(f"- {v.get('title', '')}{': ' + v['subtitle'] if v.get('subtitle') else ''}"
              f" | {', '.join(v.get('authors', []))} | {v.get('publisher', '')} {v.get('publishedDate', '')}"
              f" | {ids} | {v.get('infoLink', '')}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    load_env()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("queue")
    a.add_argument("--input", type=Path, required=True,
                   help="CSV of rows to source, with columns variety, tier and E (description)")
    a.add_argument("--tiers", nargs="+", default=["nolink"])
    a.add_argument("--force", action="store_true")
    a = sub.add_parser("search")
    a.add_argument("--provider", nargs="+", choices=["cse", "gemini"])
    a.add_argument("--only", nargs="+", help="limit to these varieties (names as in queue.csv)")
    a = sub.add_parser("fetch")
    a.add_argument("--workers", type=int, default=6)
    a = sub.add_parser("report")
    a.add_argument("--top", type=int, default=6)
    a = sub.add_parser("book")
    a.add_argument("query")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    {"queue": cmd_queue, "search": cmd_search, "fetch": cmd_fetch, "report": cmd_report,
     "book": cmd_book}[args.cmd](args)


if __name__ == "__main__":
    main()
