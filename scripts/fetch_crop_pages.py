#!/usr/bin/env python3
"""
Fetch every page of one crop from the live wiki to <WIKICROP_CROPS>/<BTnn>/<Crop>/ (default Crops/). Read-only.

A crop's pages are the members of `Thể loại:<Crop>` plus every main-namespace
title that is the crop name, starts with "<crop> ", or is its agronomy or list
page (`Kỹ thuật trồng và chăm sóc <crop>`, `Danh sách giống <crop>`), all
matched case-insensitively. Titles that merely mention the crop elsewhere are
listed, not fetched. Redirects are listed, not fetched.

Each page is saved as `<title>.txt`. A local file whose name matches the title
case-insensitively counts as that page's copy, so a renamed local draft
(`Xoài cát Chu.txt` for `Xoài Cát Chu`) is found, not duplicated. Local files
are never overwritten unless --overwrite is given: a copy that differs from
live is reported, since it is usually work in progress.

`_revisions.tsv` in the crop folder records the live revision each page was
compared against (title, revid, local file), the base for a later upload.

    python scripts/fetch_crop_pages.py Xoài
    python scripts/fetch_crop_pages.py "Thanh long" --overwrite
    python scripts/fetch_crop_pages.py Lúa --group BT01
"""

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from create_categories import READ_DELAY  # noqa: E402
from reformat_infoboxes import group_of  # noqa: E402
from retrofit_pass import Bot  # noqa: E402
import project_config as cfg  # noqa: E402

UA = cfg.user_agent("WikicropFetchBot", "read-only page fetch")
ROOT = cfg.CROPS
UNSAFE = re.compile(r'[\\/:*?"<>|]')    # not allowed in Windows file names


def listing(bot, params, key):
    out, cont = [], {}
    while True:
        r = bot._call({"action": "query", **params, **cont})
        out += r["query"][key]
        if "continue" not in r:
            return out
        cont = r["continue"]
        time.sleep(READ_DELAY)


def discover(bot, crop):
    """(titles to fetch, redirects skipped, titles that only mention the crop)."""
    low = crop.lower()
    own = {low, f"kỹ thuật trồng và chăm sóc {low}", f"danh sách giống {low}"}
    titles, _ = bot.all_titles()
    redirects = {p["title"] for p in listing(
        bot, {"list": "allpages", "apnamespace": 0, "aplimit": 500,
              "apfilterredir": "redirects"}, "allpages")}
    cat = "Thể loại:" + crop[0].upper() + crop[1:]
    members = {p["title"] for p in listing(
        bot, {"list": "categorymembers", "cmtitle": cat, "cmnamespace": 0,
              "cmlimit": 500}, "categorymembers")}

    found = set(members)
    mention = set()
    for t in titles:
        t_low = t.lower()
        if t_low in own or t_low.startswith(low + " "):
            found.add(t)
        elif low in t_low:
            mention.add(t)
    skipped = sorted(found & redirects)
    return sorted(found - redirects), skipped, sorted(mention - found)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("crop", help="species page title, e.g. Xoài")
    ap.add_argument("--group", help="BT folder, e.g. BT13. Default: looked up for groups 7-13")
    ap.add_argument("--dir", type=Path, help="output folder. Default: Crops/<group>/<Crop>")
    ap.add_argument("--overwrite", action="store_true",
                    help="replace local copies that differ from live")
    args = ap.parse_args()
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    out = args.dir
    if out is None:
        group = args.group or (f"BT{group_of(args.crop):02d}" if group_of(args.crop) else None)
        if not group:
            raise SystemExit(f"No group known for '{args.crop}': pass --group BTnn or --dir")
        parent = ROOT / group
        # reuse an existing folder whatever its case (`Thanh Long` for `Thanh long`)
        out = next((p for p in parent.glob("*") if p.is_dir() and p.name.casefold() == args.crop.casefold()),
                   parent / args.crop)
    out.mkdir(parents=True, exist_ok=True)

    bot = Bot()
    bot.opener.addheaders = [("User-Agent", UA)]
    titles, redirects, mention = discover(bot, args.crop)
    if not titles:
        raise SystemExit(f"No pages found for '{args.crop}'")
    pages = bot.fetch(titles)

    local = {p.stem.casefold(): p for p in out.glob("*.txt")}
    norm = lambda s: s.replace("\r\n", "\n").rstrip()   # noqa: E731
    rows, counts = [], {"new": 0, "same": 0, "differs": 0, "overwritten": 0}
    for t in titles:
        revid, text = pages[t]
        path = local.get(UNSAFE.sub("_", t).casefold())
        if path is None:
            path = out / f"{UNSAFE.sub('_', t)}.txt"
            state = "new"
        elif norm(path.read_text(encoding="utf-8-sig")) == norm(text):
            state = "same"
        else:
            state = "overwritten" if args.overwrite else "differs"
        if state in ("new", "overwritten"):
            path.write_text(text + "\n", encoding="utf-8", newline="\n")
        counts[state] += 1
        note = "" if path.stem == UNSAFE.sub("_", t) else f"  (local file '{path.name}')"
        print(f"  {state:<11} {t}  rev {revid}{note}")
        rows.append(f"{t}\t{revid}\t{path.name}")

    (out / "_revisions.tsv").write_text("title\trevid\tfile\n" + "\n".join(rows) + "\n",
                                        encoding="utf-8", newline="\n")
    print(f"\n{len(titles)} pages -> {out}")
    print("  " + ", ".join(f"{n} {k}" for k, n in counts.items() if n))
    if counts["differs"]:
        print("  'differs' = local copy kept; rerun with --overwrite to replace it with live")
    if redirects:
        print("Redirects, not fetched: " + ", ".join(redirects))
    if mention:
        print(f"Other titles mentioning '{args.crop}', not fetched: " + ", ".join(mention))


if __name__ == "__main__":
    main()
