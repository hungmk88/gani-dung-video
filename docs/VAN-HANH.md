# Sổ tay vận hành hằng ngày — App Dựng Video GANI

Dành cho người dùng máy dựng (Dell i5-8265U, Windows). Kỹ thuật chi tiết của API nằm ở
[`api-jobs.md`](api-jobs.md); thiết kế video 3 phút ở [`GD7-THIET-KE.md`](GD7-THIET-KE.md).
Mọi con số dưới đây lấy từ `config/rules.json` hoặc số đo thật; chỗ nào là ước tính thì ghi rõ.

---

## 1. Cài một lần

| Cần có | Ghi chú |
|---|---|
| Python ≥ 3.11 | cách chạy mã của dự án: xem "Cách để Python thấy mã dự án" ngay dưới bảng |
| ffmpeg + ffprobe trên `PATH` | thiếu thì dựng và kiểm kê đều báo lỗi có hướng dẫn |
| Node.js | chỉ để build giao diện: `cd frontend && npm ci && npm run build:real` |
| Tệp `.env` | chép `.env.example` thành `.env` rồi điền; **không dán khóa API vào chat hay vào code** |

**Cách để Python thấy mã dự án.** Mọi lệnh `python -m gani_dung_video …` trong sổ tay này cần
một trong hai cách sau. Chưa làm cách nào thì lệnh báo `No module named gani_dung_video`.

| Cách | Lệnh | Khi nào dùng |
|---|---|---|
| **A. `PYTHONPATH=src` (khuyến nghị)** | cửa sổ lệnh (cmd): `set PYTHONPATH=src`<br>PowerShell: `$env:PYTHONPATH = "src"`<br>Git Bash: `export PYTHONPATH=src` | luôn đúng với thư mục đang đứng, an toàn khi có nhiều bản làm việc (worktree) song song |
| **B. `pip install -e .`** | `pip install -e .` (thêm `".[ai]"` nếu máy chạy được nhận diện CLIP/torch) | chỉ khi máy chỉ có **một** bản mã duy nhất |

- Chạy từ **thư mục gốc của dự án** (nơi có `src\` và `.env`); `PYTHONPATH=src` là đường dẫn
  tương đối nên đứng sai thư mục sẽ không tìm thấy mã. Biến chỉ có hiệu lực trong cửa sổ lệnh
  đó, mở cửa sổ mới thì đặt lại.
- **Cảnh báo với cách B khi có nhiều worktree:** `pip install -e .` ghi cứng đường dẫn của **một**
  thư mục vào môi trường Python. Các worktree khác chạy `python -m gani_dung_video` sẽ vẫn nạp mã
  của thư mục đã cài, tức là chạy **nhầm mã** mà không có báo lỗi nào. Máy có nhiều worktree thì
  dùng cách A, và nếu đã lỡ cài thì gỡ bằng `pip uninstall gani-dung-video`.
- `scripts\chay-gani.bat` (biểu tượng "GANI Video") tự đặt `PYTHONPATH=src` và tự `cd` về thư mục
  dự án, nên nhấp đúp biểu tượng không cần làm gì thêm.

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

Mở `http://127.0.0.1:8765/`. Ô **"Máy dựng"** trên thanh trên cùng phải ghi **"đang chạy · N video
chờ/đang dựng"** (xanh; có thêm "· M chờ duyệt chi phí" thì ô chuyển vàng). Ghi **"máy dựng đang
TẮT — chạy lệnh daemon"**: job vẫn xếp hàng nhưng chưa chạy. Ghi **"không kết nối được máy
dựng"**: máy chủ `serve` chưa chạy. Cạnh đó là ô "Thư viện nhạc" và, khi `.env` thiếu khóa, ô
"Khóa Claude: chưa cài".

- Chỉ chạy **một** daemon mỗi hàng đợi (chạy thêm sẽ báo đã có daemon khác).
- Dừng: `Ctrl+C` = chạy nốt job đang làm rồi dừng; `Ctrl+C` lần hai = dừng ngay, job đang chạy
  được trả về hàng đợi. Hoặc từ cửa sổ khác: `python -m gani_dung_video queue stop` (thêm `--now`
  để dừng ngay).
- Mất điện / tắt máy đột ngột: khi daemon bật lại, job **phân tích và dựng** đang chạy tự trở lại
  hàng đợi (còn lượt thử). Job **kịch bản có gọi Claude thì không tự thử lại** (tránh tốn tiền
  hai lần): nó báo lỗi, bấm "Thử lại (duyệt lại)" để duyệt chi phí mới.
- Cổng 8765 bận: cách đơn giản nhất là tìm và tắt tiến trình đang giữ cổng 8765. Muốn dùng cổng
  khác thì chạy `serve --port <cổng>` rồi mở `http://127.0.0.1:<cổng>/`, không cần build lại:
  giao diện gọi API theo đường dẫn tương đối nên đi theo cổng của trang. Biểu tượng "GANI Video"
  (`scripts\chay-gani.bat`) thì ghi cứng cổng 8765, và `npm run dev:real` của giao diện chỉ
  chuyển `/api`, `/media` tới 8765 (`frontend/vite.config.ts`).

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
- Bài ngắn hơn video được lặp theo ô nhịp (X13). "Ngắn hơn" tính **phần nghe được** (từ nhịp đầu tiên của bài) so với độ dài video **cộng 2 giây khung kết**: bài đúng 15 giây cho video 15 giây vẫn được lặp, nếu không nhạc hết trước khung kết. Bài nhạc **tải lên phải tick "tôi có quyền sử
  dụng"** (X14), nếu không bị chặn.
- **Cảnh báo BPM:** mỗi bài nhạc làm kế hoạch dài ra vì khối nào cũng cần ít nhất một cảnh. Bài
  chậm hoặc nhanh có thể khiến video 15 giây lập kế hoạch dài hơn 16,5s và bị `E1-DUR-GAP` chặn khi giao. Nên
  khi chọn bài, hệ thống cảnh báo trước theo bảng `music.bpm_safe_by_style_duration` trong
  `rules.json` (đo thật bằng bộ lập kế hoạch, không phải một khoảng min–max):
  - `W_BPM_CUT`: phần cuối bị cắt bớt, trong ±10%, video vẫn dựng được;
  - `W_BPM_BLOCK`: kế hoạch lệch quá ±10%, video bị chặn khi giao; thông báo kèm các đoạn BPM
    dựng được.
  Giao diện hiện hộp vàng dưới danh sách bài với hai nút **Chọn bài khác** và **Vẫn dùng bài
  này** (ghi `bpm_override: true`). Lệnh `plan` (và hàng đợi) **dừng ngay ở bước plan** với mã
  thoát 4 nếu kế hoạch lệch quá ±10% mà chưa chọn dùng tiếp: chưa gọi Claude, 0đ. Muốn dùng tiếp:
  thêm `--bpm-override` vào lệnh `plan`, hoặc `bpm_override: true` trong `yeu-cau.md`. Bật cờ chỉ
  tắt cảnh báo; `E1-DUR-GAP` vẫn chặn khi giao nếu kế hoạch vẫn lệch quá ±10%.
- **Danh sách nhạc trên giao diện** đến từ `GET /api/music` (chế độ `real`); chế độ `mock` giữ
  4 bài thử nghiệm. Chưa đặt `GANI_DV_MUSIC_DIR` thì giao diện hiện hướng dẫn, không rơi về
  bài giả. Bài chưa có trong `library.json` được đo nền lần lượt từng bài (máy yếu), cache ở
  `music-cache.json` cạnh `queue.db`; `library.json` và thư mục nhạc không bao giờ bị ghi.
  `library.json` nhận thêm khóa tùy chọn `title` và `rights` (`thu_vien_noi_bo`,
  `mua_ban_quyen`, `royalty_free`); bản chương trình cũ từ chối khóa lạ nên cập nhật cùng lúc.
- **Kiểm thư mục nhạc trước khi dùng** (chỉ đọc, không ghi gì):
  `python -m gani_dung_video music-check [thư mục nhạc] [--chi-tiet] [--ca-180]`. Không truyền
  thư mục thì lấy `GANI_DV_MUSIC_DIR`. In từng bài (BPM, nguồn `library` / `measured` /
  `unknown`, độ dài) và, cho mỗi phong cách × độ dài, số bài **ok** (khít), **cắt** (cắt đuôi,
  trong ±10%), **chặn** (bị chặn khi giao) và **lưới** (không đo được nhịp), kèm tên các bài dùng
  được. Kết luận lấy từ chính bộ lập kế hoạch ở đúng BPM của bài, không tra bảng. Bài chưa có
  trong `library.json` được đo ngay (vài giây mỗi bài); BPM `measured` có thể ra nửa hoặc gấp
  đôi, nên nghe lại rồi khai báo vào `library.json`. Cũng báo bài khai báo mà không còn file.
- Đổi phong cách, độ dài, quy tắc nhịp thì tạo lại bảng: `sweep_table` trong
  `domain/bpm_measure.py`; `tests/test_bpm_safety.py` quét lại bằng bộ lập kế hoạch và so với bảng.
  Bản sao cho giao diện nằm ở `frontend/src/data/bpmSafe.ts` (test của giao diện so với `rules.json`).

## 5. Làm một video trên giao diện

1. Tab **Tạo video mới** → chọn **dự án** → chọn phong cách, mục đích, độ dài, số bản nháp, nhạc.
   Khung **Preflight** bên phải báo đỏ (bị chặn), vàng (dùng được có lưu ý) hay xanh **trước khi
   tốn gì**. Nút "3 phút" bị khóa nếu máy chủ chưa mở video 3 phút.
2. **Chạy cả chuỗi**: phân tích footage (0đ, chậm: vài chục phút, vì nhận dạng hình và lời trên
   CPU; chạy lại dự án không đổi thì dùng lại kết quả) → kịch bản → dựng.
3. Khi tới kịch bản, job **dừng chờ duyệt chi phí** (tab **Video đang làm**, nút "Xem & duyệt
   chi phí"):
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
5. Ưng bản nào thì dựng **bản cuối 1080×1920**: tab **Tạo video mới** → chọn dự án → chế độ
   **Chỉ dựng lại**, chọn đúng bản nháp, tick "Dựng bản cuối 1080×1920" (sản phẩm và chiến dịch
   lấy theo lần chạy cả chuỗi gần nhất của dự án, xem mục 9c). Nút **Dựng lại** trên trang video chỉ dựng lại đúng
   cỡ của video đó, không đổi nháp thành bản cuối. Tải về bằng nút **"Tải bản cuối"**.
   `cover.jpg` và `phude.srt` nằm cùng thư mục làm việc (mục 9).

### Video có người nói: cắt theo lời (từ 26/10/2026)

- **Bán hàng có lời** luôn cắt theo lời nói.
- **Review món ăn** và **Kể chuyện nghề** tự chọn cách cắt theo footage: bước phân tích nhận
  ra **có lời nói** (`edit/speech.json`, `co_loi_noi`) thì video cắt theo câu nói giống Bán hàng
  có lời. B-roll món ăn hay xưởng phủ lên giữa câu theo phách (2 hoặc 4 phách), nhạc hạ −18 dB
  khi có người nói, phụ đề chạy theo từng chữ, kịch bản AI được soát bằng S1–S6. Footage không
  lời thì cắt theo phách nhạc như trước.
- Video **3 phút** của hai phong cách này vẫn cắt theo nhạc: chế độ lời 3 phút chưa làm (GĐ7f).
- Câu nào có cụm cấm (`banned_claims`, ví dụ "ngon nhất", "số 1") bị **bỏ khỏi bản cắt**, không
  đưa cho AI chọn, nên video vẫn dựng được. Câu bị bỏ được liệt kê trong `edit/phrases.json`.
- Khi quay review: người nói tránh các cụm cấm ngay từ đầu (đỡ mất câu hay); không tự chấm điểm
  sản phẩm GANI ("9/10"); so sánh với hàng hay thương hiệu khác thì đưa NVH xem trước.
- Số liệu nằm trong `config/rules.json` → `styles.<phong cách>`: `driver_if_speech`,
  `broll_beats`, `pad_ms`, `duck_db` (chép từ Bán hàng có lời, chủ duyệt 26/10/2026),
  `silence_keep` (của từng phong cách).

### Zoom tự động (từ 26/10/2026)

| Phong cách | Kiểu zoom | Khi nào |
|---|---|---|
| TVC cắt nhanh | Zoom bật vào 5% | Ở phách thứ hai của cảnh dài từ 2 phách, tối đa 3 lần mỗi video, rải đều |
| Bán hàng có lời | Khung gần hơn 1,12× | Cứ một câu thì câu sau quay gần hơn, che chỗ nối hai câu cùng một lần quay |
| Review món ăn (có lời) | Zoom bật vào 8% | Đúng lúc người nói nói từ khen vị ("ngon", "giòn", "thơm", "đáng tiền"…) khi đang thấy mặt, tối đa 3 lần |

- Chữ, logo và khung kết không bị zoom (chồng lên sau).
- Clip **không đủ điểm ảnh thì không zoom** để hình không bị nhòe: clip dọc 1080×1920 zoom được,
  clip 720p hoặc clip quay ngang thì không. Quay review bằng điện thoại để **dọc, 1080p hoặc 4K**.
- Số liệu và danh sách từ nằm trong `config/rules.json`: `styles.<phong cách>.punch_zoom`,
  `zoom_alternate`, `keyword_zoom`, `max_effects`; `effects.keywords`, `effects.max_upscale`,
  `effects.keyword_min_hold_sec`. Muốn tắt một kiểu thì xóa khóa đó ở phong cách.

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

## 6a. Bộ lọc hoàn thiện (khử ồn, tông màu): đang TẮT

Hai bộ lọc tùy chọn nằm trong `config/rules.json` → `render`. **Mặc định tắt**; chỉ bật sau khi chủ
đã nghe/xem thử và đồng ý. Đổi `"enabled": false` thành `true` rồi dựng lại, không cần sửa mã.

| Bộ lọc | Khóa | Làm gì | Chi phí dựng (đo 37 s, 1080p) |
|---|---|---|---|
| Khử ồn | `denoise` (`nr` 12, `nf` -40) | `afftdn` chỉ trên tiếng camera/tiếng nói, trước khi trộn nhạc | +0 s (84 → 84) |
| Tông màu thực phẩm | `grade` (`strength` 0,4) | tươi + ấm nhẹ (`eq` + `lutyuv`), không đụng chữ và khung kết | +0 s (84–89 → 87) |

`strength` từ 0 đến 1 nhân mọi mức chỉnh (0 = không đổi). Giữ nhẹ để giò chả không ngả hồng/đỏ hơn
thực tế. **Lưu ý khử ồn ở chế độ lời:** bước T8 nghe lại video đã dựng, nên nếu bật khử ồn thì T8
nghe âm thanh đã khử ồn. Phép thử A/B trên 8 clip xưởng chưa cho thấy khử ồn giúp Whisper nghe đúng
hơn, nên đừng bật khử ồn cho chế độ lời trước khi người nghe thử xác nhận.

## 6b. Từ nhấn trong phụ đề và chốt chặn C1

- **Từ nhấn:** danh sách nằm ở `config/glossary.json` → `caption_highlight_keywords` (hiện chỉ
  `"HACCP"`, vì đã có giấy chứng nhận HACCP Codex 2022 do VCA cấp). Bản nhạc: từ nhấn đổi sang vàng và
  phóng nhẹ; bản có lời: từ nhấn luôn to hơn, vàng khi được đọc. Không áp cho dòng CTA và không áp
  cho file `.srt`. Muốn thêm từ: chỉ thêm khi chủ và NVH đã xác nhận có chứng cứ (không thêm
  "không hàn the", "thịt sạch" khi chưa có kiểm nghiệm).
- **C1 (Claims & Compliance):** trước khi gọi ffmpeg, mọi chữ của video (chữ trên màn hình, tiêu đề
  chương, CTA, lời nói trong chế độ lời, từ nhấn) bị soát với `banned_claims`. Có vi phạm thì **dừng,
  không dựng**, và báo "C1: …" kèm nguồn chữ và cụm cấm. Ngoài ra, nạp `glossary.json` cũng dừng nếu một
  từ nhấn chứa cụm cấm, trống hay bị lặp. Soát không phân biệt hoa thường và dấu thanh.

## 6c. Logo và số Zalo (từ 14/10/2026)

Mọi video mang logo thật của GANI và số **Zalo 0853 9999 77**. Thông số nằm trong
`config/rules.json` → `render.brand`; đổi số ở đó, không cần sửa mã.

- **Logo trong video:** lá và chữ GANI (`assets/brand/logo-video.png`). Logo chỉ hiện **từ giây
  thứ 5** (`logo_start_sec`), sau đó cứ **4 giây** (`logo_hop_sec`) lại nhảy sang vị trí kế tiếp
  trong `logo_positions`: trên trái → trên phải → giữa bên trái → lặp lại. Logo không đứng yên
  một chỗ. Mọi vị trí đều nằm trong vùng an toàn, không đè tiêu đề chương, phụ đề hay cột nút
  bên phải của TikTok (một test kiểm điều này). Độ rộng 170 px trên khung 1080, mờ 80%.
- **Khung kết 2 giây:** logo đầy đủ trên nền tối (`assets/brand/logo-khung-ket.png`), dưới là số
  Zalo và dòng "GANI FOODS – Đạt chuẩn HACCP Codex 2022".
- **Đổi logo:** thay `assets/brand/logo-goc.ai` (file Illustrator của bên thiết kế) rồi chạy
  `python scripts/tao_logo_video.py` (cần `pdftocairo` và Pillow, chỉ dùng khi tạo lại ảnh).
  Muốn quay về logo chữ thì xóa khóa `logo_image`.

## 6d. Đăng TikTok và Facebook Reels (từ 16/10/2026)

GANI đăng mỗi video lên **TikTok** và **Facebook Reels** (có chạy quảng cáo, không gắn giỏ hàng).
Ở bước **"Đăng lên"** của Wizard, hai ô đều được chọn sẵn; bỏ ô nào thì không dựng bản đó. Mỗi
nền tảng là **một video riêng**, dựng từ **cùng** footage, kịch bản và nhịp cắt: chọn 2 nền tảng
thì thời gian dựng gấp đôi nhưng **không tốn thêm tiền AI**.

| | TikTok | Facebook Reels |
|---|---|---|
| Câu kêu gọi (Bán hàng) | "Bình luận để GANI tư vấn" | "Nhắn tin đặt hàng" (như mục đích) |
| Câu kêu gọi (Giới thiệu xưởng) | "Bình luận để GANI liên hệ" | "Nhắn tin nhận báo giá sỉ" |
| Khuyến mãi, Kể chuyện thương hiệu | như mục đích | như mục đích |
| Khung kết | logo + dòng GANI FOODS, **không** có số Zalo | logo + Zalo + dòng GANI FOODS |
| Tên file giao | `…_9x16_tiktok_v1_….mp4` | `…_9x16_facebook_v1_….mp4` |

- Khách TikTok liên hệ bằng bình luận, tin nhắn TikTok và link trong hồ sơ, nên bản TikTok tránh
  các từ đưa người xem ra ngoài ứng dụng (nhắn tin, inbox, Zalo, sỉ…). Đổi câu chữ trong
  `config/rules.json` → `platforms.profiles.<nền tảng>.cta` (không dùng ô `[ngày]`).
- Trang chi tiết video có nút chuyển **"Bản A · TikTok / Bản A · Reels"**; khung "Duyệt video
  cuối" có một dòng cho từng bản, duyệt riêng. Thư mục làm việc: `edit/render/A-tiktok/`,
  `edit/render/A-facebook/`.
- **Cảnh báo từ ngữ rủi ro** (mục "Từ ngữ rủi ro theo nền tảng" trong khung soát chất lượng,
  biểu tượng vàng): chữ trên màn hình hoặc lời nói có từ nền tảng đó hay hạn chế, ví dụ bản TikTok
  có "nhắn tin", "Zalo", "giá sỉ", "giảm giá"; bản nào cũng tránh "máu", "chết", "ngộ độc", nhắc sàn
  khác. **Chỉ cảnh báo, không chặn giao hay duyệt**; người duyệt quyết định. Danh sách nằm ở
  `config/glossary.json` → `platform_risk_words` (so theo âm tiết, giữ dấu: "màu" không bị nhầm là
  "máu"), tách khỏi danh sách cụm cấm của NVH. Đây là kinh nghiệm của người làm nội dung, không
  phải chính sách công bố của TikTok hay Meta.
- Dòng lệnh: `render <thư mục> --nen-tang tiktok` (bỏ `--nen-tang` = không theo nền tảng, như
  trước); `qc … --nen-tang tiktok` để soát lại đúng bản đó.
- **Không nhúng nhạc** (từ 16/10/2026): ô "Không nhúng nhạc — chèn nhạc trên app khi đăng" ở
  bước "Đăng lên". Dùng khi chạy quảng cáo: quảng cáo TikTok và Facebook cần nhạc có bản quyền của
  chính nền tảng, chèn trên app lúc đăng. Kịch bản, nhịp cắt và độ dài **giữ như kế hoạch** (vẫn
  chọn nhạc ở bước 3 để có nhịp), chỉ bỏ nhạc khỏi file:
  - Video còn lời nói (chế độ lời) và tiếng hiện trường, chuẩn âm lượng như thường. Phong cách tắt
    tiếng máy quay (như TVC cắt nhanh) thì video **không có tiếng**; soát chất lượng ghi "video
    không tiếng: không nhúng nhạc", không báo lỗi. Trên app, chỉnh âm lượng "âm thanh gốc" khi
    chèn nhạc.
  - Tên file giao có thêm `khong-nhac`: `…_9x16_tiktok_khong-nhac_v1_….mp4`; `manifest.json` và
    `render_log.json` có `"no_music": true`.
  - Áp cho mọi bản của lần gửi; dựng lại một bản có nhạc thì bản mới thay bản cũ trong cùng thư
    mục làm việc (bản đã giao vẫn nằm ở thư mục giao với tên riêng).
  - Bản có nhạc và bản không nhạc của cùng một bản nháp, cùng nền tảng, dùng chung thư mục làm
    việc: khi một bản còn đang chờ hoặc đang dựng, gửi bản kia sẽ bị từ chối kèm số job (không xếp
    gì thêm). Chờ job đó xong hoặc hủy nó rồi gửi lại.
  - Dòng lệnh: `render … --khong-nhac`, `qc … --khong-nhac`, `queue add … --khong-nhac`.

## 7. Nhãn QC T8 (chế độ lời)

T8 là bước Whisper **nghe lại** video đã dựng để đo sai lệch lời và độ lệch phụ đề; chỉ chạy cho
**bản cuối của chế độ lời**. Các nhãn `qa.t8` (`ran`, `skipped_draft`, `skipped_music`,
`skipped_no_asr`, `unavailable`) và cách giao diện hiển thị nằm trong `api-jobs.md`, mục
"T8 (Whisper nghe lại thành phẩm)". Video chế độ nhạc (kể cả 3 phút) không có T8.

## 7a. Dung sai số khung hình của T2 (`frame_slack_sec`)

T2 đếm từng khung hình của file xuất và so với độ dài × fps. Bộ mã H.264 sắp lại B-frame, nên khi
`-shortest` cắt giữa một nhóm khung, 2–3 khung cuối rơi khi giải mã: đó là hành vi bình thường của
bộ mã, không phải lỗi (phong cách `theo_mo_ta` từng ra 507 khung thay vì 510 và bị chặn nhầm).

Dung sai là `max(frame_slack, làm tròn lên nửa(fps × frame_slack_sec))` khung, trong khối `qc` của
`config/rules.json`: `frame_slack = 1` (sàn, cho fps rất thấp hoặc metadata lỗi) và
`frame_slack_sec = 0,15` (giây). Ví dụ: 24 fps → 4 khung, 30 fps → 5, 60 fps → 9. Lệch hơn dung sai
(ví dụ mất 10 khung ở 30 fps) vẫn bị chặn. Dung sai độ dài T1 vẫn dùng `frame_slack` cũ.

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
| `render/<A>/nhap_A_540p.mp4`, `video_A_1080x1920.mp4` | video nháp / cuối; bản theo nền tảng nằm ở `render/<A>-<nền tảng>/` (ví dụ `render/A-tiktok/`, mục 6d), các tệp dưới đây cũng vậy |
| `render/<A>/cover.jpg`, `phude.srt`, `qc_report.json`, `render_log.json` | ảnh bìa, phụ đề, kết quả QC, nhật ký dựng để dựng lại đúng bản này |
| `render/<A>/manifest.json` | hồ sơ truy vết của video: tùy chọn, mã băm `plan.json`/`rules.json`/`glossary.json`, model, file nguồn, kết quả E1, chi phí thật (mục 9a) |
| `delivery/<chiến-dịch>/{raw,drafts,final,metadata}` | bản đã đạt kiểm tra, đặt tên theo quy ước (mục 9a) |

Nhật ký từng job (khi cần gỡ lỗi): `<GANI_DV_QUEUE_DIR>/logs/<số job>.log`.

## 9a. Kiểm tra E1, manifest và giao hàng (Dev Package 03, T-E)

Sau mỗi lần dựng, ngoài T1–T8 còn có **E1**: tám mục pass/fail kèm số đo, ghi trong
`manifest.json` (mỗi mục có `value`, `limit`, `detail`); bản dựng theo nền tảng có thêm mục thứ
chín `E1-NEN-TANG`, chỉ cảnh báo. Mục nào trượt (mức chặn) thì video **không được giao** và
`qc_passed` là sai, giống một lỗi QC.

| Mục | Đo gì | Ngưỡng (`rules.json` → `qc`) |
|---|---|---|
| `E1-DUR` | độ dài nội dung (file trừ khung kết) | sai số `duration_tolerance_sec`: ≤ 60 giây ±0,5; 180 giây ±1; đem so với `duration_reference` (`plan` hoặc `chosen`, xem dưới) |
| `E1-DUR-GAP` | độ dài kế hoạch (tổng phách, trước khi cắt đuôi) so với độ dài đã chọn; chạy với mọi `duration_reference` | `duration_plan_gap_pct` = ±10% (15 giây ±1,5s; 180 giây ±18s) |
| `E1-FORMAT` | độ phân giải, tỷ lệ 9:16, codec, số khung hình (T1, T2) | đúng cấu hình |
| `E1-LOUD` | âm lượng, đỉnh thật, không clip (T3) | −14 ±1 LUFS, đỉnh ≤ trần |
| `E1-PICTURE` | khung đen (chặn), hình đứng (chỉ cảnh báo) (T4, T5) | `black_min_sec`, `freeze_min_sec` |
| `E1-SAFE` | chữ nằm trong vùng an toàn 1080×1920 | `safe_zone`: trên 250, dưới 400, trái 60, phải 60 (px) |
| `E1-GLYPH` | mọi ký tự có glyph trong font (dấu tiếng Việt không thành ô trống) | 0 ký tự thiếu |
| `C1` | không cụm cấm trên màn hình (cổng C1 trước ffmpeg vẫn chặn trước khi dựng) | `glossary.json` → `banned_claims` |
| `E1-NEN-TANG` | chỉ ở bản theo nền tảng: từ ngữ nền tảng đó hay hạn chế, trên màn hình hoặc lời nói (mục 6d). **Chỉ cảnh báo**, mục luôn đạt, không chặn giao hay duyệt | `glossary.json` → `platform_risk_words` |

**Độ dài:** video ra **đúng độ dài đã chọn** (15/30/45/60/180 giây; chưa tính khung kết). Quy tắc
chốt 13/10/2026 ("hướng B"): giữ số phách đã lập, chỉ đổi độ dài phách.

- **Không nhạc (lưới ảo):** sau khi lập kế hoạch, 1 phách = độ dài chọn ÷ số phách đã lập. Video
  15 giây: Kể chuyện nghề 0,75s (giữ), TVC 1,3 → 1,25s, Review 1,0 → 0,9375s, Sang trọng
  0,667 → 0,625s. Độ lệch tối đa 6,7% ở 15 giây, từ 30 giây trở lên không quá 2,2%.
  Kế hoạch ghi dòng "Tự điều chỉnh Co giãn lưới ảo…".
- **Có nhạc:** nhịp bài cố định nên không co giãn được. Bộ lập kế hoạch thử các tổng phách quanh
  mục tiêu và chọn bản cần cắt ít nhất. `qc.duration_trim_threshold_sec` (0,5s) là ngưỡng:
  - bản trong ngưỡng (±0,5s): giữ nguyên, bản gần độ dài chọn nhất thắng;
  - không có bản trong ngưỡng: lấy bản dài hơn ngắn nhất, **cắt** ở đúng độ dài chọn, hình và
    tiếng mờ dần theo `qc.duration_trim_policy` (`fade_0.5s` = 0,5 giây cuối) rồi tới khung kết.
    Đoạn cắt còn lại ngắn hơn thời gian mờ dần thì nhập vào cảnh trước;
  - chỉ còn bản ngắn hơn: dùng bản dài nhất (E1-DUR sẽ báo).
  Tắt cắt bằng `duration_trim_policy = "none"` (lưới ảo vẫn co giãn).
- **Kế hoạch lệch quá ±10%:** mỗi khối cần ít nhất một cảnh (4–8 phách), nên bài nhạc chậm làm
  khối dài ra và kế hoạch vượt xa độ dài chọn dù đã cắt đuôi. Khi đó lúc lập kế hoạch có cảnh báo
  `W_DUR_GAP`, và lúc giao `E1-DUR-GAP` chặn (đo theo độ dài tự nhiên của các phách, trước khi
  cắt, để cắt đuôi không che được). Cách xử lý: chọn bài có BPM hợp phong cách hoặc độ dài dài hơn.
- Kiểm tra: `E1-DUR` đo **so với độ dài đã chọn** (`duration_reference` = `"chosen"`), sai số
  ±0,5s (≤ 60 giây) hoặc ±1s (180 giây). `E1-DUR-GAP` vẫn giữ: kế hoạch không được lệch độ dài
  đã chọn quá ±10%. Khoảng mờ dần trước khung kết không bị tính là khung đen (T4).
- Chế độ lời: độ dài chạy theo lời nói nên dung sai rộng hơn nhưng có biên cứng, đo so với độ dài
  đã chọn (`duration_tolerance_voice_sec`, lấy giá trị lớn hơn): ≤ 60 giây ±25% hoặc ±7s
  (30 giây: ±7,5s); 180 giây ±20% hoặc ±20s (±36s). Trong biên: cảnh báo, ghi manifest, cho qua.
  Ngoài biên: chặn (video chọn 30 giây ra 17,3s lệch −12,7s bị chặn).
- Manifest luôn ghi cả hai độ lệch (giây, âm = ngắn hơn), ở `deviation_vs_chosen` và
  `deviation_vs_plan`, bất kể `duration_reference`.

**Giao hàng:** video đạt cả bộ kiểm tra được chép sang `edit/delivery/<chiến-dịch>/drafts/` với tên
`GANI_{sản-phẩm}_{phong-cách}_{mục-đích}_{độ-dài}s_9x16_{nền-tảng}_{nhạc}_v{n}_{YYYYMMDD}.mp4`
(chữ thường, gạch ngang, không dấu; `{nền-tảng}` chỉ có ở bản theo nền tảng, `{nhạc}` =
`khong-nhac` chỉ có ở bản không nhúng nhạc, phần trống bị bỏ cùng một dấu `_`; `v{n}` tăng theo
số bản cùng ngày, tính cả `drafts/` lẫn `final/`), manifest sang `metadata/`. `final/` chỉ nhận
video đã được người duyệt (mục 9b) và `raw/` dành cho footage gốc (không chép vào đây).
Đặt sản phẩm và chiến dịch bằng cờ của lệnh dựng: `render <thư mục> --san-pham "Giò lụa"
--chien-dich "Ra mắt tháng 10"`; không đặt thì sản phẩm là `chung` và chiến dịch là tên thư mục
footage. Sản phẩm ngoài danh mục `delivery.naming.products` vẫn được nhưng manifest có cảnh báo.

**Chi phí mỗi video** (manifest → `cost`): token và đồng thật đọc từ `ai_log.jsonl` của lượt viết
kịch bản gần nhất (phần của bản nháp này = chi phí mỗi vòng chia đều cho các bản nó viết), cộng thời
gian dựng thật. Bản nháp ghép tay chưa gọi Claude thì ghi rõ lý do thay vì số 0.

## 9b. Duyệt video cuối (T-F)

Một **bản cuối 1080×1920** đã qua mọi kiểm tra nằm ở `edit/delivery/<chiến-dịch>/drafts/` với
`review.status = pending` trong manifest. Bản **nháp 540p không bao giờ được duyệt**. Người duyệt
quyết định **trên giao diện** (tab "Video đang làm" → chọn video → khung "Duyệt video cuối": ghi
tên, bấm "Duyệt video" hoặc ghi lý do rồi bấm "Yêu cầu sửa"), bằng dòng lệnh, hoặc qua API (cuối
mục này). Giao diện nhớ tên người duyệt trên máy đó và hỏi xác nhận trước khi gửi. Nút "Duyệt
video" bị khóa khi còn mục kiểm tra hỏng (danh sách hiện ngay trên nút).

| Việc | Lệnh | Kết quả |
|---|---|---|
| Xem danh sách chờ | `python -m gani_dung_video review list` (thêm `--trang-thai approved\|changes_requested\|tat-ca`, `--tu-ngay 2026-10-01`, `--limit 50`) | một dòng mỗi video: số job, trạng thái, tên tệp, "QUÁ HẠN" nếu chờ lâu |
| Duyệt | `review approve ID --nguoi-duyet "Tên"` (thêm `--ghi-chu "..."` nếu muốn ghi chú) | tệp **chuyển** sang `final/`, manifest ghi người và giờ |
| Yêu cầu sửa | `review reject ID --ly-do "chữ cuối bị che"` | tệp ở lại `drafts/`, lý do ghi vào manifest; **bắt buộc có lý do** |

- `ID` là số job dựng bản cuối (cột `#` của `review list`). Không đặt `--nguoi-duyet` thì dùng tên
  người dùng của hệ điều hành.
- **Duyệt cần MỌI mục kiểm tra E1 trong manifest đạt.** Mục nào hỏng thì bị từ chối và liệt kê
  từng mã ("E1-SAFE: …"); `review reject` thì vẫn bấm được. Chỉ mục **hỏng** mới chặn: cảnh báo
  (`warn`, ví dụ lệch độ dài trong biên chế độ lời) và `manifest.warnings` không chặn duyệt.
- Một video đã quyết định (duyệt hay yêu cầu sửa) **không đổi được nữa**; bấm lần hai bị từ chối,
  không ghi gì. Muốn làm lại thì dựng lại: video mới có phiên bản `v{n}` mới và chờ duyệt riêng.
- **Quá hạn:** video chờ lâu hơn `review.ttl_sec` (172800 giây = 48 giờ, trong `config/rules.json`)
  được **nhắc một lần**: sự kiện cảnh báo trên job, `reminded_at` trong manifest. Không tự từ chối;
  vẫn duyệt được sau đó. Việc nhắc do `daemon` làm (mỗi 60 giây): daemon tắt thì không nhắc.
- Video giao **trước** T-F (manifest không có khối `review`) được coi là chờ duyệt: danh sách có thể
  dài. Lọc bằng `--tu-ngay`.
- Manifest có thẩm quyền là `metadata/<tên>.manifest.json`; manifest ở `edit/render/<bản>/` bị
  ghi đè mỗi lần dựng lại bản đó nên chỉ được cập nhật khi vẫn là cùng video.
- Hai người bấm cùng lúc (hai cửa sổ, hoặc dòng lệnh và API) vẫn an toàn: tệp khóa
  `<tên>.manifest.json.review.lock` cho từng người một; người sau thấy trạng thái đã quyết.

**API** (xem `docs/api-jobs.md` mục 4c): `GET /api/reviews`, `POST /api/jobs/{id}/review/approve`,
`POST /api/jobs/{id}/review/reject`. Chưa có xác thực: máy chủ chỉ nghe `127.0.0.1` và vẫn kiểm
Host/Origin; chỗ cắm cho bot Telegram sau này là hàm `authorize_reviewer`.

## 9c. Hạn chế đã biết

- **Có nhạc, cắt đuôi:** phần cuối bị cắt thuộc khối cuối của video (thường là cảnh kêu gọi).
  Đo lại ngày 15/10/2026 trên 620 tổ hợp phong cách × độ dài × BPM 55–145 (bước 3): 19% bị cắt
  (118), trung vị 0,71s; 113 ca trong ±10%, 5 ca (0,8%) vượt ±10% và bị `E1-DUR-GAP` chặn khi
  giao (3 ca Sang trọng 15 giây ở 103–109 BPM, 1 Kể chuyện nghề 15 giây, 1 Theo mô tả 15 giây).
  Ca tệ nhất: Sang trọng 103 BPM, 15 giây, kế hoạch 17,5s (+17%).
  Hai thay đổi đã giảm số ca bị chặn (trước đó 21 ca, 3,4%; ca tệ nhất 21,6s, +44%):
  · 14/10/2026: khi cách đếm X12 chọn theo cảm giác nhạc không thể vừa độ dài, bộ lập kế hoạch
    thử cách đếm khác vẫn trong dung sai BPM của phong cách và giữ cách gần độ dài nhất (ví dụ
    Sang trọng 100 BPM: đếm 100 thay vì 50; ghi trong `adjustments`).
  · 15/10/2026 (chủ duyệt): Sang trọng có thêm cảnh 5 phách (`beats_allowed` [6, 8] →
    [5, 6, 8]). Cảnh 5 phách dài 3,5–5,0s ở 60–85 BPM, trong `scene_sec` 3–5s. Bảng BPM: 15 giây
    còn 7 BPM bị chặn (103–109) thay vì 53; 30 giây còn 0 thay vì 7; số BPM phải cắt đuôi ở 30/45/
    60 giây và 3 phút giảm từ ~50 xuống 4–12. Video Sang trọng không nhạc: lưới ảo 0,8s/phách
    (trước 0,67s), vì một phách lưới = cảnh trung bình 4s chia cho cảnh ngắn nhất.
- **Hộp cảnh báo BPM chưa được thử tay với nhạc thật của xưởng.** Đã thử trên máy dựng thật
  với bài tổng hợp (ca vừa khít, cắt đuôi, không dựng được, đang đo, không đo được); giao diện
  chỉ có test tự động cho hàm thuần, không có test component: vẫn cần thử tay với nhạc thật và
  `library.json` thật.
- Danh sách nhạc không đệ quy thư mục con và tối đa 500 bài. Liên kết tượng trưng ra ngoài
  thư mục nhạc bị bỏ qua (test liên kết bị bỏ qua trên Windows khi không đủ quyền tạo).
- Bảng chỉ biết BPM nguyên trong 55–180 (làm tròn gần nhất): bài ngoài dải này không được kết luận
  bằng bảng, bước `plan` vẫn đo chính xác.
- Giao diện (web) chọn được sản phẩm và chiến dịch ở bước "Tên file giao" (từ 14/10/2026):
  hai trường đi cùng tùy chọn vào `edit/options.json` rồi tới lệnh `render`. Bỏ trống thì sản
  phẩm là `chung` và chiến dịch là tên thư mục footage. Chế độ "Chỉ dựng lại" không gửi tùy
  chọn: từ 19/10/2026 nó dùng sản phẩm và chiến dịch đã lưu ở `edit/options.json` của lần chạy
  cả chuỗi gần nhất (trước đó luôn rơi về mặc định); dự án chưa chạy chuỗi nào thì dùng mặc định.
- Hàng đợi đã truyền `--san-pham` và `--chien-dich` (từ 06/10/2026): job đọc hai trường từ
  `yeu-cau.md` (khối `---`, xem Phụ lục D) và thêm vào lệnh con. Thiếu trường nào thì mặc định
  như trên. `san_pham` ngoài danh mục `rules.delivery.naming.products` vẫn chạy, chỉ ghi cảnh
  báo vào manifest. Cờ CLI `--san-pham` / `--chien-dich` vẫn ghi đè được khi gõ `render` tay.
- Biến thể đã bắt thêm (14/10/2026): `trăm phần trăm` / `một trăm phần trăm` viết thay "100"
  (trừ khi có số khác đứng trước, "hai trăm phần trăm" là 200), `100pt`, chữ gõ lặp
  (`trămm`, `nhấtt`), chữ Cyrillic hoặc Hy Lạp trông giống chữ Latin (`sо 1` với "о" Cyrillic),
  chữ số gõ cách nhau ngay trước dấu % hoặc "phần trăm" (`1 0 0 %`).
- Biến thể chưa bắt (theo dõi, bổ sung nếu gặp thực tế):
  · `100 percent natural` (tiếng Anh — ngoài phạm vi bộ lọc tiếng Việt)
  · Các tuyên bố tương đương khác chỉ bị chặn khi NVH thêm cụm vào `banned_claims` (danh sách đã
    chốt, app không tự thêm).
- Không chặn theo quyết định của NVH (17/10/2026): `100 phần trăm nguyên chất`. Lưu ý dạng viết số
  `100% nguyên chất` vẫn bị chặn vì chứa cụm `100%`.
- Không coi là cụm cấm (14/10/2026): cụm bắt đầu ở âm tiết thứ hai của một từ có sẵn trong
  danh sách `_WORD_BEFORE` (`domain/glossary.py`: giá trị, quản trị, chính trị, cai trị, thống
  trị, văn phòng, sửa chữa), nên "giá trị bệnh nhân" không bị bắt là "trị bệnh"; số có dấu thập
  phân hoặc dấu nghìn là một số ("số 1,5 kg", "số 1.000 khách" không bị bắt là "số 1"). Vẫn
  bị bắt: "trị bệnh nhân", "điều trị bệnh", "top số 1, giá tốt". Còn bắt nhầm: "bước số 1",
  "phòng số 1" ("số 1" theo nghĩa thứ tự). Cần NVH xem lại danh sách từ.

## 10. Xử lý sự cố nhanh

| Thấy gì | Nguyên nhân thường gặp → cách xử lý |
|---|---|
| Ô "Máy dựng" ghi "máy dựng đang TẮT" | chưa chạy `daemon` hoặc đã dừng → chạy lại |
| Job lỗi ngay, tốn 0đ, nói thiếu khóa | `ANTHROPIC_API_KEY` chưa có trong `.env` |
| "Dừng trước khi gọi Claude (0đ)" (mã 3) | cổng khả thi: footage không đủ cảnh cho kế hoạch → thêm clip, giảm độ dài hoặc số bản |
| Job kịch bản trượt sau 3 vòng | xem bảng "Kiểm tra kịch bản AI": V4 lặp cảnh thường là thiếu footage; nút "Thử lại (duyệt lại)" yêu cầu duyệt chi phí mới |
| "chạm trần chi phí" | vòng kế tiếp có thể vượt số đã duyệt nên **không gửi**; duyệt số cao hơn nếu thấy xứng đáng |
| QC: T3 đỉnh âm thanh | tự xử lý: dựng lại một lần với trần đỉnh thấp hơn (hiện ở dòng "Tự chỉnh") |
| QC: T4 khung đen / T5 đứng hình | xem cảnh ở giây được báo; thường do clip nguồn hỏng hoặc quá ngắn |
| Job dựng bị báo "treo" | không có dòng log trong khoảng `stall` (video 3 phút đã nới tự động); xem log job |
| 409 `project_locked` | xem mục 8 |
| Cổng 8765 bận | tìm và tắt tiến trình `serve` cũ, hoặc `serve --port <cổng khác>` (không cần build lại; biểu tượng "GANI Video" chỉ dùng 8765, xem mục 2) |
| `review approve` báo "đang được người khác duyệt" | tệp khóa `.review.lock` còn từ lần trước; quá 2 phút tự được bỏ qua. Xóa tay tệp đó nếu chắc không ai đang duyệt |
| Duyệt nhầm / cần hoàn tác sau khi duyệt | không có lệnh hoàn tác. Làm tay 4 bước: (1) chuyển tệp từ `final/` về `drafts/`; (2) mở `metadata/<tên>.manifest.json`, sửa `review.status` về `pending` (xóa `decided_at`, `decided_by`, `reason`) hoặc xóa cả khối `review`; (3) làm y như vậy với `edit/render/<bản>/manifest.json` nếu `delivered_as` của nó cùng tên; (4) `review list` để kiểm lại. Manifest cũ (v1) vẫn đọc được nên không mất dữ liệu |
| Video 3 phút không chọn được | máy chủ chưa mở (`durations.180.enabled = false` trong `config/rules.json`, bật sau nghiệm thu thật) |
| "ffmpeg … quá cũ" hoặc `Unrecognized option '/filter_complex'` | ffmpeg dưới 7.0 (Ubuntu 24.04 mặc định chỉ có 6.1) → cập nhật ffmpeg lên 7.0 trở lên, kiểm bằng `ffmpeg -version`; job kịch bản dừng ở 0đ nên không mất tiền |
