#!/usr/bin/env python3
"""
Create the Wikicrop category scheme.

Creates the category pages defined in categories.toml (WIKICROP_CATEGORIES): two
roots, the 13 crop groups of the Main Page, the page-type and maintenance
categories, and one category per crop. Edit that file to change the scheme. The scheme and its naming rule are section 7
of the writing standard, `Wikicrop:Tiêu chuẩn viết bài` on the wiki; section
numbers in this folder's tools all refer to that page.

Design notes
------------
* Standard library only. No pywikibot, no requests, no pip install.
* DRY RUN BY DEFAULT. Writing requires --run, explicitly.
* Deliberately slow: a fixed delay between every write (default 30s), maxlag
  on every request, and exponential backoff on any rate limit. The job is not
  time-critical; being a good citizen on a small shared server is worth more.
* `createonly=1` on every edit, so an existing page is NEVER overwritten even
  if the pre-flight existence check goes stale. This script cannot destroy
  content - the worst case is that it does nothing.
* Credentials are never stored or hard-coded. They are prompted for at run
  time via getpass and held only in memory.

Usage
-----
    python create_categories.py                 # dry run, no login needed
    python create_categories.py --show-bodies   # dry run + full page text
    python create_categories.py --run           # create pages (prompts login)
    python create_categories.py --run --delay 60

Login
-----
Use a BotPassword, not your main account password. At Special:BotPasswords,
tick exactly ONE grant:

    "Tạo, sửa đổi, và di chuyển trang"  =  createeditmovepage   <- this one

It confers both `edit` and `createpage`, so the smaller "Sửa đổi các trang đã
tồn tại" (editpage) grant is redundant.

Do NOT tick "Truy cập tốc độ cao (bot)" (highvolume). It confers bot,
noratelimit and apihighlimits, none of which this account holds - a grant can
only restrict, never add (see Special:ListGrants). And if it ever did apply,
`bot` hides edits from RecentChanges by default, defeating the per-page edit
summaries this script writes so the run can be audited.

You get a username like `ExampleUser@wikicrop-bot` plus a generated password;
those are what this script prompts for.
"""

import argparse
import getpass
import http.cookiejar
import json
import socket
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request

from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import project_config as cfg  # noqa: E402

API = cfg.API

# Try IPv4 addresses first. The wiki sits behind Cloudflare, which also
# advertises IPv6; on a network that cannot route IPv6, every new connection
# waited ~21s per IPv6 address before falling back (42s per request, seen
# 2026-10-27). urllib opens a connection per request, so this applies to every
# script importing this module.
_getaddrinfo = socket.getaddrinfo


def _ipv4_first(*args, **kwargs):
    return sorted(_getaddrinfo(*args, **kwargs), key=lambda ai: ai[0] != socket.AF_INET)


socket.getaddrinfo = _ipv4_first


def error_text(err):
    """The whole of an API error, never cut short: code, message, and the
    blacklist match or AbuseFilter that refused the edit, when there is one."""
    out = f"{err.get('code', '?')}: {err.get('info', '')}"
    matches = (err.get("spamblacklist") or {}).get("matches")
    if matches:
        out += f" [blacklisted: {', '.join(matches)}]"
    if err.get("abusefilter"):
        out += f" [AbuseFilter: {err['abusefilter'].get('description', err['abusefilter'])}]"
    return out
UA = cfg.user_agent("WikicropCategoryBot", "category scheme")

# Server-courtesy settings -----------------------------------------------
MAXLAG = 5          # seconds of replication lag we tolerate before backing off
READ_DELAY = 1.0    # between read-only requests
DEFAULT_WRITE_DELAY = 30.0
MAX_RETRIES = 5


# ---------------------------------------------------------------- page data
# The scheme lives in categories.toml (WIKICROP_CATEGORIES). It is read once, at import, into the
# tuples below, which validate_categories.py and audit_categories.py import as well.

def load_scheme(path=None):
    """Read the category scheme. Raises SystemExit naming the problem if the file is incomplete."""
    path = Path(path or cfg.CATEGORIES)
    try:
        with open(path, "rb") as fh:
            s = tomllib.load(fh)
    except FileNotFoundError:
        raise SystemExit(f"Category scheme not found: {path} (set WIKICROP_CATEGORIES)")
    except tomllib.TOMLDecodeError as e:
        raise SystemExit(f"{path}: {e}")
    try:
        groups = [(g["name"], int(g["num"]), g["en"]) for g in s["group"]]
        page_types = [(x["name"], x["desc"], x["sortkey"], x["note"], x["what"]) for x in s["page_type"]]
        maintenance = [(x["name"], x["sortkey"], x["desc"], x["what"]) for x in s["maintenance"]]
        crops = [(c["category"], c["article"], c["display"], c["binomial"], c["group"], c.get("note"))
                 for c in s["crop"]]
        for key in ("summary", "root", "maintenance_root", "templates"):
            s[key]
    except KeyError as e:
        raise SystemExit(f"{path}: missing {e}")
    names = {g[0] for g in groups}
    bad = [c[0] for c in crops if c[4] not in names]
    if bad:
        raise SystemExit(f"{path}: crops with a group that is not listed: {', '.join(bad)}")
    return s, groups, page_types, maintenance, crops


SCHEME, GROUPS, PAGE_TYPES, MAINTENANCE, CROPS = load_scheme()
SUMMARY = SCHEME["summary"]


def build_pages():
    """Return [(title, wikitext, summary)] in creation order: parents first."""
    pages = []
    tpl = SCHEME["templates"]

    def add(title, body, what):
        pages.append((title, body, SUMMARY.format(what=what)))

    for root in (SCHEME["root"], SCHEME["maintenance_root"]):
        add(root["title"], root["body"], root["what"])
    for name, num, en in GROUPS:
        add(f"Thể loại:{name}", tpl["group"].format(name=name, num=num, en=en),
            tpl["group_what"].format(name=name, num=num, en=en))
    for name, desc, sortkey, note, what in PAGE_TYPES:
        add(f"Thể loại:{name}", tpl["page_type"].format(name=name, desc=desc, note=note, sortkey=sortkey), what)
    for name, sortkey, desc, what in MAINTENANCE:
        add(f"Thể loại:{name}", tpl["maintenance"].format(name=name, desc=desc, sortkey=sortkey), what)
    for cat, article, display, binomial, group, note in CROPS:
        add(f"Thể loại:{cat}",
            tpl["crop"].format(article=article, display=display, binomial=binomial, group=group,
                               note_block=f"{note}\n\n" if note else ""),
            binomial)
    return pages


# ------------------------------------------------------------------ api i/o

class Wiki:
    def __init__(self):
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj))
        self.opener.addheaders = [("User-Agent", UA)]
        self.csrf = None

    def _call(self, params, post=False):
        """One API call, with maxlag / ratelimit backoff."""
        params = dict(params, format="json", formatversion="2")
        for attempt in range(1, MAX_RETRIES + 1):
            data = urllib.parse.urlencode(params).encode("utf-8")
            try:
                if post:
                    resp = self.opener.open(API, data, timeout=60)
                else:
                    resp = self.opener.open(API + "?" + data.decode("utf-8"), timeout=60)
                raw = resp.read().decode("utf-8")
                retry_after = resp.headers.get("Retry-After")
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8", "replace")
                retry_after = e.headers.get("Retry-After")
            except urllib.error.URLError as e:
                wait = min(60, 5 * attempt)
                print(f"    network error ({e.reason}); waiting {wait}s "
                      f"[attempt {attempt}/{MAX_RETRIES}]")
                time.sleep(wait)
                continue

            try:
                out = json.loads(raw)
            except json.JSONDecodeError:
                print(f"    non-JSON response; waiting 10s [attempt {attempt}]")
                time.sleep(10)
                continue

            err = out.get("error", {})
            code = err.get("code")
            if code in ("maxlag", "readonly"):
                wait = int(retry_after or 10)
                print(f"    server busy ({code}: {err.get('info','')[:60]}); "
                      f"waiting {wait}s")
                time.sleep(wait)
                continue
            if code == "ratelimited":
                wait = min(300, 30 * attempt)
                print(f"    rate limited; backing off {wait}s "
                      f"[attempt {attempt}/{MAX_RETRIES}]")
                time.sleep(wait)
                continue
            return out

        raise RuntimeError(f"gave up after {MAX_RETRIES} attempts: {params.get('action')}")

    def edit(self, params):
        """POST an action=edit. When ConfirmEdit asks for a CAPTCHA (an edit that
        adds an external link, for an account without skipcaptcha), show it and
        let the operator answer at the console, then resend. The script never
        answers it itself. An empty answer skips the page. Returns the reply."""
        for _ in range(3):
            r = self._call(params, post=True)
            cap = r.get("edit", {}).get("captcha")
            if not cap:
                return r
            if "question" in cap and cap.get("mime", "text/plain") == "text/plain":
                print(f"    CAPTCHA ({cap.get('type', '?')}): {cap['question']}")
            elif cap.get("url"):
                print(f"    CAPTCHA image: {API.rsplit('/', 1)[0]}{cap['url']}")
            else:
                print(f"    CAPTCHA of type {cap.get('type', '?')} cannot be answered here")
                return r
            try:
                answer = input("    your answer (empty = skip this page): ").strip()
            except EOFError:                        # no console attached: skip, do not crash
                answer = ""
            if not answer:
                return r
            params = dict(params, captchaid=cap["id"], captchaword=answer)
        return r

    def token(self, kind):
        r = self._call({"action": "query", "meta": "tokens", "type": kind})
        return r["query"]["tokens"][kind + "token"]

    def login(self, user, password):
        t = self.token("login")
        r = self._call({"action": "login", "lgname": user,
                        "lgpassword": password, "lgtoken": t}, post=True)
        result = r.get("login", {}).get("result")
        if result != "Success":
            reason = r.get("login", {}).get("reason", result)
            raise SystemExit(f"Login failed: {reason}")
        self.csrf = self.token("csrf")
        return r["login"].get("lgusername", user)

    def existing(self, titles):
        """Return the subset of `titles` that already exist."""
        found = set()
        for i in range(0, len(titles), 25):
            batch = titles[i:i + 25]
            r = self._call({"action": "query", "prop": "info",
                            "titles": "|".join(batch)})
            for p in r.get("query", {}).get("pages", []):
                if not p.get("missing"):
                    found.add(p["title"])
            time.sleep(READ_DELAY)
        return found

    def create(self, title, text, summary):
        r = self._call({
            "action": "edit", "title": title, "text": text,
            "summary": summary, "token": self.csrf,
            "createonly": "1",      # never overwrite an existing page
            "assert": "user",       # fail fast if the session died
            "maxlag": str(MAXLAG),
        }, post=True)
        if "error" in r:
            code = r["error"].get("code", "?")
            if code == "articleexists":
                # createonly=1 did its job: someone made the page since our check
                return "skipped", "already exists"
            return False, error_text(r["error"])
        return True, r.get("edit", {}).get("result", "?")


# --------------------------------------------------------------------- main

def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Create the Wikicrop category scheme.")
    ap.add_argument("--run", action="store_true",
                    help="actually create pages (default is a dry run)")
    ap.add_argument("--delay", type=float, default=DEFAULT_WRITE_DELAY,
                    help=f"seconds between writes (default {DEFAULT_WRITE_DELAY:.0f}, minimum 5)")
    ap.add_argument("--show-bodies", action="store_true",
                    help="print the full wikitext of every page")
    ap.add_argument("--limit", type=int, default=0,
                    help="only process the first N pending pages (useful for a cautious first run)")
    args = ap.parse_args()

    delay = max(5.0, args.delay)
    pages = build_pages()
    titles = [t for t, _, _ in pages]

    if len(set(titles)) != len(titles):
        raise SystemExit("BUG: duplicate titles in page list")

    print(f"Category scheme: {len(pages)} pages defined\n")

    wiki = Wiki()
    print("Checking which pages already exist...")
    exists = wiki.existing(titles)
    pending = [p for p in pages if p[0] not in exists]

    print(f"  already present : {len(exists)}")
    print(f"  to create       : {len(pending)}\n")

    for t in titles:
        mark = "skip (exists)" if t in exists else "CREATE"
        print(f"  [{mark:>13}] {t}")

    if args.show_bodies:
        print("\n" + "=" * 70)
        for t, b, s in pending:
            print(f"\n--- {t} ---\nsummary: {s}\n\n{b}")
        print("\n" + "=" * 70)

    if args.limit:
        pending = pending[:args.limit]
        print(f"\n--limit {args.limit}: restricting this run to {len(pending)} page(s)")

    if not args.run:
        est = len(pending) * delay / 60
        print(f"\nDRY RUN - nothing was written.")
        print(f"Re-run with --run to create {len(pending)} page(s) "
              f"at {delay:.0f}s apart (about {est:.0f} min).")
        return

    if not pending:
        print("\nNothing to do.")
        return

    print(f"\nAbout to create {len(pending)} pages, {delay:.0f}s apart "
          f"(about {len(pending) * delay / 60:.0f} min).")
    print("Use a BotPassword from Special:BotPasswords, not your account password.")
    user = input(cfg.bot_login_hint()).strip()
    password = getpass.getpass("Bot password (input hidden): ")
    if not user or not password:
        raise SystemExit("No credentials given; aborting.")

    who = wiki.login(user, password)
    del password
    print(f"Logged in as {who}\n")

    ok = failed = skipped = 0
    for i, (title, body, summary) in enumerate(pending, 1):
        print(f"[{i}/{len(pending)}] {title}")
        success, detail = wiki.create(title, body, summary)
        if success == "skipped":
            skipped += 1
            print(f"    skipped - {detail}")
        elif success:
            ok += 1
            print(f"    created ({detail})")
        else:
            failed += 1
            print(f"    FAILED - {detail}")
        if i < len(pending):
            time.sleep(delay)

    print(f"\nDone. created={ok} skipped={skipped} failed={failed}")
    if failed:
        print("Re-running is safe: createonly=1 means existing pages are skipped.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Re-running is safe - pages already created are skipped.")
        sys.exit(1)
