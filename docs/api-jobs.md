# API web cục bộ — hợp đồng kỹ thuật (Giai đoạn 6b)

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

## 2. Quy ước chung

- Chỉ nghe ở **127.0.0.1**. Mọi yêu cầu phải có `Host: 127.0.0.1:8765` hoặc
  `localhost:8765`, nếu không trả 403.
- Dữ liệu là JSON UTF-8. Thời gian là ISO 8601 UTC, đuôi `Z` (`2026-09-29T04:56:08Z`).
- Mã dự án (`project_id`) là chuỗi **mờ** `"<chỉ số thư mục gốc>/<tên thư mục>"`, ví dụ
  `"0/Xuong GANI"` (`"0/."` nếu chính thư mục gốc là dự án). Server không bao giờ trả đường
  dẫn tuyệt đối. Giao diện lấy mã từ `GET /api/projects`, không tự ghép.
- **Không có tải footage lên qua HTTP.** Footage nằm sẵn trên máy dựng, trong một thư mục
  thuộc `GANI_DV_ALLOWED_ROOTS`.
- Mỗi **job** là một bản nháp ở một kích thước (`preview` 540p hoặc `final` 1080×1920).
  Server không gộp; giao diện tự gộp theo `(project_id, draft)` nếu muốn.

### Lỗi

Luôn có dạng `{"error": {"code": "...", "message": "tiếng Việt"}}`, không kèm đường dẫn hay
stack trace.

| HTTP | `code` | Khi nào |
|---|---|---|
| 400 | `bad_request` | Thân/tham số sai (`draft` không phải A/B/C/all, `limit` ngoài 1–200, `project_id` sai dạng, JSON hỏng, trường thừa) |
| 403 | `host_khong_hop_le` / `origin_khong_hop_le` / `yeu_cau_cheo_trang` | Sai Host, Origin không nằm trong danh sách, yêu cầu chéo trang không có Origin/Referer tin cậy |
| 404 | `not_found` | Không có dự án / job / tệp / đường dẫn |
| 405 | `method_not_allowed` | Sai phương thức (kèm header `Allow`) |
| 409 | `not_ready` | Dự án chưa có `plan.json` hoặc `drafts.json`/`speech_drafts.json` — chạy `plan`/`pick`/`draft` trước |
| 409 | `conflict` | Thử lại một job đang chờ/đang chạy/đã đạt |
| 411 | `can_content_length` | Thân dùng `Transfer-Encoding` (không hỗ trợ) |
| 413 | `than_qua_lon` | Thân > `api.max_body_bytes` (64KB) |
| 415 | `content_type_no` | `Content-Type` có mặt nhưng không phải `application/json`, hoặc có thân mà thiếu `Content-Type` |
| 500 | `loi_may_chu` | Lỗi không lường trước (chi tiết chỉ nằm ở log của `serve`) |
| 503 | `unavailable` | Máy dựng chưa đặt `GANI_DV_ALLOWED_ROOTS` |

## 3. Endpoint

| Phương thức + đường dẫn | Việc | Trả về |
|---|---|---|
| `GET /api/health` | Daemon online không (nhịp tim < 15 giây), số job theo trạng thái | `HealthView` |
| `GET /api/projects` | Dự án trong allow-list và đã có gì | `ProjectView[]` |
| `POST /api/preflight` | Chạy đúng `preflight_check` của Python (nguồn sự thật duy nhất) | Kết quả preflight (`valid`, `errors`, `warnings`, `adjustments`, …) |
| `POST /api/jobs` | Đưa bản dựng vào hàng đợi | `201` + `JobView[]` |
| `GET /api/jobs?status=&project_id=&limit=` | Danh sách, mới nhất trước (`limit` 1–200, mặc định 50) | `JobView[]` |
| `GET /api/jobs/{id}` | Chi tiết kèm 200 dòng console cuối | `JobDetail` |
| `GET /api/jobs/{id}/events?after=N` | Dòng console có `id > N` (con trỏ) | `EventView[]` |
| `POST /api/jobs/{id}/cancel` | Chờ thì hủy ngay; đang chạy thì gửi cờ dừng | `JobView` |
| `POST /api/jobs/{id}/retry` | Xếp lại job lỗi / đã hủy / QC không đạt | `JobView` |
| `GET\|HEAD /media/{id}/{video\|cover\|srt}` | Thành phẩm của job đã xong, hỗ trợ HTTP **Range** | tệp |

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
  "jobs": {"queued": 0, "processing": 0, "completed": 1, "failed": 0, "cancelled": 0},
  "allowed_roots_configured": true
}
```

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
    "lufs": -13.9, "true_peak_db": -2.0,
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

## 5. Ánh xạ sang `RenderJob` của frontend

### Trạng thái (`frontend_status` đã tính sẵn)

| `status` (SQLite) | `result` | `JobStatus` của frontend |
|---|---|---|
| `queued` | — | `queued` |
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
`qa.checks` là T1–T7 (thêm T8 khi có kết quả Whisper) với `code`, `label`, `passed`, `detail`;
`qa.lufs`, `qa.true_peak_db` là số đo thật của lần dựng cuối (`null` nếu chưa đo hoặc im lặng).
Để lấp `pass3_audio` của frontend: dùng các `checks` có `code == "T3"`.

## 6. Khoảng trống đã biết (nói thẳng)

1. **`QaReport.pass1_sync` / `pass2_glossary` (kiểm tra V2–V9 của bước AI viết kịch bản) chưa có
   dữ liệu thật**: kết quả V1–V9 không được lưu theo từng kiểm tra (chỉ còn ghi chú V3/V6 trong
   `drafts.json`). API chỉ trả kiểm tra kỹ thuật T1–T8. Giao diện nên hiện phần V ở dạng "chưa
   có dữ liệu" thay vì bịa số.
2. **Wizard "tải footage rồi chạy cả 8 bước" chưa dùng được với backend thật** cho tới
   Giai đoạn 6c (job `analyze`, `draft`). Hiện giao diện chọn một **dự án đã sẵn sàng** và
   xếp job dựng; các bước trước `render` chỉ hiển thị trạng thái.
3. `cover.jpg` và `phude.srt` dùng chung một tên cho mọi lần dựng của cùng một bản nháp: nếu
   dựng lại (nháp rồi cuối), chúng là của lần dựng **gần nhất**.
4. `qc_report.json` cũng dùng chung theo bản nháp: API chỉ trả `qa` nếu tệp được ghi trong
   khoảng thời gian chạy của chính job đó, nên job cũ bị dựng đè sẽ có `qa: null`.

## 7. An toàn (đã kiểm bằng test với server thật)

- **Host**: chỉ `127.0.0.1:<cổng>`, `localhost:<cổng>` (chặn DNS rebinding).
- **Origin**: nếu có, phải nằm trong `api.allowed_origins` (mặc định
  `http://localhost:5173`, `http://127.0.0.1:5173`) hoặc là chính server. Không có Origin
  (curl, công cụ dòng lệnh) được chấp nhận; trình duyệt luôn gửi Origin cho POST.
- **Chéo trang**: `Sec-Fetch-Site: cross-site` chỉ qua nếu Origin hoặc Referer tin cậy —
  nên `<video src="http://127.0.0.1:8765/media/…">` từ giao diện Vite hoạt động, còn trang lạ
  thì không.
- **POST** phải là `application/json` (chặn `text/plain`, `multipart`, form chéo trang), thân ≤
  64KB, `Content-Length`, không nhận `Transfer-Encoding`.
- **CORS** chỉ trả cho Origin được phép; `OPTIONS` (preflight) chỉ cho Origin được phép.
- **Media**: client chỉ gửi `id` + loại tệp; server tự dựng đường dẫn từ thư mục dự án và bản
  nháp của job, kiểm tra lại allow-list mỗi lần, chặn liên kết trỏ ra ngoài; kiểu tệp cố định
  (`video/mp4`, `image/jpeg`, `text/plain`), `X-Content-Type-Options: nosniff`.
- **Đường dẫn**: dòng console (`events`) và `error_msg` do tiến trình khác sinh ra nên có thể
  chứa đường dẫn tuyệt đối; server thay thư mục dự án bằng `<tên dự án>` và thư mục gốc bằng
  `<gốc>` trước khi trả về.
- **Lỗi** không lộ đường dẫn hay stack trace. Cổng bị chiếm được báo rõ (không chia sẻ cổng
  trên Windows).

## 8. Gợi ý cho `realApi` (phía frontend)

Vite hiện chỉ proxy `/api`. Với video, hoặc thêm `'/media'` vào `proxy` của `vite.config.ts`,
hoặc dùng URL tuyệt đối `http://127.0.0.1:8765` (đã được phép nhờ Referer).

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
