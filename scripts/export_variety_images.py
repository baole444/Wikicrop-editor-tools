"""Export the variety images embedded in the source snapshot workbooks, named by catalogue name.

The images live in column G (Hình ảnh) of `Thông tin loài` in the two snapshot workbooks
(tag CG = primary, newer, CT = fallback, set in project_config.py), mostly as floating pictures anchored to
their row, some as WPS in-cell pictures. The live Google Sheet has none: they were lost when
the data moved to Sheets. Snapshots are only read, never modified.

Each image is matched to a name in the live catalogue (`Danh mục giống cây`, both halves) and
written as `<catalogue name>.<ext>`.

    python scripts/fetch_live_sheet.py          # refresh the catalogue first
    python scripts/export_variety_images.py

Output (OUT):
    <name>.<ext>          one image per catalogue name (originals)
    upload/<name>.jpg     the same, resized (≤ MAX_PX, JPEG) for inserting into the sheet
    alternates/           other distinct images found for the same name
    not-in-catalogue/     images whose row has no catalogue name, under their snapshot label
    manifest.csv          name ↔ catalogue row ↔ snapshot book/row ↔ notes

A re-run replaces only the files above. Everything in KEEP is left as it is.
"""
import csv
import hashlib
import io
import posixpath
import re
import shutil
import argparse
import sys
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import openpyxl
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import project_config as cfg  # noqa: E402

BOOKS = list(cfg.SOURCE_WORKBOOKS.items())
CATALOGUE = cfg.CATALOGUE
OUT = cfg.IMAGES
MAX_PX = 1000          # long side of the upload copies
KEEP = {"gapfill", "vision-cache", "image-sources.csv", "reverse-search.csv", "upload-plan.csv",
        "wiki-hash-cache.json"}
SHEET = "Thông tin loài"

# Snapshot label (normalised, lower) -> catalogue name, for names the matcher cannot pair.
OVERRIDES = {
    "dừa xiêm dâu": "Dừa dâu",                      # renamed in the catalogue (decision 8)
    "omdn 25-20": "OMĐN 25-20",
    "quế thanh": "Quế Thanh (Cinnamomum loureiroi)",  # species-level image
    "thầu dầu": "Thầu dầu (Ricinus communis)",        # species-level image
}
# Images known to be wrong for their row: (book, snapshot label lower) -> reason.
EXCLUDE = {
    ("CT", "vn84.4137"): "same picture as ĐX14 (mung bean); row headed the Đậu xanh block",
    ("CG", "vn84.4137"): "same picture as ĐX14 (mung bean); row headed the Đậu xanh block",
}

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
R_ID = f"{{{NS['r']}}}id"
R_EMBED = f"{{{NS['r']}}}embed"


def norm(v) -> str:
    return "" if v is None else re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(v)).strip())


def safe_name(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "-", s).strip(" .")


# ---------------------------------------------------------------- snapshot extraction
def _rels(z, path):
    rp = posixpath.join(posixpath.dirname(path), "_rels", posixpath.basename(path) + ".rels")
    if rp not in z.namelist():
        return {}
    out = {}
    for r in ET.fromstring(z.read(rp)).findall("rel:Relationship", NS):
        t = r.get("Target")
        if r.get("TargetMode") != "External":
            t = t.lstrip("/") if t.startswith("/") else posixpath.normpath(posixpath.join(posixpath.dirname(path), t))
        out[r.get("Id")] = t
    return out


def _col(ref):
    v = 0
    for ch in re.match(r"[A-Z]+", ref).group():
        v = v * 26 + ord(ch) - 64
    return v


def snapshot_images(tag, path):
    """Yield dicts: tag, row, col, media path, label (D, else C), species (C carried down)."""
    z = zipfile.ZipFile(path)
    sst = ["".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t"))
           for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS)]
    wbrels = _rels(z, "xl/workbook.xml")
    spath = next(wbrels[s.get(R_ID)] for s in ET.fromstring(z.read("xl/workbook.xml")).find("m:sheets", NS)
                 if s.get("name") == SHEET)
    cellimg = {}
    if "xl/cellimages.xml" in z.namelist():
        cr = _rels(z, "xl/cellimages.xml")
        for pic in ET.fromstring(z.read("xl/cellimages.xml")).iter(f"{{{NS['xdr']}}}pic"):
            cellimg[pic.find("xdr:nvPicPr/xdr:cNvPr", NS).get("name")] = cr[pic.find(".//a:blip", NS).get(R_EMBED)]
    rows, found = {}, []
    for row in ET.fromstring(z.read(spath)).iter(f"{{{NS['m']}}}row"):
        r = int(row.get("r"))
        cells = {}
        for c in row.findall("m:c", NS):
            ci, v, f = _col(c.get("r")), c.find("m:v", NS), c.find("m:f", NS)
            cells[ci] = norm(sst[int(v.text)] if c.get("t") == "s" and v is not None else (v.text if v is not None else ""))
            if f is not None and f.text and "DISPIMG" in f.text:
                m = re.search(r'DISPIMG\("([^"]+)"', f.text)
                if m and m.group(1) in cellimg:
                    found.append(dict(row=r, col=ci, media=cellimg[m.group(1)]))
        rows[r] = cells
    for target in _rels(z, spath).values():
        if "drawings/drawing" not in target:
            continue
        drels = _rels(z, target)
        for anc in ET.fromstring(z.read(target)):
            blip, frm = anc.find(".//a:blip", NS), anc.find("xdr:from", NS)
            if blip is not None and frm is not None:
                found.append(dict(row=int(frm.find("xdr:row", NS).text) + 1, col=int(frm.find("xdr:col", NS).text) + 1,
                                  media=drels[blip.get(R_EMBED)]))
    species, carry = {}, ""
    for r in sorted(rows):
        carry = rows[r].get(3) or carry
        species[r] = carry
    for f in found:
        cells = rows.get(f["row"], {})
        data = z.read(f["media"])
        yield dict(f, tag=tag, label=cells.get(4) or cells.get(3) or "", is_variety=bool(cells.get(4)),
                   species=species.get(f["row"], ""), data=data, md5=hashlib.md5(data).hexdigest(),
                   ext=Path(f["media"]).suffix.lower())


# ---------------------------------------------------------------- catalogue matching
PREFIX = ["giống khoai tây", "giống khoai mì", "giống khoai", "giống", "khoai tây", "khoai mì", "khoai lang", "lúa", "ngô",
          "bắp", "lạc", "đậu xanh", "đậu tương", "đậu đen", "đậu cô ve", "mía", "cỏ voi", "sầu riêng", "măng cụt",
          "thanh long", "vú sữa", "chôm chôm", "dừa", "xoài", "mít", "ổi", "chuối", "bưởi", "cam", "mận", "khóm", "dứa",
          "sả", "bạc hà", "điều", "dưa hấu", "quế", "dâu", "hương nhu", "húng quế", "gấc", "hồi", "nếp", "mè", "vừng"]


def keys(s):
    s = norm(s).lower().lstrip("-–• ")
    out = set()
    for p in [re.sub(r"\(.*?\)", "", s).strip()] + re.findall(r"\((.*?)\)", s):
        for q in re.split(r"\s*/\s*|:", p):
            q = q.strip(" -–.")
            if q:
                out.add(q)
                out.update(q[len(pre) + 1:] for pre in PREFIX if q.startswith(pre + " "))
    return {k for k in (re.sub(r"[\s\-\.]", "", x) for x in out) if len(k) >= 2}


def catalogue_targets():
    ws = openpyxl.load_workbook(CATALOGUE)["Danh mục giống cây"]
    out, grp, sp = [], "", ""
    for r in range(2, ws.max_row + 1):
        a, c, e = norm(ws.cell(r, 1).value), norm(ws.cell(r, 3).value), norm(ws.cell(r, 5).value)
        if a.startswith("BT"):
            grp = a
        if c and not c.startswith("Species"):
            sp = c
            out.append(dict(row=r, grp=grp, species=sp, name=c, level="species"))
        # Editors mark rows for removal in "Lý do sửa" (H) instead of deleting them:
        # "Bỏ vì trùng tên", "xóa giống đã xuất hiện" … Those are not targets.
        removed = re.match(r"(bỏ|xóa|xoá)\b", norm(ws.cell(r, 8).value).lower())
        if e and not e.startswith("Varieties") and not removed:
            out.append(dict(row=r, grp=grp, species=sp, name=e.lstrip("- ").strip(), level="variety"))
    return out


def match(img, targets, by_name):
    lab = img["label"].lower().lstrip("-– ").strip()
    if lab in OVERRIDES:
        return by_name.get(OVERRIDES[lab])
    exact = [t for t in targets if t["name"].lower() == lab]
    if exact:
        return exact[0]
    lk, best, score = keys(img["label"]), None, 0
    for t in targets:
        if (t["level"] == "variety") != img["is_variety"]:
            continue
        s = len(lk & t["keys"])
        if s > score:
            best, score = t, s
    return best


# ---------------------------------------------------------------- export
def main():
    cfg.require(CATALOGUE, *(p for _, p in BOOKS))
    targets = catalogue_targets()
    for t in targets:
        t["keys"] = keys(t["name"])
    by_name = {t["name"]: t for t in targets}

    imgs = [im for tag, p in BOOKS for im in snapshot_images(tag, p)]
    # Clear only what this script writes. KEEP holds the hand-made work beside it: gapfill/
    # (fetch_commons_images.py), the source and reverse-search CSVs, the Cloud Vision cache.
    OUT.mkdir(parents=True, exist_ok=True)
    for p in OUT.iterdir():
        if p.name in KEEP:
            continue
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    for sub in ("upload", "alternates", "not-in-catalogue"):
        (OUT / sub).mkdir(parents=True)

    groups, manifest, orphans = {}, [], []
    for im in imgs:
        why = EXCLUDE.get((im["tag"], im["label"].lower()))
        t = None if why else match(im, targets, by_name)
        if t is None:
            orphans.append((im, why or "no catalogue name"))
        else:
            groups.setdefault(t["name"], (t, []))[1].append(im)

    order = {"CG": 0, "CT": 1}

    def base(s):
        return re.sub(r"\(.*?\)", "", s).strip(" -–").lower()

    for name, (t, ims) in groups.items():
        # label naming the catalogue entry itself first, then the CG workbook, then column G, then larger file
        ims.sort(key=lambda i: (base(i["label"]) != base(name), order[i["tag"]], i["col"] != 7, -len(i["data"])))
        seen, alt = set(), 0
        for i, im in enumerate(ims):
            if im["md5"] in seen:
                continue
            seen.add(im["md5"])
            if i == 0:
                fn = f"{safe_name(name)}{im['ext']}"
                (OUT / fn).write_bytes(im["data"])
                up = OUT / "upload" / f"{safe_name(name)}.jpg"
                pic = Image.open(io.BytesIO(im["data"]))
                pic.thumbnail((MAX_PX, MAX_PX))
                if pic.mode != "RGB":
                    bg = Image.new("RGB", pic.size, "white")
                    bg.paste(pic, mask=pic.convert("RGBA").split()[-1])
                    pic = bg
                pic.save(up, "JPEG", quality=85, optimize=True)
                role = "primary"
            else:
                alt += 1
                fn = f"alternates/{safe_name(name)} - alt{alt} ({im['tag']}){im['ext']}"
                (OUT / fn).write_bytes(im["data"])
                role = "alternate"
            manifest.append([fn, role, t["grp"], t["species"], name, t["level"], t["row"], im["tag"], im["row"],
                             im["label"], ""])
    for im, why in orphans:
        fn = f"not-in-catalogue/{safe_name(im['label'] or 'no label')} ({im['tag']} R{im['row']}){im['ext']}"
        (OUT / fn).write_bytes(im["data"])
        manifest.append([fn, "orphan", "", im["species"], "", "", "", im["tag"], im["row"], im["label"], why])

    with open(OUT / "manifest.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file", "role", "group", "species", "catalogue name", "level", "catalogue row",
                    "source", "source row (Thông tin loài)", "source label", "note"])
        w.writerows(sorted(manifest, key=lambda m: (m[1] != "primary", m[6] or 9999, m[0])))

    prim = sum(1 for m in manifest if m[1] == "primary")
    ours = sum(1 for m in manifest if m[1] == "primary" and m[2] >= "BT07")
    print(f"{len(imgs)} images read · {prim} catalogue names with an image ({ours} in BT07–BT13, "
          f"{prim - ours} in BT01–BT06) · {sum(1 for m in manifest if m[1] == 'alternate')} alternates · "
          f"{len(orphans)} not in catalogue")
    print(f"catalogue names without any image: {len(targets) - prim} of {len(targets)}")
    print("output:", OUT)


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    main()
