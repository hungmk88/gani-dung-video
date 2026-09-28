# Kế hoạch viết lại app Dựng Video (v2)

Nguồn chuẩn: `DEV_PACKAGE_00_v2.1_SPEC.md` (Đặc tả v2.1). Viết mới hoàn toàn, không dùng code dung-video v0.6.2.

## Quyết định đã chốt (28/09/2026)

| # | Quyết định | Hệ quả kỹ thuật |
|---|---|---|
| 1 | Tùy chọn theo **đặc tả v2.1** (không theo brief/Master Guide) | 6 phong cách · 4 mục đích · 15/30/45/60/180s · 1–3 bản · 4 chế độ nhạc |
| 2 | Tên thương hiệu trên phụ đề: **GANI** | glossary.json ánh xạ các cách nghe nhầm → "GANI" |
| 3 | Không đưa tên đối thủ vào glossary | chỉ có `banned_claims` |
| 4 | Từ cấm V7 lọc theo **cụm y tế**, không theo từ đơn | tránh bắt nhầm "giá trị", "quản trị" |
| 5 | Bảng 5.3.A giữ **TVC 15s = 11 cảnh** | số cảnh đọc từ bảng, không tính |
| 6 | **Đa nền tảng**, chạy chính Mac Mini | không dùng Apple Vision; đo nét/rung bằng OpenCV |
| 7 | Gọi **Claude API bằng API key** | key nạp từ `.env` qua pydantic-settings; đo chi phí từng video |
| 8 | **Dòng lệnh trước**, web sau | giai đoạn 1–5 là CLI; giao diện + hàng đợi SQLite ở giai đoạn 6 |

## Kiến trúc

```
src/gani_dung_video/
  domain/          thuần Python, không I/O: tùy chọn, quy tắc, preflight, biên dịch plan, căn phách, glossary, verify V1–V10
  application/     điều phối pipeline: kiểm kê → nhận dạng → preflight → chọn cảnh → plan → dựng → soát
  infrastructure/  ffmpeg/ffprobe, faster-whisper, OpenCV, CLIP, Claude API, SQLite, đọc/ghi file
config/            rules.json, glossary.json (sửa số ở đây, không sửa code)
```

## Giai đoạn

| GĐ | Nội dung | Đầu ra kiểm được | Cần gì từ anh |
|---|---|---|---|
| **1** | Lớp quy tắc: rules.json, glossary.json, preflight X1–X15, đọc `yeu-cau.md` (Phụ lục D), lọc từ cấm V7, CLI `preflight` | 18 file mẫu xung đột + quét 1.440 tổ hợp | — |
| 2 | Kiểm kê footage: ffprobe, lọc file trùng (" 2"), phát hiện HDR, cờ "không lời" | `inventory.json` trên footage thật | 1 thư mục footage mẫu (vd "Xuong VL") |
| 3 | Nhận dạng: faster-whisper (lời + mốc từng từ), tách cảnh, đo nét/rung OpenCV, CLIP nhãn cảnh + điểm thẩm mỹ | `scenes.json` có nhãn | Chạy thử trên Mac Mini (laptop Dell quá chậm cho CLIP ViT-L/14) |
| 4 | Biên dịch plan: tùy chọn → khối/số phách/driver (Mục 7.2), gọi Claude API chọn cảnh + viết chữ, căn phách + lùi đỉnh (7.3) | `plan.json` + chi phí/lần gọi | API key trong `.env` |
| 5 | Dựng + soát: render nháp 540p / bản cuối 1080×1920, phụ đề ASS, brand kit, verify V1–V10 tối đa 3 vòng | chạy 18 ca không phải 3 phút (cổng T4) | Logo, số Zalo, thư viện nhạc ≥ 24 bài |
| 6 | Hàng đợi SQLite + daemon + web 127.0.0.1:8765 | dựng 3 bản không chặn giao diện | — |
| 7 | 3 phút: chia chương, nhạc dài, nhiều đoạn/clip | ca T03, T09, T18, T22 | — |

## Chuẩn mã

Python ≥ 3.11 · ruff (line 100) · mypy --strict · pytest · Pydantic cho mọi input ngoài · không hardcode bí mật · chú thích tiếng Anh, thông báo cho người dùng tiếng Việt.
