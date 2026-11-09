"""Collect pictures for `Thông tin loài` rows that have no usable image in the source workbooks.

For rows the snapshot workbooks never had a picture for (so export_variety_images.py has nothing to
export), or whose picture was blocked, a picture is taken from one of three places:

    PICTURES   Wikimedia Commons, freely licensed (preferred)
    WEB        an institutional, press or seller page: not free, cite the page when reusing
    LOCAL      a source-workbook picture already exported to variety-images/, whose source is now known

Each file is saved under the row's catalogue name, so insert_images.gs matches it the same way as
the exported ones. The choice for each row is noted beside its entry below.

Saved in `variety-images/gapfill/`, which export_variety_images.py leaves alone when it rebuilds the
rest of that folder. upload_variety_images.py reads it from there, so one run uploads both sets.

    python scripts/sheet-images/fetch_commons_images.py

Output (OUT):
    originals/<name>.<ext>   the picture as downloaded
    upload/<name>.jpg        resized (≤ MAX_PX, JPEG), for rows whose image cell is empty
    upload-replace/<name>.jpg   the same, for rows holding a blocked picture: run insertImages on
                             this folder separately, with OVERWRITE = true
    hold/<name>.jpg          picked but waiting on a check (HOLD): not for the sheet yet
    sources.csv              one row per picture: where it came from, author or publisher, licence.
                             The attribution a free licence asks for, the citation a web picture needs

The wiki does not need the Commons pictures uploaded: Wikicrop has InstantCommons, so an article can
use `[[Tập tin:<Commons file name>]]` directly. WEB and LOCAL pictures go up with a {{ChuthichWeb}}
citation to the page, never as the uploader's own work. upload_variety_images.py does both from sources.csv.
"""
import csv
import html
import io
import json
import re
import shutil
import argparse
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from PIL import Image
from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import project_config as cfg  # noqa: E402

SRC = cfg.SOURCE_BOOKS
OUT = cfg.IMAGES / "gapfill"
MAX_PX = 1000
UA = {"User-Agent": "wikicrop-sheet-images/1.0 (https://github.com/nphphat/Wikicrop)"}

# Catalogue name (column D, or C for species-only rows, as on the sheet) -> Commons file. Pick photos
# that show the plant as a crop (leaves, flowers, fruit), never a processing or harvesting step.
PICTURES = {
    "Thuốc lá (Nicotiana spp.)": "Tabak 9290019.JPG",                    # N. tabacum in flower, field
    "Thuốc phiện (Papaver somniferum)": "Papaver somniferum 2021 G5.jpg",  # flower and unripe capsules
    "Khoai tây Marabel": "Kartoffel Marabel 03 (fcm).jpg",
    "Rau đắng biển (Bacopa monnieri)": "Starr 010818-0007 Bacopa monnieri.jpg",
    "Sả Java": "Cymbopogon winterianus.jpeg",
    "Sả bẹ": "Cymbopogon nardus 0zz.jpg",
    "Bạc hà âu": "Mentha-piperita.JPG",
    "Húng lủi": "Mentha aquatica (2005 09 18) - uitsnede.jpg",          # M. aquatica, per Phạm Hoàng Hộ
    "Hương nhu trắng (Hương nhu xanh)": "Ocimum gratissimum (21913853081).jpg",
    "Húng quế tía / đỏ": "Purple Basil (Ocimum basilicum).jpg",
    "Húng quế chanh (É chanh)": "Kemangi.jpg",
    "Quế Trà My": "Vietnam cinnamon trees.jpg",                          # taken in Bắc Trà My
    "Chuối ngự": "Chuối ngự.jpg",
    "Bưởi Luận Văn": "Bưởi Luận Văn.png",
    "Dừa xiêm đỏ": "Dừa Xiêm lửa.jpg",                                   # Rạch Giá
    "Thanh long ruột trắng": "Hylocereus undatus red pitahaya.jpg",
    "Vú sữa tím": "Chrysophyllum cainito0.jpg",
    "Dâu bòn bon (Lansium domesticum)": "Lanzones.jpg",
    "Dưa hấu ruột vàng": "Watermelon yellow 2024 G1.jpg",
    # Canopy only, no fruit: the one picture found that names the variety (searched under every alias).
    "Chôm chôm Nhãn (Chôm chôm Đường)": "Chôm chôm nhãn Thanh Sử - panoramio.jpg",
}

FCRI = "Viện Cây lương thực và Cây thực phẩm"
GIONGMIA = "Chuyên trang giống mía (giongmia.wordpress.com)"
NINHBINH = ("https://chicucttbvtv.ninhbinh.gov.vn/giong-cay-trong/"
            "gioi-thieu-mot-so-giong-khoai-tay-dang-trong-pho-bien-o-viet-nam-104.html")


def fcri(slug, img, title):
    return dict(img=f"https://fcri.com.vn/wp-content/uploads/{img}", page=f"https://fcri.com.vn/sanpham/{slug}/",
                publisher=FCRI, title=title)


# Catalogue name -> picture on a web page. `img` is the image itself; for a PDF, `pdf_image` names the
# embedded picture to take. Not free: `page` is what a reuse must cite.
WEB = {
    "Mía KU00-1-61": dict(img="https://giongmia.wordpress.com/wp-content/uploads/2008/09/ku00-1-61.jpg",
                          page="https://giongmia.wordpress.com/2008/09/12/gi%E1%BB%91ng-mia-ku00-1-61-k84-200-x-da-giao/",
                          publisher=GIONGMIA, title="Giống mía KU00-1-61 (K84-200 x Đa giao)"),
    "Mía KU 60-3": dict(img="https://giongmia.wordpress.com/wp-content/uploads/2006/10/ku60-3-6.jpg",
                        page="https://giongmia.wordpress.com/wp-content/uploads/2006/10/ku60-3-6.jpg",
                        publisher=GIONGMIA, title="Giống mía KU 60-3"),
    "Mía KK3": dict(img="http://www.nasugar.com/Uploads/files/ToRoi/43_%20Giong%20mia%20KK3.pdf", pdf_image="Image46.jpg",
                    page="http://www.nasugar.com/Uploads/files/ToRoi/43_%20Giong%20mia%20KK3.pdf",
                    publisher="Công ty TNHH Mía đường Nghệ An (NASU)", title="Giống mía KK3 (tờ rơi)"),
    "Khoai tây Jelly": fcri("giong-khoai-tay-jelly", "2020/11/jelly.jpg", "Giống khoai tây Jelly"),
    "Khoai tây KT4": fcri("giong-khoai-tay-kt4", "2020/11/kt4.jpg", "Giống khoai tây KT4"),
    "Khoai tây KT5": fcri("giong-khoai-tay-kt5", "2020/11/kt5.jpg", "Giống khoai tây KT5"),
    "Khoai tây KT7": fcri("giong-khoai-tay-kt7", "2020/11/kt7.jpg", "Giống khoai tây KT7"),
    "Khoai lang KB1": fcri("giong-khoai-lang-kb1", "2020/11/kb1.jpg", "Giống khoai lang KB1"),
    "Khoai lang KL03": fcri("giong-khoai-lang-kl03", "2023/07/ec7a8450ee8439da6095.jpg", "Giống khoai lang KL03"),
    "Khoai lang KL5": fcri("giong-khoai-lang-kl5", "2020/11/kl5.jpg", "Giống khoai lang KL5"),
    "Khoai lang KLC3": fcri("giong-khoai-lang-klc3", "2020/11/klc3.jpg", "Giống khoai lang KLC3"),
    "Khoai lang KLC15": fcri("giong-khoai-lang-klc15", "2020/11/klc15.jpg", "Giống khoai lang KLC15"),
    "Khoai lang VC6": fcri("khoai-lang-vc6", "2021/08/slide0001-image001-1.png", "Khoai lang VC6"),
    "Khoai lang VC7": fcri("khoai-lang-vc7", "2021/08/screenshot-2021-08-13-150929.png", "Khoai lang VC7"),
    "Khoai lang VĐ1": fcri("giong-khoai-lang-vd1", "2020/11/vd1.jpg", "Giống khoai lang VĐ1"),
    "Quế Trà Bồng": dict(img="https://media.baovanhoa.vn/Portals/0/EasyGalleryImages/1/57051/IMG_7244.jpg",
                         page="https://baovanhoa.vn/kinh-te/nang-tam-thuong-hieu-dac-san-que-tra-bong-40954.html",
                         publisher="Báo Văn hóa", title="Nâng tầm thương hiệu đặc sản quế Trà Bồng"),
    "Quế Quỳ (bảo tồn, nhân giống)": dict(
        img="https://khuyennongnghean.com.vn/uploads/news/2024_08/image-20240821102121-3.jpeg",
        page="https://khuyennongnghean.com.vn/index.php/lam-nghiep/que-phong-don-nguon-luc-bao-ton-phat-trien-cay-con-ban-dia-1765.html",
        publisher="Trung tâm Khuyến nông Nghệ An", title="Quế Phong dồn nguồn lực bảo tồn, phát triển cây, con bản địa"),
    "Ổi mỡ": dict(img="https://static-images.vnncdn.net/files/publish/2022/7/30/oi-mo-869.jpg?width=0&s=IdaUOJ59UQ9altFUo-NxkA",
                  page="https://vietnamnet.vn/oi-mo-thom-lung-ba-noi-tro-chieu-chong-dat-mua-ve-ngam-ruou-2044480.html",
                  publisher="VietNamNet", title="Ổi mỡ thơm lừng, bà nội trợ chiều chòng đặt mua về ngâm rượu"),
    "Khóm Cầu Đúc": dict(img="https://cdn2.tuoitre.vn/thesaigontimes/uploads/2022/01/khom-cau-duc-Phan-Hue.jpg",
                         page="https://thesaigontimes.vn/ghe-canh-dong-khom-cau-duc-de-tham-lang-du-lich-cong-dong/",
                         publisher="Kinh tế Sài Gòn (ảnh: Phan Huế)",
                         title="Ghé cánh đồng khóm Cầu Đúc để thăm làng du lịch cộng đồng"),
    # Dừa xiêm lục: no pick. The moit.gov.vn "dừa xiêm xanh" photo tried on 19/10/2026 shows a different
    # variety.
    "Chôm chôm Cái Mơn (Chôm chôm Cái Mơn Ra Ca)": dict(
        img="https://i.ex-cdn.com/nongnghiepmoitruong.vn/files/f1/2017/7/23/09-11-30-1-nh-duong-vn-loi-gioi-thieu-chum-chom-chom-giong-moi-sp-thu-hoch120648133.jpg",
        page="https://nongnghiepmoitruong.vn/ben-tre-co-giong-chom-chom-moi-trai-to-rat-ngon-d198772.html",
        publisher="Nông nghiệp và Môi trường", title="Bến Tre có giống chôm chôm mới trái to, rất ngon"),
}

# Catalogue name -> source-workbook picture (path under variety-images/) whose source has since been found.
LOCAL = {
    "Khoai tây Eben": dict(file="alternates/Khoai tây Eben - alt1 (CG).jpeg", page=NINHBINH,
                           publisher="Chi cục Trồng trọt và Bảo vệ thực vật Ninh Bình",
                           title="Giới thiệu một số giống khoai tây đang trồng phổ biến ở Việt Nam"),
}

# The image cell already holds a picture that must go (blocked on 05/10/2026): upload-replace/.
REPLACE = {"Khoai tây Eben", "Khóm Cầu Đúc", "Chôm chôm Nhãn (Chôm chôm Đường)"}
# Picked, but the row needs a check first: hold/, not for the sheet yet.
HOLD = {
    "Sả bẹ": "E238/E239 describe the wrong plants. Confirm sả bẹ = C. nardus first",
}


def get(url):
    req = urllib.request.Request(urllib.parse.quote(url, safe=":/?&=%#+,;@!$'()*~"), headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def commons_info(file):
    q = urllib.parse.urlencode(dict(
        action="query", titles="File:" + file, prop="imageinfo", format="json",
        iiprop="url|extmetadata", iiextmetadatafilter="LicenseShortName|LicenseUrl|Artist"))
    page = next(iter(json.loads(get("https://commons.wikimedia.org/w/api.php?" + q))["query"]["pages"].values()))
    if "imageinfo" not in page:
        sys.exit(f"Not on Commons: {file}")
    ii = page["imageinfo"][0]
    meta = {k: html.unescape(re.sub(r"<[^>]+>", "", str(v.get("value", "")))).strip() for k, v in ii["extmetadata"].items()}
    return ii["url"].split("?")[0], ii["descriptionurl"], meta


def pdf_image(data, name):
    for page in PdfReader(io.BytesIO(data)).pages:
        for im in page.images:
            if im.name == name:
                buf = io.BytesIO()
                im.image.convert("RGB").save(buf, "JPEG", quality=95)
                return buf.getvalue(), ".jpg"
    sys.exit(f"{name} not found in the PDF")


def safe_name(s):
    """Characters a file name cannot hold become '-', as export_variety_images.py does."""
    return re.sub(r'[\\/:*?"<>|]', "-", s).strip(" .")


def save(name, data, ext):
    fn = safe_name(name)
    (OUT / "originals" / f"{fn}{ext}").write_bytes(data)
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((MAX_PX, MAX_PX), Image.LANCZOS)
    folder = "hold" if name in HOLD else "upload-replace" if name in REPLACE else "upload"
    img.save(OUT / folder / f"{fn}.jpg", "JPEG", quality=85, optimize=True)
    return folder, f"{img.width}x{img.height}"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    cfg.require(cfg.IMAGES)
    for sub in ("originals", "upload", "upload-replace", "hold"):
        shutil.rmtree(OUT / sub, ignore_errors=True)
        (OUT / sub).mkdir(parents=True)
    accessed = date.today().strftime("%d/%m/%Y")
    rows = []

    for name, file in PICTURES.items():
        url, page, meta = commons_info(file)
        folder, size = save(name, get(url), Path(urllib.parse.unquote(url)).suffix.lower())
        rows.append([name, folder, "commons", file, page, url, meta.get("Artist", ""),
                     meta.get("LicenseShortName", ""), meta.get("LicenseUrl", ""), "", accessed, size, HOLD.get(name, "")])
        print(f"{folder:15} {name}: {file} ({meta.get('LicenseShortName')}, {meta.get('Artist')})")

    for name, w in WEB.items():
        data = get(w["img"])
        if "pdf_image" in w:
            data, ext = pdf_image(data, w["pdf_image"])
        else:
            ext = Path(urllib.parse.urlparse(w["img"]).path).suffix.lower() or ".jpg"
        folder, size = save(name, data, ext)
        rows.append([name, folder, "web", "", w["page"], w["img"], w["publisher"],
                     "not free: cite the page", "", w["title"], accessed, size, HOLD.get(name, "")])
        print(f"{folder:15} {name}: {w['publisher']}")

    for name, c in LOCAL.items():
        path = cfg.IMAGES / c["file"]
        folder, size = save(name, path.read_bytes(), path.suffix.lower())
        rows.append([name, folder, "local", c["file"], c["page"], "", c["publisher"],
                     "not free: cite the page", "", c["title"], accessed, size, HOLD.get(name, "")])
        print(f"{folder:15} {name}: {c['file']}")

    with open(OUT / "sources.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["catalogue name", "folder", "kind", "commons file / local file", "source page",
                    "image url", "author / publisher", "licence", "licence url", "page title", "accessed",
                    "upload size", "hold reason"])
        w.writerows(rows)
    print(f"\n{len(rows)} pictures -> {OUT}")


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    main()
