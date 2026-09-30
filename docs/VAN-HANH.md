# Sổ tay vận hành hằng ngày — App Dựng Video GANI

Dành cho người dùng máy dựng (Dell i5-8265U, Windows). Kỹ thuật chi tiết của API nằm ở
[`api-jobs.md`](api-jobs.md); thiết kế video 3 phút ở [`GD7-THIET-KE.md`](GD7-THIET-KE.md).
Mọi con số dưới đây lấy từ `config/rules.json` hoặc số đo thật; chỗ nào là ước tính thì ghi rõ.

---

## 1. Cài một lần

| Cần có | Ghi chú |
|---|---|
| Python ≥ 3.11 | cài gói trong thư mục dự án: `pip install -e .` (thêm `".[ai]"` nếu máy chạy được nhận diện CLIP/torch) |
| ffmpeg + ffprobe trên `PATH` | thiếu thì dựng và kiểm kê đều báo lỗi có hướng dẫn |
| Node.js | chỉ để build giao diện: `cd frontend && npm ci && npm run build:real` |
| Tệp `.env` | chép `.env.example` thành `.env` rồi điền; **không dán khóa API vào chat hay vào code** |

Các biến trong `.env` (xem `.env.example`):

| Biến | Dùng để |
|---|---|
| `ANTHROPIC_API_KEY` | khóa gọi Claude (bước viết kịch bản). Thiếu khóa: job kịch bản báo lỗi **trước** khi gọi, tốn 0đ |
| `GANI_DV_ALLOWED_ROOTS` | các thư mục cha chứa dự án footage được phép đưa vào hàng đợi (Windows ngăn cách bằng `;`). **Để trống = từ chối mọi job** |
| `GANI_DV_QUEUE_DIR` | nơi đặt `queue.db` và log job; để trống = thư mục `jobs/` của dự án |
| `GANI_DV_WHISPER_MODEL` | model nhận dạng lời; máy Dell đặt `small` |
| `GANI_DV_MUSIC_DIR` | thư viện nhạc của máy (xem mục 4) |
| `GANI_DV_CLAUDE_MODEL` | chỉ khi cần model khác `rules.ai.model` |

## 2. Khởi động mỗi ngày

**Cách nhanh nhất: nhấp đúp biểu tượng "GANI Video" trên Desktop.** Nó chạy
`scripts\chay-gani.bat`: bật máy chủ và daemon nếu chưa chạy (mỗi cái một cửa sổ nhỏ tên
"GANI - …" trên thanh tác vụ), đợi máy chủ sẵn sàng, rồi mở giao diện trong một cửa sổ riêng
không có thanh địa chỉ. Bấm lại lúc đang chạy thì không bật trùng. Tắt hết: đóng hai cửa sổ nhỏ
đó (hoặc `Ctrl+C` trong từng cửa sổ).

- Biểu tượng là lối tắt (`.lnk`) trỏ tới `scripts\chay-gani.bat`, dùng icon
  `frontend\public\favicon.ico` (logo lá GANI). Làm lại khi mất: chuột phải vào
  `chay-gani.bat` → Send to → Desktop (create shortcut) → chuột phải lối tắt → Properties →
  Change Icon → chọn `favicon.ico`.
- Cần có sẵn bản giao diện đã build (`frontend\dist`); thiếu thì file báo lệnh build cần chạy.
- Cấu hình (khóa API, thư mục footage…) đọc từ `.env` của dự án, xem mục 1.

**Cách thủ công** (hai cửa sổ lệnh, cùng thư mục dự án):

```bash
python -m gani_dung_video serve    # máy chủ giao diện + API, chỉ 127.0.0.1:8765
python -m gani_dung_video daemon   # người làm việc: lấy job từ hàng đợi và chạy
```

Mở `http://127.0.0.1:8765/`. Thanh trên cùng phải ghi **"Máy chủ OK · daemon BẬT"**. Nếu ghi
"daemon TẮT", job vẫn xếp hàng nhưng chưa chạy.

- Chỉ chạy **một** daemon mỗi hàng đợi (chạy thêm sẽ báo đã có daemon khác).
- Dừng: `Ctrl+C` = chạy nốt job đang làm rồi dừng; `Ctrl+C` lần hai = dừng ngay, job đang chạy
  được trả về hàng đợi. Hoặc từ cửa sổ khác: `python -m gani_dung_video queue stop` (thêm `--now`
  để dừng ngay).
- Mất điện / tắt máy đột ngột: khi daemon bật lại, job **phân tích và dựng** đang chạy tự trở lại
  hàng đợi (còn lượt thử). Job **kịch bản có gọi Claude thì không tự thử lại** (tránh tốn tiền
  hai lần): nó báo lỗi, bấm "Thử lại (duyệt lại)" để duyệt chi phí mới.
- Cổng 8765 bận: giao diện đã build gọi cứng `http://127.0.0.1:8765`. Đổi cổng thì phải build
  lại giao diện với `VITE_API_BASE=http://127.0.0.1:<cổng>` rồi `serve --port <cổng>`; cách đơn
  giản hơn là tìm và tắt tiến trình đang giữ cổng 8765.

## 3. Chuẩn bị footage

- Mỗi dự án là **một thư mục con** của thư mục trong `GANI_DV_ALLOWED_ROOTS`. Đặt clip **trực
  tiếp** trong thư mục dự án (không tìm vào thư mục con).
- Đuôi file được nhận: `.mp4 .mov .m4v .mkv .avi .webm .mts`. Clip quay dọc hay ngang đều được
  (cắt vừa khung 1080×1920); clip HDR được đổi sang SDR khi dựng.
- Tên file: không cần đặt tên đặc biệt. Bản sao do hệ điều hành đặt tên `ten 2.mp4` hoặc
  `ten (1).mp4` được so với file gốc (cùng dung lượng và cùng mã băm) và **bị bỏ nếu trùng**; nên
  xóa bản sao trước khi ném vào thư mục.
- Dự án chỉ đọc footage, **không bao giờ sửa hay xóa** file gốc. Mọi thứ máy sinh ra nằm trong
  `edit/` (đã nằm trong `.gitignore`).
- Đủ cảnh hay chưa: cổng khả thi (0đ) chặn kế hoạch chắc chắn không lấp được, và giao diện hiện
  **cảnh báo vàng "Footage sát nhu cầu"** ở bước duyệt chi phí khi chỉ vừa khít. Số cảnh khác nhau
  cần (bảng `scenes_needed`): Kể chuyện nghề 15s = 5, 30s = 10, 60s = 20, 3 phút = 60. Mỗi clip
  dùng tối đa 3 đoạn cách nhau ≥ 3 giây, nên video 3 phút cần **từ khoảng 20 clip** cho 1 bản và
  **khoảng 28** cho 2 bản (ước tính, xem `GD7-THIET-KE.md` mục 4).
- Mục đích "Giới thiệu xưởng" cần các cảnh nhãn: công nhân thao tác, máy móc thiết bị, giấy
  chứng nhận. Thiếu nhãn thì vẫn dựng bằng cảnh gần nhất, V6 chỉ cảnh báo.

## 4. Nhạc

- Đặt nhạc trong `GANI_DV_MUSIC_DIR` (đuôi `.mp3 .m4a .wav .aac .flac .ogg`).
- Nên khai báo nhịp trong `library.json` cùng thư mục, tránh đoán sai:
  `{"tracks": {"ten.mp3": {"bpm": 92, "first_beat_sec": 0.35}}}`. Bài không có trong `library.json`
  được đo nhịp tự động; nếu đo không chắc thì dựng bằng lưới nhịp ảo và có cảnh báo.
- Bài ngắn hơn video được lặp theo ô nhịp (X13). Bài nhạc **tải lên phải tick "tôi có quyền sử
  dụng"** (X14), nếu không bị chặn.

## 5. Làm một video trên giao diện

1. Tab **Tạo video mới** → chọn **dự án** → chọn phong cách, mục đích, độ dài, số bản nháp, nhạc.
   Khung **Preflight** bên phải báo đỏ (bị chặn), vàng (dùng được có lưu ý) hay xanh **trước khi
   tốn gì**. Nút "3 phút" bị khóa nếu máy chủ chưa mở video 3 phút.
2. **Chạy cả chuỗi**: phân tích footage (0đ, chậm: vài chục phút, vì nhận dạng hình và lời trên
   CPU; chạy lại dự án không đổi thì dùng lại kết quả) → kịch bản → dựng.
3. Khi tới kịch bản, job **dừng chờ duyệt chi phí** (tab Hàng đợi, nút "Xem & duyệt chi phí"):
   - xem ước tính một lần gọi tệ nhất, trần cho độ dài video, hạn mức ngày còn lại, cảnh báo
     footage;
   - nhập **số tiền tối đa** cho job (gợi ý sẵn, không áp dụng tự động), **tick đồng ý đúng số
     đó**, rồi "Xác nhận duyệt". Đổi số thì phải tick lại. Không có duyệt tự động, không có
     "duyệt tất cả";
   - Claude gọi tối đa 3 vòng để sửa lỗi nhưng **tổng không bao giờ vượt số anh duyệt**; ước
     tính đổi thì job quay lại chờ duyệt mà không tốn đồng nào; quá hạn duyệt (24 giờ) thì việc
     duyệt bị từ chối (409), phải chạy lại phần kịch bản để có ước tính mới.
4. Dựng bản nháp 540p, soát kỹ thuật T1–T7 tự động (chữ, hình, âm lượng −14 LUFS, đỉnh). Xem
   video, đọc "Soát chất lượng" và dòng **"Tự chỉnh"** (việc QC tự sửa, ví dụ hạ trần đỉnh ở vòng
   2). Bảng **"Kiểm tra kịch bản AI"** cho biết V1–V9 (V10 với video 3 phút) đạt/trượt từng vòng.
5. Ưng bản nào thì bấm **Render lại** để dựng **bản cuối 1080×1920**. Tải về từ nút "Tải video
   final". Kèm `cover.jpg` và `phude.srt` cùng thư mục.

### Chi phí (số thật và trần)

| | Số |
|---|---|
| Đo thật 6i (15s, 2 bản, đạt ngay vòng 1) | 669đ |
| Trần một job, video ≤ 60s | 20.000đ |
| Trần một job, video 3 phút | 30.000đ (khi được mở) |
| Hạn mức mỗi ngày (cộng mọi job) | 100.000đ |
| Video 3 phút, ước tính offline | một vòng 2 bản ≈ 3–4 nghìn đ; xấu nhất một lần gọi ≈ 4.800đ (chưa có số thật: sẽ có ở 7e) |

Dùng **2 bản** cho video 3 phút: 3 bản dễ bị cắt cụt câu trả lời và tốn gấp đôi.

## 6. Thời gian dựng trên máy Dell (đo thật, video 180 giây)

| | Nháp 540p | Bản cuối 1080×1920 |
|---|---|---|
| Một vòng dựng + soát | ≈ 2,5 phút | ≈ 7–8 phút |
| Cả lượt (QC phải dựng lại vòng 2) | ≈ 5 phút | ≈ 17 phút |
| RAM đỉnh của ffmpeg | 1,4 GB | 2,4 GB |

Lúc đo, máy chỉ còn trống khoảng 0,6 GB RAM mà bản cuối 3 phút vẫn chạy xong (ffmpeg đỉnh 2,4 GB),
nhưng rất sát: **đóng bớt trình duyệt và ứng dụng khác** trước khi dựng bản cuối. Thanh tiến độ
và ETA tự tính theo độ dài video.

## 7. Nhãn QC T8 (chế độ lời)

T8 là bước Whisper **nghe lại** video đã dựng để đo sai lệch lời và độ lệch phụ đề; chỉ chạy cho
**bản cuối của chế độ lời**. Các nhãn `qa.t8` (`ran`, `skipped_draft`, `skipped_music`,
`skipped_no_asr`, `unavailable`) và cách giao diện hiển thị nằm trong `api-jobs.md`, mục
"T8 (Whisper nghe lại thành phẩm)". Video chế độ nhạc (kể cả 3 phút) không có T8.

## 8. Khóa thư mục `.project.lock`

Hai hàng đợi khác nhau (hai `queue.db`) không được cùng làm một dự án: chúng sẽ ghi đè
`plan.json`, `options.json` và file dựng của nhau. Vì vậy mỗi dự án có khóa `edit/.project.lock`
ghi tên hàng đợi đang giữ. Hàng đợi khác bị từ chối (lỗi 409 `project_locked`, kèm job đang giữ)
cho tới khi hàng đợi kia hết job cho dự án đó.
- Gặp lỗi này: thường là đang mở hai lần `serve`/`daemon` với `GANI_DV_QUEUE_DIR` khác nhau.
  Dùng chung một hàng đợi.
- Gỡ khẩn khi chắc chắn không còn job nào chạy: xóa tệp `edit/.project.lock`.
- Các lệnh chạy tay (`plan`, `pick`, `draft`, `render`) không kiểm tra khóa.

## 9. Tệp kết quả của một dự án (`<thư mục dự án>/edit/`)

| Tệp | Là gì |
|---|---|
| `inventory.json`, `scenes.json`, `speech.json` | kết quả phân tích footage |
| `plan.json` | số phách từng khối (video 3 phút: có `chapters`) |
| `candidates.json` | các cảnh Claude được phép chọn cho từng khối |
| `drafts.json` | kịch bản đã kiểm và căn phách (tiêu đề chương nằm ở đây) |
| `verify_report.json` | kết quả V1–V10 (S1–S6) **theo từng vòng**, kể cả khi trượt |
| `ai_log.jsonl` | mỗi vòng gọi Claude: token vào/ra, chi phí (không ghi nội dung gửi đi, không ghi khóa) |
| `render/<A>/nhap_A_540p.mp4`, `video_A_1080x1920.mp4` | video nháp / cuối |
| `render/<A>/cover.jpg`, `phude.srt`, `qc_report.json`, `render_log.json` | ảnh bìa, phụ đề, kết quả QC, nhật ký dựng để dựng lại đúng bản này |

Nhật ký từng job (khi cần gỡ lỗi): `<GANI_DV_QUEUE_DIR>/logs/<số job>.log`.

## 10. Xử lý sự cố nhanh

| Thấy gì | Nguyên nhân thường gặp → cách xử lý |
|---|---|
| "daemon TẮT" | chưa chạy `daemon` hoặc đã dừng → chạy lại |
| Job lỗi ngay, tốn 0đ, nói thiếu khóa | `ANTHROPIC_API_KEY` chưa có trong `.env` |
| "Dừng trước khi gọi Claude (0đ)" (mã 3) | cổng khả thi: footage không đủ cảnh cho kế hoạch → thêm clip, giảm độ dài hoặc số bản |
| Job kịch bản trượt sau 3 vòng | xem bảng "Kiểm tra kịch bản AI": V4 lặp cảnh thường là thiếu footage; nút "Thử lại (duyệt lại)" yêu cầu duyệt chi phí mới |
| "chạm trần chi phí" | vòng kế tiếp có thể vượt số đã duyệt nên **không gửi**; duyệt số cao hơn nếu thấy xứng đáng |
| QC: T3 đỉnh âm thanh | tự xử lý: dựng lại một lần với trần đỉnh thấp hơn (hiện ở dòng "Tự chỉnh") |
| QC: T4 khung đen / T5 đứng hình | xem cảnh ở giây được báo; thường do clip nguồn hỏng hoặc quá ngắn |
| Job dựng bị báo "treo" | không có dòng log trong khoảng `stall` (video 3 phút đã nới tự động); xem log job |
| 409 `project_locked` | xem mục 8 |
| Cổng 8765 bận | tìm và tắt tiến trình `serve` cũ (đổi cổng cần build lại giao diện, xem mục 2) |
| Video 3 phút không chọn được | máy chủ chưa mở (`durations.180.enabled = false` trong `config/rules.json`, bật sau nghiệm thu thật) |
