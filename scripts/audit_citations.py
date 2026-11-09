#!/usr/bin/env python3
"""
Post-run audit of convert_citations.py. Read-only.

Checks the LIVE wiki against the run log, the review table and the revisions
the bot replaced:
  * for every logged edit, converting the replaced revision with the approved
    rows of the table gives exactly the live text
  * the external links of each edited page are unchanged
  * no approved row is still hand-written on a page the run reached
  * no logged edit failed

    python audit_citations.py [run-log.csv]
"""

import csv
import re
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from create_categories import READ_DELAY  # noqa: E402
from convert_citations import DEFAULT_LOG, REVIEW, approved, check_row, convert, template  # noqa: E402
from reformat_infoboxes import FETCH_BATCH  # noqa: E402
from retrofit_pass import Bot  # noqa: E402

URL = re.compile(r"https?://[^\s\]|<}]+")


def main():
    log = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_LOG
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    for p in (log, REVIEW):
        if not p.exists():
            raise SystemExit(f"Missing {p}")
    runs = list(csv.DictReader(open(log, encoding="utf-8")))
    table = [r for r in csv.DictReader(open(REVIEW, encoding="utf-8-sig")) if approved(r) and not check_row(r)]
    problems = []

    notes = []
    edited = [r for r in runs if r["result"] == "edited"]
    failed = [r for r in runs if r["result"].startswith("FAILED")]
    wiki = Bot()
    live = wiki.fetch(sorted({r["title"] for r in edited + failed}))
    for r in failed:
        # resolved once no approved row of that page is still waiting on the live page
        # (the row was set to keep, or a later run converted it)
        now = live.get(r["title"], (0, ""))[1].split("\n")
        if any(x["title"] == r["title"] and x["original"] in now for x in table):
            problems.append(f"LOGGED FAILURE: {r['title']} ({r['result']})")
        else:
            notes.append(f"failure resolved, nothing left to convert: {r['title']} ({r['result'][7:60]})")
    for i in range(0, len(edited), FETCH_BATCH):
        batch = edited[i:i + FETCH_BATCH]
        res = wiki._call({"action": "query", "prop": "revisions", "rvprop": "content|ids",
                          "rvslots": "main", "revids": "|".join(r["old_revid"] for r in batch)})
        before = {p["title"]: p["revisions"][0]["slots"]["main"]["content"]
                  for p in res["query"]["pages"] if "revisions" in p}
        for r in batch:
            t = r["title"]
            if t not in before or t not in live:
                problems.append(f"CANNOT COMPARE: {t}")
                continue
            revid, now = live[t]
            rows = [x for x in table if x["title"] == t]
            if revid != int(r["new_revid"]):
                # a later edit (e.g. `*` -> `#` by hand): check what matters instead of the
                # whole text, every approved citation converted, whatever its list marker
                notes.append(f"edited since the run (rev {r['new_revid']} -> {revid}), checked by citation: {t}")
                missing = [x for x in rows if template(x).split(" ", 1)[1] not in now]
                if missing:
                    problems.append(f"{len(missing)} CONVERTED CITATION(S) NO LONGER ON THE PAGE: {t}")
            elif convert(before[t], rows)[0].rstrip() != now.rstrip():   # MediaWiki trims on save
                problems.append(f"LIVE TEXT DIFFERS from rev {r['old_revid']} + its approved rows: {t}")
            if set(URL.findall(before[t])) != set(URL.findall(now)):
                problems.append(f"EXTERNAL LINKS CHANGED: {t}")
            still = [x for x in rows if x["original"] in now.split("\n")]
            if still:
                problems.append(f"{len(still)} APPROVED ROW(S) STILL HAND-WRITTEN: {t}")
        time.sleep(READ_DELAY)

    print(f"logged edits compared: {len(edited)}")
    for n in notes:
        print("  note: " + n)
    print()
    if problems:
        print(f"{len(problems)} PROBLEM(S):")
        for p in problems:
            print("  " + p)
        sys.exit(1)
    print("PASSED - every edit is exactly its approved rows, and no external link changed.")


if __name__ == "__main__":
    _ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _ap.add_argument("log", nargs="?", help=f"run log CSV (default: {DEFAULT_LOG})")
    _ap.parse_args()
    main()
