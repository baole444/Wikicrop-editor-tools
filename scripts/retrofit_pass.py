#!/usr/bin/env python3
"""
One retrofit pass per page over crop groups 7-13 (BT07-BT13).

Applies every mechanical fix a page needs in a single edit, so each page is
opened once and its history shows one readable change. Each fix can be turned
off with --skip, or chosen alone with --fix.

Fixes, in the order they run
----------------------------
  layout      infobox one parameter per line (standard 6.6)       render-neutral
  placement   infobox before the first heading (6.1)
  taxonomy    cultivar taxonomy rows copied from the species article (6.3)
  cacloai     species `CacLoai` as {{DanhsachLoai}} with full binomials (6.2)
  tenloai     cultivar `TenLoai` = the catalogue binomial (6.2)
  span        strip <span id="..."></span> (9.2)                   render-neutral
  entities    &quot; &amp; &lt; &gt; typed as characters (9.3)   render-neutral
  underline   drop the <u> wrapper inside citation links (3)
  dashes      plain hyphen for every dash, ranges `85-90` (9.11)
  agrolink    agronomy link to the existing title, exact case (5.4)
  categories  section 7.1 tags with 7.2 sortkeys                  render-neutral

Data
----
* Groups, crops, binomials and the variety list come from the project's
  catalogue, `Danh mục giống cây` in the local copy of the live sheet
  (WIKICROP_CATALOGUE, see project_config.py). Run
  `python scripts/fetch_live_sheet.py` first so it is current.
* Taxonomy rows come from each crop's species article on the wiki, which is
  sourced from Wikipedia. SPECIES_TAXONOMY_FIX corrects the one species
  article whose own rows are shifted, before the cultivars copy from it.
* A cultivar whose family or genus differs from its species article is not
  overwritten unless listed in GENUS_CHANGE_OK: that is a content question.

Design notes
------------
* Same safety model as reformat_infoboxes.py: dry run by default, --validate
  renders before anything is written, baserevid + nocreate + maxlag on every
  edit, the page re-read and the pass recomputed just before each write, a
  CSV log, and a separate read-only audit (audit_retrofit_pass.py).
* --validate checks the render-neutral fixes by rendering the page with only
  those applied, which must match the live render. The rest change what the
  reader sees by design; for those it checks that red links did not increase
  and that every category tag renders and exists.
* Idempotent: a second run finds nothing to do.
* Group 1-6 pages are never touched: a page is in scope only through CROPS.

Usage
-----
    python retrofit_pass.py                          # dry run, every fix
    python retrofit_pass.py --show-diff --only "Mía KK2"
    python retrofit_pass.py --validate               # render checks, no login
    python retrofit_pass.py --skip dashes            # everything but one fix
    python retrofit_pass.py --fix span --fix entities
    python retrofit_pass.py --run --limit 3          # cautious first run
"""

import argparse
import csv
import difflib
import getpass
import html
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from create_categories import MAXLAG, READ_DELAY, Wiki, error_text  # noqa: E402
from reformat_infoboxes import FETCH_BATCH, TARGET, parse_call, reformat, scan_call  # noqa: E402
import project_config as cfg  # noqa: E402

ROOT = cfg.ROOT
SHEET = cfg.CATALOGUE
UA = cfg.user_agent("WikicropRetrofitBot", "retrofit pass")
DEFAULT_LOG = cfg.LOGS / "p1-retrofit-run-log.csv"
DEFAULT_WRITE_DELAY = 20.0
AGRO, LIST = "Kỹ thuật trồng và chăm sóc ", "Danh sách giống "

# (category, title prefixes, species article, species name in the catalogue).
# Every crop of groups 7-13 that has pages on the wiki. A prefix matches the
# whole title or `<prefix> <variety>`, case-insensitively.
CROPS = [
    ("Cỏ voi", ("Cỏ voi",), "Cỏ voi", "Cỏ voi"),
    ("Mía", ("Mía",), "Mía", "Mía"),
    ("Khoai tây", ("Khoai tây",), "Khoai tây", "Khoai tây"),
    ("Khoai lang", ("Khoai lang",), "Khoai lang", "Khoai lang"),
    ("Bạc hà", ("Bạc hà", "Húng lũi"), "Bạc hà", "Bạc hà"),   # catalogue: Húng lủi under Bạc hà
    ("Chuối", ("Chuối",), "Chuối", "Chuối"),
    ("Bưởi", ("Bưởi",), "Bưởi", "Bưởi"),
    ("Cam", ("Cam",), "Cam", "Cam"),
    ("Dưa hấu", ("Dưa hấu",), "Dưa hấu", "Dưa hấu"),
    ("Xoài", ("Xoài",), "Xoài", "Xoài"),
    ("Mít", ("Mít",), "Mít", "Mít"),
    ("Ổi", ("Ổi",), "Ổi", "Ổi"),
    ("Dừa", ("Dừa",), "Dừa", "Dừa"),
    ("Chôm chôm", ("Chôm chôm",), "Chôm chôm", "Chôm chôm"),
    ("Mận", ("Mận",), "Mận", "Mận"),
    ("Điều", ("Điều",), "Điều", "Điều"),
    ("Sầu riêng", ("Sầu riêng",), "Sầu Riêng", "Sầu riêng"),
    ("Măng cụt", ("Cây măng cụt", "Măng cụt"), "Cây măng cụt", "Măng cụt"),
    ("Khóm", ("Khóm", "Dứa"), "Khóm / Dứa", "Khóm / Dứa"),
]

# Pages waiting on a content decision: left alone by every fix.
EXCLUDE = {
    "Mận Lý": "Prunus salicina, removed from the catalogue, to be blanked or redirected",
    "Chuối tạ quạ": "duplicate of Chuối tá quạ, to be redirected",
}

TAX = ["Gioi", "KhongPhanLoai1", "KhongPhanLoai2", "KhongPhanLoai3", "Bo", "Ho", "Chi"]
# Rows shifted up by one on the species article itself (`Tracheophyta` in the
# first unranked row, `Commelinids` missing). APG IV, as on vi.wikipedia for
# the other Poales pages here (Mía, Cỏ voi).
SPECIES_TAXONOMY_FIX = {
    "Khóm / Dứa": {"KhongPhanLoai1": "Angiospermae", "KhongPhanLoai2": "Monocots",
                   "KhongPhanLoai3": "Commelinids"},
}
# Cultivars whose family/genus may be overwritten from the species article.
GENUS_CHANGE_OK = {
    "Chuối Sứ",     # carries jute taxonomy (Malvaceae, Corchorus), a copy-paste slip
}
# Parameter order used when a parameter has to be added to an infobox.
ORDER = ["TenCayTrong", "Hinh", "MoTaHinh"] + TAX + ["CacLoai", "TenGiong", "TenLoai",
                                                     "LoaiGiong", "XuatXu"]

HEADING = re.compile(r"^(=+)[^=\n].*?\1[ \t]*$", re.M)
SPAN = re.compile(r'<span id="[^"]*"></span>')
CATEGORY_TAG = re.compile(r"\[\[\s*(?:Thể loại|Category)\s*:", re.I)
BINOMIAL = re.compile(r"[A-Z][a-z]+ (?:× )?[a-z][a-z-]+")


def key(s):
    """Same normalisation as sheetKey_() in scripts/sheet-common/sheet_config.gs."""
    s = unicodedata.normalize("NFC", str(s or "")).lower()
    s = re.sub(r'[\\/:*?"<>|]', "-", s)
    s = re.sub(r"^[\s\-–•]+", "", s)
    s = re.sub(r"^giống\s+", "", s)
    return re.sub(r"[\s\-.]", "", s)


def crop_of(title):
    """(crop row, variety part of the title) or (None, None)."""
    low = title.lower()
    best = None
    for crop in CROPS:
        for p in crop[1]:
            if (low == p.lower() or low.startswith(p.lower() + " ")) and (not best or len(p) > len(best[1])):
                best = (crop, p)
    if not best:
        return None, None
    return best[0], title[len(best[1]):].strip()


# ---------------------------------------------------------------- catalogue

def read_catalogue(path):
    """Groups 7-13 of `Danh mục giống cây`: {species name: {group, binomial, varieties}}.

    `binomial` is None for `spp.` entries. `varieties` maps a name key (the
    name, and each bracketed alias with and without the crop name in front)
    to (sheet name, variety binomial or None).

    The extra entry "_merged" maps the key of every variety the catalogue
    merged into another (`Gộp dòng Chuối cơm vào` in the reason column) to
    (merged name, variety it went into). Those wiki pages are duplicates.
    """
    try:
        import openpyxl
    except ImportError:
        raise SystemExit("openpyxl is needed to read the catalogue (pip install openpyxl).")
    if not path.exists():
        raise SystemExit(f"No catalogue at {path}. Run scripts/fetch_live_sheet.py first.")
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True)["Danh mục giống cây"]
    out, group, bt, sp, name = {"_merged": {}}, None, 0, None, ""
    for row in ws.iter_rows(max_col=8, values_only=True):
        a, b, c, _, e, _, _, reason = (list(row) + [None] * 8)[:8]
        if a and str(a).strip().startswith("BT"):
            bt = int(str(a).strip()[2:])
            group = re.sub(r"\s*\(.*\)\s*$", "", str(b)).strip()
            sp = None
        elif bt >= 7 and c:
            m = re.match(r"(.+?)\s*\((.+)\)\s*$", str(c).strip())
            name, latin = (m.group(1), m.group(2)) if m else (str(c).strip(), "")
            sp = out[name] = {"group": group, "binomial": latin if BINOMIAL.fullmatch(latin) else None,
                              "varieties": {}}
        elif bt >= 7 and e and sp is not None:
            v = str(e).strip()
            alias = [a.strip() for x in re.findall(r"\(([^()]+)\)", v) for a in x.split("/")]
            latin = next((x for x in alias if BINOMIAL.fullmatch(x)), None)
            sp["varieties"][key(re.sub(r"\s*\(.*?\)", "", v))] = (v, latin)
            for x in alias:
                if x != latin:
                    sp["varieties"].setdefault(key(x), (v, latin))
                    sp["varieties"].setdefault(key(f"{name} {x}"), (v, latin))
            for merged in re.findall(r"[Gg]ộp dòng (.+?) vào", str(reason or "")):
                out["_merged"][key(merged)] = (merged, v)
    return out


# ------------------------------------------------------------ infobox edits

def first_call(text):
    return TARGET.search(text)


def infobox_params(text):
    m = first_call(text)
    return dict(parse_call(text, m.start())["params"]) if m else {}


def set_params(text, updates):
    """Set parameters on the first infobox call in place, keeping each line's
    spacing. A missing parameter is added after its predecessor in ORDER.
    Needs the one-per-line layout to add a parameter; raises ValueError."""
    m = first_call(text)
    end, pipes, eqs = scan_call(text, m.start())
    bounds = pipes + [end - 2]
    pieces = {}
    for k, p in enumerate(pipes):
        eq = next((e for e in eqs if p < e < bounds[k + 1]), None)
        if eq is not None:
            pieces[text[p + 1:eq].strip()] = (eq, bounds[k + 1])
    edits = []
    for name, value in updates.items():
        if name in pieces:
            eq, pe = pieces[name]
            old = text[eq + 1:pe]
            if old.strip() != value:
                lead, trail = old[:len(old) - len(old.lstrip())], old[len(old.rstrip()):]
                edits.append((eq + 1, pe, lead + value + trail))
            continue
        before = [n for n in ORDER[:ORDER.index(name)] if n in pieces] if name in ORDER else []
        if not before:
            raise ValueError(f"no place to add {name}")
        eq, pe = pieces[before[-1]]
        if not text[eq + 1:pe].endswith("\n"):
            raise ValueError("infobox is not one parameter per line")
        edits.append((pe, pe, f"|{name}={value}\n"))
    for a, b, new in sorted(edits, reverse=True):
        text = text[:a] + new + text[b:]
    return text


# --------------------------------------------------------------------- fixes
# Each fix takes (text, ctx) and returns (text, note). A note starting with
# "skip:" means the fix declined and a human should look.

@dataclass
class Ctx:
    title: str
    kind: str                 # species | cultivar | agronomy | list | other
    crop: tuple
    variety: str
    sheet: dict
    species_rows: dict = field(default_factory=dict)
    titles: set = field(default_factory=set)            # every main-namespace title
    titles_ci: dict = field(default_factory=dict)       # lowercase -> title


def fix_layout(text, ctx):
    new, status, detail = reformat(text)
    return new, (f"skip: {detail}" if status == "skip" else None)


def fix_placement(text, ctx):
    m = first_call(text)
    heads = list(HEADING.finditer(text))
    if not m or not heads or heads[0].start() > m.start():
        return text, None
    if len(heads) > 1 and heads[1].start() < m.start():
        return text, "skip: infobox is below the first section"
    call = parse_call(text, m.start())
    end = call["end"] + (text[call["end"]:call["end"] + 1] == "\n")
    block, rest = text[m.start():end], text[:m.start()] + text[end:]
    at = heads[0].start()
    return rest[:at] + block + rest[at:], None


def fix_taxonomy(text, ctx):
    if ctx.kind == "species":
        upd = SPECIES_TAXONOMY_FIX.get(ctx.title)
        return (set_params(text, upd) if upd else text), None
    if ctx.kind != "cultivar":
        return text, None
    rows = ctx.species_rows
    if not rows:
        return text, "skip: species article has no taxonomy to copy"
    cur = infobox_params(text)
    if ctx.title not in GENUS_CHANGE_OK and any(cur.get(k) != rows[k] for k in ("Ho", "Chi")):
        return text, (f"skip: family/genus {cur.get('Ho')}/{cur.get('Chi')} differ from "
                      f"{ctx.crop[2]} ({rows['Ho']}/{rows['Chi']})")
    return set_params(text, {k: rows[k] for k in TAX}), None


def cacloai_names(cur, genus, catalogue):
    """(names, note) for a species page's CacLoai."""
    listed = re.findall(r"^\s*\*?\s*([A-Z][a-z]+ [a-z][a-z-]+)\s*:", cur, re.M)
    if len(listed) >= 2:                                   # Mía: one species per line
        return listed, None
    m = re.fullmatch(r"([A-Z])\.\s+(.+)", cur.strip())
    page = f"{genus} {m.group(2)}" if m and genus.startswith(m.group(1)) else cur.strip()
    if catalogue:
        note = None
        if page and page != catalogue:
            note = f"note: CacLoai `{cur.strip()}` replaced by the catalogue binomial `{catalogue}`"
        return [catalogue], note
    if BINOMIAL.match(page):
        return [page], None
    return [], "skip: no species name on the page or in the catalogue (`spp.`)"


def fix_cacloai(text, ctx):
    if ctx.kind != "species":
        return text, None
    p = infobox_params(text)
    cur = p.get("CacLoai", "")
    if "DanhsachLoai" in cur:
        return text, None
    names, note = cacloai_names(cur, p.get("Chi", ""), ctx.sheet["binomial"])
    if not names:
        return text, note
    calls = ["{{DanhsachLoai|" + "|".join(names[i:i + 20]) + "}}" for i in range(0, len(names), 20)]
    return set_params(text, {"CacLoai": "".join(calls)}), note


def fix_tenloai(text, ctx):
    if ctx.kind != "cultivar":
        return text, None
    # Only a variety the catalogue lists: an unlisted one may be another species
    # of the genus (Húng lũi is not Mentha arvensis).
    v = ctx.sheet["varieties"].get(key(re.sub(r"\s*\(.*?\)", "", ctx.title)))
    target = v and (v[1] or ctx.sheet["binomial"])
    if not target:
        return text, None
    cur = infobox_params(text).get("TenLoai", "")
    if cur == target:
        return text, None
    head = BINOMIAL.match(cur)
    if head and head.group(0) != target:
        return text, f"note: TenLoai `{cur}` differs from the catalogue `{target}`, left as is"
    return set_params(text, {"TenLoai": target}), None


def fix_span(text, ctx):
    out = []
    for line in text.split("\n"):
        new = SPAN.sub("", line)
        if new == line or new.strip():
            out.append(new)
    return "\n".join(out), None


def fix_entities(text, ctx):
    text = text.replace("&quot;", '"').replace("&gt;", ">")
    text = re.sub(r"&lt;(?![A-Za-z/!?])", "<", text)          # never form a tag
    return re.sub(r"&amp;(?!(?:[A-Za-z]+|#\d+);)", "&", text), None


def fix_underline(text, ctx):
    def strip(m):
        s = m.group(0)
        broken = "<u>" in s and "</u>" not in s            # `<u>... 2026u>]`: a mistyped </u>
        # `[https://...xi%C3%AAm<u>Cộng` has no space: the tag ends the URL, so
        # dropping it would glue the text onto the URL (a new external link)
        s = re.sub(r"(?<=\S)<u>", " ", s).replace("<u>", "").replace("</u>", "")
        return re.sub(r"(?<=\S)u>(?=\s*\]$)", "", s) if broken else s

    return re.sub(r"\[https?://[^\s\]]+\s[^\]\n]*\]", strip, text), None


# URLs, link targets, file names and comments keep their dashes.
PROTECT = re.compile(r"https?://[^\s\]|<}]+|\[\[[^\]|]*|^[ \t]*\|[ \t]*Hinh[ \t]*=[^\n]*|<!--[\s\S]*?-->", re.M)


def restyle(s):
    """Same rule as restyle() in sheet-varieties/build_punctuation_rules.py."""
    s = re.sub("[–—]", "-", s)
    return re.sub(r"(\d) *- *(?=\d)", r"\1-", s)


def fix_dashes(text, ctx):
    out, pos = [], 0
    for m in PROTECT.finditer(text):
        out += [restyle(text[pos:m.start()]), m.group(0)]
        pos = m.end()
    return "".join(out) + restyle(text[pos:]), None


def fix_agrolink(text, ctx):
    def repl(m):
        target = m.group(1).strip()
        target = target[0].upper() + target[1:]
        real = ctx.titles_ci.get(target.lower())
        if target in ctx.titles or not real:
            return m.group(0)
        return "[[" + real + m.group(2)
    return re.sub(r"\[\[(Kỹ thuật trồng và chăm sóc [^\]|#]+)([|\]])", repl, text), None


def expected_categories(ctx):
    crop, group = ctx.crop[0], ctx.sheet["group"]
    return {
        "species": [(crop, " "), (group, ""), ("Bài loài cây trồng", "")],
        "cultivar": [(crop, ctx.variety or ctx.title), ("Bài giống cây trồng", "")],
        "agronomy": [(crop, "Kỹ thuật"), ("Kỹ thuật canh tác", "")],
        "list": [(crop, "Danh sách"), ("Danh sách giống", "")],
    }.get(ctx.kind, [])


def fix_categories(text, ctx):
    if CATEGORY_TAG.search(text):
        return text, "skip: already carries a category tag"
    tags = expected_categories(ctx)
    if not tags:
        return text, "skip: page type unknown (no infobox, not agronomy or list)"
    lines = [f"[[Thể loại:{c}|{k}]]" if k else f"[[Thể loại:{c}]]" for c, k in tags]
    # no final newline: MediaWiki trims trailing whitespace on save
    return text.rstrip() + "\n\n" + "\n".join(lines), None


# (name, function, render-neutral, edit-summary label)
FIXES = [
    ("layout", fix_layout, True, "one infobox parameter per line (6.6)"),
    ("placement", fix_placement, False, "infobox before the first heading (6.1)"),
    ("taxonomy", fix_taxonomy, False, "taxonomy from the species article (6.3)"),
    ("cacloai", fix_cacloai, False, "CacLoai with DanhsachLoai (6.2)"),
    ("tenloai", fix_tenloai, False, "TenLoai binomial (6.2)"),
    ("span", fix_span, True, "remove span anchors (9.2)"),
    ("entities", fix_entities, True, "HTML entities as characters (9.3)"),
    ("underline", fix_underline, False, "drop <u> in citations (3)"),
    ("dashes", fix_dashes, False, "plain hyphens (9.11)"),
    ("agrolink", fix_agrolink, False, "exact agronomy link title (5.4)"),
    ("categories", fix_categories, True, "categories (7)"),
]
FIX_NAMES = [f[0] for f in FIXES]


def apply(text, ctx, enabled):
    """Run the enabled fixes. Returns (new_text, applied fix names, notes)."""
    applied, notes = [], []
    for name, fn, _, _ in FIXES:
        if name not in enabled:
            continue
        try:
            new, note = fn(text, ctx)
        except ValueError as e:
            new, note = text, f"skip: {e}"
        if note:
            notes.append(f"{name}: {note}")
        if new != text:
            applied.append(name)
            text = new
    return text, applied, notes


def summary(applied):
    labels = [lbl for name, _, _, lbl in FIXES if name in applied]
    return "Retrofit to the article standard: " + ", ".join(labels)


# ------------------------------------------------------------------ api i/o

class Bot(Wiki):
    def __init__(self):
        super().__init__()
        self.opener.addheaders = [("User-Agent", UA)]

    def all_titles(self):
        """(every main-namespace title, {lowercase: title} of non-redirects)."""
        titles, ci, cont = set(), {}, {}
        while True:
            r = self._call({"action": "query", "list": "allpages", "apnamespace": 0,
                            "aplimit": 500, **cont})
            for p in r["query"]["allpages"]:
                titles.add(p["title"])
            if "continue" not in r:
                break
            cont = r["continue"]
            time.sleep(READ_DELAY)
        cont = {}
        while True:
            r = self._call({"action": "query", "list": "allpages", "apnamespace": 0,
                            "aplimit": 500, "apfilterredir": "nonredirects", **cont})
            for p in r["query"]["allpages"]:
                ci[p["title"].lower()] = p["title"]
            if "continue" not in r:
                return titles, ci
            cont = r["continue"]
            time.sleep(READ_DELAY)

    def fetch(self, titles):
        """{title: (revid, text)} for the titles that exist."""
        got = {}
        for i in range(0, len(titles), FETCH_BATCH):
            r = self._call({"action": "query", "prop": "revisions", "rvprop": "content|ids",
                            "rvslots": "main", "titles": "|".join(titles[i:i + FETCH_BATCH])})
            for p in r["query"]["pages"]:
                if "revisions" in p:
                    rev = p["revisions"][0]
                    got[p["title"]] = (rev["revid"], rev["slots"]["main"]["content"])
            time.sleep(READ_DELAY)
        return got

    def parse(self, title, text):
        """(normalised HTML, red-link count, [(category, sortkey, missing)])."""
        r = self._call({"action": "parse", "title": title, "text": text, "prop": "text|categories",
                        "contentmodel": "wikitext", "disablelimitreport": "1",
                        "disableeditsection": "1"}, post=True)
        if "error" in r:
            raise RuntimeError(r["error"].get("code", "?"))
        raw = r["parse"]["text"]
        cats = [(c["category"].replace("_", " "), c.get("sortkey", ""), bool(c.get("missing")))
                for c in r["parse"].get("categories", [])]
        return normalise(raw), raw.count('class="new"'), cats

    def replace(self, title, text, revid, summary_text):
        r = self.edit({"action": "edit", "title": title, "text": text, "summary": summary_text,
                       "token": self.csrf, "baserevid": str(revid), "nocreate": "1",
                       "minor": "1", "assert": "user", "maxlag": str(MAXLAG)})
        if "error" in r:
            return False, error_text(r["error"]), None
        e = r.get("edit", {})
        if e.get("result") != "Success":
            return False, "captcha" if "captcha" in e else str(e)[:80], None
        if "nochange" in e:
            return "skipped", "no change", e.get("oldrevid")
        return True, "ok", e.get("newrevid")


def normalise(h):
    """HTML compared for the render-neutral fixes: anchors, entities and
    empty paragraphs are what those fixes remove, so they are ignored."""
    h = re.sub(r"<!--.*?-->", "", h, flags=re.S)
    h = re.sub(r'<span id="[^"]*"></span>', "", h)
    h = html.unescape(h)
    h = re.sub(r"<p>\s*(?:<br\s*/?>)?\s*</p>", "", h)
    h = re.sub(r">\s+<", "><", h)
    return re.sub(r"\s+", " ", h).strip()


# --------------------------------------------------------------------- plan

def build_contexts(wiki, catalogue, only):
    """{title: (revid, text, Ctx)} for every in-scope page, plus problems."""
    titles, ci = wiki.all_titles()
    problems = []
    members = {}
    for t in sorted(ci.values()):
        body = t[len(AGRO):] if t.startswith(AGRO) else t[len(LIST):] if t.startswith(LIST) else t
        crop, variety = crop_of(body)
        if not crop:
            continue
        if t in EXCLUDE:
            print(f"  excluded: {t} ({EXCLUDE[t]})")
            continue
        merged = catalogue["_merged"].get(key(t))
        if merged:
            print(f"  excluded: {t} (merged into {merged[1]} in the catalogue, to be redirected)")
            continue
        kind = "agronomy" if t.startswith(AGRO) else "list" if t.startswith(LIST) else None
        members[t] = (crop, variety, kind)
    if only:
        missing = [t for t in only if t not in members]
        if missing:
            raise SystemExit(f"Not in scope (groups 7-13), excluded, or not a page: {missing}")
    wanted = only or sorted(members)
    crops = {members[t][0] for t in wanted}
    # the wanted pages, plus the species article of each of their crops
    pages = wiki.fetch(sorted(set(wanted) | {c[2] for c in crops}))

    # species rows, after the species fix, so cultivars copy the corrected block
    species_rows = {}
    for crop in CROPS:
        if crop not in crops:
            continue
        if crop[2] in pages:
            text = pages[crop[2]][1]
            fixed = SPECIES_TAXONOMY_FIX.get(crop[2], {})
            p = infobox_params(text)
            species_rows[crop[0]] = {k: fixed.get(k, p.get(k, "")) for k in TAX}
        else:
            problems.append(f"species article missing: {crop[2]}")

    out = {}
    for t in wanted:
        if t not in pages:
            continue
        crop, variety, kind = members[t]
        sheet = catalogue.get(crop[3])
        if not sheet:
            problems.append(f"{t}: crop {crop[3]} not in the catalogue")
            continue
        revid, text = pages[t]
        if not kind:
            m = first_call(text)
            kind = ("other" if not m else
                    "species" if "InfoPlant1" in text[m.start():m.start() + 14] else "cultivar")
        out[t] = (revid, text, Ctx(t, kind, crop, variety, sheet, species_rows.get(crop[0], {}),
                                   titles, ci))
        if kind == "cultivar" and key(re.sub(r"\s*\(.*?\)", "", t)) not in sheet["varieties"]:
            problems.append(f"not in the catalogue (kept, check its tier): {t}")
    return out, problems


def main():
    ap = argparse.ArgumentParser(description="One retrofit pass per page, groups 7-13.")
    ap.add_argument("--run", action="store_true", help="write the edits (default is a dry run)")
    ap.add_argument("--fix", action="append", choices=FIX_NAMES, help="only these fixes (repeatable)")
    ap.add_argument("--skip", action="append", choices=FIX_NAMES, default=[], help="leave a fix out")
    ap.add_argument("--only", action="append", metavar="TITLE", help="restrict to this page (repeatable)")
    ap.add_argument("--limit", type=int, default=0, help="only the first N pages to change")
    ap.add_argument("--delay", type=float, default=DEFAULT_WRITE_DELAY,
                    help=f"seconds between writes (default {DEFAULT_WRITE_DELAY:.0f}, minimum 5)")
    ap.add_argument("--show-diff", action="store_true", help="print a unified diff per page")
    ap.add_argument("--validate", action="store_true", help="render checks with action=parse")
    ap.add_argument("--sheet", type=Path, default=SHEET, help="local copy of the live catalogue")
    ap.add_argument("--log", type=Path, default=DEFAULT_LOG, help="CSV of writes, for the audit")
    args = ap.parse_args()
    cfg.require(SHEET)
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    enabled = [f for f in (args.fix or FIX_NAMES) if f not in args.skip]
    print(f"Fixes: {', '.join(enabled)}")

    print(f"Reading the catalogue from {args.sheet.name} ...")
    catalogue = read_catalogue(args.sheet)
    wiki = Bot()
    pages, problems = build_contexts(wiki, catalogue, args.only)

    plan, notes, skips = {}, [], []
    for t, (revid, text, ctx) in pages.items():
        new, applied, page_notes = apply(text, ctx, enabled)
        notes += [f"{t}: {n}" for n in page_notes if "skip:" not in n]
        skips += [f"{t}: {n}" for n in page_notes if "skip:" in n]
        if applied:
            plan[t] = (revid, text, new, applied, ctx)

    kinds = {}
    for _, _, ctx in pages.values():
        kinds[ctx.kind] = kinds.get(ctx.kind, 0) + 1
    print(f"\nIn scope: {len(pages)} pages ({', '.join(f'{k} {n}' for k, n in sorted(kinds.items()))})")
    print(f"  to edit : {len(plan)}")
    for name in enabled:
        print(f"    {name:<11} {sum(name in p[3] for p in plan.values())}")
    for label, items in (("NEEDS A HUMAN", skips), ("Notes", notes), ("Catalogue cross-check", problems)):
        if items:
            print(f"\n{label} ({len(items)}):")
            for i in items:
                print("  " + i)

    pending = list(plan)
    if args.limit:
        pending = pending[:args.limit]
        print(f"\n--limit {args.limit}: restricting this run to {len(pending)} page(s)")
    print()
    for t in pending:
        print(f"  [EDIT] {t}: {', '.join(plan[t][3])}")
    if args.show_diff:
        for t in pending:
            _, old, new, _, _ = plan[t]
            print("\n" + "".join(difflib.unified_diff(
                old.splitlines(True), new.splitlines(True), f"{t} (live)", f"{t} (new)", n=1)))

    if args.validate:
        neutral = [n for n, _, flag, _ in FIXES if flag and n in enabled]
        print(f"\nValidating {len(pending)} page(s): render-neutral fixes ({', '.join(neutral)}) "
              "must not change the render ...")
        bad = 0
        for t in pending:
            _, old, new, _, ctx = plan[t]
            neutral_text, _, _ = apply(old, ctx, neutral)
            a, red_a, _ = wiki.parse(t, old)
            b, _, _ = wiki.parse(t, neutral_text)
            _, red_c, cats = wiki.parse(t, new)
            time.sleep(READ_DELAY)
            issues = []
            if a != b:
                i = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]), min(len(a), len(b)))
                issues.append(f"neutral fixes change the render at ...{a[max(0, i - 50):i + 60]}")
            if red_c > red_a:
                issues.append(f"red links {red_a} -> {red_c}")
            if "categories" in enabled and "categories" in plan[t][3]:
                want = {(c.replace("_", " "), k) for c, k in expected_categories(ctx)}
                got = {(c, k) for c, k, _ in cats}
                if not want <= got:
                    issues.append(f"categories missing from the render: {sorted(want - got)}")
                missing = [c for c, _, m in cats if m]
                if missing:
                    issues.append(f"category page does not exist: {missing}")
            if issues:
                bad += 1
                print(f"  PROBLEM  {t}")
                for i in issues:
                    print(f"      {i}")
            else:
                print(f"  ok       {t}  (red links {red_a} -> {red_c})")
        print(f"\nValidation: {len(pending) - bad} ok, {bad} with problems.")
        if bad:
            print("Do not --run until every problem is explained.")

    delay = max(5.0, args.delay)
    if not args.run:
        print(f"\nDRY RUN - nothing was written.")
        print(f"Re-run with --run to edit {len(pending)} page(s) at {delay:.0f}s apart "
              f"(about {len(pending) * delay / 60:.0f} min).")
        return
    if not pending:
        print("\nNothing to do.")
        return

    print(f"\nAbout to edit {len(pending)} pages, {delay:.0f}s apart (about {len(pending) * delay / 60:.0f} min).")
    print("Use a BotPassword from Special:BotPasswords, not your account password.")
    user = input(cfg.bot_login_hint()).strip()
    password = getpass.getpass("Bot password (input hidden): ")
    if not user or not password:
        raise SystemExit("No credentials given; aborting.")
    who = wiki.login(user, password)
    del password
    print(f"Logged in as {who}\n")

    args.log.parent.mkdir(parents=True, exist_ok=True)
    new_log = not args.log.exists()
    ok = skipped = failed = 0
    with open(args.log, "a", newline="", encoding="utf-8") as fh:
        log = csv.writer(fh)
        if new_log:
            log.writerow(["title", "kind", "old_revid", "new_revid", "fixes", "result"])
        for i, title in enumerate(pending, 1):
            print(f"[{i}/{len(pending)}] {title}")
            ctx = plan[title][4]
            fresh = wiki.fetch([title]).get(title)          # re-read right before writing
            if not fresh:
                failed += 1
                print("    FAILED - page vanished")
                continue
            revid, text = fresh
            new, applied, _ = apply(text, ctx, enabled)
            if not applied:
                skipped += 1
                print("    skipped - nothing to do now")
            else:
                success, info, newrev = wiki.replace(title, new, revid, summary(applied))
                if success is True:
                    ok += 1
                    print(f"    edited (rev {newrev}): {', '.join(applied)}")
                    log.writerow([title, ctx.kind, revid, newrev, " ".join(applied), "edited"])
                elif success == "skipped":
                    skipped += 1
                    print(f"    skipped - {info}")
                else:
                    failed += 1
                    print(f"    FAILED - {info}")
                    log.writerow([title, ctx.kind, revid, "", " ".join(applied), "FAILED " + info])
                fh.flush()
            if i < len(pending):
                time.sleep(delay)
    print(f"\nDone. edited={ok} skipped={skipped} failed={failed}")
    print(f"Log: {args.log}")
    if failed:
        print("Re-running is safe: fixed pages have nothing left to do.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Re-running is safe - pages already fixed are skipped.")
        sys.exit(1)
