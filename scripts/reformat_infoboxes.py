#!/usr/bin/env python3
"""
Reformat infoboxes to one parameter per line.

Rewrites every `{{InfoCropPlant ...}}` and `{{InfoPlant1 ...}}` call on the
pages of crop groups 7-13 (BT07-BT13) so that `{{Name` and `}}` sit
on their own lines and each parameter has a line to itself (standard section
6.6). Nothing else on the page is touched.

Scope
-----
Only groups 7-13 are in scope. Pages of groups 1-6 are maintained by other
editors and are never edited, whatever is passed on the command line. Membership is decided by title: the species title
or `<species> <variety>`, for the species listed on the Main Page under groups
7-13 (SCOPE below), plus the few pages whose title does not follow the rule
(EXTRA_TITLES). Check the list printed by the dry run before a first --run.

The transform
-------------
* The call is split on `|` at brace depth 0, so `[[a|b]]`, `{{DanhsachLoai|A|B}}`
  and `{{{x|}}}` inside a value stay whole. Comments and <nowiki> are skipped.
* Named parameters are trimmed (MediaWiki trims them too), so the template
  receives exactly the same arguments as before. Every page is re-parsed after
  the rewrite and the argument lists are compared; a mismatch skips the page.
* A call is left alone when it is already one-per-line (hand-aligned
  `|Key = Value` included) or has fewer than 4 parameters.
* A page is SKIPPED, not guessed at, when a call has an unnamed parameter, a
  comment-only piece, or `{{` mid-line. Text glued to `}}` is moved to its own
  line, and --validate shows whether that changes the render.

Design notes
------------
* Standard library only. DRY RUN BY DEFAULT: writing needs --run.
* --validate renders the old and the new wikitext of every changed page with
  action=parse and compares the HTML. Run it before --run (it needs no login).
* Edits carry baserevid (an edit conflict fails, never overwrites), nocreate,
  minor, maxlag, and a fixed delay between writes. The text is re-read and the
  transform recomputed immediately before each write, so a stale dry run
  cannot clobber a newer edit.
* Idempotent: re-running skips pages that are already compliant.
* Each write is logged to a CSV (title, old and new revid) for the audit,
  audit_infobox_reformat.py.
* Credentials are prompted for at run time, never stored. Use a BotPassword
  with only `createeditmovepage` (see create_categories.py).

Usage
-----
    python reformat_infoboxes.py                    # dry run, no login
    python reformat_infoboxes.py --show-diff        # + unified diffs
    python reformat_infoboxes.py --validate         # + render old vs new
    python reformat_infoboxes.py --run --limit 3    # cautious first run
    python reformat_infoboxes.py --run              # the whole scope
    python reformat_infoboxes.py --only "Mía KK2"   # one page (dry or --run)
"""

import argparse
import csv
import difflib
import getpass
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from create_categories import MAXLAG, READ_DELAY, Wiki, error_text  # noqa: E402
import project_config as cfg  # noqa: E402

UA = cfg.user_agent("WikicropInfoboxBot", "infobox layout")
SUMMARY = "Reformat infobox to one parameter per line (standard section 6.6)"
DEFAULT_WRITE_DELAY = 20.0
DEFAULT_LOG = cfg.LOGS / "p1-1-infobox-run-log.csv"
FETCH_BATCH = 25
MIN_PARAMS = 4          # standard 6.6: 1-3 parameters may stay inline

# Main Page groups 7-13. A page is in scope when its title is the prefix or
# starts with "<prefix> " (case-insensitive: cultivar titles use `Sầu riêng`,
# the species page is `Sầu Riêng`).
SCOPE = {
    7: ("Cỏ voi", "Lúa mạch đen", "Lucerne", "Alfalfa"),
    8: ("Mía", "Củ cải đường"),
    9: ("Khoai tây", "Khoai lang", "Sắn", "Khoai mì", "Khoai mỳ"),
    10: ("Thuốc lá", "Thuốc phiện"),
    11: ("Sâm Ấn Độ", "Rau đắng biển", "Sả"),
    12: ("Bạc hà", "Hương nhu", "Húng", "Vetiver"),
    13: ("Chuối", "Bưởi", "Cam", "Dưa hấu", "Xoài", "Mít", "Ổi", "Dừa", "Khóm", "Dứa",
         "Chôm chôm", "Mận", "Sầu riêng", "Cây măng cụt", "Măng cụt", "Thanh long",
         "Vú sữa", "Dâu", "Điều"),
}
# Titles that belong to groups 7-13 but do not start with a species name above.
EXTRA_TITLES = {
    "Húng lũi": 12,     # Mentha, group 12
}

TARGET = re.compile(r"\{\{\s*[Ii]nfo(?:CropPlant|Plant1)\s*(?=[|}])")


def group_of(title):
    if title in EXTRA_TITLES:
        return EXTRA_TITLES[title]
    low = title.lower()
    for group, prefixes in SCOPE.items():
        for p in prefixes:
            p = p.lower()
            if low == p or low.startswith(p + " "):
                return group
    return None


# ------------------------------------------------------------- the transform

def _special_end(text, i):
    """End index of a comment or <nowiki> starting at i, else None."""
    if text.startswith("<!--", i):
        j = text.find("-->", i + 4)
        return len(text) if j < 0 else j + 3
    if text.startswith("<nowiki>", i):
        j = text.find("</nowiki>", i + 8)
        return len(text) if j < 0 else j + 9
    return None


def _protected(text):
    """Ranges holding comments and <nowiki>, where a call is not a call."""
    out, i = [], 0
    while i < len(text):
        end = _special_end(text, i)
        if end:
            out.append((i, end))
            i = end
        else:
            i += 1
    return out


def scan_call(text, start):
    """Parse the call whose `{{` is at `start`.

    Returns (end, pipes, eqs): index after the closing `}}`, and the positions
    of the depth-0 `|` and `=` inside it. None if the braces never balance.
    """
    stack, pipes, eqs = [], [], []
    i, n = start, len(text)
    while i < n:
        end = _special_end(text, i)
        if end:
            i = end
            continue
        if text.startswith("{{{", i):
            stack.append("{{{")
            i += 3
        elif text.startswith("{{", i):
            stack.append("{{")
            i += 2
        elif text.startswith("[[", i):
            stack.append("[[")
            i += 2
        elif text.startswith("}}}", i) and stack and stack[-1] == "{{{":
            stack.pop()
            i += 3
        elif text.startswith("}}", i) and stack and stack[-1] == "{{":
            stack.pop()
            i += 2
            if not stack:
                return i, pipes, eqs
        elif text.startswith("]]", i) and stack and stack[-1] == "[[":
            stack.pop()
            i += 2
        else:
            if len(stack) == 1:
                if text[i] == "|":
                    pipes.append(i)
                elif text[i] == "=":
                    eqs.append(i)
            i += 1
    return None


def parse_call(text, start):
    """Return a dict describing the call at `start`, or raise ValueError."""
    scanned = scan_call(text, start)
    if scanned is None:
        raise ValueError("unbalanced braces")
    end, pipes, eqs = scanned
    close = end - 2
    name = text[start + 2:(pipes[0] if pipes else close)].strip()
    bounds = pipes + [close]
    params, dropped = [], 0
    for k, p in enumerate(pipes):
        piece_end = bounds[k + 1]
        piece = text[p + 1:piece_end]
        eq = next((e for e in eqs if p < e < piece_end), None)
        if eq is None:
            if "<!--" in piece:
                raise ValueError("comment-only piece between parameters")
            if piece.strip():
                raise ValueError(f"unnamed parameter: {piece.strip()[:40]!r}")
            dropped += 1
            continue
        key = text[p + 1:eq].strip()
        if not key:
            raise ValueError("empty parameter name")
        params.append((key, text[eq + 1:piece_end].strip()))

    def own_line(pos):
        return not text[text.rfind("\n", 0, pos) + 1:pos].strip()

    compliant = (bool(pipes) and all(own_line(p) for p in pipes) and own_line(close)
                 and "\n" in text[start:pipes[0]])
    return {"start": start, "end": end, "name": name, "params": params,
            "dropped": dropped, "compliant": compliant}


def layout(call):
    lines = ["{{" + call["name"]]
    lines += [f"|{k}={v}" for k, v in call["params"]]
    lines.append("}}")
    return "\n".join(lines)


def reformat(text):
    """Return (new_text, status, detail).

    status: "none" no infobox, "ok" already compliant, "change", or "skip"
    (detail says why). The new text only differs from `text` for "change".
    """
    guarded = _protected(text)
    calls = []
    for m in TARGET.finditer(text):
        if any(a <= m.start() < b for a, b in guarded):
            continue
        try:
            call = parse_call(text, m.start())
        except ValueError as e:
            return text, "skip", str(e)
        if m.start() and text[m.start() - 1] != "\n":
            return text, "skip", "`{{` is not at the start of a line"
        # Text glued to `}}` gets a line of its own: the call ends in a table,
        # so this renders the same (checked by --validate, not assumed).
        call["glued"] = bool(text[call["end"]:].split("\n", 1)[0].strip())
        call["compliant"] = call["compliant"] and not call["glued"]
        calls.append(call)
    if not calls:
        return text, "none", ""

    out, pos, changed = [], 0, 0
    for call in calls:
        if call["compliant"] or len(call["params"]) < MIN_PARAMS:
            continue
        new = layout(call)
        back = parse_call(new, 0)       # round trip: same arguments, same order
        if back["params"] != call["params"]:
            return text, "skip", "round-trip mismatch"
        out.append(text[pos:call["start"]])
        out.append(new + "\n" if call["glued"] else new)
        pos = call["end"]
        changed += 1
    if not changed:
        return text, "ok", ""
    out.append(text[pos:])
    glued = any(c["glued"] for c in calls)
    return "".join(out), "change", f"{changed} call(s)" + (", text glued to `}}` moved down" if glued else "")


def duplicates(text):
    """Parameter names given twice in one call (last one wins on the wiki)."""
    names = []
    for m in TARGET.finditer(text):
        try:
            seen = [k for k, _ in parse_call(text, m.start())["params"]]
        except ValueError:
            continue
        names += [k for k in set(seen) if seen.count(k) > 1]
    return sorted(set(names))


# ------------------------------------------------------------------ api i/o

class Bot(Wiki):
    def __init__(self):
        super().__init__()
        self.opener.addheaders = [("User-Agent", UA)]

    def titles_in_scope(self):
        """Every main-namespace non-redirect title that falls in groups 7-13."""
        found, cont = {}, {}
        while True:
            r = self._call({"action": "query", "list": "allpages", "apnamespace": 0,
                            "aplimit": 500, "apfilterredir": "nonredirects", **cont})
            for p in r["query"]["allpages"]:
                g = group_of(p["title"])
                if g:
                    found[p["title"]] = g
            if "continue" not in r:
                return found
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

    def render(self, title, text):
        r = self._call({"action": "parse", "title": title, "text": text, "prop": "text",
                        "contentmodel": "wikitext", "disablelimitreport": "1",
                        "disableeditsection": "1"}, post=True)
        if "error" in r:
            raise RuntimeError(r["error"].get("code", "?"))
        html = re.sub(r"<!--.*?-->", "", r["parse"]["text"], flags=re.S)
        html = re.sub(r">\s+<", "><", html)
        return re.sub(r"\s+", " ", html).strip()

    def replace(self, title, text, revid):
        r = self.edit({"action": "edit", "title": title, "text": text, "summary": SUMMARY,
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


# --------------------------------------------------------------------- main

def plan(wiki, only):
    """Classify every in-scope page. Returns (groups, rows) with
    rows = {title: (revid, old, new, status, detail)}."""
    members = wiki.titles_in_scope()
    if only:
        missing = [t for t in only if t not in members]
        if missing:
            raise SystemExit(f"Not in scope (groups 7-13) or not a page: {missing}")
        members = {t: members[t] for t in only}
    print(f"In scope (groups 7-13): {len(members)} pages "
          f"({', '.join(f'{g}:{sum(1 for v in members.values() if v == g)}' for g in sorted(set(members.values())))})")
    pages = wiki.fetch(sorted(members))
    rows = {}
    for title in sorted(members):
        if title not in pages:
            rows[title] = (0, "", "", "skip", "page not found")
            continue
        revid, old = pages[title]
        new, status, detail = reformat(old)
        rows[title] = (revid, old, new, status, detail)
    return members, rows


def main():
    ap = argparse.ArgumentParser(description="Reformat infoboxes of groups 7-13 to one parameter per line.")
    ap.add_argument("--run", action="store_true", help="write the edits (default is a dry run)")
    ap.add_argument("--delay", type=float, default=DEFAULT_WRITE_DELAY,
                    help=f"seconds between writes (default {DEFAULT_WRITE_DELAY:.0f}, minimum 5)")
    ap.add_argument("--limit", type=int, default=0, help="only the first N pages to change")
    ap.add_argument("--only", action="append", metavar="TITLE",
                    help="restrict to this in-scope page (repeatable)")
    ap.add_argument("--show-diff", action="store_true", help="print a unified diff per page")
    ap.add_argument("--validate", action="store_true",
                    help="render old and new wikitext with action=parse and compare")
    ap.add_argument("--log", type=Path, default=DEFAULT_LOG, help="CSV of writes, for the audit")
    args = ap.parse_args()
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    wiki = Bot()
    members, rows = plan(wiki, args.only)
    by = {s: [t for t, r in rows.items() if r[3] == s] for s in ("change", "ok", "skip", "none")}
    print(f"  to reformat            : {len(by['change'])}")
    print(f"  already compliant      : {len(by['ok'])}")
    print(f"  no infobox (list etc.) : {len(by['none'])}")
    print(f"  SKIPPED, needs a human : {len(by['skip'])}")
    for t in by["skip"]:
        print(f"      {t}: {rows[t][4]}")
    for t, (_, old, _, status, _) in rows.items():
        dup = duplicates(old) if status in ("change", "ok") else []
        if dup:
            print(f"  note: {t} repeats {', '.join(dup)} (last value wins, kept as is)")

    pending = by["change"]
    if args.limit:
        pending = pending[:args.limit]
        print(f"\n--limit {args.limit}: restricting this run to {len(pending)} page(s)")

    print()
    for t in pending:
        print(f"  [CHANGE] g{members[t]:<2} {t}  ({rows[t][4]})")
    if args.show_diff:
        for t in pending:
            _, old, new, _, _ = rows[t]
            print("\n" + "".join(difflib.unified_diff(
                old.splitlines(True), new.splitlines(True), f"{t} (live)", f"{t} (new)", n=0)))

    if args.validate:
        print("\nValidating by rendering old vs new with action=parse ...")
        bad = 0
        for t in pending:
            _, old, new, _, _ = rows[t]
            a, b = wiki.render(t, old), wiki.render(t, new)
            time.sleep(READ_DELAY)
            if a == b:
                print(f"  identical  {t}")
            else:
                bad += 1
                i = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]), min(len(a), len(b)))
                print(f"  DIFFERS    {t}\n      old: ...{a[max(0, i - 60):i + 80]}\n      new: ...{b[max(0, i - 60):i + 80]}")
        print(f"\nValidation: {len(pending) - bad} identical, {bad} differ.")
        if bad:
            print("Do not --run until every difference is explained. A heading glued to `}}` "
                  "(`}}== Giới thiệu ==`) shows as plain text today, so it differs by design.")

    if not args.run:
        delay = max(5.0, args.delay)
        print(f"\nDRY RUN - nothing was written.")
        print(f"Re-run with --run to edit {len(pending)} page(s) at {delay:.0f}s apart "
              f"(about {len(pending) * delay / 60:.0f} min).")
        return
    if not pending:
        print("\nNothing to do.")
        return

    delay = max(5.0, args.delay)
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
            log.writerow(["title", "group", "old_revid", "new_revid", "result"])
        for i, title in enumerate(pending, 1):
            print(f"[{i}/{len(pending)}] {title}")
            fresh = wiki.fetch([title]).get(title)          # re-read right before writing
            if not fresh:
                failed += 1
                print("    FAILED - page vanished")
                continue
            revid, text = fresh
            new, status, detail = reformat(text)
            if status != "change":
                skipped += 1
                print(f"    skipped - now {status} {detail}")
            else:
                success, info, newrev = wiki.replace(title, new, revid)
                if success is True:
                    ok += 1
                    print(f"    edited (rev {newrev})")
                    log.writerow([title, members[title], revid, newrev, "edited"])
                elif success == "skipped":
                    skipped += 1
                    print(f"    skipped - {info}")
                else:
                    failed += 1
                    print(f"    FAILED - {info}")
                    log.writerow([title, members[title], revid, "", "FAILED " + info])
                fh.flush()
            if i < len(pending):
                time.sleep(delay)
    print(f"\nDone. edited={ok} skipped={skipped} failed={failed}")
    print(f"Log: {args.log}")
    if failed:
        print("Re-running is safe: compliant pages are skipped.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Re-running is safe - pages already reformatted are skipped.")
        sys.exit(1)
