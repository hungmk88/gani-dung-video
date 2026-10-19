# GANI FOODS AI Studio — frontend

Bảng điều khiển (Studio Wizard + Job Queue) cho pipeline dựng video tự động
`gani-dung-video`. Giao diện và logic Preflight Validator chạy trên trình duyệt, bám vào
`config/rules.json`/`domain/preflight.py` của repo Python. Có hai bản:

- **Bản thật** (`npm run build:real`, chạy từ `serve`): nói chuyện với hàng đợi và daemon ở
  `127.0.0.1:8765` (xem `docs/api-jobs.md`).
- **Bản mô phỏng** (`npm run dev`, `npm run build`): `src/services/mockApi.ts` giả lập hàng đợi để
  xem thử giao diện mà không cần máy dựng, gồm cả duyệt chi phí, duyệt video cuối, tab "Bản A ·
  TikTok / Bản A · Reels" theo nền tảng chọn ở bước "Đăng lên" (mỗi bản duyệt riêng) và ô "Không
  nhúng nhạc". Mọi bản dùng chung một clip mẫu; số đo và cảnh báo trong khung soát chất lượng là
  ví dụ, không phải đo thật.

## Chạy thử

```bash
npm install
npm run dev
```

## Chạy từ `serve` (không cần Vite)

```bash
npm run build:real                # bản nối API thật (`npm run build` là bản mô phỏng để xem thử)
python -m gani_dung_video serve   # rồi mở http://127.0.0.1:8765/
```

## Đồng bộ tài nguyên demo

`public/audio/` và `public/preview/` không nằm trong git (xem `.gitignore`) — đó là file
thật, không phải sinh ra, được copy từ chỗ khác trong repo lớn hơn. Sau khi clone, chạy:

```bash
# 4 track test BPM thật (tổng hợp bằng ffmpeg, không vướng bản quyền — xem
# "Nhac test BPM/DOC-TRUOC.txt"), dùng cho nút nghe thử trong MusicPicker.
cp "../../Nhac test BPM/"*.m4a public/audio/

# Bản render 540p thật của lần chạy GĐ5b (29/09/2026), dùng cho VideoPlayerWithQA.
cp "../../Xuong GANI/edit/render/A/nhap_A_540p.mp4" public/preview/
```

Không có 2 bước trên thì `dev`/`build` vẫn chạy bình thường — Studio Wizard vẫn dùng được,
chỉ là nút nghe thử nhạc và khung xem preview không có gì để phát.

## Cấu trúc

```
src/
  App.tsx, main.tsx         hai tab "Tạo video mới" (wizard) và "Video đang làm" (queue)
  types/api.ts              kiểu dữ liệu trên dây, chép tay từ domain/api_models.py
  types/rules.ts            kiểu dữ liệu của giao diện (tùy chọn, preflight, job, QC)
  data/rules.ts             bản sao config/rules.json, glossary.json cho wizard và bản mô
                            phỏng (rules.test.ts so với file gốc)
  data/bpmSafe.ts           bản sao music.bpm_safe_by_style_duration (test so với rules.json)
  data/mockMusic.ts         4 track BPM thật cho bản mô phỏng
  data/mockPlan.ts          1 bản plan.json thật cho bản mô phỏng
  lib/                      hàm thuần, test bằng `node --test` (duyệt chi phí, BPM, nhạc,
                            nền tảng, duyệt video cuối, chương 3 phút, bảng V/S, polling)
  lib/preflight.ts          preflight của bản mô phỏng: chỉ các mã chỉ phụ thuộc tùy chọn
                            (X1–X4, X9–X11, X14, X15, ma trận tương thích; X5/X6 ước tính);
                            bản thật gọi POST /api/preflight
  services/config.ts        công tắc USE_MOCK (VITE_USE_MOCK)
  services/api.ts           interface StudioApi — component chỉ import `api` từ đây
  services/realApi.ts       StudioApi qua API cục bộ (polling, docs/api-jobs.md)
  services/http.ts          gọi JSON theo đường dẫn tương đối, lỗi {"error": …} → ApiError
  services/mappers.ts       đổi kiểu trên dây ↔ kiểu giao diện (docs/api-jobs.md §5)
  services/mockApi.ts       mô phỏng hàng đợi trong trình duyệt (setTimeout)
  components/layout/        Header: ô "Máy dựng", "Thư viện nhạc", hai tab
  components/wizard/        chọn dự án, tùy chọn, nhạc, nền tảng, tên file giao, Preflight
  components/queue/         danh sách job, duyệt chi phí, video và QC, duyệt video cuối
```

Bản thật hay mô phỏng chọn lúc build: `npm run dev` / `npm run build` là mô phỏng;
`npm run dev:real` / `npm run build:real` đọc `.env.real` (`VITE_USE_MOCK=false`) và dùng
`realApi`. Khi chạy `dev:real`, `vite.config.ts` chuyển `/api` và `/media` tới
`127.0.0.1:8765`; bản build được `serve` phục vụ cùng gốc nên gọi được ở cổng nào cũng vậy.

Kiểm tra: `npm test`, `npx tsc -b`, `npm run lint`, `npm run build:real`.
