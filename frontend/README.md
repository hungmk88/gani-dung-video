# GANI FOODS AI Studio — frontend (GĐ6 UI prototype)

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
  types/rules.ts        kiểu dữ liệu khớp domain/options.py, preflight.py, verify.py, qc.py
  data/rules.ts          dữ liệu THẬT copy từ config/rules.json (không bịa)
  data/mockMusic.ts       4 track BPM thật (xem trên)
  data/mockPlan.ts        1 bản plan.json thật (Xuong GANI/edit/plan.json, 29/09/2026)
  lib/preflight.ts        preflight phía trình duyệt — mirror domain/preflight.py (X1–X18)
  services/config.ts      công tắc USE_MOCK — chỗ duy nhất cần đổi khi GĐ6 có backend thật
  services/api.ts         interface StudioApi — component chỉ import từ đây
  services/mockApi.ts      mô phỏng daemon SQLite Queue (setTimeout, không phải WS thật)
  components/wizard/       FootageDropzone, RuleSelector, MusicPicker, PreflightValidator
  components/queue/        PipelineStepper, VideoPlayerWithQA, ConsoleLog, PlanModal
```

Khi GĐ6 được duyệt và có daemon thật: implement `realApi` trong `services/api.ts`, đổi
`USE_MOCK` (hoặc `VITE_USE_MOCK=false`) trong `services/config.ts`. Không component nào
cần sửa vì tất cả chỉ gọi `api` từ `services/api.ts`.
