# Công cụ Wikicrop

**Tiếng Việt** | [English](README.en.md)

Bộ công cụ bảo trì [Wikicrop](http://wikicrop.lab.io.vn/wikicrop/index.php/Main_Page), bách khoa toàn thư
MediaWiki về cây trồng và giống cây trồng dành cho người trồng Việt Nam, cùng bảng tính Google Sheet chứa
danh mục giống. Các công cụ sửa hàng loạt các trang wiki theo tiêu chuẩn viết bài, kiểm tra lại kết quả,
chuyển hình ảnh giống từ các bảng tính nguồn sang bảng tính dự án và lên wiki, và hỗ trợ tìm nguồn cho dữ liệu.

Số mục trong chú thích mã nguồn và tóm lược sửa đổi (`standard section 6.6`, `(9.11)`) là số mục của tiêu
chuẩn viết bài trên wiki: [Wikicrop:Tiêu chuẩn viết bài](http://wikicrop.lab.io.vn/wikicrop/index.php/Wikicrop:Tiêu_chuẩn_viết_bài).

Mã nguồn, chú thích trong mã và hướng dẫn chi tiết của từng công cụ viết bằng tiếng Anh. Bản tiếng Anh của
tệp này là [README.en.md](README.en.md).

## Cài đặt

### Python và Node.js

- **Python 3.11 trở lên**: tải tại [python.org/downloads](https://www.python.org/downloads/). Trên Windows,
  chọn "Add python.exe to PATH" ở màn hình cài đặt đầu tiên, hoặc cài bằng lệnh
  `winget install Python.Python.3.12`. Kiểm tra bằng `python --version`.
- **Node.js** chỉ cần cho `sheet-varieties/mock/`. Nên cài qua trình quản lý phiên bản Node để đổi phiên bản
  dễ dàng:
  - Windows: [nvm-windows](https://github.com/coreybutler/nvm-windows/releases) (tải `nvm-setup.exe`), rồi
    `nvm install lts` và `nvm use lts`.
  - macOS, Linux: [nvm](https://github.com/nvm-sh/nvm#installing-and-updating), rồi `nvm install --lts`.

  Kiểm tra bằng `node --version`.

### Môi trường Python

Script cài đặt tạo môi trường ảo `.venv` bên cạnh thư mục `scripts/` và cài các gói
trong `scripts/requirements.txt` (openpyxl, Pillow, pypdf). Có thể chạy lại bất cứ lúc nào.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_env.ps1
.\.venv\Scripts\Activate.ps1
```

```bash
bash scripts/setup_env.sh
source .venv/bin/activate          # Git Bash trên Windows: source .venv/Scripts/activate
```

Tùy chọn: `-Video` / `--video` cài thêm faster-whisper cho công cụ ghi lời video, kèm `-Gpu` / `--gpu` để
cài thêm thư viện CUDA cho card đồ họa NVIDIA. Script cũng báo các chương trình tùy chọn tìm thấy trên máy:

| Chương trình | Dùng cho                                                                              |
|--------------|---------------------------------------------------------------------------------------|
| [LibreOffice](https://www.libreoffice.org/download/download-libreoffice/) | `document-export/odt_finish.py`, chạy bằng Python đi kèm LibreOffice (có sẵn `uno`)   |
| pdftotext    | `source-finder/find_sources.py`, để đọc nguồn dạng PDF. Thuộc Poppler: Windows [poppler-windows](https://github.com/oschwartz10612/poppler-windows/releases), macOS `brew install poppler`, Linux `apt install poppler-utils` |
| curl         | `fetch_live_sheet.py`, cách tải dự phòng. Có sẵn trên Windows 10 trở lên, macOS và hầu hết bản Linux |
| Node.js      | `sheet-varieties/mock/`, để chạy thử Apps Script trên máy. Cách cài ở trên            |

Các Apps Script (`.gs`) chạy ngay trong Google Sheet (Tiện ích mở rộng → Apps Script), không chạy trên máy.

## Thiết lập

Mọi đường dẫn và địa chỉ nằm trong `scripts/project_config.py`. Thiết lập chúng mà không cần sửa công cụ
nào, bằng một tệp thiết lập cục bộ hoặc bằng biến môi trường (biến môi trường được ưu tiên):

```bash
cp scripts/settings.example.toml scripts/settings.local.toml     # rồi điền vào
```

`settings.local.toml` chứa mã bảng tính và cách bố trí thư mục trên máy của bạn: không đưa tệp này vào quản
lý phiên bản hay bất cứ thứ gì chia sẻ ra ngoài. Các biến cần đặt trước:

| Biến                | Ý nghĩa                                                                                   |
|---------------------|-------------------------------------------------------------------------------------------|
| `WIKICROP_OPERATOR` | Tên tài khoản wiki của bạn. Được gửi trong User-Agent để quản trị viên wiki liên hệ được người chạy bot |
| `WIKICROP_SHEET_ID` | Mã Google Sheet của dự án, lấy từ địa chỉ của bảng tính. Cần cho `fetch_live_sheet.py`     |
| `WIKICROP_API`      | Địa chỉ API của MediaWiki, khi wiki đổi địa chỉ                                           |

Các biến còn lại (`WIKICROP_ROOT`, `_CATALOGUE`, `_SOURCE_BOOKS`, `_IMAGES`, `_LOGS`, `_CROPS`, `_CATEGORIES`)
chỉ nơi đặt dữ liệu trên máy, mặc định trong `data/`, và được mô tả trong `project_config.py`. Sơ đồ thể loại
mà `create_categories.py` tạo cũng là dữ liệu, nằm trong `scripts/categories.toml`. Khóa API của các công cụ
tìm kiếm được đọc từ biến môi trường như hướng dẫn của từng công cụ. Không lưu thông tin đăng nhập ở đâu cả:
mật khẩu wiki được hỏi khi chạy.

## Nguyên tắc an toàn của các công cụ sửa wiki

Mọi công cụ ghi lên wiki đều theo cùng các quy tắc:

- **Mặc định chỉ chạy thử.** Không ghi gì nếu không có `--run`. Chạy thử không cần đăng nhập và cho thấy
  những gì sẽ thay đổi. Ở công cụ có `--validate`, các trang đã sửa được hiển thị thử qua bộ phân tích của
  wiki trước.
- **Đăng nhập bằng BotPassword**, hỏi khi chạy và chỉ giữ trong bộ nhớ. Tạo tại `Special:BotPasswords` với
  đúng một quyền mà hướng dẫn của công cụ nêu.
- **Không mất sửa đổi.** Mỗi lần sửa mang theo mã phiên bản mà nó được tính từ đó (`baserevid`), nên nếu có
  người khác sửa trang trong lúc đó thì lần sửa bị dừng. `nocreate` hoặc `createonly` ngăn việc vô tình tạo
  mới hay ghi đè trang. `maxlag` và khoảng nghỉ cố định giữ tải thấp cho máy chủ dùng chung.
- **Nhật ký CSV cho từng trang**, và một script kiểm tra riêng, chỉ đọc, đối chiếu wiki thật với nhật ký sau
  khi chạy.

## Công cụ

Phần chú thích ở đầu mỗi tệp là hướng dẫn sử dụng của công cụ đó: chạy với `--help`, hoặc đọc phần đầu tệp.

**Trang wiki** (sửa hàng loạt, mỗi công cụ có công cụ kiểm tra đi kèm)

| Công cụ | Chức năng |
|---------|-----------|
| `create_categories.py`, `validate_categories.py`, `audit_categories.py` | Tạo sơ đồ thể loại, kiểm tra trước và sau khi tạo |
| `reformat_infoboxes.py`, `audit_infobox_reformat.py` | Viết lời gọi hộp thông tin thành mỗi tham số một dòng |
| `retrofit_pass.py`, `audit_retrofit_pass.py` | Một lượt gồm mọi sửa chữa hình thức một trang cần: bố cục và vị trí hộp thông tin, phân loại học, dấu gạch, ký tự mã hóa, liên kết, thể loại |
| `convert_citations.py`, `audit_citations.py` | Chuyển trích dẫn web sang `{{ChuthichWeb}}` và `<ref>` có tên |
| `fetch_crop_pages.py`, `audit_crop_pages.py` | Sao chép các trang của một cây trồng từ wiki (chỉ đọc), và kiểm tra chúng theo các quy tắc hình thức của tiêu chuẩn |

**Hình ảnh giống**

| Công cụ | Chức năng |
|---------|-----------|
| `export_variety_images.py` | Xuất các hình nhúng trong bảng tính nguồn, đặt tên theo tên trong danh mục |
| `find_image_sources.py` | Tìm trang web gốc của từng hình từ nguồn ghi ở dòng đó |
| `reverse_search_images.py` | Tìm ngược hình ảnh (Google Cloud Vision) cho các hình còn lại |
| `sheet-images/fetch_commons_images.py` | Tìm hình cho các dòng chưa có: Wikimedia Commons trước, sau đó các trang web được trích dẫn |
| `upload_variety_images.py` | Tải hình lên wiki, không ghi đè, không trùng lặp, có ghi nguồn |
| `sheet-images/insert_images.gs` | Apps Script: đưa hình vào cột hình ảnh của bảng tính |

**Google Sheet**

| Công cụ | Chức năng |
|---------|-----------|
| `fetch_live_sheet.py` | Tải bảng tính đang dùng về dạng .xlsx, kiểm tra trước khi thay bản trên máy |
| `sheet-common/sheet_config.gs` | Apps Script: bố cục bảng tính và các công tắc chạy, dùng chung cho các script sửa bảng tính |
| `sheet-varieties/build_table3_block.py` | Kiểm tra một đợt nội dung soạn sẵn với bản chụp của bảng tính, rồi viết tệp duyệt và Apps Script để tải lên. Đợt mẫu: `table3_block_example_drafts.py` |
| `sheet-varieties/mock/` | Chạy thử một Apps Script trên bản sao bảng tính trên máy trước khi chạy trên bảng tính thật |

**Nguồn và tài liệu**

| Công cụ | Chức năng |
|---------|-----------|
| `source-finder/find_sources.py` | Thu thập, tải và xếp hạng các nguồn ứng viên cho các dòng chưa có nguồn, để người duyệt |
| `video-transcript/transcribe_video.py` | Bản ghi lời có mốc thời gian của một chương trình đã phát, dùng riêng cho nghiên cứu (xem README của công cụ) |
| `document-export/` | Chuyển Markdown sang ODT và PDF, và sang wikitext cho trang wiki (xem README của công cụ) |
