#!/usr/bin/env python3
"""
Web citations as {{ChuthichWeb}} on the pages of crop groups 7-13 (standard section 3).

Hand-written web citations under `== Tham khảo ==`

    # [https://... Viện KHNN Việt Nam. Kỹ thuật trồng chuối. Truy cập ngày 3 tháng 11 năm 2025.]

become

    # {{ChuthichWeb
    | url         = https://...
    | NhaXuatBan  = Viện KHNN Việt Nam
    | TieuDe      = Kỹ thuật trồng chuối
    | NgayTruyCap = 3 tháng 11 năm 2025
    }}

Splitting free text into publisher and title is a guess, so nothing is written
from the guess alone. The work goes through a review table:

  1. --draft   reads every citation and writes the review table (CSV). Each row
               carries the proposed fields and a status:
                 auto    `Publisher. Title. Truy cập ...`, pre-approved (approve = y)
                 review  ambiguous split, single part or no access date: check
                         and correct the fields, then set approve = y
                 keep    offline-style or journal (DOI, author-year) sources and
                         URLs outside a link, which stay hand-written (3.3)
  2. edit the CSV: correct fields, set approve to y on rows to convert
  3. dry run   (no flag) applies the approved rows in memory and reports
  4. --validate renders every changed page before and after. The template prints
               `Publisher. Title. Truy cập ngày D.`, so a faithful conversion
               renders the same. External links must not change, so ConfirmEdit
               has no reason to ask for a CAPTCHA
  5. --run     writes, then audit_citations.py checks the live wiki

Order: run retrofit_pass.py first. The draft is taken from each page as it will
be after the retrofit pass (no <u>, plain hyphens), so the table stays valid
across that run, and --run refuses a page the retrofit pass has not reached.

Same safety model as the other bots: dry run by default, baserevid, nocreate,
maxlag, re-read before each write, run log, and a separate audit.

Usage
-----
    python convert_citations.py --draft              # write the review table
    python convert_citations.py --draft --force      # rewrite it (loses your edits)
    python convert_citations.py                      # dry run from the table
    python convert_citations.py --show-diff --only "Mía KK2"
    python convert_citations.py --validate
    python convert_citations.py --run --limit 3
"""

import argparse
import csv
import difflib
import getpass
import html
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from create_categories import READ_DELAY  # noqa: E402
from retrofit_pass import (FIX_NAMES, ROOT, SHEET, Bot as RetrofitBot, apply,  # noqa: E402
                           build_contexts, read_catalogue)

import project_config as cfg  # noqa: E402

UA = cfg.user_agent("WikicropCitationBot", "web citations as ChuthichWeb")
SUMMARY = "Web citations as ChuthichWeb template (standard section 3)"
REVIEW = cfg.LOGS / "citation-review.csv"
DEFAULT_LOG = cfg.LOGS / "citation-run-log.csv"
DEFAULT_WRITE_DELAY = 20.0
# `replacement`: a hand-written line (without the list marker) used instead of the
# template, for a source the standard keeps hand-written, such as a book (3.3).
COLUMNS = ["approve", "status", "title", "NhaXuatBan", "TieuDe", "NgayTruyCap", "url", "note", "original",
           "replacement"]

DATE = re.compile(r"[Tt]ruy cập(?:\s+ngày)?\s*(?:lúc\s*)?"
                  r"(?:(\d{1,2})\s+tháng\s+(\d{1,2}),?\s+(?:năm\s+)?(\d{4})"
                  r"|(\d{1,2})/(\d{1,2})/(\d{4}))\.?")
LINK = re.compile(r"\[(https?://[^\s\]]+)\s+([^\]\n]+)\]\s*\.?")
SITE = re.compile(r"[\w-]+(?:\.[\w-]+)*\.(?:vn|com|org|net|gov|edu|info)(?:\.vn)?", re.I)
JOURNAL = re.compile(r"doi\.org/|\((?:\d{4}|n\.d\.)\)\.", re.I)    # DOI, or author-year `(2023).`


def section(text):
    """(start, end) of the `Tham khảo` section body, or None."""
    m = re.search(r"^==\s*Tham khảo\s*==[ \t]*$", text, re.M)
    if not m:
        return None
    nxt = re.search(r"^==[^=]", text[m.end():], re.M)
    return m.end(), (m.end() + nxt.start() if nxt else len(text))


def split_parts(s):
    """Split on `. ` outside ''italics''. Returns the parts without the stops."""
    parts, buf, italic, i = [], "", False, 0
    while i < len(s):
        if s.startswith("''", i):
            italic = not italic
            buf += "''"
            i += 2
            continue
        if not italic and s[i] == "." and (i + 1 == len(s) or s[i + 1] == " "):
            if buf.strip():
                parts.append(buf.strip())
            buf = ""
            i += 1
            continue
        buf += s[i]
        i += 1
    if buf.strip():
        parts.append(buf.strip())
    return parts


def draft_row(title, line):
    """A review-table row for one citation line, or None if it is not one."""
    if not line.lstrip().startswith(("#", "*")):
        return None                                      # only list items are citations
    body = line.strip().lstrip("#*").strip()
    row = dict.fromkeys(COLUMNS, "")
    row.update(title=title, original=line)
    if not body or "ChuthichWeb" in body:
        return None
    m = LINK.fullmatch(body)
    if not m:
        if "http" in body:
            row.update(status="keep", approve="keep", note="URL outside a [link]: convert by hand")
            return row
        return None                                      # offline source, written by hand
    url, text = m.group(1), m.group(2).strip()
    row["url"] = url
    if JOURNAL.search(url + " " + text):
        row.update(status="keep", approve="keep", note="journal or author-year source: stays hand-written (3.3)")
        return row
    d = DATE.search(text)
    if d:
        day, month, year = (d.group(1), d.group(2), d.group(3)) if d.group(1) else d.group(4, 5, 6)
        row["NgayTruyCap"] = f"{int(day)} tháng {int(month)} năm {year}"
        text = (text[:d.start()] + text[d.end():]).strip()
    parts = split_parts(text.rstrip(". "))
    if len(parts) == 2 and d:
        row.update(status="auto", approve="y", NhaXuatBan=parts[0], TieuDe=parts[1])
        return row
    # `Publisher. Title. Site` (decided 2026-10-27): for Wikipedia the site is the
    # publisher and `Cộng đồng Wikipedia` goes, otherwise the domain goes (the
    # link already shows it). The note names the dropped text for --validate.
    if len(parts) == 3 and d and "wikipedia" in parts[2].lower():
        row.update(status="auto", approve="y", NhaXuatBan=parts[2], TieuDe=parts[1],
                   note=f"site rule: dropped `{parts[0]}`")
        return row
    if len(parts) == 3 and d and SITE.fullmatch(parts[2]):
        row.update(status="auto", approve="y", NhaXuatBan=parts[0], TieuDe=parts[1],
                   note=f"site rule: dropped `{parts[2]}`")
        return row
    notes = []
    if not d:
        notes.append("no access date: add NgayTruyCap or mark keep")
    if len(parts) == 1:
        row["TieuDe"] = parts[0]
        notes.append("one part: is it the publisher or the title?")
    elif len(parts) >= 2:
        row.update(NhaXuatBan=parts[0], TieuDe=". ".join(parts[1:]))
        if len(parts) > 2:
            notes.append(f"{len(parts)} parts: check where the publisher ends")
    row.update(status="review", note=". ".join(notes))
    return row


def template(row):
    """The citation line for an approved row, laid out as in standard section 3."""
    def esc(v):
        return v.replace("|", "{{!}}")
    fields = [("url", row["url"]), ("NhaXuatBan", row["NhaXuatBan"]),
              ("TieuDe", row["TieuDe"]), ("NgayTruyCap", row["NgayTruyCap"])]
    # keep the line's own list marker: turning one `*` line into `#` would split the list
    marker = re.match(r"\s*([#*]+)", row["original"]).group(1)
    if row.get("replacement", "").strip():
        return f"{marker} {row['replacement'].strip()}"
    lines = [f"{marker} {{{{ChuthichWeb"] + [f"| {k:<11} = {esc(v.strip())}" for k, v in fields if v.strip()]
    return "\n".join(lines + ["}}"])


def convert(text, rows):
    """Apply approved rows to a page. Returns (new_text, converted, stale rows).
    A row whose converted line is already on the page is done, not stale."""
    done, stale = 0, []
    lines = text.split("\n")
    for row in rows:
        try:
            i = lines.index(row["original"])
        except ValueError:
            if template(row).split(" ", 1)[1] not in text:     # done, whatever list marker it has now
                stale.append(row)
            continue
        lines[i] = template(row)
        done += 1
    return "\n".join(lines), done, stale


def approved(row):
    return row["approve"].strip().lower() in ("y", "yes", "x")


def check_row(row):
    """Reasons an approved row cannot be written as is."""
    if row.get("replacement", "").strip():
        return []                                        # hand-written line, used as given
    out = []
    if not row["url"].startswith("http"):
        out.append("no url")
    if not row["NgayTruyCap"].strip():
        out.append("NgayTruyCap is required")
    elif row["NgayTruyCap"].strip().lower().startswith("ngày"):
        out.append("NgayTruyCap must not start with 'ngày'")
    if not (row["NhaXuatBan"].strip() or row["TieuDe"].strip()):
        out.append("needs a publisher or a title")
    return out


def words(h):
    """The words a reader sees, as a bag: tags, stops, the date words and leading zeros ignored."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", h))
    text = re.sub(r"(?<!\d)0(?=\d)", "", text)
    return Counter(w for w in re.findall(r"\w+", text.lower()) if w not in ("ngày", "năm"))


def structure(h):
    """The sequence of list, item and link tags: must not change."""
    return re.findall(r"</?(?:ol|ul|li|a)\b", h)


class Bot(RetrofitBot):
    def __init__(self):
        super().__init__()
        self.opener.addheaders = [("User-Agent", UA)]

    def links(self, title, text):
        r = self._call({"action": "parse", "title": title, "text": text, "prop": "externallinks",
                        "contentmodel": "wikitext"}, post=True)
        return set(r["parse"].get("externallinks", []))


def retrofitted(pages):
    """{title: text as it will be after retrofit_pass} and the titles it has not reached."""
    out, pending = {}, set()
    for t, (_, text, ctx) in pages.items():
        new, applied, _ = apply(text, ctx, FIX_NAMES)
        out[t] = new
        if applied:
            pending.add(t)
    return out, pending


def do_draft(pages, path, force):
    if path.exists() and not force:
        raise SystemExit(f"{path.name} exists and may hold your review. Use --force to rewrite it.")
    texts, pending = retrofitted(pages)
    rows = []
    for t in sorted(texts):
        span = section(texts[t])
        if not span:
            continue
        for line in texts[t][span[0]:span[1]].split("\n"):
            row = draft_row(t, line)
            if row:
                rows.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:   # BOM: Excel reads it as UTF-8
        w = csv.DictWriter(fh, COLUMNS)
        w.writeheader()
        w.writerows(rows)
    by = {}
    for r in rows:
        by[r["status"]] = by.get(r["status"], 0) + 1
    print(f"Wrote {len(rows)} citations to {path}")
    for s in ("auto", "review", "keep"):
        print(f"  {s:<7} {by.get(s, 0)}")
    if pending:
        print(f"\nTaken from the post-retrofit text of {len(pending)} page(s) not yet retrofitted. "
              "Run retrofit_pass.py before --run.")
    print("\nNext: open the table, check the `review` rows, correct their fields and set approve = y.")


def main():
    ap = argparse.ArgumentParser(description="Web citations as {{ChuthichWeb}}, groups 7-13.")
    ap.add_argument("--draft", action="store_true", help="write the review table and stop")
    ap.add_argument("--force", action="store_true", help="with --draft: overwrite an existing table")
    ap.add_argument("--table", type=Path, default=REVIEW, help="the review table (CSV)")
    ap.add_argument("--run", action="store_true", help="write the edits (default is a dry run)")
    ap.add_argument("--only", action="append", metavar="TITLE", help="restrict to this page (repeatable)")
    ap.add_argument("--limit", type=int, default=0, help="only the first N pages to change")
    ap.add_argument("--delay", type=float, default=DEFAULT_WRITE_DELAY,
                    help=f"seconds between writes (default {DEFAULT_WRITE_DELAY:.0f}, minimum 5)")
    ap.add_argument("--show-diff", action="store_true", help="print a unified diff per page")
    ap.add_argument("--validate", action="store_true", help="render and link checks with action=parse")
    ap.add_argument("--log", type=Path, default=DEFAULT_LOG, help="CSV of writes, for the audit")
    args = ap.parse_args()
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    wiki = Bot()
    pages, _ = build_contexts(wiki, read_catalogue(SHEET), args.only)
    if args.draft:
        do_draft(pages, args.table, args.force)
        return

    if not args.table.exists():
        raise SystemExit(f"No review table at {args.table}. Run with --draft first.")
    rows = list(csv.DictReader(open(args.table, encoding="utf-8-sig")))
    todo = [r for r in rows if approved(r) and r["title"] in pages]
    invalid = [(r, check_row(r)) for r in todo if check_row(r)]
    todo = [r for r in todo if not check_row(r)]
    # Preview from each page as it will be after retrofit_pass, so the table can be
    # checked and validated before that pass runs. Only --run needs it live.
    texts, pending = retrofitted(pages)

    plan, stale = {}, []
    blocked = sorted(pending & {r["title"] for r in todo})
    for t in sorted({r["title"] for r in todo}):
        revid, text = pages[t][0], texts[t]
        new, done, st = convert(text, [r for r in todo if r["title"] == t])
        stale += st
        if done:
            plan[t] = (revid, text, new, done)

    by = {}
    for r in rows:
        by[r["status"]] = by.get(r["status"], 0) + 1
    print(f"\nReview table: {len(rows)} rows ({', '.join(f'{k} {n}' for k, n in sorted(by.items()))}), "
          f"{sum(approved(r) for r in rows)} approved")
    print(f"  pages to edit : {len(plan)} ({sum(p[3] for p in plan.values())} citations)")
    left = [r for r in rows if r["status"] == "review" and not approved(r)]
    if left:
        print(f"  review rows not approved yet: {len(left)} (left as they are)")
    for label, items in (("APPROVED BUT INVALID", [f"{r['title']}: {', '.join(e)}" for r, e in invalid]),
                         ("STALE (line no longer on the page; re-draft)", [f"{r['title']}: {r['original'][:80]}" for r in stale]),
                         ("NOT RETROFITTED ON THE WIKI YET (previewed; --run skips them until retrofit_pass.py has run)", blocked)):
        if items:
            print(f"\n{label} ({len(items)}):")
            for i in items:
                print("  " + i)

    pending_pages = list(plan)
    if args.limit:
        pending_pages = pending_pages[:args.limit]
        print(f"\n--limit {args.limit}: restricting this run to {len(pending_pages)} page(s)")
    print()
    for t in pending_pages:
        print(f"  [EDIT] {t}: {plan[t][3]} citation(s)")
    if args.show_diff:
        for t in pending_pages:
            _, old, new, _ = plan[t]
            print("\n" + "".join(difflib.unified_diff(
                old.splitlines(True), new.splitlines(True), f"{t} (live)", f"{t} (new)", n=0)))

    if args.validate:
        print(f"\nValidating {len(pending_pages)} page(s): same render, same external links ...")
        bad = 0
        for t in pending_pages:
            _, old, new, _ = plan[t]
            a, _, _ = wiki.parse(t, old)
            b, _, _ = wiki.parse(t, new)
            la, lb = wiki.links(t, old), wiki.links(t, new)
            time.sleep(READ_DELAY)
            issues = []
            # a row may declare a link it adds on purpose (a URL that was plain text in <nowiki>)
            declared = {u for r in todo if r["title"] == t for u in re.findall(r"adds link `(.+?)`", r["note"])}
            # SpamBlacklist refuses a new link outright, whatever the CAPTCHA (the global
            # Wikimedia list applies here too), so ask the wiki before any run
            for u in sorted(lb - la):
                hit = wiki._call({"action": "spamblacklist", "url": u}).get("spamblacklist", {})
                if hit.get("result") == "blacklisted":
                    issues.append(f"new link is on the spam blacklist ({', '.join(hit.get('matches', []))}), "
                                  "the wiki will refuse the edit")
            if la - lb or (lb - la) - declared:
                issues.append(f"external links change (CAPTCHA risk): +{sorted(lb - la)[:2]} -{sorted(la - lb)[:2]}")
            elif lb - la:
                print(f"  note     {t}: adds the declared link {sorted(lb - la)[0]} (the wiki may ask a CAPTCHA)")
            if a != b:
                # What a faithful conversion may change: the order of the parts
                # (the site rule moves the site to the front), stops, commas,
                # spacing, the date format (`03 tháng 11, 2025` -> `3 tháng 11 năm
                # 2025`) and the text a site-rule row says it dropped. The list and
                # link structure must stay exactly the same.
                # A row's note may also name text a reviewer added (an access date
                # filled after checking the link): `added `Truy cập ngày ...``.
                def noted(kind):                                  # noqa: E306
                    return sum((words(m.replace("''", "")) for r in todo if r["title"] == t
                                for m in re.findall(kind + r" `(.+?)`", r["note"])), Counter())
                dropped, added = noted("dropped"), noted("added")
                # unwrap only the link a row declares it adds, then the rest must match
                b_cmp = b
                for u in declared:
                    b_cmp = re.sub(r'<a [^>]*href="' + re.escape(html.escape(u)) + r'"[^>]*>(.*?)</a>', r"\1", b_cmp)
                if structure(a) == structure(b_cmp) and words(a) - dropped + added == words(b_cmp):
                    note = ("differs only by the text its rows' notes declare" if dropped or added
                            else "differs only in full stops, spacing or date format")
                    if not issues:
                        print(f"  ok       {t}  ({note})")
                        continue
                else:
                    i = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]), min(len(a), len(b)))
                    issues.append(f"render differs at ...{a[max(0, i - 50):i + 70]}\n"
                                  f"                    new ...{b[max(0, i - 50):i + 70]}")
            if issues:
                bad += 1
                print(f"  PROBLEM  {t}")
                for i in issues:
                    print(f"      {i}")
            elif a == b:
                print(f"  ok       {t}  (identical)")
        print(f"\nValidation: {len(pending_pages) - bad} ok, {bad} with problems.")
        if bad:
            print("Do not --run until every problem is explained. A changed access date "
                  "(`29 tháng 11, 2025` -> `29 tháng 11 năm 2025`) is an expected difference.")

    delay = max(5.0, args.delay)
    if not args.run:
        print(f"\nDRY RUN - nothing was written.")
        print(f"Re-run with --run to edit {len(pending_pages)} page(s) at {delay:.0f}s apart "
              f"(about {len(pending_pages) * delay / 60:.0f} min).")
        return
    if not pending_pages:
        print("\nNothing to do.")
        return

    print(f"\nAbout to edit {len(pending_pages)} pages, {delay:.0f}s apart.")
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
            log.writerow(["title", "old_revid", "new_revid", "citations", "result"])
        for i, title in enumerate(pending_pages, 1):
            print(f"[{i}/{len(pending_pages)}] {title}")
            fresh = wiki.fetch([title]).get(title)          # re-read right before writing
            if not fresh:
                failed += 1
                print("    FAILED - page vanished")
                continue
            revid, text = fresh
            if apply(text, pages[title][2], FIX_NAMES)[1]:
                skipped += 1
                print("    skipped - not retrofitted yet, run retrofit_pass.py first")
                continue
            new, done, st = convert(text, [r for r in todo if r["title"] == title])
            if st:
                print(f"    note - {len(st)} row(s) no longer match the page, left as they are")
            if not done:
                skipped += 1
                print("    skipped - nothing to convert now")
            else:
                rows = [r for r in todo if r["title"] == title and r["original"] in text.split("\n")]
                hand = any(r.get("replacement", "").strip() for r in rows)
                webs = any(not r.get("replacement", "").strip() for r in rows)
                summary = ("Web citations as ChuthichWeb template" if webs else "") + \
                          (", " if webs and hand else "") + \
                          ("book citation written out by hand" if hand else "") + " (standard section 3)"
                success, info, newrev = wiki.replace(title, new, revid, summary[0].upper() + summary[1:])
                if success is True:
                    ok += 1
                    print(f"    edited (rev {newrev}): {done} citation(s)")
                    log.writerow([title, revid, newrev, done, "edited"])
                elif success == "skipped":
                    skipped += 1
                    print(f"    skipped - {info}")
                else:
                    failed += 1
                    print(f"    FAILED - {info}")
                    log.writerow([title, revid, "", done, "FAILED " + info])
                fh.flush()
            if i < len(pending_pages):
                time.sleep(delay)
    print(f"\nDone. edited={ok} skipped={skipped} failed={failed}")
    print(f"Log: {args.log}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Re-running is safe - converted lines no longer match the table.")
        sys.exit(1)
