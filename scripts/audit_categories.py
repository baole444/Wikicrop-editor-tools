#!/usr/bin/env python3
"""
Post-run audit of the category scheme made by create_categories.py. Read-only.

Checks the LIVE wiki against what create_categories.py intended:
  * all 41 pages exist, and nothing unexpected was created
  * each sits in the intended parent with the intended sortkey
  * the root lists its children in the intended order (sortkeys working)
  * every page carries the intended creation summary
"""

import json
import argparse
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from create_categories import (API, UA, build_pages, GROUPS, PAGE_TYPES,
                               MAINTENANCE, CROPS)

DELAY = 0.5


def api(**params):
    params.update(format="json", formatversion="2")
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    pages = build_pages()
    intended = {t: (b, s) for t, b, s in pages}
    problems = []

    # -- 1. existence + no strays ------------------------------------------
    live = set()
    cont = {}
    while True:
        r = api(action="query", list="allpages", apnamespace=14, aplimit=500, **cont)
        live |= {p["title"] for p in r["query"]["allpages"]}
        if "continue" not in r:
            break
        cont = r["continue"]

    missing = sorted(set(intended) - live)
    extra = sorted(live - set(intended))
    print(f"category pages live: {len(live)}  (intended {len(intended)})")
    for m in missing:
        problems.append(f"MISSING: {m}")
    for e in extra:
        problems.append(f"UNEXPECTED: {e}")

    # -- 2. parent + sortkey per page --------------------------------------
    want = {}
    for name, num, _ in GROUPS:
        want[f"Thể loại:{name}"] = ("Thể loại:Cây trồng", f"{num:02d}")
    for name, _, sk, _, _ in PAGE_TYPES:
        want[f"Thể loại:{name}"] = ("Thể loại:Cây trồng", sk)
    for name, sk, _, _ in MAINTENANCE:
        want[f"Thể loại:{name}"] = ("Thể loại:Bảo trì", sk)
    for cat, _, _, _, group, _ in CROPS:
        want[f"Thể loại:{cat}"] = (f"Thể loại:{group}", "")

    titles = sorted(want)
    for i in range(0, len(titles), 25):
        batch = titles[i:i + 25]
        r = api(action="query", prop="categories", clprop="sortkey",
                cllimit=500, titles="|".join(batch))
        for p in r["query"]["pages"]:
            if p.get("missing"):
                continue
            cats = p.get("categories", [])
            wcat, wkey = want[p["title"]]
            if len(cats) != 1:
                problems.append(f"{p['title']}: expected 1 parent, got {len(cats)}")
                continue
            got = cats[0]
            gotkey = got.get("sortkeyprefix", "")
            if got["title"] != wcat:
                problems.append(f"{p['title']}: parent {got['title']!r} != {wcat!r}")
            if gotkey != wkey:
                problems.append(f"{p['title']}: sortkey {gotkey!r} != {wkey!r}")
        time.sleep(DELAY)

    # -- 3. root listing order --------------------------------------------
    print("\nThể loại:Cây trồng lists, in order:")
    r = api(action="query", list="categorymembers",
            cmtitle="Thể loại:Cây trồng", cmlimit=500, cmprop="title|sortkeyprefix")
    members = r["query"]["categorymembers"]
    for m in members:
        print(f"   {m['sortkeyprefix']:<4} {m['title']}")
    order = [m["sortkeyprefix"] for m in members]
    if order != sorted(order):
        problems.append(f"root children not in sortkey order: {order}")
    expect_root = [f"{n:02d}" for _, n, _ in GROUPS] + [p[2] for p in PAGE_TYPES]
    if sorted(order) != sorted(expect_root):
        problems.append(f"root children {order} != expected {expect_root}")

    print("\nThể loại:Bảo trì lists:")
    r = api(action="query", list="categorymembers",
            cmtitle="Thể loại:Bảo trì", cmlimit=500, cmprop="title|sortkeyprefix")
    for m in r["query"]["categorymembers"]:
        print(f"   {m['sortkeyprefix']:<4} {m['title']}")

    # -- 4. crop children per group ---------------------------------------
    print("\ncrop categories per group:")
    for name, num, _ in GROUPS:
        r = api(action="query", list="categorymembers",
                cmtitle=f"Thể loại:{name}", cmlimit=500, cmprop="title")
        kids = [m["title"] for m in r["query"]["categorymembers"]]
        expect = sorted(f"Thể loại:{c[0]}" for c in CROPS if c[4] == name)
        flag = "" if sorted(kids) == expect else "   <-- MISMATCH"
        print(f"   {num:02d} {name:<28} {len(kids)} crop(s){flag}")
        if sorted(kids) != expect:
            problems.append(f"group {name}: children {sorted(kids)} != {expect}")
        time.sleep(DELAY)

    # -- 5. creation summaries --------------------------------------------
    # rvlimit cannot be combined with multiple titles, so one request per page
    bad_summary = 0
    for t in titles:
        r = api(action="query", prop="revisions", rvprop="comment|user",
                rvdir="newer", rvlimit=1, titles=t)
        p = r["query"]["pages"][0]
        if p.get("missing"):
            continue
        rev = p.get("revisions", [{}])[0]
        got = rev.get("comment", "")
        exp = intended[p["title"]][1]
        if got != exp:
            bad_summary += 1
            problems.append(f"{p['title']}: summary {got!r} != {exp!r}")
        time.sleep(DELAY)
    print(f"\ncreation summaries matching intent: {len(titles) - bad_summary}/{len(titles)}")

    print()
    if problems:
        print(f"{len(problems)} PROBLEM(S):")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    print("AUDIT PASSED - scheme is live and correct.")


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    main()
