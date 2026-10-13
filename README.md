# gani-dung-video

App dựng video ngắn cho GANI: từ thư mục footage thô ra video dọc 1080×1920 (15, 30, 45, 60
giây và 3 phút ở chế độ nhạc) có nhạc theo nhịp, chữ, phụ đề và bản soát kỹ thuật tự động. Claude
chỉ chọn cảnh và viết chữ trong khuôn số phách do code tính sẵn; mọi phép kiểm và mọi khoản chi
phí đều do người dùng duyệt trên giao diện.

- Python 3.11+, ffmpeg, SQLite (hàng đợi), React/Vite (giao diện, chạy ở `127.0.0.1:8765`).
- Mọi số chỉnh được nằm ở `config/rules.json`, không sửa trong code.

## Chạy nhanh

```bash
set PYTHONPATH=src                 # cmd; PowerShell: $env:PYTHONPATH = "src"; Git Bash: export PYTHONPATH=src
copy .env.example .env             # Windows; điền ANTHROPIC_API_KEY, GANI_DV_ALLOWED_ROOTS, ...
cd frontend && npm ci && npm run build:real && cd ..
python -m gani_dung_video serve    # cửa sổ 1: giao diện + API
python -m gani_dung_video daemon   # cửa sổ 2: người làm việc
```

Chạy từ thư mục gốc dự án (`PYTHONPATH=src` là đường dẫn tương đối, chỉ có hiệu lực trong cửa sổ lệnh
đã đặt). Chưa đặt thì lệnh báo `No module named gani_dung_video`.

- **Cách A, khuyến nghị: `PYTHONPATH=src`** như trên. Luôn đúng với thư mục đang đứng, an toàn khi
  có nhiều bản làm việc (worktree) song song.
- **Cách B: `pip install -e .`**, chỉ khi máy chỉ có **một** bản mã duy nhất. Gói cài kiểu editable
  ghi cứng đường dẫn của một thư mục, nên các worktree khác sẽ chạy **nhầm mã** của thư mục đó mà
  không báo lỗi. Lỡ cài thì gỡ bằng `pip uninstall gani-dung-video`.

Trên Windows có thể nhấp đúp `scripts\chay-gani.bat` (hoặc lối tắt "GANI Video" trên Desktop) thay cho hai lệnh trên;
tệp này tự đặt `PYTHONPATH=src`.

Mở `http://127.0.0.1:8765/`. Từng bước, chi phí, xử lý sự cố: [`docs/VAN-HANH.md`](docs/VAN-HANH.md).

## Tài liệu

| | |
|---|---|
| [`docs/VAN-HANH.md`](docs/VAN-HANH.md) | sổ tay vận hành hằng ngày |
| [`docs/api-jobs.md`](docs/api-jobs.md) | hợp đồng API (job, pipeline, duyệt chi phí, `footage`, `verify`, `structure`, QC T1–T8) |
| [`docs/KE-HOACH.md`](docs/KE-HOACH.md) | các giai đoạn và quyết định đã chốt |
| [`docs/GD7-THIET-KE.md`](docs/GD7-THIET-KE.md) | thiết kế và số đo video 3 phút |

## Phát triển

```bash
pip install -e ".[dev]"   # lấy công cụ kiểm (ruff, mypy, pytest); máy nhiều worktree: xem cảnh báo cách B ở trên
ruff check . && ruff format --check . && mypy --strict src tests && pytest
cd frontend && npm test && npx tsc -b
```
