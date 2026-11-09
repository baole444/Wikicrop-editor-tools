#!/usr/bin/env python3
"""
Pre-flight check for create_categories.py.

Renders every page body through the live parser (action=parse, read-only - it
creates and changes nothing) and asserts:

  * no parser errors in the output
  * the page lands in exactly one parent category, with the intended sortkey
  * no red links (a link to a page that does not exist)
  * no unexpanded wikitext left visible in the rendered body

Run this before create_categories.py --run.
"""

import json
import re
import argparse
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from create_categories import API, UA, build_pages, GROUPS, PAGE_TYPES, MAINTENANCE, CROPS

DELAY = 1.0

# title -> expected (parent category, sortkey or None for default)
EXPECTED = {"Thể loại:Cây trồng": None, "Thể loại:Bảo trì": None}
for name, num, _ in GROUPS:
    EXPECTED[f"Thể loại:{name}"] = ("Cây_trồng", f"{num:02d}")
for name, _, sortkey, _, _ in PAGE_TYPES:
    EXPECTED[f"Thể loại:{name}"] = ("Cây_trồng", sortkey)
for name, sortkey, _, _ in MAINTENANCE:
    EXPECTED[f"Thể loại:{name}"] = ("Bảo_trì", sortkey)
for cat, _, _, _, group, _ in CROPS:
    EXPECTED[f"Thể loại:{cat}"] = (group.replace(" ", "_"), "")


def parse(title, text):
    data = urllib.parse.urlencode({
        "action": "parse", "format": "json", "formatversion": "2",
        "contentmodel": "wikitext", "prop": "text|categories|links",
        "title": title, "text": text,
    }).encode("utf-8")
    req = urllib.request.Request(API, data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))["parse"]


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    pages = build_pages()
    problems = []
    print(f"Validating {len(pages)} page bodies against the live parser...\n")

    for title, body, summary in pages:
        p = parse(title, body)
        html = re.sub(r"(?s)<!--.*?-->", "", p["text"])
        issues = []

        # MediaWiki truncates edit summaries past 500 characters
        if len(summary.encode("utf-8")) > 500:
            issues.append(f"summary too long ({len(summary.encode('utf-8'))} bytes)")

        if 'class="error"' in html or "Template loop" in html:
            issues.append("parser error in output")

        # red links: <a ... class="new" ... title="X (page does not exist)">
        red = re.findall(r'class="new"[^>]*title="([^"(]+)', html)
        if red:
            issues.append("red link(s): " + ", ".join(sorted(set(r.strip() for r in red))))

        # leftover raw wikitext that should have been consumed
        for marker in ("[[", "]]", "'''", "{{"):
            if marker in html:
                issues.append(f"unrendered {marker!r} in body")

        cats = p.get("categories", [])
        want = EXPECTED.get(title)
        if want is None:
            if cats:
                issues.append(f"expected no parent, got {[c['category'] for c in cats]}")
        else:
            wcat, wkey = want
            if len(cats) != 1:
                issues.append(f"expected exactly 1 parent, got {len(cats)}")
            else:
                got_cat = cats[0]["category"]
                got_key = cats[0].get("sortkey", "")
                if got_cat != wcat:
                    issues.append(f"parent is {got_cat!r}, expected {wcat!r}")
                if got_key != wkey:
                    issues.append(f"sortkey is {got_key!r}, expected {wkey!r}")

        status = "ok" if not issues else "**"
        parent = cats[0]["category"] + (f"|{cats[0].get('sortkey','')}"
                                        if cats[0].get("sortkey") else "") if cats else "(root)"
        print(f"  [{status}] {title:<34} -> {parent}")
        print(f"         summary: {summary}")
        for i in issues:
            print(f"         - {i}")
            problems.append((title, i))
        time.sleep(DELAY)

    print()
    if problems:
        print(f"FAILED: {len(problems)} issue(s) across "
              f"{len(set(t for t, _ in problems))} page(s). Do not run the creator yet.")
        sys.exit(1)
    print(f"PASSED: all {len(pages)} bodies render cleanly, "
          "parents and sortkeys as intended.")


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    main()
