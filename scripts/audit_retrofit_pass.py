#!/usr/bin/env python3
"""
Post-run audit of retrofit_pass.py. Read-only.

Checks the LIVE wiki against the run log and the revisions the bot replaced:
  * every page in groups 7-13 has nothing left to fix (declared skips aside)
  * for every logged edit, applying the logged fixes to the replaced revision
    gives exactly the live text, so nothing else was added or lost
  * infobox parameters: none removed, and only the taxonomy rows, TenLoai and
    CacLoai changed (any other value only by the dash fix)
  * every category tag is live, with the intended sortkey
  * no logged edit failed

    python audit_retrofit_pass.py [run-log.csv]
"""

import csv
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from create_categories import READ_DELAY  # noqa: E402
from reformat_infoboxes import FETCH_BATCH, TARGET, parse_call  # noqa: E402
from retrofit_pass import (DEFAULT_LOG, FIX_NAMES, SHEET, TAX, Bot, apply, restyle,  # noqa: E402
                           build_contexts, expected_categories, read_catalogue)

ALLOWED = set(TAX) | {"TenLoai", "CacLoai"}


def params(text):
    m = TARGET.search(text)
    return dict(parse_call(text, m.start())["params"]) if m else {}


def main():
    log = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_LOG
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    if not log.exists():
        raise SystemExit(f"No run log at {log}")
    rows = list(csv.DictReader(open(log, encoding="utf-8")))
    problems = []

    wiki = Bot()
    pages, _ = build_contexts(wiki, read_catalogue(SHEET), None)
    print(f"pages in scope: {len(pages)}")

    # -- 1. nothing left to fix ---------------------------------------------
    for t, (_, text, ctx) in pages.items():
        _, applied, _ = apply(text, ctx, FIX_NAMES)
        if applied:
            problems.append(f"STILL TO FIX ({', '.join(applied)}): {t}")

    # -- 2. live text is exactly old revision + logged fixes ------------------
    for r in rows:
        if r["result"].startswith("FAILED"):
            problems.append(f"LOGGED FAILURE: {r['title']} ({r['result']})")
    edited = [r for r in rows if r["result"] == "edited"]
    for i in range(0, len(edited), FETCH_BATCH):
        batch = edited[i:i + FETCH_BATCH]
        res = wiki._call({"action": "query", "prop": "revisions", "rvprop": "content|ids",
                          "rvslots": "main", "revids": "|".join(r["old_revid"] for r in batch)})
        before = {p["title"]: p["revisions"][0]["slots"]["main"]["content"]
                  for p in res["query"]["pages"] if "revisions" in p}
        for r in batch:
            t = r["title"]
            if t not in before or t not in pages:
                problems.append(f"CANNOT COMPARE: {t}")
                continue
            live, ctx = pages[t][1], pages[t][2]
            if pages[t][0] != int(r["new_revid"]):
                problems.append(f"EDITED SINCE THE RUN (rev {r['new_revid']} -> {pages[t][0]}): {t}")
            intended, _, _ = apply(before[t], ctx, r["fixes"].split())
            # MediaWiki trims trailing whitespace on save, so compare without it
            if intended.rstrip() != live.rstrip() and pages[t][0] == int(r["new_revid"]):
                problems.append(f"LIVE TEXT DIFFERS from rev {r['old_revid']} + {r['fixes']}: {t}")
            old_p, new_p = params(before[t]), params(live)
            gone = sorted(set(old_p) - set(new_p))
            # outside ALLOWED, the only change a value may carry is the dash fix (9.11)
            changed = sorted(k for k in old_p if k in new_p and old_p[k] != new_p[k]
                             and k not in ALLOWED and new_p[k] != restyle(old_p[k]))
            if gone or changed:
                problems.append(f"INFOBOX: removed {gone} changed {changed}: {t}")
        time.sleep(READ_DELAY)
    print(f"logged edits compared: {len(edited)}")

    # -- 3. categories live, with the intended sortkeys -----------------------
    titles = sorted(pages)
    for i in range(0, len(titles), FETCH_BATCH):
        res = wiki._call({"action": "query", "prop": "categories", "clprop": "sortkey",
                          "cllimit": "max", "titles": "|".join(titles[i:i + FETCH_BATCH])})
        for p in res["query"]["pages"]:
            got = {(c["title"].split(":", 1)[1], c.get("sortkeyprefix", "")) for c in p.get("categories", [])}
            want = set(expected_categories(pages[p["title"]][2]))
            if not want <= got:
                problems.append(f"CATEGORIES missing {sorted(want - got)}: {p['title']}")
        time.sleep(READ_DELAY)

    print()
    if problems:
        print(f"{len(problems)} PROBLEM(S):")
        for p in problems:
            print("  " + p)
        sys.exit(1)
    print("PASSED - every in-scope page is fixed, each edit is exactly the logged fixes, "
          "and every category is live.")


if __name__ == "__main__":
    _ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _ap.add_argument("log", nargs="?", help=f"run log CSV (default: {DEFAULT_LOG})")
    _ap.parse_args()
    main()
