# API web cục bộ — hợp đồng kỹ thuật (Giai đoạn 6b + 6c)

Tài liệu này là **hợp đồng** giữa backend (`gani-dung-video`) và giao diện (nhánh
`feature/gd6-frontend-preview`). Backend không sửa gì ở nhánh giao diện; phía giao diện viết
`realApi` trong `src/services/api.ts` theo tài liệu này, rồi đổi `USE_MOCK`.

Mọi ví dụ JSON bên dưới được **in ra từ chính mã nguồn** (không viết tay).

## 1. Chạy

```bash
python -m gani_dung_video daemon      # cửa sổ 1: dựng nền, lấy job từ hàng đợi
python -m gani_dung_video serve       # cửa sổ 2: API cho giao diện, http://127.0.0.1:8765
```

Hai tiến trình **độc lập**, dùng chung `queue.db` (SQLite WAL): `serve` sập không làm gián
đoạn việc dựng, `daemon` khởi động lại không làm mất bảng điều khiển. Trước khi dùng cần
`.env` có `GANI_DV_ALLOWED_ROOTS` (thư mục chứa các dự án footage). Cổng và danh sách Origin
nằm ở `config/rules.json` → `api`.

### Giao diện chạy thẳng từ `serve` (Giai đoạn 6g)

Không cần bật Vite: build giao diện một lần rồi mở địa chỉ của `serve`. Dùng `build:real`: `npm run build` mặc định là bản mô phỏng (mock) để xem thử không cần backend.

```bash
cd frontend && npm run build:real     # tạo frontend/dist, nối API thật (không phải bản mô phỏng)
python -m gani_dung_video serve       # in "Giao diện: mở http://127.0.0.1:8765/"
```

- `serve` phục vụ `rules.api.frontend_dist` (mặc định `frontend/dist`, tính từ **gốc dự án**, không
  phụ thuộc thư mục đang đứng; rỗng hoặc `null` là tắt) cho mọi `GET`/`HEAD` **không** thuộc
  `/api/...` hay `/media/...`. Hai nhóm đó không bao giờ trả về tệp giao diện: `/api/khong-co`
  vẫn là 404 JSON.
- Đường dẫn giao diện không có đuôi tệp (`/studio`, `/queue/12`) trả `index.html` để tải lại
  trang hoặc gõ tay địa chỉ vẫn chạy. Tệp thiếu có đuôi (`/assets/x.js`) là 404 thật.
- Chưa build thì `serve` vẫn chạy bình thường, ghi cảnh báo một lần, `/` trả 404 JSON như cũ.
  Build xong **không cần khởi động lại** `serve`.
- Giao diện gọi API bằng đường dẫn tương đối (`/api/...`, `/media/...`) nên cùng nguồn gốc
  (origin) với `serve`; mở bằng `127.0.0.1:8765` hoặc `localhost:8765` đều được.
- Khi phát triển giao diện (`npm run dev`, cổng 5173): Vite vẫn proxy `/api` và `/media` sang
  `serve`, nhưng `serve` **không còn tin cổng 5173 mặc định**. Đặt
  `GANI_DV_DEV_ORIGINS=http://localhost:5173,http://127.0.0.1:5173` trong `.env` của máy lập
  trình rồi chạy lại `serve`. Máy dựng thật không cần biến này (giao diện build cùng nguồn gốc).

## 2. Quy ước chung

- Chỉ nghe ở **127.0.0.1**. Mọi yêu cầu phải có `Host: 127.0.0.1:8765` hoặc
  `localhost:8765`, nếu không trả 403.
- Dữ liệu là JSON UTF-8. Thời gian là ISO 8601 UTC, đuôi `Z` (`2026-09-29T04:56:08Z`).
- Mã dự án (`project_id`) là chuỗi **mờ** `"<chỉ số thư mục gốc>/<tên thư mục>"`, ví dụ
  `"0/Xuong GANI"` (`"0/."` nếu chính thư mục gốc là dự án). Server không bao giờ trả đường
  dẫn tuyệt đối. Giao diện lấy mã từ `GET /api/projects`, không tự ghép.
- **Không có tải footage lên qua HTTP.** Footage nằm sẵn trên máy dựng, trong một thư mục
  thuộc `GANI_DV_ALLOWED_ROOTS`.
- Mỗi job có `kind`: `render` (một bản nháp ở một kích thước, `preview` 540p hoặc `final`
  1080×1920), `analyze` (phân tích footage) hoặc `draft` (kịch bản, có bước duyệt chi phí). Server
  không gộp; giao diện tự gộp theo `(project_id, draft)` nếu muốn, hoặc theo `pipeline_id` (mục 4b).

### Lỗi

Luôn có dạng `{"error": {"code": "...", "message": "tiếng Việt"}}`, không kèm đường dẫn hay
stack trace. Một số lỗi có thêm trường trong `error` (`pipeline_id`, `job_id` ở `pipeline_active`).

| HTTP | `code` | Khi nào |
|---|---|---|
| 400 | `bad_request` | Thân/tham số sai (`draft` không phải A/B/C/all, `limit` ngoài 1–200, `project_id` sai dạng, JSON hỏng hoặc lồng quá sâu, trường thừa) |
| 403 | `host_khong_hop_le` / `origin_khong_hop_le` / `yeu_cau_cheo_trang` | Sai Host, Origin không nằm trong danh sách, yêu cầu chéo trang không có Origin/Referer tin cậy |
| 404 | `not_found` | Không có dự án / job / tệp / đường dẫn; job hoặc pipeline có thư mục nằm ngoài `GANI_DV_ALLOWED_ROOTS` cũng là 404 (mọi route `/api/jobs/{id}…`, `/media/{id}/…`; `GET /api/jobs` bỏ hẳn các job đó khỏi danh sách) |
| 405 | `method_not_allowed` | Sai phương thức (kèm header `Allow`) |
| 409 | `not_ready` | Dự án chưa có `plan.json` hoặc `drafts.json`/`speech_drafts.json` — chạy `plan`/`pick`/`draft` trước |
| 409 | `conflict` | Thử lại một job đang chờ/đang chạy/đã đạt, hoặc job có thư mục dự án không còn; duyệt chi phí một job không phải `draft` |
| 409 | `pipeline_active` | Dự án đã có job đang chờ/chạy/chờ duyệt (kèm `pipeline_id`, `job_id`) |
| 409 | `project_locked` | Thư mục dự án đang được **một hàng đợi khác** (`queue.db` khác) dùng (kèm `pipeline_id`, `job_id` của hàng đợi đó nếu biết) |
| 409 | `approval_refused` | Duyệt chi phí bị từ chối (lệch ước tính, hết hạn, quá hạn mức…) |
| 411 | `can_content_length` | Thân dùng `Transfer-Encoding` (không hỗ trợ) |
| 413 | `than_qua_lon` | Thân > `api.max_body_bytes` (64KB) |
| 415 | `content_type_no` | `Content-Type` có mặt nhưng không phải `application/json`, hoặc có thân mà thiếu `Content-Type` |
| 500 | `loi_may_chu` | Lỗi không lường trước (chi tiết chỉ nằm ở log của `serve`) |
| 503 | `unavailable` | Máy dựng chưa đặt `GANI_DV_ALLOWED_ROOTS` |

## 3. Endpoint

| Phương thức + đường dẫn | Việc | Trả về |
|---|---|---|
| `GET /api/health` | Daemon online không (nhịp tim < 15 giây), số job theo trạng thái, có khóa API không, ngân sách ngày | `HealthView` |
| `GET /api/projects` | Dự án trong allow-list và đã có gì | `ProjectView[]` |
| `POST /api/preflight` | Chạy đúng `preflight_check` của Python (nguồn sự thật duy nhất) | Kết quả preflight (`valid`, `errors`, `warnings`, `adjustments`, …) |
| `POST /api/jobs` | Đưa bản dựng vào hàng đợi | `201` + `JobView[]` |
| `POST /api/pipelines` | Ghi `options.json` + tạo chuỗi `analyze → draft → render` (mục 4b) | `201` + `PipelineView` |
| `GET /api/pipelines/{id}` | Các job của một pipeline và trạng thái chung | `PipelineView` |
| `POST /api/jobs/{id}/approve` | Duyệt chi phí gọi Claude của job `draft` đang chờ | `JobView` |
| `GET /api/jobs?status=&project_id=&kind=&limit=` | Danh sách mọi loại job, mới nhất trước (`limit` 1–200, mặc định 50; `kind` = `render`\|`draft`\|`analyze`, mặc định không lọc) | `JobView[]` |
| `GET /api/jobs/{id}` | Chi tiết kèm 200 dòng console cuối | `JobDetail` |
| `GET /api/jobs/{id}/events?after=N` | Dòng console có `id > N` (con trỏ) | `EventView[]` |
| `POST /api/jobs/{id}/cancel` | Chờ thì hủy ngay; đang chạy thì gửi cờ dừng | `JobView` |
| `POST /api/jobs/{id}/retry` | Xếp lại job lỗi / đã hủy / QC không đạt | `JobView` |
| `GET\|HEAD /media/{id}/{video\|cover\|srt}` | Thành phẩm của job đã xong, hỗ trợ HTTP **Range** | tệp |
| `GET /api/music` | Nhạc trong `GANI_DV_MUSIC_DIR` kèm BPM, độ dài, nguồn BPM (mục 3b) | `MusicListView` |
| `GET\|HEAD /media/music/{tên file}` | Nghe thử một bài nhạc, hỗ trợ HTTP **Range** | tệp âm thanh |

### 3b. Nhạc của máy dựng (`GET /api/music`)

```json
{
  "configured": true,
  "pending": 1,
  "tracks": [
    {"file": "am_ap_92.mp3", "path": "am_ap_92.mp3", "title": "Ấm áp", "bpm": 92.0,
     "bpm_source": "library", "first_beat_sec": 0.35, "duration_sec": 187.4,
     "rights": "thu_vien_noi_bo"},
    {"file": "moi.wav", "path": "moi.wav", "title": "moi", "bpm": null,
     "bpm_source": "pending", "first_beat_sec": null, "duration_sec": null,
     "rights": "thu_vien_noi_bo"}
  ]
}
```

- `file` là **tên file** (không đường dẫn tuyệt đối); `path` là tên đó so với thư mục nhạc, vì
  thư mục **không** được duyệt đệ quy. Tối đa 500 bài; bỏ file ẩn và file sai đuôi
  (`rules.music_analysis.extensions`).
- `bpm_source`: `library` (khai báo trong `library.json`, người đã duyệt), `measured` (máy đo),
  `unknown` (không có nhịp rõ: vẫn chọn được, dựng bằng lưới ảo), `pending` (nền chưa đo tới).
- Danh sách trả về **ngay**. Bài chưa có trong `library.json` được đo bởi **một luồng nền, lần lượt
  từng bài**; kết quả lưu ở `music-cache.json` cạnh `queue.db`, khóa theo (kích thước, mtime), nên
  file đổi nội dung thì đo lại. Giao diện hỏi lại vài giây một lần khi `pending > 0`.
- `title` và `rights` lấy từ `library.json` (khóa tùy chọn; mặc định tên file không đuôi và
  `thu_vien_noi_bo`). `library.json` cũ không có hai khóa này vẫn đọc được, nhưng bản chương
  trình cũ **từ chối** file có khóa mới: cập nhật máy dựng cùng lúc.
- Chưa đặt `GANI_DV_MUSIC_DIR` hoặc thư mục không có: `{"configured": false, "pending": 0,
  "tracks": []}` (không phải lỗi). `library.json` hỏng: `422` `music_library_invalid`, tiếng Việt.
- `GET /media/music/{tên file}`: chỉ file âm thanh nằm **trực tiếp** trong thư mục nhạc. Tên có
  dấu phân cách, bắt đầu bằng `.`, sai đuôi, không có, hoặc là liên kết ra ngoài thư mục đều là
  `404` như nhau. Cùng quy tắc Host / Origin với các route khác.

`POST /api/jobs` nhận:

```json
{"project_id": "0/Xuong GANI", "draft": "all", "final": false, "no_asr": false}
```

`draft`: `"A"`, `"B"`, `"C"` hoặc `"all"` (mặc định, mọi bản kế hoạch có). Hỏi lại đúng bản
đang chờ hoặc đang chạy thì trả về job cũ, không xếp trùng. Tùy chọn (phong cách, độ dài…) lấy
từ `plan.json` của dự án, không gửi lại.

## 4. Ví dụ (in từ mã nguồn)

`GET /api/projects`

```json
[
  {
    "id": "0/Xuong GANI",
    "name": "Xuong GANI",
    "has_inventory": true,
    "has_plan": true,
    "has_drafts": true,
    "ready_to_render": true
  }
]
```

`GET /api/health`

```json
{
  "ok": true,
  "rules_version": "2.0",
  "daemon_online": false,
  "daemon_pid": null,
  "daemon_stopping": null,
  "jobs": {"queued": 0, "processing": 0, "awaiting_approval": 0, "completed": 1, "failed": 0,
           "cancelled": 0},
  "allowed_roots_configured": true,
  "ai_key_configured": false,
  "budget": {"limit_vnd": null, "spent_24h_vnd": 0.0, "reserved_vnd": 0.0},
  "features": {"long_video": false}
}
```

`features.long_video`: video 3 phút đã mở chưa (`rules.durations.180.enabled`; bật ở pha 7e sau khi
nghiệm thu thật). Giao diện khóa nút 3 phút khi là `false`; máy chủ cũ không có trường này.

`GET /api/jobs/1` cho một job đã xong (rút gọn `steps`):

```json
{
  "id": 1,
  "project_id": "0/Xuong GANI",
  "project_name": "Xuong GANI",
  "title": "Xuong GANI — bản A (nháp)",
  "kind": "render",
  "draft": "A",
  "size": "preview",
  "status": "completed",
  "result": "passed",
  "frontend_status": "done",
  "attempts": 1,
  "max_attempts": 2,
  "progress_pct": 100.0,
  "stage": "khoi_dong",
  "eta_sec": null,
  "created_at": "2026-09-29T04:56:08Z",
  "started_at": "2026-09-29T04:56:08Z",
  "finished_at": "2026-09-29T04:56:08Z",
  "error_msg": null,
  "no_asr": false,
  "options": {"style": "ke_chuyen_nghe", "purpose": "gioi_thieu_xuong", "duration": 15,
              "drafts": 2, "music": "khong_nhac"},
  "video_url": "/media/1/video",
  "cover_url": "/media/1/cover",
  "srt_url": "/media/1/srt",
  "steps": [
    {"id": "inventory", "label": "Inventory (kiểm kê footage)", "status": "done", "detail": null},
    {"id": "render_preview", "label": "Dựng bản xem trước (540p)", "status": "done", "detail": null},
    {"id": "qc_verify", "label": "Soát kỹ thuật (T1–T7)", "status": "done", "detail": null},
    {"id": "final_export", "label": "Xuất bản cuối (1080×1920)", "status": "queued", "detail": null}
  ],
  "qa": {
    "passed": true, "rounds": 1, "fixes": [], "loudness_two_pass": false,
    "lufs": -13.9, "true_peak_db": -2.0, "t8": "skipped_draft",
    "checks": [
      {"code": "T3", "label": "Độ to và đỉnh âm thanh", "passed": true,
       "detail": "Đo được -13.9 LUFS, đỉnh thật -2.0 dBTP."}
    ]
  },
  "events": [
    {"id": 1, "ts": "2026-09-29T04:56:08Z", "level": "info", "text": "Bắt đầu dựng bản A (lần 1/2)."}
  ]
}
```

`stage` là tên nội bộ: `khoi_dong` (vừa nhận job), `dung` (đang dựng). `progress_pct` chỉ là
**ước tính** theo thời gian dự kiến (3–95%), chỉ thành 100 khi job xong thật.
`options` là `plan.json → options` nguyên văn (khóa tiếng Anh: `style`, `purpose`,
`duration`, `drafts`, `music`).

## 4b. Pipeline, duyệt chi phí và các loại job (Giai đoạn 6c)

Từ 6c, API trả **mọi loại job** (`kind`): `analyze` (phân tích footage: kiểm kê, Whisper, cắt
cảnh, CLIP), `draft` (kịch bản: `plan` → `pick` → ước tính chi phí → **chờ duyệt** → gọi Claude)
và `render` (như cũ). Một lần bấm "Chạy" của Wizard tạo cả chuỗi bằng `POST /api/pipelines`.

### Luồng Wizard

1. `POST /api/preflight` với các tùy chọn (như cũ) — hiện lỗi/cảnh báo ngay trên form.
2. `POST /api/pipelines` → server chạy lại preflight (không tin client), ghi
   `<dự án>/edit/options.json` (nguyên tử: ghi tệp tạm rồi đổi tên) và tạo **một chuỗi job trong
   một giao dịch**: `analyze → draft → render` (mỗi bản A/B/C một job `render`, tất cả chờ job
   `draft`). Không có gì được ghi nếu preflight chặn hoặc dự án đang bận.
3. Polling `GET /api/pipelines/{id}` (hoặc `GET /api/jobs?kind=…`). Daemon chạy tuần tự:
   phân tích (có thể vài chục phút trên máy yếu; nếu footage không đổi thì dùng lại kết quả)
   → `draft` pha miễn phí (plan, pick, **ước tính chi phí**).
4. Job `draft` dừng ở `status = "awaiting_approval"` (`frontend_status` cũng là
   `"awaiting_approval"`, `needs_approval = true`). Giao diện mở hộp thoại duyệt chi phí, hiện
   `cost.worst_call_vnd` (một lần gọi tối đa), `cost.cap_vnd` (trần cho độ dài video này) và
   `approval_expires_at` (hết hạn duyệt, mặc định 24 giờ).
5. `POST /api/jobs/{id}/approve` với `{"max_vnd", "estimate_vnd"}`. `estimate_vnd` phải **lặp
   lại đúng ước tính người dùng vừa thấy** (làm tròn đồng); lệch thì bị từ chối (409) — nghĩa là
   ước tính đã đổi hoặc nhập sai. Daemon còn **tính lại ước tính** trước khi gọi Claude: đổi số
   thì job quay về `awaiting_approval` với số mới và **không tốn đồng nào**.
6. Sau khi duyệt và gọi xong, các job `render` tự chạy.

Không duyệt trong hạn → job `draft` tự hủy (`error_msg` "Hết hạn duyệt chi phí"), các job
`render` chờ nó bị hủy theo. Máy chưa có `ANTHROPIC_API_KEY` → job `draft` báo lỗi **trước khi
gọi**, chi phí 0đ; `GET /api/health` cho biết qua `ai_key_configured` (chỉ có/không, không bao
giờ trả giá trị khóa).

### `POST /api/pipelines`

```json
{
  "project_id": "0/Xuong GANI",
  "options": {
    "phong_cach": "ke_chuyen_nghe",
    "muc_dich": "gioi_thieu_xuong",
    "do_dai_giay": 15,
    "so_ban_nhap": 2,
    "nhac": "khong_nhac"
  },
  "draft": "all",
  "final": false,
  "no_asr": false,
  "force_analyze": false
}
```

- `options`: đúng khuôn của `POST /api/preflight` (khóa tiếng Việt như `yeu-cau.md`).
- `draft`: `"A"`, `"B"`, `"C"` hoặc `"all"` — chỉ định bản nào được dựng; số bản kịch bản
  luôn theo `so_ban_nhap`. Bản không có trong `options` → 400.
- `final`, `no_asr`: như `POST /api/jobs`. `force_analyze`: phân tích lại dù footage không đổi.
- Trả `201` + `PipelineView` (rút gọn):

```json
{
  "id": 1,
  "project_id": "0/Xuong GANI",
  "project_name": "Xuong GANI",
  "status": "queued",
  "needs_approval": false,
  "jobs": [
    {"id": 1, "pipeline_id": 1, "depends_on": null, "kind": "analyze", "draft": "",
     "status": "queued", "frontend_status": "queued", "needs_approval": false,
     "approval_expires_at": null, "cost": null},
    {"id": 2, "pipeline_id": 1, "depends_on": 1, "kind": "draft", "draft": "",
     "status": "queued", "frontend_status": "queued", "needs_approval": false,
     "approval_expires_at": null, "cost": null},
    {"id": 3, "pipeline_id": 1, "depends_on": 2, "kind": "render", "draft": "A",
     "status": "queued", "frontend_status": "queued", "needs_approval": false,
     "approval_expires_at": null, "cost": null},
    {"id": 4, "pipeline_id": 1, "depends_on": 2, "kind": "render", "draft": "B",
     "status": "queued", "frontend_status": "queued", "needs_approval": false,
     "approval_expires_at": null, "cost": null}
  ]
}
```

`PipelineView.status` (từ mọi job của chuỗi, theo từ vựng của giao diện): `error` nếu có job
lỗi/hủy → `awaiting_approval` nếu đang chờ duyệt → `running` (đang chạy, hoặc đang ở giữa hai job) →
`queued` (chưa job nào chạy) → `qa_review` (đã xong nhưng có bản QC chưa đạt) → `done`.

**Một dự án chỉ có một pipeline hoạt động.** Nếu dự án đã có job đang chờ, đang chạy hoặc chờ
duyệt (bất kể loại), server trả **409** và **không ghi gì** (kể cả `options.json`):

```json
{
  "error": {
    "code": "pipeline_active",
    "message": "Dự án 'Xuong GANI' đang có job #1 chạy hoặc chờ (pipeline #1). Chờ nó xong hoặc hủy nó rồi tạo lại.",
    "pipeline_id": 1,
    "job_id": 1
  }
}
```

`pipeline_id` là `null` khi job đang chạy là một job dựng lẻ (`POST /api/jobs`).

**Khóa cấp thư mục (giữa nhiều hàng đợi).** Chốt "một dự án một pipeline" ở trên chỉ nhìn thấy job trong cùng một `queue.db`. Nếu hai hàng đợi (hai `queue.db`, ví dụ hai lần chạy `serve`/`daemon` riêng) cùng làm việc trên một thư mục, chúng sẽ ghi đè `options.json`, `plan.json` và tệp dựng của nhau. Vì vậy mọi cách đưa job vào hàng đợi (`POST /api/pipelines`, `POST /api/jobs`, `POST /api/jobs/{id}/retry`, và các lệnh `queue add/analyze/draft/retry`) còn lấy khóa `<dự án>/edit/.project.lock`, ghi tên `queue.db` đang giữ. Hàng đợi khác thấy khóa còn hiệu lực thì bị từ chối bằng **409 `project_locked`** (không ghi gì, kể cả `options.json`). Khóa còn hiệu lực khi hàng đợi giữ nó còn job chờ/đang chạy/chờ duyệt cho thư mục đó; hết job hoặc mất `queue.db` thì khóa cũ và hàng đợi khác chiếm được. Đọc không được `queue.db` của chủ khóa thì coi là còn hiệu lực. Các lệnh chạy tay (`plan`, `pick`, `draft`, `render`) không kiểm tra khóa. Gỡ khẩn cấp: xóa tệp `edit/.project.lock`; tắt hẳn bằng `queue.project_lock = false` trong `rules.json`. Muốn đổi cấu
hình: `POST /api/jobs/{id}/cancel` cho các job cũ (job đang chờ bị hủy ngay; job phụ thuộc vào
nó bị hủy theo), rồi tạo lại.

### `GET /api/pipelines/{id}`

Trả `PipelineView` như trên (các job theo thứ tự tạo). `404` nếu không có pipeline, hoặc
thư mục dự án của nó nằm ngoài `GANI_DV_ALLOWED_ROOTS`.

### Job `draft` đang chờ duyệt

`GET /api/jobs/{id}` (rút gọn):

```json
{
  "id": 2,
  "pipeline_id": 1,
  "depends_on": 1,
  "kind": "draft",
  "draft": "",
  "title": "Xuong GANI — kịch bản",
  "status": "awaiting_approval",
  "frontend_status": "awaiting_approval",
  "stage": "cho_duyet",
  "needs_approval": true,
  "approval_expires_at": "2026-09-30T09:31:00Z",
  "cost": {
    "input_tokens": 1678,
    "worst_call_vnd": 4165.58,
    "cap_vnd": 20000.0,
    "approved_vnd": null,
    "spent_vnd": null
  }
}
```

`cost` chỉ có ở job `draft` đã có ước tính (còn lại là `null`). `worst_call_vnd` là một lần gọi
Claude ở trường hợp xấu nhất (job có thể gọi tối đa vài vòng nhưng luôn dưới `cap_vnd` và dưới
`approved_vnd`); `spent_vnd` là số thật đọc lại từ `edit/ai_log.jsonl` khi job kết thúc.

### `footage`: footage còn dư bao nhiêu (Giai đoạn 6h)

Cổng khả thi (6e) chỉ chặn kế hoạch **không thể** lấp. Còn kế hoạch lấp được nhưng chỉ vừa khít
(ví dụ 9 cảnh khác nhau cho plan cần 8–9 cảnh) thì Claude dễ lặp cảnh (V4) và mất vài vòng. Job
`draft` **đang chờ duyệt** vì thế trả thêm `footage` để giao diện cảnh báo trước khi người dùng
bấm duyệt tiền. Không chặn gì.

```json
"footage": {
  "scenes_available": 9,
  "scenes_needed_min": 8,
  "scenes_needed_max": 9,
  "warn_below": 12,
  "tight": true,
  "blocks": [
    {"role": "hook", "candidates": 9, "beats_needed": 4, "max_fill": 54,
     "scenes_min": 1, "scenes_max": 1, "tight": false},
    {"role": "quy_trinh", "candidates": 9, "beats_needed": 16, "max_fill": 54,
     "scenes_min": 3, "scenes_max": 4, "tight": false}
  ]
}
```

- `scenes_available`: số cảnh **khác nhau** dùng được cho cả plan (một cảnh được chào cho nhiều
  khối chỉ tính một lần; tối đa `vision.max_segments_per_clip` cảnh của một clip).
- `scenes_needed_min` / `scenes_needed_max`: số cảnh plan cần, cộng theo từng khối. Mỗi khối là
  tổng của các cỡ cảnh cho phép (`beats_per_scene_allowed`) đúng bằng số phách của khối, nên
  khối 10 phách với cỡ 4 hoặc 6 chỉ có thể là 4+6 (2 cảnh).
- `warn_below` = `ceil(scenes_needed_max × rules.pick.footage_warn_ratio)` (mặc định 1,3);
  `tight` = `scenes_available < warn_below`. Cảnh báo vàng, người dùng vẫn duyệt được.
- Mỗi khối: `candidates` (cảnh chào riêng cho khối), `max_fill` (số phách tối đa các cảnh đó mang
  được), `scenes_min` / `scenes_max`, và `tight` riêng của khối.
- `footage` là `null` khi: job không phải `draft` hoặc không còn chờ duyệt; phong cách dẫn lời
  (không có khối cảnh); `plan.json` / `candidates.json` thiếu, hỏng hoặc cũ hơn lần chạy của
  job (hai tệp dùng chung theo dự án, cùng luật với `qc_report.json`).

### `verify`: các phép kiểm V1–V9 / S1–S6 theo từng vòng (Giai đoạn 6i)

Trước 6i kết quả kiểm bản nháp chỉ nằm trong bộ nhớ: bản nháp trượt 3 vòng thì không còn dấu vết
vì sao (ca thật: 30 giây, 3 vòng liền trượt V4 "dùng lặp cảnh", tốn 2.992đ). Nay mỗi lần chạy lưu
`edit/verify_report.json` sau **từng vòng** (kể cả khi job bị dừng giữa chừng), và job `draft` **đã
kết thúc** (`completed` hoặc `failed`) trả thêm `verify`.

```json
"verify": {
  "mode": "music",
  "final_status": "failed",
  "rounds_used": 3,
  "max_rounds": 3,
  "checks": [
    {"code": "V1", "label": "Cấu trúc câu trả lời và mã cảnh", "status": "pass", "detail": ""},
    {"code": "V4", "label": "Không dùng lặp cảnh", "status": "fail",
     "detail": "Cảnh 'VID_150353_s0_2' dùng 2 lần.; Cảnh 'VID_160553_s0_0' dùng 2 lần."}
  ],
  "rounds": [
    {"round": 1, "drafts": ["A", "B"], "parsed": true, "passed": false, "cost_vnd": 702.42,
     "checks": ["... cùng dạng với checks ..."]}
  ]
}
```

- `mode`: `music` (V1–V9 + `SNAP`; kế hoạch 3 phút thêm V10 ngay trước `SNAP`) hoặc `speech` (S1–S6 + `SNAP`). `checks` là vòng **cuối**, mỗi
  phép kiểm một dòng, đúng thứ tự; rỗng nếu chưa gọi Claude lần nào (chạm trần chi phí ngay từ đầu).
- `status` của một phép kiểm: `pass`; `fail` (Claude phải sửa, gửi lại); `fixed` (code tự sửa, V3);
  `warn` (chỉ cảnh báo, V6/S5); `skipped` (câu trả lời không đọc được nên phép kiểm chưa chạy: phép
  đầu tiên nhận lỗi cấu trúc, các phép còn lại `skipped`). Nhiều phát hiện cùng một mã: `fail`
  thắng `fixed` thắng `warn` thắng `pass`.
- `detail`: các câu phát hiện khác nhau của phép kiểm, tối đa 3 câu rồi "(+N nữa)"; rỗng nếu sạch.
  Đã bỏ đường dẫn tuyệt đối. `label` lấy từ `rules.verify_labels` — giao diện không tự đặt tên.
- `final_status`: `passed`; `failed` (hết `max_rounds` vòng); `cost_cap` (chạm trần chi phí);
  `api_error` (gọi API lỗi; không lưu nội dung lỗi); `interrupted` (job dừng giữa chừng, tệp còn
  ở trạng thái đang chạy).
- `rounds[]`: `drafts` là các bản được hỏi trong vòng đó (vòng 2 trở đi chỉ hỏi lại bản bị lỗi),
  `parsed=false` nghĩa là câu trả lời không đọc được, `passed` là không còn lỗi Claude phải sửa,
  `cost_vnd` là tiền của riêng vòng đó.
- `verify` là `null` khi: job không phải `draft` hoặc chưa kết thúc (ví dụ đang chờ duyệt); tệp
  thiếu, hỏng hoặc được ghi ngoài khoảng thời gian chạy của chính job (tệp dùng chung theo dự án,
  cùng luật với `qc_report.json`; `started_at` được đặt lại mỗi lần daemon nhận job nên luôn tính
  theo lần chạy trả phí cuối).

### `structure`: mở đầu / chương / kêu gọi của video 3 phút (Giai đoạn 7g)

Job `draft` và `render` của một dự án có plan chia chương (`plan.json` có `chapters`) trả thêm
`structure`, để giao diện vẽ dòng thời gian các chương:

```json
"structure": {
  "duration_sec": 180.0,
  "items": [
    {"kind": "opening", "id": "hook", "start_sec": 0.0, "end_sec": 4.29, "title": null},
    {"kind": "chapter", "id": "ve_sinh", "start_sec": 4.29, "end_sec": 50.0,
     "title": "Khởi đầu sạch sẽ"},
    {"kind": "closing", "id": "cta", "start_sec": 168.57, "end_sec": 180.0, "title": null}
  ]
}
```

- Giây tính từ phách đầu tiên (= giây 0 của video): `start_beat × beat_sec` của plan; các mục liền
  nhau không hở, không chồng, mục cuối kết thúc ở `duration_sec`.
- `title`: tiêu đề chương Claude viết cho **bản nháp của job** (chữ cái `draft`) trong
  `drafts.json`; `null` khi chưa có bản nháp (job `draft` đang chờ duyệt), khi bản đó không có tiêu
  đề, và luôn `null` ở `opening` / `closing`.
- `structure` là `null` với video 15–60 giây, job `analyze`, và khi `plan.json` thiếu/hỏng. Như
  `options`, nó đọc từ tệp hiện tại của dự án (không có cửa sổ thời gian như `qa`).

### `POST /api/jobs/{id}/approve`

```json
{"max_vnd": 6000, "estimate_vnd": 4166}
```

- `max_vnd`: số đồng **tối đa** cho phép tiêu cho job này (> 0, hữu hạn). Không được nhỏ hơn một
  lần gọi tệ nhất, không được vượt `cost.cap_vnd`.
- `estimate_vnd`: ước tính một lần gọi mà người dùng đã thấy (phải khớp số job đang giữ).
- Trả `200` + `JobView` của job (giờ `status = "queued"`, `cost.approved_vnd = 6000.0`).
- `400 bad_request`: thiếu trường, số âm/không hữu hạn/không phải số, trường thừa.
- `404`: không có job. `409 conflict`: job không phải job `draft`.
- `409 approval_refused`: job không còn chờ duyệt, đã hết hạn duyệt, ước tính lệch, `max_vnd`
  quá thấp/quá cao, hoặc vượt **hạn mức ngày** (`daily_budget_vnd` trong `rules.json`: đã tiêu 24
  giờ + đã đặt cọc + số duyệt mới). Thông báo tiếng Việt nói rõ lý do; không có gì thay đổi:

```json
{
  "error": {
    "code": "approval_refused",
    "message": "Ước tính bạn nhập (3.000đ) không khớp ước tính hiện tại của job (4.166đ): ước tính đã đổi hoặc nhập sai; xem lại rồi duyệt."
  }
}
```

### Hành vi cần biết

- **Cổng khả thi (chế độ nhạc, 0đ).** Ngay sau `pick` và trước bước ước tính chi phí, job `draft` kiểm tra footage đã chọn có thể lấp plan không (từng khối đủ sức chứa; nhóm khối dùng chung cảnh; nhiều bản nháp cần hook khác nhau và đủ cảnh khác nhau). Không đủ thì job **`failed` ngay, không tốn đồng nào**, không bao giờ vào `awaiting_approval`, và `error_msg` là thông điệp tiếng Việt nguyên văn, ví dụ: "Dừng trước khi gọi Claude (0đ): khối 'san_pham' cần 24 phách nhưng chỉ có 2 cảnh ứng viên, tối đa lấp được 8 phách …" — giao diện chỉ việc hiển thị `error_msg`. Thiếu **nhãn** mà đủ số cảnh thì không bị chặn. Kiểm tra chỉ dùng điều kiện cần (không từ chối oan): qua cổng không bảo đảm Claude sẽ dựng được. Chế độ lời không áp dụng. Lệnh `draft` chạy tay thoát mã 3 trong trường hợp này. Tắt bằng `pick.feasibility_gate = false`.

- **Thử lại từng job** (`POST /api/jobs/{id}/retry`): không có thử lại cả pipeline. Job phân tích
  lỗi thì các job sau nó bị hủy dây chuyền; thử lại job phân tích **không** tự khôi phục các job
  đã hủy — giao diện thử lại từng job theo thứ tự (`analyze`, `draft`, `render`). Thử lại job
  `draft` luôn quay về bước ước tính và **cần duyệt lại chi phí**; job `draft` không bao giờ tự
  thử lại. Thử lại cũng lấy lại khóa thư mục như khi tạo job: nếu trong lúc đó một hàng đợi
  khác đã nhận thư mục thì trả **409 `project_locked`** và job giữ nguyên trạng thái.
- **Hủy**: job đang chờ hủy ngay và kéo theo các job chờ nó; job đang chạy nhận cờ dừng. Hủy job
  `draft` đang chờ duyệt không tốn đồng nào.
- `GET /api/jobs` mặc định **không lọc** theo loại; `?kind=render|draft|analyze` để lọc. Thẻ
  job `analyze`/`draft` có `draft = ""`, `size = "preview"` (giữ nguyên hợp đồng, giao diện dựa
  vào `kind`). `steps` của các job này cho cùng 8 `id`: job `analyze` chạy trên `nhan_dang`, job
  `draft` chạy trên `plan_compile` → `pick` → `ai_draft`; bước ở trạng thái chờ duyệt có
  `detail = "Chờ duyệt chi phí"`.
- `GET /api/health` có thêm `ai_key_configured` và `budget`: `limit_vnd` (`null` = không giới hạn
  ngày), `spent_24h_vnd`, `reserved_vnd` (đã duyệt nhưng chưa tiêu).

## 5. Ánh xạ sang `RenderJob` của frontend

### Trạng thái (`frontend_status` đã tính sẵn)

| `status` (SQLite) | `result` | `JobStatus` của frontend |
|---|---|---|
| `queued` | — | `queued` |
| `awaiting_approval` | — | `awaiting_approval` (giá trị **mới**: hiện hộp thoại duyệt chi phí) |
| `processing` | — | `running` |
| `completed` | `passed` | `done` |
| `completed` | `qc_failed` | `qa_review` |
| `failed` | — | `error` |
| `cancelled` | — | `error` |

Job `qa_review` **có video** (giữ làm bằng chứng) nhưng không được giao; `qa.checks` cho biết
kiểm tra nào chưa đạt.

### Từng trường

| `RenderJob` (frontend) | API | Ghi chú |
|---|---|---|
| `id: string` | `String(id)` | |
| `title` | `title` | |
| `options: VideoOptions` | `options` | Khóa tiếng Anh của `plan.json`, không phải `VideoOptions` tiếng Việt; giao diện tự đổi nếu cần hiển thị |
| `status` | `frontend_status` | Bảng trên |
| `createdAt` | `created_at` | |
| `etaSec` | `eta_sec` | `null` khi không chạy |
| `steps: PipelineStep[]` | `steps` | Cùng 8 `id` của `PIPELINE_STEPS`; `status` ∈ `done/running/queued/error` |
| `logLines: ConsoleLine[]` | `events` (hoặc `GET …/events`) | `ts`, `level` (`info/warn/error`), `text`: cùng hình với `ConsoleLine` |
| `previewUrl` | `video_url` của job `size = "preview"` | Ghép tiền tố `http://127.0.0.1:8765` khi không dùng proxy |
| `finalUrl` | `video_url` của job `size = "final"` | Giao diện gộp hai job cùng `(project_id, draft)` |
| `qa: QaReport` | `qa` | Xem dưới |

**`steps`** suy ra từ file thật: `inventory` (← `inventory.json`), `nhan_dang` (← `scenes.json`
và `speech.json`), `plan_compile` (← `plan.json`), `pick` (← `candidates.json` hoặc
`phrases.json`), `ai_draft` (← `drafts.json` hoặc `speech_drafts.json`); ba bước sau lấy từ job:
`render_preview`, `qc_verify` (đạt/không đạt theo `result`), `final_export` (chỉ chạy khi job
là `final`). Bước lỗi kèm `detail` là thông báo lỗi.

**`qa`** (khác `QaReport` của frontend, xem "Khoảng trống"):
`qa.checks` là T1–T7 với `code`, `label`, `passed`, `detail`;
`qa.lufs`, `qa.true_peak_db` là số đo thật của lần dựng cuối (`null` nếu chưa đo hoặc im lặng).
Để lấp `pass3_audio` của frontend: dùng các `checks` có `code == "T3"`.

**T8 (Whisper nghe lại thành phẩm)** không phải kiểm tra logo hay đoạn mở đầu: Whisper chép lại
lời của video đã dựng để đo sai lệch lời (WER), độ lệch thời gian phụ đề và nhạc nền có đè tiếng
nói không. Điều kiện chạy: chỉ **bản cuối** (`size = "final"`, 1080×1920) của **chế độ lời**
(`ban_hang_co_loi`) và `no_asr = false`.

`qa.t8` cho biết vì sao có hoặc không có dòng T8 trong `qa.checks`:

| `qa.t8` | Nghĩa | Dòng T8 trong `checks` |
|---|---|---|
| `ran` | Whisper đã nghe lại và có số đo | **Luôn có**, kể cả khi đạt |
| `skipped_draft` | Bản nháp 540p: không chạy T8 | không |
| `skipped_music` | Chế độ nhạc: không có giọng để đo | không |
| `skipped_no_asr` | Dựng bản cuối chế độ lời nhưng `no_asr = true` | không |
| `unavailable` | Whisper không chạy được (lỗi môi trường, khi đó có dòng T8 cảnh báo), hoặc báo cáo dựng từ trước phiên bản này không lưu số đo | có nếu có cảnh báo, không thì không |

Khi `qa.t8 = "ran"`, dòng T8 có `passed = false` chỉ khi WER vượt ngưỡng (lỗi); độ lệch phụ đề
quá ngưỡng là cảnh báo và vẫn `passed = true`. Lúc đạt, `detail` mang số đo thật (một chữ số
thập phân cho WER, mili-giây cho độ lệch; ngưỡng lấy từ `rules.json` → `qc.t8`):

```json
{"code": "T8", "label": "Whisper nghe lại thành phẩm", "passed": true,
 "detail": "Đạt: Whisper nghe lại sai 6,2% số từ (tối đa 15%), phụ đề lệch tiếng trung vị 85ms (tối đa 150ms)."}
```

Khi có cảnh báo hoặc lỗi, `detail` giữ nguyên câu chẩn đoán (ví dụ nhạc đè tiếng). Số đo được
lưu trong `qc_report.json` (trường `t8`) nên đọc lại được về sau; báo cáo cũ không có trường này
vẫn đọc bình thường (`qa.t8 = "unavailable"`, không có dòng T8).

## 6. Khoảng trống đã biết (nói thẳng)

1. **`QaReport.pass1_sync` / `pass2_glossary`** (kiểm tra V2–V9 của bước AI) vẫn không có trong
   `qa` (báo cáo kỹ thuật T1–T8 của video). Kết quả V/S nay nằm ở `verify` (mục trên) của job
   `draft`; chỉ có từ khi chạy bằng bản có 6i — job cũ có `verify: null`.
2. **Wizard chạy cả chuỗi** dùng được từ 6c (`POST /api/pipelines`, mục 4b), nhưng footage vẫn
   phải nằm sẵn trên máy dựng (không tải lên qua HTTP). `frontend_status = "awaiting_approval"` là
   giá trị **mới** — giao diện phải xử lý (mở hộp thoại duyệt chi phí), backend không sửa nhánh
   giao diện.
3. `cover.jpg` và `phude.srt` dùng chung một tên cho mọi lần dựng của cùng một bản nháp: nếu
   dựng lại (nháp rồi cuối), chúng là của lần dựng **gần nhất**.
4. `qc_report.json` cũng dùng chung theo bản nháp: API chỉ trả `qa` nếu tệp được ghi trong
   khoảng thời gian chạy của chính job đó, nên job cũ bị dựng đè sẽ có `qa: null`.

## 7. An toàn (đã kiểm bằng test với server thật)

- **Host**: chỉ `127.0.0.1:<cổng>`, `localhost:<cổng>` (chặn DNS rebinding).
- **Origin**: nếu có, phải là chính server hoặc nằm trong `api.allowed_origins` (mặc định
  rỗng) cộng `GANI_DV_DEV_ORIGINS` trong `.env`. Không có Origin (curl, công cụ dòng lệnh)
  được chấp nhận; trình duyệt luôn gửi Origin cho POST. Cổng 5173 của Vite **không còn được
  tin mặc định**: một trang chạy ở cổng đó gọi được API như chính người dùng, kể cả duyệt
  chi phí. Tiến trình đã chạy trên máy vốn đọc ghi thẳng được `queue.db`, nên không có Origin
  không phải đường tấn công thêm; token xác thực chỉ đáng làm khi mở server ra ngoài
  `127.0.0.1` hoặc nhiều người dùng chung một máy.
- **Chéo trang**: `Sec-Fetch-Site: cross-site` chỉ qua nếu Origin hoặc Referer tin cậy —
  nên `<video src="http://127.0.0.1:8765/media/…">` từ giao diện Vite hoạt động, còn trang lạ
  thì không.
- **POST** phải là `application/json` (chặn `text/plain`, `multipart`, form chéo trang), thân ≤
  64KB, `Content-Length`, không nhận `Transfer-Encoding`.
- **CORS** chỉ trả cho Origin được phép; `OPTIONS` (preflight) chỉ cho Origin được phép.
- **Media**: client chỉ gửi `id` + loại tệp; server tự dựng đường dẫn từ thư mục dự án và bản
  nháp của job, kiểm tra lại allow-list mỗi lần, chặn liên kết trỏ ra ngoài (tệp thật, sau khi
  đi hết mọi liên kết ở `edit/`, `render/`, `render/<bản>`, phải còn nằm trong allow-list); kiểu tệp cố định
  (`video/mp4`, `image/jpeg`, `text/plain`), `X-Content-Type-Options: nosniff`.
- **Đường dẫn**: dòng console (`events`) và `error_msg` do tiến trình khác sinh ra nên có thể
  chứa đường dẫn tuyệt đối; server thay thư mục dự án bằng `<tên dự án>` và thư mục gốc bằng
  `<gốc>` trước khi trả về.
- **Lỗi** không lộ đường dẫn hay stack trace. Cổng bị chiếm được báo rõ (không chia sẻ cổng
  trên Windows).
- **Giao diện tĩnh (6g)** đi qua đúng cổng kiểm Host / Origin / chéo trang ở trên. Thêm:
  - *Đường dẫn*: mỗi đoạn được giải mã % đúng **một** lần rồi bị từ chối nếu chứa `/`, `\`, `:`
    (chặn `x::$DATA` trên Windows), ký tự NUL/điều khiển, `.`/`..`, bắt đầu bằng dấu chấm (`.env`,
    `.git`, `.gitkeep`) hoặc UTF-8 hỏng; đoạn rỗng giữa đường dẫn (`//`) cũng bị từ chối.
  - *Ranh giới thư mục*: đường dẫn thật (sau khi đi theo symlink/junction) phải còn nằm trong
    `frontend_dist`, nếu không là 404. `frontend_dist` trỏ ra ngoài thư mục dự án thì bị bỏ qua.
  - *Loại tệp*: bảng đuôi cố định (`.html .js .mjs .css .json .map .txt .svg .png .jpg .jpeg .webp
    .ico .woff2 .mp4 .m4a .mp3`), không đọc registry Windows; đuôi lạ (`.exe`, `.pem`, `.sqlite`…)
    là 404, không bao giờ `application/octet-stream`.
  - *Phương thức*: chỉ `GET`/`HEAD`; phương thức khác trả 405 kèm `Allow: GET, HEAD`.
  - *HTML* mang `Content-Security-Policy` (`rules.api.frontend_csp`: mặc định chỉ cho tải từ chính
    server, cộng Google Fonts và `blob:` cho video xem thử), `X-Frame-Options: DENY`,
    `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`.
  - *Bộ nhớ đệm*: `index.html` và tệp thường dùng `no-cache` + `ETag` (hỗ trợ `If-None-Match` →
    304); tệp trong `/assets/` (Vite đặt tên có mã băm) `public, max-age=31536000, immutable`.
    Phản hồi `/api/...` vẫn `no-store`. Tệp hỗ trợ HTTP `Range` (một khoảng).
  - Bấm vào giao diện từ **trang khác** bị 403 `yeu_cau_cheo_trang`; gõ tay địa chỉ thì bình
    thường (`Sec-Fetch-Site: none`).

## 8. Gợi ý cho `realApi` (phía frontend)

`frontend/vite.config.ts` đã proxy cả `/api` lẫn `/media` sang `serve`, nên giao diện dùng URL
tương đối cho video. Có thể dùng URL tuyệt đối `http://127.0.0.1:8765` (được phép nhờ Referer).

```ts
// Theo dõi một job bằng polling 1 giây (thay cho WebSocket mô phỏng của mockApi)
subscribeJob(id, onUpdate) {
  let after = 0;
  const timer = setInterval(async () => {
    const job = await (await fetch(`/api/jobs/${id}`)).json();
    onUpdate(toRenderJob(job));                  // tự ánh xạ theo mục 5
    if (['completed', 'failed', 'cancelled'].includes(job.status)) clearInterval(timer);
  }, 1000);
  return () => clearInterval(timer);
}
// Chỉ lấy dòng log mới: fetch(`/api/jobs/${id}/events?after=${after}`), cập nhật `after` = id cuối.
// Hủy / thử lại: fetch(`/api/jobs/${id}/cancel`, { method: 'POST',
//   headers: { 'Content-Type': 'application/json' } })
```

Thử nhanh bằng dòng lệnh:

```bash
curl http://127.0.0.1:8765/api/health
curl -X POST http://127.0.0.1:8765/api/jobs -H "Content-Type: application/json" \
     -d '{"project_id": "0/Xuong GANI", "draft": "A"}'
curl -H "Range: bytes=0-99" http://127.0.0.1:8765/media/1/video -o part.bin
```
