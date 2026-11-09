#!/usr/bin/env python3
"""
Check a crop's local page copies against the mechanical rules of the article
standard. Read-only, works on the files fetch_crop_pages.py writes.

Catches what a script can see: page type and skeleton headings, infobox
placement and layout, taxonomy against the species page, citations, categories
and sortkeys, `Xem thêm`, and the §9 clean-up rules. It cannot judge content:
whether a figure is sourced by the ref beside it, whether the region and
calendar agree, or whether a weakness is real. Read the pages for that.

    python scripts/audit_crop_pages.py Crops/BT13/Xoài
    python scripts/audit_crop_pages.py Crops/BT13/Xoài --group "Cây ăn quả nhiệt đới"
"""

import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from reformat_infoboxes import group_of  # noqa: E402

GROUPS = {1: "Ngũ cốc", 2: "Các loại kê", 3: "Cây họ đậu", 4: "Cây dầu ăn", 5: "Cây dầu không ăn được",
          6: "Cây lấy sợi", 7: "Cây thức ăn chăn nuôi", 8: "Cây lấy đường", 9: "Cây củ", 10: "Cây kích thích",
          11: "Cây dược liệu", 12: "Cây hương liệu", 13: "Cây ăn quả nhiệt đới"}
# §4.3: the two extra species sections of each group
GROUP_SECTIONS = {1: ("Các giai đoạn sinh trưởng", "Thời vụ"), 2: ("Các giai đoạn sinh trưởng", "Thời vụ"),
                  3: ("Các giai đoạn sinh trưởng", "Thời vụ"), 4: ("Các giai đoạn sinh trưởng", "Thời vụ"),
                  5: ("Thời vụ", "Thu hoạch và bảo quản"), 6: ("Thời vụ", "Thu hoạch và sơ chế"),
                  7: ("Chu kỳ lưu gốc", "Năng suất sinh khối"), 8: ("Chu kỳ lưu gốc", "Năng suất và chữ đường"),
                  9: ("Nhân giống", "Thu hoạch và bảo quản"), 10: ("Thời vụ", "Thu hoạch và sấy lá"),
                  11: ("Vị thuốc", "Thu hái và sơ chế"), 12: ("Hàm lượng tinh dầu", "Thu hái và sơ chế"),
                  13: ("Nhân giống", "Thời vụ")}

# §4.1 and §5.1 skeletons: required `==` sections, in order
SPECIES = ["Giới thiệu", "Nguồn gốc", "Đặc điểm", "Yêu cầu sinh thái", "Công dụng", "Giá trị kinh tế",
           "Dữ liệu Gen", "Tham khảo"]
SPECIES_OPTIONAL = ["Phân loại", "Giá trị dinh dưỡng", "Danh sách giống", "So sánh thuộc tính các giống"]
CULTIVAR = ["Giới thiệu", "Nguồn gốc", "Đặc điểm", "Kỹ thuật canh tác", "Xem thêm", "Tham khảo"]
TAXONOMY = ["Gioi", "KhongPhanLoai1", "KhongPhanLoai2", "KhongPhanLoai3", "Bo", "Ho", "Chi"]
GROWER = ["NamCongNhan", "DonViChonTao", "ThoiGianSinhTruong", "NangSuat", "ChieuCaoCay"]
ACTIONABLE = re.compile(r"\d[\d.,]*\s*(?:-\s*\d[\d.,]*\s*)?(?:tấn/ha|kg/ha|tấn|kg|ngày|tháng|năm|°Brix|đồng/kg|đ/kg|g\b|gram|m\b|mét|cm|ha\b)")

HEADING = re.compile(r"^(=+)\s*(.*?)\s*\1\s*$", re.M)
CATEGORY = re.compile(r"\[\[\s*Thể loại\s*:\s*([^|\]]+?)\s*(?:\|([^\]]*))?\]\]")
LINK = re.compile(r"\[\[(?!\s*(?:Thể loại|Tập tin|File|Category|Image|Hình)\s*:)([^|\]#]+)")


def page_type(title, crop):
    low, c = title.lower(), crop.lower()
    if low == c:
        return "species"
    if low == f"kỹ thuật trồng và chăm sóc {c}":
        return "agronomy"
    if low == f"danh sách giống {c}":
        return "list"
    return "cultivar"


def infobox(text):
    """(name, start, end, {param: value}) of the first InfoPlant1 / InfoCropPlant call."""
    m = re.search(r"\{\{\s*(InfoPlant1|InfoCropPlant|InfoPlant|InfoPlant1Giong)\s*(?=[|}\n])", text)
    if not m:
        return None
    depth, i = 0, m.start()
    while i < len(text):
        if text.startswith("{{", i):
            depth += 1
            i += 2
        elif text.startswith("}}", i):
            depth -= 1
            i += 2
            if depth == 0:
                break
        else:
            i += 1
    body = text[m.end():i - 2]
    params, depth, cur = {}, 0, ""
    parts = []
    for ch_i, ch in enumerate(body):
        if body.startswith("{{", ch_i) or body.startswith("[[", ch_i):
            depth += 1
        elif body.startswith("}}", ch_i) or body.startswith("]]", ch_i):
            depth -= 1
        if ch == "|" and depth <= 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.strip()] = v.strip()
    return m.group(1), m.start(), i, params


def check(title, text, kind, crop, group, species_tax):
    out = []
    add = out.append
    heads = [(len(m.group(1)), m.group(2), m.start()) for m in HEADING.finditer(text)]
    h2 = [h for lvl, h, _ in heads if lvl == 2]
    first_head = heads[0][2] if heads else len(text)

    # §9.12 headings
    if any(lvl == 1 for lvl, _, _ in heads):
        add("§9.12 single `=` heading")

    # §6 infobox
    box = infobox(text)
    if kind in ("species", "cultivar"):
        want = "InfoPlant1" if kind == "species" else "InfoCropPlant"
        if not box:
            add(f"§6 no {{{{{want}}}}}")
        else:
            name, start, end, params = box
            if name != want:
                add(f"§6 uses {{{{{name}}}}}, want {{{{{want}}}}}")
            if start > first_head:
                add("§6.1 infobox after the first heading")
            if text[start:end].count("\n") < len(params):
                add("§6.6 infobox not one parameter per line")
            if not params.get("Hinh"):
                add("§6.4 no Hinh")
            # §6.4: the page's own photo is `<title>.ext`, or `<title> N.ext` for a later one
            elif kind == "cultivar" and not re.fullmatch(re.escape(title) + r"( \d+)?",
                                                         params["Hinh"].rsplit(".", 1)[0]):
                add(f"§6.4 photo `{params['Hinh']}` is not the page's own; check MoTaHinh describes it"
                    f" (`{params.get('MoTaHinh', '')}`)")
            if kind == "species":
                cl = params.get("CacLoai", "")
                if not cl.startswith("{{DanhsachLoai|"):
                    add("§6.2 CacLoai not {{DanhsachLoai|...}}")
            if kind == "cultivar":
                if species_tax:
                    diff = [k for k in TAXONOMY if params.get(k, "") != species_tax.get(k, "")]
                    if diff:
                        add("§6.3 taxonomy differs from species page: " + ", ".join(
                            f"{k}=`{params.get(k, '')}`≠`{species_tax.get(k, '')}`" for k in diff))
                for k in ("TenGiong", "TenLoai", "LoaiGiong", "XuatXu"):
                    if not params.get(k):
                        add(f"§6.2 {k} empty")
                empty = [k for k in GROWER if not params.get(k)]
                if empty:
                    add("§6.5 grower fields empty: " + ", ".join(empty))

    # §4.1 / §5.1 skeleton
    if kind == "species":
        want = SPECIES[:5] + list(GROUP_SECTIONS.get(group, ())) + SPECIES[5:]
        missing = [s for s in want if s not in h2]
        extra = [s for s in h2 if s not in want + SPECIES_OPTIONAL]
    elif kind == "cultivar":
        missing = [s for s in CULTIVAR if s not in h2]
        extra = [s for s in h2 if s not in CULTIVAR]
    else:
        missing, extra = (["Tham khảo"] if kind == "agronomy" and "Tham khảo" not in h2 else []), []
    if missing:
        add("§4.1/§5.1 missing sections: " + ", ".join(missing))
    if extra:
        add("§4.2 sections not in the skeleton: " + ", ".join(extra))
    # §4.1 order: required and optional sections interleaved, crop-group sections after `Yêu cầu sinh thái`
    species_order = (["Giới thiệu", "Nguồn gốc", "Phân loại", "Đặc điểm", "Yêu cầu sinh thái"]
                     + list(GROUP_SECTIONS.get(group, ()))
                     + ["Công dụng", "Giá trị dinh dưỡng", "Giá trị kinh tế", "Danh sách giống",
                        "So sánh thuộc tính các giống", "Dữ liệu Gen", "Tham khảo"])
    order = species_order if kind == "species" else CULTIVAR
    present = [s for s in h2 if s in order]
    if kind in ("species", "cultivar") and present != sorted(present, key=lambda s: order.index(s)):
        add("§4.1/§5.1 sections out of skeleton order")
    if any(crop.lower() in h.lower() for _, h, _ in heads):
        add("§4.2 crop name inside a heading")

    # §3 citations
    if "<references" not in text and "{{reflist" not in text.lower():
        add("§3 no <references>")
    refs_named = re.findall(r"<ref\s+name\s*=", text)
    refs_bare = re.findall(r"<ref>(?!</)", text)
    if refs_bare:
        add(f"§3 {len(refs_bare)} unnamed <ref>")
    tk = re.search(r"^==\s*Tham khảo\s*==\s*$(.*?)(?=^==[^=]|\Z)", text, re.M | re.S)
    if tk and re.search(r"^\s*[#*]", tk.group(1), re.M):
        add("§3 hand-written #/* list under Tham khảo")
    body = text[:tk.start()] if tk else text
    if not refs_named and kind != "list":
        add("§3.1 no inline refs at all")
    else:
        unref = 0
        for para in re.split(r"\n", body):
            if (ACTIONABLE.search(para) and "<ref" not in para and "Cập nhật lần cuối" not in para
                    and not para.lstrip().startswith(("|", "{{", "!"))):
                unref += 1
        if unref:
            add(f"§3.1 {unref} line(s) with a figure and no ref")
    if re.search(r"(?<![=\[|])https?://", re.sub(r"\{\{ChuthichWeb[^}]*\}\}|\[https?://\S+", "", text)):
        add("§3 bare URL")
    if "<u>" in text:
        add("§3 <u> wrapper")
    if re.search(r"NgayTruyCap\s*=\s*ngày", text, re.I):
        add("§3 NgayTruyCap starts with 'ngày'")
    for name in re.findall(r'<ref\s+name\s*=\s*"?([^"/>]+)', text):
        if not re.fullmatch(r":[a-z0-9-]+:[a-z0-9-]+:\d{4}", name.strip()):
            add(f"§3.3 ref name `{name.strip()}`")
    if re.search(r"\n\s*<ref\s+name[^>]*/>", body):
        add("§3 rule 2: ref marker on its own line")

    # §7 categories
    cats = CATEGORY.findall(text)
    names = {c for c, _ in cats}
    crop_cat = crop[0].upper() + crop[1:]
    want_cats = {"species": {crop_cat, GROUPS.get(group, "?"), "Bài loài cây trồng"},
                 "cultivar": {crop_cat, "Bài giống cây trồng"},
                 "agronomy": {crop_cat, "Kỹ thuật canh tác"},
                 "list": {crop_cat, "Danh sách giống"}}[kind]
    if want_cats - names:
        add("§7.1 missing categories: " + ", ".join(sorted(want_cats - names)))
    key = dict(cats).get(crop_cat)
    want_key = {"species": " ", "agronomy": "Kỹ thuật", "list": "Danh sách",
                "cultivar": title[len(crop) + 1:] if title.lower().startswith(crop.lower() + " ") else None}[kind]
    if crop_cat in names and want_key is not None and key != want_key:
        add(f"§7.2 sortkey `{key}`, want `{want_key}`")

    # §5.4 Xem thêm
    if kind == "cultivar":
        xt = re.search(r"^==\s*Xem thêm\s*==\s*$(.*?)(?=^==[^=]|\Z)", text, re.M | re.S)
        links = {l.strip() for l in LINK.findall(xt.group(1))} if xt else set()
        for t in (crop_cat, f"Kỹ thuật trồng và chăm sóc {crop.lower()}"):
            if t not in links:
                add(f"§5.4 Xem thêm lacks [[{t}]]")
        if not re.search(r"Vùng trồng phù hợp", text):
            add("§1A.4 no `Vùng trồng phù hợp`")
        if not re.search(r"[Nn]hược điểm", text):
            add("§5.3 no `Nhược điểm` stated")
    if kind == "list" and not re.search(r"Cập nhật lần cuối", text):
        add("§7.5 no `Cập nhật lần cuối` review date")

    # §9 clean-up
    if "<span id=" in text:
        add("§9.2 <span id>")
    if re.search(r"&(?:gt|lt|quot|amp|nbsp);", text):
        add("§9.3 HTML entity")
    prose = re.sub(r"\{\{ChuthichWeb[^}]*\}\}|<ref[^>]*>.*?</ref>|https?://\S+", "", text, flags=re.S)
    if ";" in prose:
        add(f"§9.10 {prose.count(';')} semicolon(s)")
    if re.search("[–—]", text):
        add("§9.11 en/em dash")
    if re.search(r"\d\s+-\s+\d", prose):
        add("§9.11 spaced range `a - b`")
    if re.search(r"\bsào\b", text) and not re.search(r"sào[^.\n]{0,40}m²", text):
        add("§1A.4 `sào` without its size")
    if "{{#morphology:" in text:
        add("note: {{#morphology:}} call (renders nothing, leave in place)")
    # §9.13: `Xem thêm`, tables and captions may repeat a link, so count prose only
    prose_body = re.sub(r"^==\s*Xem thêm\s*==\s*$.*?(?=^==[^=]|\Z)", "", body[first_head:], flags=re.M | re.S)
    prose_body = re.sub(r"^\{\|.*?^\|\}", "", prose_body, flags=re.M | re.S)
    linked = [l.strip() for l in LINK.findall(prose_body)]
    dup = sorted({l for l in linked if linked.count(l) > 1})
    if dup:
        add("§9.13 linked more than once in the body: " + ", ".join(dup))
    if title in linked:
        add("§9.13 links to itself")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path, help="crop folder written by fetch_crop_pages.py")
    ap.add_argument("--crop", help="species title. Default: the folder name")
    ap.add_argument("--group", type=int, help="Main Page group number. Default: looked up for groups 7-13")
    args = ap.parse_args()
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    crop = args.crop or args.folder.name
    group = args.group or group_of(crop)
    manifest = args.folder / "_revisions.tsv"
    files = ({r["title"]: args.folder / r["file"] for r in csv.DictReader(open(manifest, encoding="utf-8"), delimiter="\t")}
             if manifest.exists() else {p.stem: p for p in args.folder.glob("*.txt")})
    species_file = next((p for t, p in files.items() if t.lower() == crop.lower()), None)
    species_tax = {}
    if species_file:
        box = infobox(species_file.read_text(encoding="utf-8-sig"))
        species_tax = box[3] if box else {}

    total = 0
    for title in sorted(files, key=lambda t: (["species", "agronomy", "list", "cultivar"].index(page_type(t, crop)), t)):
        kind = page_type(title, crop)
        problems = check(title, files[title].read_text(encoding="utf-8-sig"), kind, crop, group, species_tax)
        total += len(problems)
        print(f"\n{title}  [{kind}]  {len(problems)} finding(s)")
        for p in problems:
            print(f"  - {p}")
    print(f"\n{len(files)} pages, {total} findings")


if __name__ == "__main__":
    main()
