# -*- coding: utf-8 -*-
"""Markdown -> MediaWiki wikitext, for putting a Wikicrop document on the wiki as a page.

    python md2wiki.py [--config FILE]                 every [[document]] that has a wiki = path
    python md2wiki.py [--config FILE] <src.md> <dst.wiki> ["<cut regex>||<cut regex>"]

Shares export-config.toml with md2odt.py: the [[document]] list, cut patterns and
the [wiki] section. Writes a file only, it never edits the wiki.

    ## / ### / ####       -> == / === / ====      (the first # line is the page title, dropped)
    **b** *i* ~~s~~       -> '''b''' ''i'' <s>s</s>
    <sup>x</sup> <sub>x</sub>  kept as they are
    `code`                -> <code><nowiki>code</nowiki></code>
    ``` block             -> <pre>, shown exactly as written
    | table |             -> {| class="wikitable"
    - / 1. / - [ ]        -> * / # / * with a checkbox character
    > quote               -> <blockquote>
    [x](#anchor)          -> [[#Heading text|x]]   (preview anchors, as md2odt.py uses)
    [x](<base_url>Page)   -> [[Page|x]]            (links to this wiki become internal)
    [x](https://...)      -> [https://... x]
    [x](file.md)          -> x                     (local files do not exist on the wiki)
"""
import io, os, re, sys, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import md2odt
from md2odt import heading_slugs, cut_sections, split_row, LIST_RE, BREAK_RE

W = {}                 # [wiki] settings, set by main()
TARGETS = {}           # preview anchor -> MediaWiki anchor text


# --------------------------------------------------------------------------
# inline

def nowiki(s):
    """Text shown exactly as typed. Entities are still decoded inside <nowiki>, so escape &."""
    return "<nowiki>%s</nowiki>" % s.replace("&", "&amp;").replace("<", "&lt;")

def escape(s):
    """Plain prose: neutralise anything MediaWiki would read as markup."""
    s = s.replace("&", "&amp;").replace("<", "&lt;")
    return re.sub(r"\[\[|\]\]|\{\{|\}\}|''+|~~~+|__", lambda m: nowiki(m.group(0)), s)

def plain_heading(text):
    """A heading as MediaWiki shows it, which is also its anchor."""
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    s = re.sub(r"</?su[pb]>", "", s)
    return re.sub(r"\*\*\*|\*\*|~~|`|\*", "", s).strip()

def target(anchor):
    """Characters a link target cannot hold, written as entities MediaWiki decodes."""
    return re.sub(r"[{}\[\]|<>]", lambda m: "&#%d;" % ord(m.group(0)), anchor)

def wiki_link(url):
    """Page title if the URL points at this wiki, else None."""
    base = re.sub(r"^https?://", "", W["base_url"])
    m = re.match(r"^https?://" + re.escape(base) + r"([^?#]+)$", url)
    return urllib.parse.unquote(m.group(1)).replace("_", " ") if m else None

def emphasis(s):
    s = escape(s)
    s = re.sub(r"&lt;(su[pb])>(.+?)&lt;/\1>", r"<\1>\2</\1>", s)       # <sup>, <sub> pass through
    s = re.sub(r"\*\*\*(.+?)\*\*\*", r"'''''\1'''''", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"'''\1'''", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"''\1''", s)
    return re.sub(r"~~(.+?)~~", r"<s>\1</s>", s)

def inline(s):
    keep = []
    def stash(x):
        keep.append(x); return "\x00%d\x00" % (len(keep) - 1)
    s = re.sub(r"`([^`]+)`", lambda m: stash("<code>%s</code>" % nowiki(m.group(1))), s)
    def link(m):
        text, url = emphasis(m.group(1)), m.group(2).strip()
        if url.startswith("#"):
            name = urllib.parse.unquote(url[1:])
            if name in TARGETS:
                return stash("[[#%s|%s]]" % (target(TARGETS[name]), text))
            print("  warning: no heading for link #%s - left as plain text" % name)
            return stash(text)
        if re.match(r"https?://", url):
            page = wiki_link(url)
            if page:
                return stash("[[%s]]" % page if text == page else "[[%s|%s]]" % (page, text))
            return stash("[%s %s]" % (url.replace(" ", "%20"), text))
        return stash(text)                                  # local file: text only
    s = emphasis(re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, s))
    while "\x00" in s:                                      # links can hold stashed code
        s = re.sub("\x00(\\d+)\x00", lambda m: keep[int(m.group(1))], s)
    return s

def line_start(s):
    """A line that opens with list or table markup would be read as one."""
    return "<nowiki/>" + s if re.match(r"[*#:;={]|-{4}", s) else s


# --------------------------------------------------------------------------
# blocks

def table(rows):
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    cell = lambda t: inline(t).replace("||", "&#124;&#124;") or " "
    out = ['{| class="wikitable"', "! " + " !! ".join(cell(t) for t in rows[0])]
    for r in rows[1:]:
        out += ["|-", "| " + " || ".join(cell(t) for t in r)]
    return "\n".join(out + ["|}"])

def pre(lines):
    body = "\n".join(l.rstrip() for l in lines).replace("&", "&amp;")
    return "<pre>%s</pre>" % body.replace("</pre>", "&lt;/pre>")

def lists(items):
    """items: [depth, ordered, number, text]. Returns wikitext, one line per item."""
    out, prefix, prev = [], [], -1
    for depth, ordered, num, txt in items:
        depth = max(0, min(depth, prev + 1)); prev = depth
        prefix = prefix[:depth] + ["#" if ordered else "*"]
        out.append("".join(prefix) + " " + inline(txt))
    return "\n".join(out)

def convert(md):
    lines, out, i = md.split("\n"), [], 0
    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith("```"):
            i += 1; buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i]); i += 1
            i += 1
            out.append(pre(buf)); continue
        if re.match(r"^\s*\|.*\|\s*$", ln) and i + 1 < len(lines) \
           and re.match(r"^\s*\|[\s:\-|]+\|\s*$", lines[i + 1]):
            rows = [split_row(ln)]; i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(split_row(lines[i])); i += 1
            out.append(table(rows)); continue
        m = re.match(r"^(#{2,6})\s+(.*)$", ln)
        if m:
            eq = "=" * len(m.group(1))                     # ## -> ==, ### -> ===
            out.append("%s %s %s" % (eq, inline(m.group(2).strip()), eq)); i += 1; continue
        if re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", ln):
            if W["rules"]: out.append("----")
            i += 1; continue
        if ln.strip().startswith(">"):
            paras, buf = [], []
            while i < len(lines) and lines[i].strip().startswith(">"):
                t = lines[i].strip()[1:].strip(); i += 1
                if t: buf.append(t)
                elif buf: paras.append(buf); buf = []
            if buf: paras.append(buf)
            out.append("<blockquote>%s</blockquote>"
                       % "\n\n".join(line_start(inline(" ".join(p))) for p in paras)); continue
        m = LIST_RE.match(ln)
        if m:
            items = []
            while i < len(lines):
                m = LIST_RE.match(lines[i])
                if not m:
                    # a numbered list split by blank lines carries on if its numbering does
                    j = i
                    while j < len(lines) and not lines[j].strip(): j += 1
                    nxt = LIST_RE.match(lines[j]) if j < len(lines) and j > i else None
                    if nxt and items and items[-1][1] and re.match(r"\d+\.", nxt.group(2)) \
                       and int(nxt.group(2)[:-1]) == items[-1][2] + 1:
                        i = j; continue
                    break
                indent, marker, txt = m.group(1), m.group(2), m.group(3)
                cb = re.match(r"^\[([ xX])\]\s*(.*)$", txt)
                if cb:
                    txt = ("\u2611 " if cb.group(1) != " " else W["checkbox"] + " ") + cb.group(2)
                ordered = bool(re.match(r"\d+\.", marker))
                num = int(marker[:-1]) if ordered else 0
                i += 1
                while i < len(lines) and lines[i].strip() and not LIST_RE.match(lines[i]) \
                      and not re.match(r"^\s*(#{1,4}\s|\||>|```|-{3,})", lines[i]):
                    txt += " " + lines[i].strip(); i += 1
                items.append([len(indent.replace("\t", "    ")) // 2, ordered, num, txt])
            if items[0][1] and items[0][2] != 1:
                print("  warning: numbered list starting at %d restarts at 1 on the wiki" % items[0][2])
            out.append(lists(items)); continue
        if not ln.strip():
            i += 1; continue
        buf = [ln.strip()]; i += 1
        while i < len(lines) and lines[i].strip() and not BREAK_RE.match(lines[i]) \
              and not re.match(r"^\*\*[^*]{1,40}:\*\*", lines[i]):
            buf.append(lines[i].strip()); i += 1
        out.append(line_start(inline(" ".join(buf))))
    return "\n\n".join(out) + "\n"


# --------------------------------------------------------------------------

def contents(headings, label):
    """Optional ([wiki] contents = true): a contents list at the top in place of
    MediaWiki's own. Off by default, Vector 2022's sidebar already does the job."""
    rows = ["%s [[#%s|%s]]" % ("*" * (l - 1), target(a), inline(t))
            for l, t, a in headings if l - 1 <= W["contents_depth"]]
    title = "'''%s'''\n" % escape(label) if label else ""
    return "__NOTOC__\n" + title + "\n".join(rows) + "\n\n"

def make(src, dst, cuts=(), contents_label=None):
    raw = io.open(src, encoding="utf-8").read()
    md = cut_sections(re.sub(r"^#\s+.*\n", "", raw, count=1), cuts)
    # map each preview anchor to the heading text MediaWiki anchors on;
    # MediaWiki numbers a repeated heading _2, _3 ...
    kept = [(l, t) for l, t, _ in heading_slugs(md)]
    TARGETS.clear(); seen, j, heads = {}, 0, []
    for l, t, a in heading_slugs(raw):
        if j < len(kept) and (l, t) == kept[j]:
            j += 1
            if l >= 2:
                p = plain_heading(t); n = seen.get(p, 0) + 1; seen[p] = n
                TARGETS[a] = p if n == 1 else "%s_%d" % (p, n)
                heads.append((l, t, TARGETS[a]))
    head = ("<!-- Generated from %s by scripts/document-export/md2wiki.py. "
            "Edit the Markdown and regenerate, not this page. -->\n"
            % os.path.basename(src))
    label = W["contents_label"] if contents_label is None else contents_label
    text = head + (contents(heads, label) if W["contents"] else "") + convert(md)
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    io.open(dst, "w", encoding="utf-8", newline="\n").write(text)
    return dst, len(TARGETS)

if __name__ == "__main__":
    args = sys.argv[1:]
    cfg = md2odt.load_config(md2odt.pop_config(args))
    W.update(cfg["wiki"])
    W["contents_label"] = cfg["cover"]["contents_label"]     # same label and depth as the PDF cover
    W["contents_depth"] = cfg["cover"]["contents_depth"]
    if args:
        if len(args) < 2:
            raise SystemExit(__doc__)
        jobs = [(args[0], args[1], [c for c in (args[2].split("||") if len(args) > 2 else []) if c], None)]
    else:
        jobs = [(d["source"], d["wiki"], d["cut"], d["contents_label"])
                for d in md2odt.documents(cfg) if d["wiki"]]
        if not jobs:
            raise SystemExit("no [[document]] has a wiki = path, and no files given")
    for src, dst, cuts, label in jobs:
        out, n = make(src, dst, cuts, label)
        print("wrote %s (%d headings)" % (out, n))
