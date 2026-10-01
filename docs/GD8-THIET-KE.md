# Thiết kế Giai đoạn 8: sản xuất theo mẻ (khung, chưa duyệt)

Trạng thái: **KHUNG để anh góp ý, chưa phải thiết kế chốt, chưa có dòng code nào.** Soạn trên `main` = `ed196e5` (01/10/2026), trong lúc chờ footage cho 7e. Chỉ viết sau khi anh đồng ý "phác khung"; các mục 9 (câu hỏi) cần anh chốt trước khi em chuyển khung này thành thiết kế đầy đủ và mở pha 8a.

Cách đọc số: **[đo]** = đo thật trên máy/lượt chạy; **[đọc]** = đọc từ code hoặc `rules.json`; **[tính]** = tính từ số khác; **[ước]** = ngoại suy, phải đo lại ở 8f trước khi tin. Chỗ nào em chưa kiểm được thì ghi **[cần kiểm]**.

---

## 0. Tóm tắt

**Mục tiêu.** Từ một kho footage, ra **5–10 video ngắn 15–30 giây mỗi mẻ** (thỉnh thoảng xen 1–2 video 180 giây), có chỗ duyệt nhanh cả mẻ và cảnh báo khi các video giống nhau quá. Không nhắm mẻ 30 video 180 giây (đã chốt 01/10/2026: vượt ngân sách ngày và cạn footage).

**Ba mảnh, theo thứ tự phụ thuộc:**
1. **Mẻ là một khái niệm thật** (8a). Hiện không có: mỗi thư mục footage chỉ có một `edit/` và một khóa dự án.
2. **Đo trùng liên video** (8b) và **đổi cảnh nhanh khi duyệt** (8c, 8d). Cả hai cần mẻ làm nền.
3. **Kho nhạc có giấy phép** (8e): độc lập với mẻ, làm khi anh và NVH giao file.

**Hai phát hiện làm đổi cách nghĩ về bài toán** (chi tiết mục 4):
- Hiện **không có "seed"**: kế hoạch và chọn cảnh cho cùng một bộ tùy chọn cho ra cùng kết quả. Năm video cùng tùy chọn sẽ gần như một bản sao. Mẻ phải sinh ra các video **khác tùy chọn** (phong cách, mục đích, độ dài, ghi chú) và chọn cảnh có **trừ điểm cảnh đã dùng ở video khác**.
- Trùng ngẫu nhiên là **sàn**, không phải ngoại lệ: hai video 10 cảnh từ kho 25 cảnh trùng kỳ vọng ~40%. Hai video 180 giây từ cùng một bộ ~78 cảnh gần như dùng hết cùng một kho (~100%). Ngưỡng cảnh báo phải đặt **trên sàn của kho**, và chỉ chốt sau khi đo (8f).

---

## 1. Hiện trạng đọc từ mã

| Thứ | Hiện nay | Ý nghĩa cho mẻ |
|---|---|---|
| Đơn vị công việc | Một **thư mục dự án** = một video (1–3 bản nháp A/B/C của cùng video) [đọc] | 30 video từ 1 kho ghi đè nhau |
| Thư mục kết quả | `<dự án>/edit/` cố định (`EDIT_DIR`) [đọc] | Mọi dịch vụ ghép đường dẫn từ thư mục dự án |
| Khóa | `.project.lock` + một pipeline hoạt động mỗi dự án (`PipelineBusyError`) [đọc] | Hai video cùng kho không chạy song song |
| Hàng đợi | `jobs` trong `queue.db`: `kind` analyze / draft / render, `pipeline_id`, `depends_on` [đọc] | Chưa có `batch_id` |
| Phân tích footage | Tạo ra `inventory.json`, `scenes.json`, `speech.json` + `analysis_stamp.json` (dấu vân tay clip) [đọc: `analyze_job.ARTIFACTS`] | **Đây chính là phần dùng chung** |
| Quét footage | Chỉ file nằm trực tiếp trong thư mục, không đệ quy, bỏ qua thư mục [đọc: `footage_fs`] | Thư mục con `batch_runs/` không bị tính nhầm là clip |
| Chọn cảnh | `plan.json` → `candidates.json` tính **theo khối của từng plan** [đọc] | Ứng viên phải riêng từng video |
| Duyệt chi phí | Mỗi job draft: ước tính → duyệt → trả tiền; ngân sách ngày 100.000đ [đọc] | Cần duyệt cấp mẻ |
| Kiểm kịch bản | V1–V10: V9 so **bản A với bản B của cùng video** [đọc] | Chưa có so liên video |
| Nhạc | `library.json`: `{"tracks": {tên: {bpm, first_beat_sec}}}`, cấm khóa lạ [đọc] | Chưa có license; 4 bài hiện có là nhạc tổng hợp [đọc: `DOC-TRUOC.txt`] |
| Ảnh thu nhỏ | Chỉ thấy `cover.jpg` của video đã dựng [đọc]; **[cần kiểm]** bước CLIP có lưu khung lấy mẫu để dùng lại không | Storyboard cần ảnh từng cảnh |

---

## 2. Mô hình mẻ

### 2.1 File dùng chung và file riêng từng video

| File | Dùng chung cả mẻ | Riêng từng video |
|---|---|---|
| `inventory.json`, `scenes.json`, `speech.json`, `analysis_stamp.json`, `transcripts/` | ✔ (chạy phân tích **một lần**) | |
| `options.json`, `plan.json` | | ✔ |
| `candidates.json`, `phrases.json` | | ✔ (tính theo khối/độ dài của plan) **[cần kiểm]** phrases |
| `drafts.json`, `speech_drafts.json`, `verify_report.json`, `ai_log.jsonl` | | ✔ |
| `render/<bản>/…` | | ✔ |

Khớp với điều anh chốt 01/10/2026: phân tích dùng chung; xếp hạng ứng viên chạy độc lập theo từng `plan.json`. "Kho ứng viên chung" (`candidates_pool.json` trong bản vẽ của anh) nếu có chỉ nên là **bảng xếp hạng cảnh trước plan** để các video con lấy chung; ứng viên theo khối vẫn riêng. Đề nghị **chưa làm pool chung ở 8a** (YAGNI), xem lại sau 8f.

### 2.2 Thư mục (đề nghị: mỗi video là một thư mục dự án thật)

```
<kho footage>/                      # chứa clip gốc, như hiện nay
├── edit/                           # phân tích dùng chung (không đổi tên, không đổi chỗ)
│   ├── inventory.json  scenes.json  speech.json  analysis_stamp.json
├── batch_runs/
│   └── <mã mẻ>/
│       ├── batch.json              # đề bài: danh sách bộ tùy chọn từng video
│       ├── batch_manifest.json     # trạng thái + thứ tự đăng + ma trận trùng (cache)
│       ├── video_01/               # = một thư mục dự án thật
│       │   ├── *.mp4               # liên kết cứng (hardlink) về clip gốc, không tốn dung lượng
│       │   └── edit/               # bản sao nhỏ của phân tích chung + file riêng video này
│       └── video_02/ …
```

**Vì sao thư mục dự án thật (phương án B), không phải sửa mọi dịch vụ (phương án A):**
- A: đưa khái niệm "workspace" vào các dịch vụ (plan, pick, draft, render, QC, API…). Sạch về lâu dài nhưng động tới khoảng 15 file: vượt ngưỡng phải dừng và xin phép.
- B: mỗi video là một thư mục dự án thật, có hardlink về clip và bản sao nhỏ của JSON phân tích. **Mọi dịch vụ hiện có chạy nguyên không sửa**; khóa dự án và pipeline riêng từng video nên chạy song song được. Rủi ro đã thấy: (1) JSON dùng chung bị lệch nếu phân tích lại → `batch_manifest.json` ghi dấu vân tay phân tích, lệch thì dựng lại thư mục video; (2) hardlink cần cùng ổ (cả hai nằm trên `E:` [đọc]) và NTFS/APFS; (3) cần nằm trong `GANI_DV_ALLOWED_ROOTS` (thư mục con của kho là đủ) **[cần kiểm]** cách API xét "nằm trong".
- Đề nghị: **B cho 8a**, ghi rõ A là đích dài hạn nếu mẻ thành công.

### 2.3 Đề bài của mẻ (`batch.json`)

Một danh sách bộ tùy chọn, không phải "N bản sao". Mỗi phần tử là một `options.json` hiện có (phong cách, mục đích, độ dài, nhạc, ghi chú). Công cụ tạo mẻ sinh danh sách bằng cách **xoay các trục**: phong cách × mục đích × độ dài × ghi chú (từ `yeu-cau.md`). Ví dụ 6 video 15–30 giây = 3 phong cách × 2 mục đích.

---

## 3. Hàng đợi cho mẻ

**Luồng [đề nghị]:**
1. `analyze` **một lần** trên kho (nếu `analysis_stamp.json` còn khớp thì bỏ qua, như hiện nay).
2. Dựng N thư mục video (hardlink + bản sao phân tích).
3. Mỗi video: `plan` → `pick` (miễn phí) → job `draft` ở pha `estimate`.
4. **Duyệt cấp mẻ:** tổng ước tính = Σ ước tính từng video; một lần duyệt `max_vnd` cho cả mẻ; hệ thống chia phần cho từng job theo tỉ lệ ước tính. Quy tắc từng lời gọi giữ nguyên (đã chi + lời gọi xấu nhất tiếp theo ≤ phần đã duyệt). Từ chối duyệt nếu `đã chi hôm nay + tổng ước tính mẻ > 100.000đ` (ngân sách ngày).
5. Gọi Claude tuần tự (hoặc song song hạn chế, xem rủi ro), sau đó dựng **bản nháp 540p** cho cả mẻ (miễn phí).
6. Trạng thái mẻ: `đang phân tích` → `chờ duyệt chi` → `đang viết kịch bản` → `đang dựng nháp` → `chờ duyệt hình` → `đang dựng bản cuối` → `xong`.
7. Duyệt xong, các video được chấp nhận vào hàng dựng **bản cuối 1080p**.

**Đổi trong `queue.db`:** thêm cột `batch_id` (null được) vào `jobs` bằng `ALTER TABLE ... ADD COLUMN`; không động dữ liệu cũ. **[cần kiểm]** cách `queue_db` hiện làm migration. Mẻ lưu bằng `batch_manifest.json` (nguồn sự thật là file, hàng đợi chỉ giữ `batch_id`).

**Chi phí [đo + ước]:** một video 15–45 giây tốn khoảng **669–804đ** [đo]. Mẻ 5–10 video khoảng **4.000–8.000đ** [ước]. Mẻ 30 video 180 giây (~4.800đ mỗi video [ước]) = ~144.000đ: vượt ngân sách ngày, không làm.

---

## 4. Giảm trùng và đo trùng liên video

### 4.1 Hai cơ chế khác nhau

- **Phòng ngừa (lúc chọn cảnh):** khi xếp hạng ứng viên cho video thứ k, **trừ điểm** cảnh đã được dùng ở các video 1..k-1 của mẻ. Là tham số mới của hàm xếp hạng (`used_elsewhere`), hàm thuần, kiểm thử dễ. Hiện không có nút nào tạo khác biệt giữa các video cùng tùy chọn, nên đây là đòn bẩy chính. (Đề nghị làm sau khi có số đo ở 8b, không làm trước.)
- **Đo và cảnh báo (sau khi có bản nháp):** ma trận trùng liên video (8b).

### 4.2 Cách đo trùng (đề nghị)

Theo **khoảng thời gian chồng lấn thật** trong clip nguồn, không chỉ theo mã cảnh (hai video dùng cùng một cảnh nhưng cắt lệch vài giây vẫn nhìn gần như nhau):

```
cho mỗi video X, mỗi clip c: I(X,c) = hợp các đoạn [src_in, src_out] của các shot thuộc c
trùng(X, Y) = Σ_c |I(X,c) ∩ I(Y,c)| / min(thời_lượng_nội_dung(X), thời_lượng_nội_dung(Y))
```

Kèm số phụ để người đọc hiểu: Jaccard theo mã cảnh. Hàm thuần `domain/overlap.py`, độ phức tạp O(n log n) mỗi cặp, không gọi AI, không gọi ffmpeg. Kết quả cache trong `batch_manifest.json`.

### 4.3 Sàn ngẫu nhiên [tính]

Hai video chọn k cảnh từ kho S cảnh, ngẫu nhiên: trùng kỳ vọng ≈ k/S.

| Kho (S) | Video (k cảnh) | Trùng ngẫu nhiên |
|---|---|---|
| 25 | 10 (15 giây) | ~40% |
| 78 | 10 | ~13% |
| 78 | 70–78 (180 giây) | ~90–100% |

Hệ quả: ngưỡng "40% giữa hai video liên tiếp" chỉ có nghĩa khi kho đủ lớn; với kho 25 cảnh nó báo gần như mọi cặp. Hai video 180 giây từ một kho: **nên tối đa một video dài mỗi kho mỗi đợt đăng**, hoặc chấp nhận trùng cao (câu hỏi Q3). Cảnh báo **mềm** (không chặn), theo ý anh; ngưỡng chốt sau 8f.

### 4.4 Thứ tự đăng

"Liên tiếp" cần thứ tự đăng dự kiến. `batch.json` có trường `thu_tu_dang` (số thứ tự hoặc ngày). Không có thì chỉ hiện cả ma trận, không đánh dấu "liên tiếp".

---

## 5. Màn duyệt nhanh cả mẻ (Storyboard)

**Hiển thị:** mỗi video một hàng: dãy ảnh thu nhỏ các cảnh theo thời gian, chữ phụ đề bên dưới, ô trùng với video khác (số lớn nhất + cặp nào).

**Ảnh thu nhỏ:** trích một khung tại đỉnh động tác của cảnh (`peak` trong `scenes.json`), lưu cache `thumbs/<mã cảnh>.jpg`. ffmpeg, vài chục mili giây mỗi ảnh, 0đ **[ước, cần đo]** trên ổ HDD.

**Đổi cảnh 1 click:** bấm ảnh → hiện tối đa 3 ứng viên thay thế (từ `candidates.json` của khối đó). Khi chọn:
1. Thay cảnh trong bản nháp, tính lại thời điểm theo phách (`snap_to_beats`).
2. **Chạy lại V1–V9 (hàm thuần, miễn phí)** trên bản nháp mới; vi phạm thì từ chối và nói rõ (ví dụ cảnh đã dùng ở khối khác: V4).
3. Ghi bản nháp mới, **giữ lịch sử** (`drafts.history/`) để hoàn tác.
4. Dựng lại **chỉ video đó** ở 540p (miễn phí). Không gọi lại Claude.

**Đo UX:** gắn bộ đếm thời gian nhỏ (thời điểm mở video, thời điểm chấp nhận) để biết thời gian duyệt thật trên 10 video đầu. Mục tiêu 30 giây mỗi clip là mục tiêu để đo, chưa phải cam kết.

**Video bị từ chối:** hai lựa chọn rẻ nhất: đổi cảnh (0đ) hoặc tạo lại bản nháp (một lần gọi Claude, ~800đ). Q5.

---

## 6. Kho nhạc và giấy phép (8e)

- Phần của anh và NVH: mua gói nhạc/SFX thương mại và lập bảng metadata (`license_id`, `bpm`, `first_beat_sec`, `mood`, mục đích phù hợp, hạn dùng).
- Phần của em khi nhận file: mở rộng `LibraryTrack` (hiện `extra="forbid"`) thêm khóa tùy chọn `license_id`, `mood`, `purposes`, `expires`; nạp thư viện **cảnh báo hoặc chặn bài hết hạn**; ghi credit/ID giấy phép vào metadata video xuất ra bằng `-metadata` của ffmpeg; tài liệu hóa cách thêm bài.
- **Ràng buộc đã biết:** 4 bài hiện có là nhạc tổng hợp (tiếng kick), nên mọi video thử đều dùng nhạc giả. Không có bài thật nào để thử 8e đến khi anh giao file.
- Em không kiểm chứng được chính sách Content ID của từng nền tảng; điều khoản giấy phép (được dùng cho quảng cáo, kênh nào, hạn nào) do anh và NVH đọc.

---

## 7. Lộ trình pha nhỏ, mỗi pha một PR gọn

| Pha | Việc | Phụ thuộc | Ghi chú |
|---|---|---|---|
| 8a-1 | Mô hình mẻ: `batch.json`, dựng thư mục video (hardlink + bản sao phân tích), `batch_manifest.json`, CLI `batch new/status` | thiết kế được duyệt | footage giả để thử |
| 8a-2 | Hàng đợi mẻ: `batch_id`, duyệt cấp mẻ, trạng thái mẻ, API `/api/batches` | 8a-1 | tuân thủ ngân sách ngày |
| 8b | Ma trận trùng liên video: `domain/overlap.py`, CLI `overlap`, cảnh báo mềm, ngưỡng đọc từ `rules.json` | 8a-1 | không cần AI |
| 8c | API ảnh thu nhỏ + đổi cảnh qua V1–V9, lịch sử bản nháp | 8a | 0đ |
| 8d | Giao diện lưới (Storyboard) + bộ đếm thời gian duyệt | 8b, 8c | |
| 8e | Giấy phép nhạc: schema `library.json`, credit trong metadata | file của anh và NVH | độc lập, làm bất cứ lúc nào |
| 8f | **Đo thật** mẻ 5–10 video: trùng tự nhiên, thời gian duyệt, chi phí; anh chốt ngưỡng | 8a–8d | tốn API thật, cần "gọi lại" của anh |
| (sau 8f) | Trừ điểm cảnh đã dùng ở video khác khi chọn cảnh | số đo 8f | chỉ làm nếu 8f cho thấy cần |

---

## 8. Rủi ro và cách đo sớm

| Rủi ro | Mức | Cách đo/xử lý sớm |
|---|---|---|
| Kho nhỏ: trùng cao dù thuật toán tốt | Cao | Đo sàn ngẫu nhiên trên kho thật ở 8b trước khi tin ngưỡng; kho ≥ ~60 cảnh cho video 180 giây (X5) |
| Mẻ có cả video 180 giây: trùng ~100% giữa các video dài | Cao | Q3: tối đa một video dài mỗi kho mỗi đợt |
| Hardlink không dùng được (khác ổ, hệ khác) | TB | Kiểm khi dựng mẻ; dự phòng sao chép (tốn dung lượng) hoặc phương án A |
| JSON phân tích dùng chung lệch | TB | Dấu vân tay trong `batch_manifest.json`; lệch thì dựng lại |
| Máy Dell: nhiều video cùng dựng làm tràn RAM/CPU | TB | Dựng bản nháp **tuần tự**; đo ở 8f (mốc: 180 giây nháp 147 s/vòng, 1,4 GB [đo]) |
| Ngân sách ngày bị vượt bởi mẻ | TB | Chặn duyệt khi `đã chi + tổng ước tính > trần ngày` |
| Đổi cảnh phá nhịp/phách | TB | Luôn qua `snap_to_beats` + V1–V9; không ghi nếu vi phạm |
| Nhạc thật chưa có | Cao với "chế độ nhạc" | Việc của anh và NVH; 8e chờ file |
| Làm chen vào 7e | TB | GĐ8 chỉ bắt đầu sau khi 7e nghiệm thu |

---

## 9. Câu hỏi cần anh chốt (chưa chốt = chưa mở 8a)

- **Q1. Thư mục video:** phương án B (thư mục dự án thật + hardlink, **đề nghị**) hay A (sửa dịch vụ, sạch hơn nhưng động ~15 file)?
- **Q2. Duyệt chi:** một lần duyệt cho cả mẻ (**đề nghị**) hay duyệt từng video?
- **Q3. Video 180 giây trong mẻ:** tối đa một video dài mỗi kho mỗi đợt (**đề nghị**) hay chấp nhận trùng cao giữa các video dài?
- **Q4. Thứ tự đăng:** anh có nhập thứ tự/ngày đăng dự kiến khi tạo mẻ không (để đánh dấu "liên tiếp")?
- **Q5. Video bị từ chối khi duyệt:** ưu tiên đổi cảnh (0đ), hay cho phép tạo lại bản nháp (~800đ) ngay trong màn duyệt?
- **Q6. Ngưỡng cảnh báo:** đề nghị **chưa đặt ngưỡng mặc định**, chỉ hiển thị số trùng cho tới khi có số đo 8f. Anh đồng ý?

---

## 10. Quyết định đã chốt (01/10/2026)

1. GĐ8 chỉ bắt đầu sau khi 7e nghiệm thu; khung này chỉ để chuẩn bị.
2. Cả mẻ dùng chung một lần phân tích footage; xếp hạng ứng viên chạy riêng theo `plan.json` từng video.
3. Mẻ chuẩn: 5–10 video 15–30 giây, xen 1–2 video 180 giây; không chạy mẻ 30 × 180 giây.
4. Trùng đo theo thời gian chồng lấn thật; cảnh báo mềm; ngưỡng chốt sau khi đo thật ở 8f.
5. Kho nhạc thương mại và bảng metadata do anh và NVH lo; em làm 8e khi nhận file.
