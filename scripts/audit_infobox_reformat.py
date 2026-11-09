#!/usr/bin/env python3
"""
Post-run audit of reformat_infoboxes.py. Read-only.

Checks the LIVE wiki against the run log, not against the run's own output:
  * every page in groups 7-13 now has compliant infoboxes (nothing left to change)
  * for every logged edit, the infobox arguments on the live page equal those
    of the revision the bot replaced (nothing lost, reordered or altered)
  * no logged edit failed
"""

import csv
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from reformat_infoboxes import (DEFAULT_LOG, FETCH_BATCH, TARGET, Bot, parse_call,  # noqa: E402
                                reformat)
from create_categories import READ_DELAY  # noqa: E402


def calls(text):
    return [parse_call(text, m.start())["params"] for m in TARGET.finditer(text)]


def main():
    log = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_LOG
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    if not log.exists():
        raise SystemExit(f"No run log at {log}")
    rows = list(csv.DictReader(open(log, encoding="utf-8")))
    problems = []

    wiki = Bot()
    members = wiki.titles_in_scope()
    live = wiki.fetch(sorted(members))
    print(f"pages in scope: {len(members)}, read live: {len(live)}")

    # -- 1. nothing left to reformat ----------------------------------------
    for title in sorted(members):
        if title not in live:
            problems.append(f"MISSING: {title}")
            continue
        _, status, detail = reformat(live[title][1])
        if status in ("change", "skip"):
            problems.append(f"NOT COMPLIANT ({status} {detail}): {title}")

    # -- 2. arguments unchanged versus the replaced revision -----------------
    edited = [r for r in rows if r["result"] == "edited"]
    for r in rows:
        if r["result"].startswith("FAILED"):
            problems.append(f"LOGGED FAILURE: {r['title']} ({r['result']})")
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
            elif calls(before[t]) != calls(live[t][1]):
                problems.append(f"ARGUMENTS DIFFER from rev {r['old_revid']}: {t}")
        time.sleep(READ_DELAY)
    print(f"logged edits compared: {len(edited)}")

    print()
    if problems:
        print(f"{len(problems)} PROBLEM(S):")
        for p in problems:
            print("  " + p)
        sys.exit(1)
    print("PASSED - every in-scope infobox is one-per-line and carries the same arguments as before.")


if __name__ == "__main__":
    _ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _ap.add_argument("log", nargs="?", help=f"run log CSV (default: {DEFAULT_LOG})")
    _ap.parse_args()
    main()
