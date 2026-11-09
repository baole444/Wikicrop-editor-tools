# -*- coding: utf-8 -*-
"""Markdown -> ODF Text (.odt) for Wikicrop documents.

    python md2odt.py [--config FILE]                      build every [[document]] in the config
    python md2odt.py [--config FILE] <src.md> <dst.odt> "<Title>" ["<cut regex>||<cut regex>"]

Look and layout come from export-config.toml beside this script (or --config).
Cover page: the title plus a live table of contents, no header or footer.
Body pages: page number only. Markdown ## / ### / #### become Heading 1 / 2 / 3;
the first # line is dropped (the title replaces it). Cut patterns drop matching
headings and everything under them.

The TOC is written as a real index; run odt_finish.py afterwards (with
LibreOffice's own python) to fill in its page numbers and export the PDF.
"""
import copy, io, os, re, sys, tomllib, urllib.parse, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "export-config.toml")

# built-in defaults; export-config.toml overrides any of them
DEFAULTS = {
    "paths":  {"root": "../.."},
    "page":   {"width": 21.0, "height": 29.7, "margin_top": 1.8, "margin_bottom": 1.1,
               "margin_left": 1.8, "margin_right": 1.8},
    "fonts":  {"body": "Arial", "mono": "Consolas"},
    "sizes":  {"body": 10, "title": 24, "heading1": 14.5, "heading2": 11.8, "heading3": 10.3,
               "table": 8.6, "code": 8.1, "inline_code": 8.8, "contents1": 10.5,
               "contents2": 8.8, "contents3": 8.2, "contents_label": 9.5, "footer": 8.5},
    "colors": {"text": "#1a1a1a", "muted": "#5b6670", "rule": "#c8d0d6",
               "table_header": "#e8d3b9", "table_zebra": "#fafbfb",
               "code_background": "#f4f6f7", "code_text": "#224433",
               "inline_code_background": "#f0f2f3", "link": "#1a5490",
               "quote_bar": "#b9c6cf", "quote_background": "#f7f9fa"},
    "style":  {"line_spacing": 135, "heading1_rule": True, "table_zebra": True},
    "cover":  {"contents": True, "contents_label": "Contents", "contents_depth": 2, "leader": " "},
    "footer": {"page_number": True, "align": "center", "start_at": 1},
    "pdf":    {"bookmarks": True, "tagged": True},
    "wiki":   {"base_url": "https://wikicrop.lab.io.vn/wikicrop/index.php/",
               "rules": False, "checkbox": "☐", "contents": False},   # used by md2wiki.py
}

C = copy.deepcopy(DEFAULTS)          # active settings; replaced by load_config()

def load_config(path=None):
    """Defaults, overlaid with the TOML file. Bad values stop with a clear message."""
    global C
    path = os.path.abspath(path or DEFAULT_CONFIG)
    cfg = copy.deepcopy(DEFAULTS)
    cfg["document"] = []
    if os.path.exists(path):
        with open(path, "rb") as f:
            user = tomllib.load(f)
        for sect, vals in user.items():
            if sect == "document":
                cfg["document"] = vals; continue
            if sect not in DEFAULTS or not isinstance(vals, dict):
                print("config: ignoring unknown section [%s]" % sect); continue
            for k, v in vals.items():
                if k not in DEFAULTS[sect]:
                    print("config: ignoring unknown key %s.%s" % (sect, k)); continue
                cfg[sect][k] = v
    elif path != DEFAULT_CONFIG:
        raise SystemExit("config file not found: %s" % path)
    errors = []
    for k, v in cfg["colors"].items():
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(v)):
            errors.append("colors.%s = %r is not #rrggbb" % (k, v))
    for sect in ("page", "sizes"):
        for k, v in cfg[sect].items():
            if not isinstance(v, (int, float)) or v < 0:
                errors.append("%s.%s = %r must be a number" % (sect, k, v))
    if cfg["footer"]["align"] not in ("left", "center", "right"):
        errors.append("footer.align must be left, center or right")
    if cfg["cover"]["contents_depth"] not in (1, 2, 3):
        errors.append("cover.contents_depth must be 1, 2 or 3")
    if len(str(cfg["cover"]["leader"])) > 1:
        errors.append("cover.leader must be a single character")
    if errors:
        raise SystemExit("config errors in %s:\n  " % path + "\n  ".join(errors))
    cfg["_root"] = os.path.normpath(os.path.join(os.path.dirname(path), cfg["paths"]["root"]))
    C = cfg
    return cfg

def text_width():
    p = C["page"]
    return p["width"] - p["margin_left"] - p["margin_right"]

SYMMAP = {"\u2705": "\u2713", "\u274C": "\u2717", "\uFE0F": "", "\u23F8": ""}


# --------------------------------------------------------------------------
# inline markdown -> ODF spans

def xesc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def sym(s):
    for k, v in SYMMAP.items():
        s = s.replace(k, v)
    return s

def spaces(s):
    """ODF collapses runs of spaces and drops leading ones; keep them."""
    s = s.replace("\t", "<text:tab/>")
    def run(m):
        n = len(m.group(0))
        lead = m.start() == 0
        if n == 1 and not lead:
            return " "
        return ("" if lead else " ") + '<text:s text:c="%d"/>' % (n if lead else n - 1)
    return re.sub(r" +", run, s)

TAG = re.compile(r"</?([a-zA-Z]+)[^>]*>")

def renest(s):
    """**a *b*** gives <b>..<i>..</b></i>; close and reopen crossed tags."""
    out, stack, pos = [], [], 0
    for m in TAG.finditer(s):
        out.append(s[pos:m.start()]); pos = m.end()
        tag, name = m.group(0), m.group(1).lower()
        if tag.startswith("</"):
            if name not in [n for n, _ in stack]:
                continue
            reopen = []
            while stack:
                n, full = stack.pop()
                out.append("</%s>" % n)
                if n == name: break
                reopen.append((n, full))
            for n, full in reversed(reopen):
                out.append(full); stack.append((n, full))
        else:
            stack.append((name, tag)); out.append(tag)
    out.append(s[pos:])
    while stack:
        n, _ = stack.pop(); out.append("</%s>" % n)
    return "".join(out)

ANCHORS = set()        # heading anchors present in the document being built

def slug(text):
    """A heading's anchor as Markdown previews make it: lower case,
    punctuation dropped, spaces to hyphens (GitHub / VS Code rules)."""
    return re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")

def heading_slugs(md):
    """(level, text, anchor) for every heading outside code blocks; a repeated
    anchor gets -1, -2 ... appended, as the previews do."""
    out, seen, fence = [], {}, False
    for ln in md.split("\n"):
        if ln.strip().startswith("```"):
            fence = not fence; continue
        m = None if fence else re.match(r"^(#{1,6})\s+(.*)$", ln)
        if not m: continue
        text = m.group(2).strip()
        base = slug(text); n = seen.get(base, 0); seen[base] = n + 1
        out.append((len(m.group(1)), text, base if n == 0 else "%s-%d" % (base, n)))
    return out

def inline(s):
    s = sym(s)
    codes = []
    def keep(m):
        codes.append(m.group(1)); return "\x00%d\x00" % (len(codes) - 1)
    s = re.sub(r"`([^`]+)`", keep, s)
    s = xesc(s)
    s = re.sub(r"&lt;(su[pb])&gt;(.+?)&lt;/\1&gt;", r"<\1>\2</\1>", s)   # <sup>, <sub> pass through
    def link(m):
        text, url = m.group(1), m.group(2)
        if re.match(r"https?://", url):
            return '<a href="%s">%s</a>' % (url.replace('"', "&quot;"), text)
        if url.startswith("#"):                       # [§1](#1-loại-trang) -> jump to heading
            name = urllib.parse.unquote(url[1:])
            if name in ANCHORS:
                return '<a href="#%s">%s</a>' % (name, text)
            print("  warning: no heading for link #%s - left as plain text" % name)
        return text                                   # local file links become text
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, s)
    s = re.sub(r"\*\*\*(.+?)\*\*\*", r"<b><i>\1</i></b>", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", s)
    s = re.sub(r"~~(.+?)~~", r"<s>\1</s>", s)
    s = renest(s)
    s = re.sub(r"<(b|i|s|sup|sub)>", lambda m: '<text:span text:style-name="T_%s">' % m.group(1), s)
    s = re.sub(r"</(b|i|s|sup|sub)>", "</text:span>", s)
    s = re.sub(r'<a href="([^"]*)">',
               r'<text:a xlink:type="simple" xlink:href="\1" '
               r'text:style-name="Internet_20_link" '
               r'text:visited-style-name="Visited_20_Internet_20_Link">', s)
    s = s.replace("</a>", "</text:a>")
    s = re.sub("\x00(\\d+)\x00", lambda m: '<text:span text:style-name="T_code">%s</text:span>'
               % spaces(xesc(codes[int(m.group(1))])), s)
    return s

def plain(s):
    """Heading text without markdown, for the placeholder contents."""
    s = sym(s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"</?su[pb]>", "", s)
    return xesc(s.replace("`", "").replace("**", "").replace("~~", "").replace("*", ""))


# --------------------------------------------------------------------------
# block parsing

def split_row(line):
    line = line.strip()
    if line.startswith("|"): line = line[1:]
    if line.endswith("|"):   line = line[:-1]
    cells, buf, esc_ = [], "", False
    for ch in line:
        if esc_: buf += ch; esc_ = False; continue
        if ch == "\\": buf += ch; esc_ = True; continue
        if ch == "|": cells.append(buf); buf = ""
        else: buf += ch
    cells.append(buf)
    return [c.strip().replace("\\|", "|") for c in cells]

def cut_sections(md, patterns):
    if not patterns: return md
    out, lines, i = [], md.split("\n"), 0
    while i < len(lines):
        m = re.match(r"^(#{1,6})\s+(.*)$", lines[i])
        if m and any(re.search(p, m.group(2), re.I) for p in patterns):
            lvl = len(m.group(1)); i += 1
            while i < len(lines):
                m2 = re.match(r"^(#{1,6})\s+", lines[i])
                if m2 and len(m2.group(1)) <= lvl: break
                i += 1
            while out and out[-1].strip() in ("", "---"): out.pop()
            continue
        out.append(lines[i]); i += 1
    while out and out[-1].strip() in ("", "---"): out.pop()
    return "\n".join(out)


class Builder:
    def __init__(self, anchors=()):
        self.body, self.auto, self.headings = [], [], []
        self.anchors = list(anchors)            # one per heading, in document order
        self.ntab = 0
        self.first_done = False

    # the first body block starts page 1 on the numbered master page
    def pstyle(self, parent):
        if self.first_done:
            return parent
        self.first_done = True
        self.auto.append(
            '<style:style style:name="P_start" style:family="paragraph" '
            'style:parent-style-name="%s" style:master-page-name="Standard">'
            '<style:paragraph-properties style:page-number="%d"/></style:style>'
            % (parent, C["footer"]["start_at"]))
        return "P_start"

    def para(self, style, xml):
        self.body.append('<text:p text:style-name="%s">%s</text:p>' % (self.pstyle(style), xml))

    def heading(self, lvl, txt):
        style = self.pstyle("Heading_20_%d" % lvl)
        mark = self.anchors.pop(0) if self.anchors else None    # link target for [..](#anchor)
        mark = '<text:bookmark text:name="%s"/>' % xesc(mark) if mark else ""
        self.body.append('<text:h text:style-name="%s" text:outline-level="%d">%s%s</text:h>'
                         % (style, lvl, mark, inline(txt)))
        self.headings.append((lvl, plain(txt)))

    def table(self, rows):
        ncol = max(len(r) for r in rows)
        rows = [r + [""] * (ncol - len(r)) for r in rows]
        weights, minw = [], []
        for c in range(ncol):
            texts = [re.sub(r"[*`\[\]]", "", r[c]) for r in rows]
            weights.append(min(max(max([len(t) for t in texts] + [3]), 6), 58))
            minw.append(max([len(x) for t in texts for x in t.split()] + [3]) * 0.19 + 0.45)
        tw = text_width()
        tot = float(sum(weights))
        widths = [max(1.1, tw * w / tot) for w in weights]
        widths = [max(widths[c], min(minw[c], tw * 0.34)) for c in range(ncol)]
        k = tw / sum(widths)
        widths = [w * k for w in widths]
        self.ntab += 1
        name = "Tbl%d" % self.ntab
        self.auto.append('<style:style style:name="%s" style:family="table">'
                         '<style:table-properties style:width="%.2fcm" table:align="left" '
                         'fo:margin-top="0.08cm" fo:margin-bottom="0.3cm"/></style:style>'
                         % (name, tw))
        for c, w in enumerate(widths):
            self.auto.append('<style:style style:name="%s.C%d" style:family="table-column">'
                             '<style:table-column-properties style:column-width="%.2fcm"/>'
                             '</style:style>' % (name, c, w))
        x = ['<table:table table:name="%s" table:style-name="%s">' % (name, name)]
        x += ['<table:table-column table:style-name="%s.C%d"/>' % (name, c) for c in range(ncol)]
        cell = lambda st, ps, t: ('<table:table-cell table:style-name="%s" office:value-type="string">'
                                  '<text:p text:style-name="%s">%s</text:p></table:table-cell>'
                                  % (st, ps, inline(t)))
        x.append('<table:table-header-rows><table:table-row table:style-name="RowKeep">')
        x += [cell("CellH", "Table_20_Heading", t) for t in rows[0]]
        x.append('</table:table-row></table:table-header-rows>')
        for n, r in enumerate(rows[1:]):                # a data row never splits over a page
            st = "CellZ" if (n % 2 and C["style"]["table_zebra"]) else "CellB"
            x.append('<table:table-row table:style-name="RowKeep">'
                     + "".join(cell(st, "Table_20_Contents", t) for t in r) + '</table:table-row>')
        x.append('</table:table>')
        self.first_done = True
        self.body.append("".join(x))

    def code(self, lines):
        self.ntab += 1
        name = "Code%d" % self.ntab
        self.auto.append('<style:style style:name="%s" style:family="table">'
                         '<style:table-properties style:width="%.2fcm" table:align="left" '
                         'fo:margin-top="0.1cm" fo:margin-bottom="0.3cm"/></style:style>'
                         % (name, text_width()))
        paras = "".join('<text:p text:style-name="Code">%s</text:p>' % spaces(xesc(sym(l.rstrip())))
                        for l in (lines or [""]))
        self.first_done = True
        self.body.append('<table:table table:name="%s" table:style-name="%s">'
                         '<table:table-column/><table:table-row table:style-name="RowSplit">'
                         '<table:table-cell table:style-name="CodeCell" office:value-type="string">'
                         '%s</table:table-cell></table:table-row></table:table>' % (name, name, paras))

    def lists(self, items):
        # items: [depth, ordered, number, text]; normalise depth jumps
        prev = -1
        for it in items:
            it[0] = max(0, min(it[0], prev + 1)); prev = it[0]
        style = "L_number" if items[0][1] else "L_bullet"
        def build(i, depth):
            x = ['<text:list%s>' % (' text:style-name="%s"' % style if depth == 0 else "")]
            first = True
            while i < len(items) and items[i][0] >= depth:
                d, ordered, num, txt = items[i]
                start = ' text:start-value="%d"' % num if (first and ordered and num != 1) else ""
                first = False
                li = ['<text:list-item%s><text:p text:style-name="List_20_Paragraph">%s</text:p>'
                      % (start, inline(txt))]
                i += 1
                if i < len(items) and items[i][0] > depth:
                    sub, i = build(i, depth + 1)
                    li.append(sub)
                li.append('</text:list-item>')
                x.append("".join(li))
            x.append('</text:list>')
            return "".join(x), i
        xml, _ = build(0, 0)
        self.first_done = True
        self.body.append(xml)


LIST_RE = re.compile(r"^(\s*)([-*+]|\d+\.)\s+(.*)$")
BREAK_RE = re.compile(r"^\s*(#{1,4}\s|\||>|```|-{3,}|[-*+]\s|\d+\.\s)")

def parse(md, anchors=()):
    b, lines, i = Builder(anchors), md.split("\n"), 0
    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith("```"):
            i += 1; buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i]); i += 1
            i += 1
            b.code(buf); continue
        if re.match(r"^\s*\|.*\|\s*$", ln) and i + 1 < len(lines) \
           and re.match(r"^\s*\|[\s:\-|]+\|\s*$", lines[i + 1]):
            rows = [split_row(ln)]; i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(split_row(lines[i])); i += 1
            b.table(rows); continue
        m = re.match(r"^(#{2,4})\s+(.*)$", ln)
        if m:
            b.heading(len(m.group(1)) - 1, m.group(2).strip()); i += 1; continue
        if re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", ln):
            i += 1; continue                          # headings carry the rules
        if ln.strip().startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip()); i += 1
            b.para("Quote", inline(" ".join(x for x in buf if x))); continue
        m = LIST_RE.match(ln)
        if m:
            items = []
            while i < len(lines):
                m = LIST_RE.match(lines[i])
                if not m: break
                indent, marker, txt = m.group(1), m.group(2), m.group(3)
                cb = re.match(r"^\[([ xX])\]\s*(.*)$", txt)
                if cb: txt = cb.group(2)                # checklist: plain bullet
                ordered = bool(re.match(r"\d+\.", marker))
                num = int(marker[:-1]) if ordered else 0
                i += 1
                while i < len(lines) and lines[i].strip() and not LIST_RE.match(lines[i]) \
                      and not re.match(r"^\s*(#{1,4}\s|\||>|```|-{3,})", lines[i]):
                    txt += " " + lines[i].strip(); i += 1
                items.append([len(indent.replace("\t", "    ")) // 2, ordered, num, txt])
            b.lists(items); continue
        if not ln.strip():
            i += 1; continue
        buf = [ln.strip()]; i += 1
        while i < len(lines) and lines[i].strip() and not BREAK_RE.match(lines[i]) \
              and not re.match(r"^\*\*[^*]{1,40}:\*\*", lines[i]):
            buf.append(lines[i].strip()); i += 1
        b.para("Text_20_body", inline(" ".join(buf)))
    return b


# --------------------------------------------------------------------------
# document parts

NS = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
      'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
      'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
      'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
      'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
      'xmlns:xlink="http://www.w3.org/1999/xlink" '
      'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" '
      'xmlns:dc="http://purl.org/dc/elements/1.1/" '
      'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
      'office:version="1.3"')

def fonts():
    """Font faces are declared as "Body" and "Mono"; the config picks the families."""
    def face(name, family, generic, pitch):
        fam = xesc(family).replace('"', "&quot;")
        if " " in fam: fam = "&apos;%s&apos;" % fam
        return ('<style:font-face style:name="%s" svg:font-family="%s" '
                'style:font-family-generic="%s" style:font-pitch="%s"/>' % (name, fam, generic, pitch))
    return ('<office:font-face-decls>%s%s</office:font-face-decls>'
            % (face("Body", C["fonts"]["body"], "swiss", "variable"),
               face("Mono", C["fonts"]["mono"], "modern", "fixed")))

def pstyle(name, display=None, parent=None, cls=None, para="", text="", extra=""):
    a = 'style:name="%s" style:family="paragraph"' % name
    if display: a += ' style:display-name="%s"' % display
    if parent:  a += ' style:parent-style-name="%s"' % parent
    if cls:     a += ' style:class="%s"' % cls
    return ('<style:style %s%s><style:paragraph-properties %s/><style:text-properties %s/></style:style>'
            % (a, extra, para, text))

def tstyle(name, text, display=None):
    d = ' style:display-name="%s"' % display if display else ""
    return ('<style:style style:name="%s"%s style:family="text"><style:text-properties %s/></style:style>'
            % (name, d, text))

def bullets(name, chars):
    lv = []
    for n, ch in enumerate(chars, 1):
        lv.append('<text:list-level-style-bullet text:level="%d" text:bullet-char="%s">'
                  '<style:list-level-properties text:list-level-position-and-space-mode="label-alignment">'
                  '<style:list-level-label-alignment text:label-followed-by="listtab" '
                  'text:list-tab-stop-position="%.2fcm" fo:text-indent="-0.4cm" fo:margin-left="%.2fcm"/>'
                  '</style:list-level-properties></text:list-level-style-bullet>'
                  % (n, ch, 0.5 * n, 0.5 * n))
    return '<text:list-style style:name="%s">%s</text:list-style>' % (name, "".join(lv))

def numbers(name):
    lv = ['<text:list-level-style-number text:level="1" style:num-suffix="." style:num-format="1">'
          '<style:list-level-properties text:list-level-position-and-space-mode="label-alignment">'
          '<style:list-level-label-alignment text:label-followed-by="listtab" '
          'text:list-tab-stop-position="0.6cm" fo:text-indent="-0.5cm" fo:margin-left="0.6cm"/>'
          '</style:list-level-properties></text:list-level-style-number>']
    for n, ch in ((2, "\u25E6"), (3, "\u2013")):
        lv.append('<text:list-level-style-bullet text:level="%d" text:bullet-char="%s">'
                  '<style:list-level-properties text:list-level-position-and-space-mode="label-alignment">'
                  '<style:list-level-label-alignment text:label-followed-by="listtab" '
                  'text:list-tab-stop-position="%.2fcm" fo:text-indent="-0.4cm" fo:margin-left="%.2fcm"/>'
                  '</style:list-level-properties></text:list-level-style-bullet>'
                  % (n, ch, 0.6 + 0.5 * (n - 1), 0.6 + 0.5 * (n - 1)))
    return '<text:list-style style:name="%s">%s</text:list-style>' % (name, "".join(lv))

def styles_xml():
    col, sz, st, pg, ft = C["colors"], C["sizes"], C["style"], C["page"], C["footer"]
    ls = st["line_spacing"]
    rule1 = ('fo:padding-bottom="0.07cm" fo:border-bottom="0.6pt solid %s" fo:border-top="none" '
             'fo:border-left="none" fo:border-right="none"' % col["rule"]) if st["heading1_rule"] else ""
    s = [
        '<style:default-style style:family="paragraph"><style:paragraph-properties fo:hyphenation-ladder-count="no-limit" '
        'fo:orphans="2" fo:widows="2"/><style:text-properties style:font-name="Body" fo:font-size="%gpt" '
        'fo:color="%s" fo:hyphenate="false"/></style:default-style>' % (sz["body"], col["text"]),
        '<style:default-style style:family="table-cell"><style:table-cell-properties fo:padding="0.08cm"/></style:default-style>',
        pstyle("Standard", cls="text",
               text='style:font-name="Body" fo:font-size="%gpt" fo:color="%s"' % (sz["body"], col["text"])),
        pstyle("Text_20_body", "Text body", "Standard", "text",
               'fo:margin-top="0cm" fo:margin-bottom="0.2cm" fo:line-height="%d%%"' % ls),
        pstyle("Title", None, "Standard", "chapter",
               'fo:margin-top="0.6cm" fo:margin-bottom="0.9cm"',
               'fo:font-size="%gpt" fo:font-weight="bold"' % sz["title"]),
        pstyle("Heading", None, "Standard", "text", 'fo:keep-with-next="always"',
               'fo:font-weight="bold"'),
        pstyle("Heading_20_1", "Heading 1", "Heading", "text",
               'fo:margin-top="0.55cm" fo:margin-bottom="0.22cm" ' + rule1,
               'fo:font-size="%gpt"' % sz["heading1"], ' style:default-outline-level="1"'),
        pstyle("Heading_20_2", "Heading 2", "Heading", "text",
               'fo:margin-top="0.38cm" fo:margin-bottom="0.12cm"',
               'fo:font-size="%gpt"' % sz["heading2"], ' style:default-outline-level="2"'),
        pstyle("Heading_20_3", "Heading 3", "Heading", "text",
               'fo:margin-top="0.3cm" fo:margin-bottom="0.1cm"',
               'fo:font-size="%gpt" fo:color="%s"' % (sz["heading3"], col["muted"]),
               ' style:default-outline-level="3"'),
        pstyle("List_20_Paragraph", "List Paragraph", "Standard", "list",
               'fo:margin-top="0cm" fo:margin-bottom="0.07cm" fo:line-height="%d%%"' % max(100, ls - 5)),
        pstyle("Quote", None, "Standard", "html",
               'fo:margin-top="0.08cm" fo:margin-bottom="0.25cm" fo:line-height="%d%%" '
               'fo:padding-left="0.25cm" fo:padding-top="0.08cm" fo:padding-bottom="0.08cm" '
               'fo:border-left="2.2pt solid %s" fo:border-right="none" fo:border-top="none" '
               'fo:border-bottom="none" fo:background-color="%s"'
               % (max(100, ls - 5), col["quote_bar"], col["quote_background"])),
        pstyle("Table_20_Contents", "Table Contents", "Standard", "extra",
               'fo:line-height="125%"', 'fo:font-size="%gpt"' % sz["table"]),
        pstyle("Table_20_Heading", "Table Heading", "Table_20_Contents", "extra", "",
               'fo:font-weight="bold"'),
        pstyle("Code", None, "Standard", "html", 'fo:margin-top="0cm" fo:margin-bottom="0cm" fo:line-height="118%"',
               'style:font-name="Mono" fo:font-size="%gpt" fo:color="%s"' % (sz["code"], col["code_text"])),
        pstyle("Footer", None, "Standard", "extra",
               'fo:text-align="%s"' % {"left": "start", "center": "center", "right": "end"}[ft["align"]],
               'fo:font-size="%gpt" fo:color="%s"' % (sz["footer"], col["muted"])),
        pstyle("Contents_20_Heading", "Contents Heading", "Standard", "index",
               'fo:margin-top="0cm" fo:margin-bottom="0.1cm"',
               'fo:font-size="%gpt" fo:font-weight="bold" fo:color="%s" '
               'fo:text-transform="uppercase" fo:letter-spacing="0.03cm"' % (sz["contents_label"], col["muted"])),
        pstyle("Contents_20_1", "Contents 1", "Standard", "index",
               'fo:margin-top="0.17cm" fo:margin-bottom="0cm"', 'fo:font-size="%gpt"' % sz["contents1"]),
        pstyle("Contents_20_2", "Contents 2", "Standard", "index",
               'fo:margin-left="0.6cm" fo:margin-top="0.03cm" fo:margin-bottom="0cm"',
               'fo:font-size="%gpt" fo:color="%s"' % (sz["contents2"], col["muted"])),
        pstyle("Contents_20_3", "Contents 3", "Standard", "index",
               'fo:margin-left="1.2cm" fo:margin-top="0cm" fo:margin-bottom="0cm"',
               'fo:font-size="%gpt" fo:color="%s"' % (sz["contents3"], col["muted"])),
        tstyle("T_b", 'fo:font-weight="bold"'),
        tstyle("T_i", 'fo:font-style="italic"'),
        tstyle("T_s", 'style:text-line-through-style="solid"'),
        tstyle("T_sup", 'style:text-position="super 58%"'),
        tstyle("T_sub", 'style:text-position="sub 58%"'),
        tstyle("T_code", 'style:font-name="Mono" fo:font-size="%gpt" fo:background-color="%s"'
               % (sz["inline_code"], col["inline_code_background"])),
        tstyle("Internet_20_link", 'fo:color="%s"' % col["link"], "Internet link"),
        tstyle("Visited_20_Internet_20_Link", 'fo:color="%s"' % col["link"], "Visited Internet Link"),
        tstyle("Index_20_Link", 'fo:color="inherit"', "Index Link"),
        '<text:outline-style style:name="Outline">' + "".join(
            '<text:outline-level-style text:level="%d" style:num-format=""/>' % n for n in range(1, 11))
        + '</text:outline-style>',
        bullets("L_bullet", ["\u2022", "\u25E6", "\u2013"]),
        numbers("L_number"),
    ]
    page = ('fo:page-width="%gcm" fo:page-height="%gcm" style:print-orientation="%s" '
            'fo:margin-top="%gcm" fo:margin-left="%gcm" fo:margin-right="%gcm"'
            % (pg["width"], pg["height"], "landscape" if pg["width"] > pg["height"] else "portrait",
               pg["margin_top"], pg["margin_left"], pg["margin_right"]))
    footer_style = ('<style:footer-style><style:header-footer-properties fo:min-height="0.5cm" '
                    'fo:margin-top="0.5cm"/></style:footer-style>') if ft["page_number"] else ""
    footer = ('<style:footer><text:p text:style-name="Footer"><text:page-number '
              'text:select-page="current">%d</text:page-number></text:p></style:footer>'
              % ft["start_at"]) if ft["page_number"] else ""
    layouts = (
        '<style:page-layout style:name="pm1"><style:page-layout-properties %s fo:margin-bottom="%gcm"/>%s'
        '</style:page-layout>' % (page, pg["margin_bottom"], footer_style) +
        '<style:page-layout style:name="pm2"><style:page-layout-properties %s fo:margin-bottom="%gcm"/>'
        '</style:page-layout>' % (page, pg["margin_top"]))           # cover: no footer
    masters = (
        '<style:master-page style:name="Standard" style:page-layout-name="pm1">%s</style:master-page>'
        '<style:master-page style:name="First_20_Page" style:display-name="First Page" '
        'style:page-layout-name="pm2" style:next-style-name="Standard"/>' % footer)
    return ('<?xml version="1.0" encoding="UTF-8"?><office:document-styles %s>%s'
            '<office:styles>%s</office:styles><office:automatic-styles>%s</office:automatic-styles>'
            '<office:master-styles>%s</office:master-styles></office:document-styles>'
            % (NS, fonts(), "".join(s), layouts, masters))

def toc_xml(headings, label=None):
    cv = C["cover"]
    if not cv["contents"]:
        return ""
    depth = cv["contents_depth"]
    leader = xesc(cv["leader"] or " ").replace('"', "&quot;")
    def template(lvl):
        return ('<text:table-of-content-entry-template text:outline-level="%d" text:style-name="Contents_20_%d">'
                '<text:index-entry-link-start text:style-name="Index_20_Link"/><text:index-entry-text/>'
                '<text:index-entry-tab-stop style:type="right" style:leader-char="%s"/>'
                '<text:index-entry-page-number/><text:index-entry-link-end/>'
                '</text:table-of-content-entry-template>' % (lvl, lvl, leader))
    # placeholder body so the file reads sensibly before its first update
    ph = "".join('<text:p text:style-name="Contents_20_%d">%s</text:p>' % (l, t)
                 for l, t in headings if l <= depth)
    label = xesc(cv["contents_label"] if label is None else label)   # per-document override
    # an empty title template stops LibreOffice inserting its own "Table of Contents"
    title_tpl = '<text:index-title-template text:style-name="Contents_20_Heading">%s</text:index-title-template>' % label
    title_body = ('<text:index-title text:style-name="Sect_TOC" text:name="Contents_Head">'
                  '<text:p text:style-name="Contents_20_Heading">%s</text:p></text:index-title>' % label) if label else ""
    return ('<text:table-of-content text:style-name="Sect_TOC" text:protected="true" text:name="Contents">'
            '<text:table-of-content-source text:outline-level="%d" text:use-index-marks="false">'
            '%s%s</text:table-of-content-source><text:index-body>%s%s</text:index-body>'
            '</text:table-of-content>'
            % (depth, title_tpl, "".join(template(l) for l in range(1, depth + 1)), title_body, ph))

def content_xml(title, b, label=None):
    col = C["colors"]
    HEAD, ZEBRA, CODEBG, RULE = (col["table_header"], col["table_zebra"],
                                 col["code_background"], col["rule"])
    auto = [
        '<style:style style:name="P_cover" style:family="paragraph" style:parent-style-name="Title" '
        'style:master-page-name="First_20_Page"/>',
        '<style:style style:name="Sect_TOC" style:family="section">'
        '<style:section-properties style:editable="false"/></style:style>',
        '<style:style style:name="CellH" style:family="table-cell"><style:table-cell-properties '
        'fo:background-color="%s" fo:padding-left="0.13cm" fo:padding-right="0.13cm" fo:padding-top="0.07cm" '
        'fo:padding-bottom="0.07cm" fo:border="0.5pt solid %s"/></style:style>' % (HEAD, RULE),
        '<style:style style:name="CellB" style:family="table-cell"><style:table-cell-properties '
        'fo:padding-left="0.13cm" fo:padding-right="0.13cm" fo:padding-top="0.07cm" '
        'fo:padding-bottom="0.07cm" fo:border="0.5pt solid %s"/></style:style>' % RULE,
        '<style:style style:name="CellZ" style:family="table-cell"><style:table-cell-properties '
        'fo:background-color="%s" fo:padding-left="0.13cm" fo:padding-right="0.13cm" fo:padding-top="0.07cm" '
        'fo:padding-bottom="0.07cm" fo:border="0.5pt solid %s"/></style:style>' % (ZEBRA, RULE),
        '<style:style style:name="CodeCell" style:family="table-cell"><style:table-cell-properties '
        'fo:background-color="%s" fo:padding-left="0.25cm" fo:padding-right="0.25cm" fo:padding-top="0.2cm" '
        'fo:padding-bottom="0.2cm" fo:border="0.5pt solid %s"/></style:style>' % (CODEBG, RULE),
        '<style:style style:name="RowSplit" style:family="table-row">'
        '<style:table-row-properties fo:keep-together="auto"/></style:style>',
        '<style:style style:name="RowKeep" style:family="table-row">'
        '<style:table-row-properties fo:keep-together="always"/></style:style>',
    ] + b.auto
    body = ('<text:p text:style-name="P_cover">%s</text:p>%s%s'
            % (xesc(title), toc_xml(b.headings, label), "".join(b.body)))
    return ('<?xml version="1.0" encoding="UTF-8"?><office:document-content %s>%s'
            '<office:automatic-styles>%s</office:automatic-styles>'
            '<office:body><office:text>%s</office:text></office:body></office:document-content>'
            % (NS, fonts(), "".join(auto), body))

def meta_xml(title):
    return ('<?xml version="1.0" encoding="UTF-8"?><office:document-meta %s><office:meta>'
            '<dc:title>%s</dc:title><meta:initial-creator>Wikicrop</meta:initial-creator>'
            '<meta:generator>md2odt.py</meta:generator></office:meta></office:document-meta>'
            % (NS, xesc(title)))

MANIFEST = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.3">'
            '<manifest:file-entry manifest:full-path="/" manifest:version="1.3" '
            'manifest:media-type="application/vnd.oasis.opendocument.text"/>'
            '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
            '<manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>'
            '<manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>'
            '</manifest:manifest>')

DOC_KEYS = {"source", "odt", "pdf_dir", "title", "cut", "contents_label", "wiki"}

def documents(cfg):
    """[[document]] entries with their paths resolved against [paths] root."""
    out = []
    for n, d in enumerate(cfg["document"], 1):
        missing = [k for k in ("source", "odt", "title") if k not in d]
        if missing:
            raise SystemExit("config: [[document]] #%d is missing %s" % (n, ", ".join(missing)))
        for k in sorted(set(d) - DOC_KEYS):
            print("config: ignoring unknown key %r in [[document]] #%d" % (k, n))
        label = d.get("contents_label")
        if label is not None and not isinstance(label, str):
            raise SystemExit("config: contents_label in [[document]] #%d must be text" % n)
        r = lambda p: os.path.normpath(os.path.join(cfg["_root"], p))
        out.append({"source": r(d["source"]), "odt": r(d["odt"]), "title": d["title"],
                    "pdf_dir": r(d["pdf_dir"]) if d.get("pdf_dir") else None,
                    "cut": list(d.get("cut", [])), "contents_label": label,
                    "wiki": r(d["wiki"]) if d.get("wiki") else None})
    return out

def make(src, dst, title, cuts=(), contents_label=None):
    """contents_label overrides [cover] contents_label for this document only."""
    global ANCHORS
    raw = io.open(src, encoding="utf-8").read()
    md = re.sub(r"^#\s+.*\n", "", raw, count=1)
    md = cut_sections(md, cuts)
    # anchors are numbered over the whole source file, as a Markdown preview numbers them;
    # keep those whose heading survives the cut and becomes a Heading 1-3 here
    kept = [(l, t) for l, t, _ in heading_slugs(md)]
    anchors, j = [], 0
    for l, t, a in heading_slugs(raw):
        if j < len(kept) and (l, t) == kept[j]:
            if 2 <= l <= 4:
                anchors.append(a)
            j += 1
    ANCHORS = set(anchors)
    b = parse(md, anchors)
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    with zipfile.ZipFile(dst, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.text",
                   compress_type=zipfile.ZIP_STORED)
        for name, data in (("content.xml", content_xml(title, b, contents_label)), ("styles.xml", styles_xml()),
                           ("meta.xml", meta_xml(title)), ("META-INF/manifest.xml", MANIFEST)):
            z.writestr(name, data.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
    return dst, len(b.headings)

def pop_config(args):
    """Remove "--config FILE" from an argument list; return the file or None."""
    if "--config" in args:
        i = args.index("--config")
        if i + 1 >= len(args):
            raise SystemExit("--config needs a file")
        path = args[i + 1]; del args[i:i + 2]
        return path
    return None

if __name__ == "__main__":
    args = sys.argv[1:]
    cfg = load_config(pop_config(args))
    if args:
        if len(args) < 3:
            raise SystemExit(__doc__)
        cuts = [c for c in (args[3].split("||") if len(args) > 3 else []) if c]
        jobs = [(args[0], args[1], args[2], cuts, None)]
    else:
        jobs = [(d["source"], d["odt"], d["title"], d["cut"], d["contents_label"])
                for d in documents(cfg)]
        if not jobs:
            raise SystemExit("no [[document]] entries in the config, and no files given")
    for src, dst, title, cuts, label in jobs:
        out, n = make(src, dst, title, cuts, label)
        print("wrote %s (%d headings)" % (out, n))
