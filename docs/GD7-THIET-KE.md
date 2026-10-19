# Thiết kế Giai đoạn 7 — video dài 3 phút (180 giây)

Trạng thái: **đã chốt Q1–Q6 ngày 01/10/2026** (xem mục 10); đang triển khai từng pha. Soạn trên `main` = `d4eecfb`. File đặc tả gốc không còn và không cần tìm lại: codebase + tài liệu này là chuẩn duy nhất.

Cách đọc số trong tài liệu: **[đo]** = đo thật trên máy/lượt chạy này; **[đọc]** = đọc từ `rules.json`/code; **[ước]** = ngoại suy từ số đo, phải hiệu chỉnh lại ở bước 7c/7e trước khi tin. Mọi con số tiền dưới đây là [ước] trừ khi ghi khác.

Một giới hạn cần nói thẳng: file đặc tả gốc `DEV_PACKAGE_00_v2.1_SPEC.md` **không còn trên máy**, nên em không đối chiếu được ý nguyên văn của đặc tả về "chương" (mục 7.x) và bốn ca kiểm T03, T09, T18, T22. Phần nào suy ra từ `rules.json` và `KE-HOACH.md` thì em ghi rõ là suy ra. Nếu anh còn bản đặc tả, đưa em đọc trước khi chốt mục 2 và 5.

---

## 0. Tóm tắt và những điều cần anh quyết

**Kết luận chính.** Không có bức tường kỹ thuật nào ngăn 180s, nhưng có **ba điểm phải đổi trước khi viết code** và **một điều kiện ngoài code**:

1. **Tỉ lệ khối hiện tại hỏng ở 180s.** `purposes.*.blocks` đặt hook 10% và CTA 15%: ở 180s là hook 18s và CTA 27s. Cần bảng tỉ lệ/thời lượng riêng cho 180s (như cách ngày nay xử lý 15s bằng `hook_max_sec`, `cta_sec`).
2. **Giới hạn số ứng viên (`MAX_CANDIDATES = 20` mỗi khối) phải nới.** Một chương cần 15–27 cảnh mà chỉ được chào 20 ứng viên thì Claude gần như không có quyền chọn, và V4/V9 sẽ trượt liên tục.
3. **Văn bản V7 "tối đa 1 dòng chữ mỗi khối" (Kể chuyện nghề) quá ít** cho một khối dài hàng chục cảnh; cần quy tắc chữ theo số cảnh.
4. **Điều kiện ngoài code: footage.** Footage thử hiện tại (9 clip) chỉ dựng được khoảng 24–27 cảnh/bản. 180s cần **khoảng 60 cảnh khác nhau cho 1 bản và khoảng 70 cho 2 bản**, tức tối thiểu **~20–24 clip** có ≥3 đoạn tách xa nhau (xem mục 4). Nghiệm thu thật (7e) cần một bộ footage như vậy từ anh.

Quyết định em xin anh chốt (mỗi cái có đề xuất, xem mục 9): Q1 tiêu đề chương, Q2 cách chia chương theo từng mục đích, Q3 một lần gọi hay gọi từng chương, Q4 trần chi phí cho nghiệm thu, Q5 ai cấp footage 20+ clip.

---

## 1. Hiện trạng: cái gì đã có, cái gì đang chặn

| Hạng mục | Hiện trạng [đọc] |
|---|---|
| Lựa chọn độ dài | `180` đã nằm trong bảng thời lượng, bảng tương thích phong cách×độ dài và bảng số cảnh cần (`scenes_needed`: Kể chuyện nghề 60, Bán hàng có lời 72, Review 90, Sang trọng 45, TVC 138) |
| Cấu hình riêng 180s | `durations["180"] = {chapters: [3,4], chapter_title_sec: 1.5, max_segments_per_clip: 3, min_gap_sec: 3}` |
| Chặn | `compile_plan` ném `PlanError("PLAN_CHAPTERS")` khi gặp `chapters`, nên **không phong cách nào biên dịch được 180s** |
| Preflight | X1 (TVC×180 chặn), X2 (Khuyến mãi×180 chặn), X15 (3 bản×180 cảnh báo), X5/X6 đã đọc được bảng `scenes_needed` |
| Mã chết | `max_segments_per_clip` và `min_gap_sec` trong `durations["180"]` **không được code nào đọc**; V4 đọc `vision.max_segments_per_clip` và `vision.segment_gap_sec` (cùng giá trị 3 và 3.0 nên chưa lộ). Phải quyết một nơi duy nhất |
| Mã V10 | `KE-HOACH.md` nhắc "V1–V10" nhưng code chỉ có V1–V9: chỗ trống để thêm phép kiểm chương |
| Nhạc | Phân tích nhạc nhận bài tới 240s; bốn bài thử trong `Nhac test BPM` dài 200s [đo], đủ phủ 180s không cần lặp. Cơ chế lặp theo ô nhịp (X13) đã có cho bài ngắn hơn |
| Gọi Claude | `ai.max_tokens = 16000`; `cost_cap_vnd` chỉ có mốc `"60": 20000` nên 180s **âm thầm dùng mốc 60s** |

Về câu hỏi "tổ hợp cổng khả thi có bùng nổ không": **không bùng nổ, nhưng mất độ chính xác.** `check_feasible` thử mọi tập con của các khối khi số khối ≤ `MAX_GROUP_BLOCKS = 12`; trên ngưỡng đó nó chỉ thử từng khối riêng và cả kế hoạch. Nó vẫn đúng (chỉ chặn khi chắc chắn không lấp được) nhưng yếu hơn. Cách giữ chính xác: chia nhóm theo chương, mỗi chương ≤ 5 khối nên 2^5 tập con mỗi chương, chi phí không đáng kể.

---

## 2. Kiến trúc phân đoạn thời gian

### 2.1 Mô hình

Video 180s = **khối mở** (hook) + **3–4 chương** + **khối đóng** (CTA), đếm bằng phách như hiện nay. Mỗi chương là một khối nội dung lớn (ví dụ Giới thiệu xưởng: *Vệ sinh*, *Quy trình*, *Bằng chứng*). Suy ra từ `rules.json`: với `ban_hang` và `gioi_thieu_xuong`, các khối ở giữa hook và CTA là đúng 3 khối, khớp `chapters: [3, 4]`.

Đề xuất làm **theo dữ liệu, không đoán trong code**: thêm vào `rules.json` cho mỗi mục đích một danh sách chương riêng cho 180s (tên, tỉ lệ), cùng `hook_max_sec` và `cta_sec` như mục `"15"`. Ví dụ ý tưởng (con số là đề xuất, chờ anh chốt):

| Mục đích | Hook | Chương | CTA |
|---|---|---|---|
| Giới thiệu xưởng | ≤ 6s | Vệ sinh ≈ 27% · Quy trình ≈ 40% · Bằng chứng ≈ 18% | ≈ 10s |
| Bán hàng | ≤ 6s | Nhu cầu ≈ 17% · Sản phẩm ≈ 50% · Bằng chứng ≈ 18% | ≈ 10s |
| Kể chuyện thương hiệu | (khối Mở 15%, bỏ hook riêng) | Con người (tách 2 chương) · Giá trị | Logo ≈ 10s |

*Kể chuyện thương hiệu* không có hook/CTA tách riêng (4 khối: Mở, Con người, Giá trị, Logo) và chỉ có 2–3 khối giữa, nên muốn đủ 3–4 chương phải tách khối *Con người* (50%) làm đôi. Đây là điểm đặc tả có thể đã nói; xin anh xác nhận (Q2).

### 2.2 Tiêu đề chương (`chapter_title_sec: 1.5`)

Có hai cách, khác nhau về hệ quả:

- **A. Chữ đè lên 1,5s đầu của chương** (khuyến nghị). Lưới phách và nhạc không lệch, vì không chèn thêm giây nào. Làm tròn 1,5s lên 2 phách (≈1,43s ở 84 BPM).
- **B. Thẻ tiêu đề riêng 1,5s.** Mỗi thẻ cộng thêm thời lượng ngoài lưới phách: 3–4 thẻ = 4,5–6s phải bù vào nhạc, dễ lệch phách, và phải đổi cả cách tính `planned_beats` và SNAP.

Em đề xuất A. Nếu đặc tả gốc bắt buộc thẻ riêng thì cần thêm nửa ngày cho việc đo bù phách ở 7d.

### 2.3 Chuẩn hóa đơn vị khối

Một chương dài 100 phách nếu giữ dung sai V2 "trong vòng 1 cảnh" thì quá lỏng (Claude có thể dồn cảnh lệch cả chục giây giữa hai đầu chương mà không bị bắt). Đề xuất: chia mỗi chương thành **tiểu khối ≈ 16–24 phách** (khoảng 12–18s) ngay trong bước biên dịch plan (`quy_trinh#1`, `quy_trinh#2`…). Ba lợi ích: dung sai V2 chặt lại, danh sách ứng viên mỗi tiểu khối nhỏ, và cổng khả thi chia nhóm theo chương gọn. Cái giá: tổng số khối lên ~12–16, nên tiêu đề chương phải gắn theo chương chứ không theo tiểu khối (mục 5).

---

## 3. Token, ngân sách và chốt chặn chi phí

### 3.1 Số đo thật làm gốc [đo]

Lượt chạy thật 01/10/2026 (15s, 2 bản, 18 ứng viên, 20 phách): **3.373 token vào, 1.949 token ra, 669,02đ**; ước tính xấu nhất của cùng lượt là 4.255đ.

Rút ra từ chính prompt của lượt đó (đã dựng lại bằng code hiện tại):

- Mỗi ứng viên: 131 ký tự ≈ **94 token** (ở 1,4 ký tự/token).
- Phần cố định (hệ thống + plan + luật): ≈ **1.600 token**.
- Phần ra nhìn thấy (chọn cảnh + chữ): ≈ 64 token/cảnh (ước theo độ dài JSON). 9 cảnh ≈ 600 token; phần còn lại của 1.949 (≈ 1.300) là **token suy nghĩ**, tính tiền như token ra.

### 3.2 Ngoại suy cho 180s Kể chuyện nghề, 84 BPM [ước]

Giả định: 252 phách, ~55 cảnh/bản, nới số ứng viên lên gấp đôi số cảnh cần (≈ 125 ứng viên).

| | 15s [đo] | 180s, 2 bản [ước] | 180s, 3 bản [ước] |
|---|---|---|---|
| Token vào vòng 1 | 3.373 | ≈ 13–14 nghìn | ≈ 13–14 nghìn |
| Token vào vòng sửa | — | ≈ 21 nghìn (kèm bản trả lời trước) | ≈ 28 nghìn |
| Token ra (có suy nghĩ) | 1.949 | ≈ 9,5–12 nghìn | ≈ 13,5–16,5 nghìn |
| Chi phí một vòng | 669đ | ≈ 3,1–3,8 nghìn đ | ≈ 4,3–5,3 nghìn đ |
| Xấu nhất một vòng (vào + `max_tokens` 16.000) | 4.255đ | ≈ 4,8–5,2 nghìn đ | ≈ 5,2–6 nghìn đ |
| Trượt cả 3 vòng | ≈ 2.992đ (ca V4 30s [đo]) | ≈ 9–11 nghìn đ | ≈ 13–16 nghìn đ |

Hai hệ quả:

1. **3 bản × 180s có nguy cơ cắt cụt** (token ra tiến sát 16.000): bản nháp bị `stop_reason = max_tokens` và tính là lỗi V1. X15 đã cảnh báo 3 bản×180s về chi phí; nên thêm lý do này. Hai cách chữa, chọn ở Q3: (i) nâng `max_tokens` riêng cho 180s (cần kiểm giới hạn đầu ra của model trong tài liệu API trước khi đặt, em chưa xác nhận con số), hoặc (ii) gọi từng chương.
2. **Trần 20.000đ hiện tại vẫn đủ cho 3 vòng** của 2 bản (≈ 11 nghìn), nhưng nó đang được dùng *ngầm* từ mốc `"60"`. Phải khai báo `"180"` tường minh (đề xuất 30.000đ) để không ai hiểu nhầm và để hộp thoại duyệt hiện đúng trần.

### 3.3 Duyệt tiền

- Hộp thoại duyệt gợi ý `min(trần, 1,5 × xấu nhất)` ≈ 7–8 nghìn đ cho 180s: chỉ đủ ~1,5 vòng. Anh nên biết trước: muốn dự phòng đủ 3 vòng thì duyệt ≈ 12–15 nghìn đ.
- Ngân sách ngày 100.000đ vẫn rộng. Chi phí là mỗi lần *bấm duyệt*, không đổi cơ chế hiện có.
- `--dry-run` phải in ước tính token thật (không phải 16.000 tối đa) để số anh thấy sát thực tế hơn; lượt 15s cho thấy ước tính xấu nhất cao gấp ~6 lần chi phí thật.

### 3.4 Gọi một lần hay gọi từng chương (Q3)

| | Một lần (như nay) | Từng chương (tuần tự, truyền danh sách cảnh đã dùng) |
|---|---|---|
| Chi phí khi mọi thứ đạt | thấp hơn | cao hơn chút (mỗi lần lặp lại phần cố định ≈ 1,6k token) |
| Khi một chương trượt | gửi lại cả bản: ≈ 3–4 nghìn đ | chỉ gửi lại chương đó: ≈ 1 nghìn đ |
| Nguy cơ cắt cụt | có (nhất là 3 bản) | gần như không |
| V4/V9 xuyên chương | Claude tự giữ trong một câu trả lời | phải truyền danh sách đã dùng, phức tạp hơn |
| Mã nguồn đổi | ít | nhiều (vòng lặp, báo cáo vòng, hàng đợi báo tiến độ) |

**Đề xuất:** làm **một lần** ở 7c, đo thật ở 7e, và chỉ chuyển sang từng chương nếu đo thấy cắt cụt hoặc trung bình > 1,5 vòng. Lý do: đổi vòng lặp tốn một PR lớn và đụng lại `verify_report`; chưa có bằng chứng là cần.

---

## 4. Footage và cổng khả thi

### 4.1 Nhu cầu footage [đọc + tính]

Ràng buộc thật là **số clip**, không chỉ số cảnh: mỗi clip tối đa 3 đoạn (`max_segments_per_clip`), các đoạn cùng clip cách nhau ≥ 3s.

| | Kể chuyện nghề, 1 bản | 2 bản (chung tối đa 60%) | 3 bản |
|---|---|---|---|
| Cảnh khác nhau tối thiểu | ~60 [đọc, bảng 5.3.A] | ≈ 60 + 0,4×60 ≈ 84 | lớn hơn nữa |
| Khuyến nghị theo X6 (×1,3) | ≈ 78 | ≈ 110 | — |
| Số clip cần (÷3 đoạn/clip) | ≥ 20 | ≥ 28 | ≥ 34 |

(Công thức 2 bản lấy từ quy tắc V9 hiện có: bản sau chung tối đa 60% cảnh với bản trước; số 84 là cận dưới lý thuyết, tính thô.)

Footage thử hiện có 9 clip, chỉ dựng được khoảng 24–27 cảnh/bản [ước: 9 clip × 3 đoạn], nên **không đủ để nghiệm thu 180s**. Đây là việc chỉ anh làm được (Q5).

### 4.2 Đổi trong code

- **`candidate_limit`:** bỏ trần cứng 20 cho khối dài; đề xuất `limit = clamp(2 × số cảnh cần, 6, 60)` và một **trần tổng** (ví dụ ≤ 160 ứng viên cả plan) để prompt không phình không kiểm soát. Trần tổng là con số phải hiệu chỉnh ở 7b.
- **`check_feasible` chia nhóm theo chương** (mục 1). Giữ tính chất "chỉ chặn khi chắc chắn": bộ test tìm kiếm vét cạn hiện có mở rộng cho nhiều chương.
- **`footage_fit`** dùng chung: danh sách `blocks` trong API tự đúng với chương/tiểu khối, UI cảnh báo vàng không đổi. Ngưỡng `footage_warn_ratio` 1,3 có thể cần số riêng cho 180s vì cần dư hơn (2 bản); để trong `rules.json`.
- Hiệu năng: 50–140 cảnh và ≤ 16 khối là nhỏ với code hiện tại, nhưng em sẽ thêm test thời gian (ví dụ dưới 1s) để không thoái hóa.
- **Dọn mã chết:** chọn `vision.*` làm nguồn duy nhất cho giới hạn clip/khoảng cách, xóa hai khóa thừa trong `durations["180"]`, hoặc cho `durations["180"]` ghi đè có kiểm tra. Đề xuất xóa để khỏi hiểu nhầm.

---

## 5. Bộ luật kiểm kịch bản V/S cho 180s

| Phép kiểm | Thay đổi đề xuất |
|---|---|
| V1 cấu trúc | Thêm trường `chapter_titles` vào schema trả lời (một tiêu đề mỗi chương); thiếu/thừa chương = V1 |
| V2 số phách | Dung sai theo **tiểu khối** (mục 2.3); tổng vẫn phải đúng |
| V3 làm tròn phách | Giữ nguyên (code tự sửa) |
| V4 lặp cảnh | Giữ nguyên quy tắc; phạm vi nay là cả video nên bắt lặp xuyên chương; thông điệp lỗi nêu chương để dễ đọc |
| V5 đúng khối | Giữ nguyên |
| V6 nhãn bắt buộc | Giữ nguyên, tính theo chương (cảnh báo) |
| V7 chữ | **Đổi:** `per_block: 1` không hợp cho khối dài; thêm `per_chapter_max` hoặc `every_n_scenes` riêng cho 180s (đề xuất: tối đa 1 dòng chữ mỗi 4 cảnh, riêng Kể chuyện nghề giữ tinh thần "ít chữ"). Con số cần anh chốt (Q2b) |
| V8 CTA | Giữ nguyên |
| V9 đa dạng | Giữ nguyên; thêm so sánh *thứ tự chương* không cần (chương cố định) |
| **V10 (mới)** | Tiêu đề chương: ≤ N từ (đề xuất 6), không cụm cấm, không trùng nhau. Mức `ai` (gửi lại) |
| SNAP | Giữ nguyên; thêm kiểm "mỗi chương bắt đầu đúng trên phách" |
| S1–S6 (lời) | **Không đụng ở GĐ7a–7e.** Chế độ lời × 180s để 7f (mục 7) |

Hệ quả giao diện: `verify_labels` thêm V10; bảng ma trận hiện tự thêm một hàng. Nếu sau này gọi từng chương, cột "vòng" thành (chương, vòng): đã thấy trước nên `verify_report.json` (version 1) để dành `chapter` tùy chọn trong `RoundRecord` khi cần, không cần đổi ngay.

---

## 6. Dựng, nhạc, soát kỹ thuật và hàng đợi

- **Đồ thị ffmpeg:** hiện mỗi cảnh là một ngõ vào riêng (bản 15s có 5 ngõ). 180s sẽ ~55 ngõ vào một `filter_complex`, nối tuần tự bằng `xfade`/`concat`. Đây là **rủi ro lớn nhất chưa đo được** (bộ nhớ trên máy 12GB lệch kênh, thời gian dựng). Nếu phình quá, đường lui là dựng **từng chương ra file trung gian rồi ghép** (không đổi kết quả hình, đổi cách dựng). Phải đo ở 7d bằng bản nháp giả, 0đ API.
- **Thời gian:** `queue.timeout_sec` = 1800s (nháp) / 5400s (cuối); `expected_sec.render_preview = 60`. Lượt 15s thật dựng mỗi bản trong khoảng 1 phút [đo thô]; nếu tuyến tính thì 180s nháp ≈ 12 phút, nhưng không nên tin tuyến tính với 55 ngõ vào. Cần: `expected_sec` theo độ dài (để thanh tiến độ/ETA không nói dối) và đo thực ở 7d trước khi chốt timeout.
- **Nhạc:** bài ≥ 180s thì dùng thẳng; ngắn hơn thì đường lặp theo ô nhịp (X13) đã có nhưng **chưa từng chạy ở quy mô 180s**; thêm test với bài 120s.
- **Soát kỹ thuật T1–T7:** độ dài/khung/âm lượng chạy tự nhiên với 180s. Hai chỗ cần xem: T6/T7 (chữ/phụ đề) với tiêu đề chương; ngưỡng `freeze_min_sec`/`black_min_sec` không được báo oan ở chỗ chuyển chương.
- **T8 (Whisper nghe lại):** chỉ áp cho chế độ lời bản cuối, nên không đụng tới ở 7a–7e.
- **Ngân sách bộ nhớ của prompt:** xem mục 3; máy dựng không gọi Claude nên không liên quan bộ nhớ máy, chỉ liên quan token.

---

## 7. Lộ trình pha nhỏ, mỗi pha một PR gọn

Nguyên tắc giữ nguyên: test đỏ trước, mỗi PR anh duyệt merge, không tốn API ở 7a–7d.

| Pha | Nội dung | Tốn API | Cỡ PR (ước) | Cần từ anh |
|---|---|---|---|---|
| **7a** Plan có chương | Bỏ `PLAN_CHAPTERS`; `rules.json` thêm chương/`hook_max_sec`/`cta_sec` cho 180s; `compile_plan` chia chương và tiểu khối; preflight/X-rule cập nhật (X15 thêm lý do cắt cụt); dọn khóa chết | 0đ | vừa | Chốt Q1, Q2 |
| **7b** Chọn cảnh và khả thi ở quy mô lớn | `candidate_limit` mới + trần tổng; `check_feasible` chia nhóm theo chương; `footage_fit` cho chương; test hiệu năng và test vét cạn mở rộng | 0đ | vừa | — |
| **7c** Kịch bản 180s | Schema + prompt có `chapter_titles`; V1/V2/V7/V10/SNAP đổi; `cost_cap_vnd["180"]`; `max_tokens` theo độ dài (nếu chọn (i)); `--dry-run` in ước tính thật; nhãn V10 | 0đ (Claude giả) | lớn, có thể tách thành 7c-1 (schema/luật) và 7c-2 (prompt/trần) | Chốt Q3, Q4 |
| **7d** Dựng 180s | Tiêu đề chương; nhiều ngõ vào; phương án ghép theo chương nếu cần; `expected_sec` theo độ dài; nhạc dài/lặp; QC ở chỗ chuyển chương | 0đ (nháp giả) | lớn | — |
| **7e** Nghiệm thu thật | 1 lượt 180s 1 bản rồi 2 bản trên footage ≥ 24 clip; đối soát file–API–log như 6i | ≈ 3–5 nghìn đ/lượt [ước] | nhỏ (chủ yếu tài liệu, số đo) | **Footage (Q5)**, hạn mức (Q4), lệnh "gọi lại" |
| **7f** Chế độ lời × 180s (Bán hàng có lời) | Phrases 72 câu, S1–S6 theo chương, T8 bản cuối; chỉ làm khi 7e xong | sau | lớn | Footage có lời nói |
| **7g** Giao diện | Chọn 180s ở wizard, hiển thị chương/tiểu khối, hàng V10, cảnh báo chi phí 180s, ETA theo độ dài | 0đ | vừa | — |

Thứ tự cứng: 7a → 7b → 7c → 7d → 7e. 7g có thể đi song song sau 7a. 7f tách riêng vì đụng cả pipeline lời.

---

## 8. Rủi ro và cách đo sớm

1. **Đồ thị ffmpeg quá nặng** (cao nhất). Đo ở 7d bằng bản nháp giả trước khi viết gì thêm; đường lui: dựng theo chương.
2. **Cắt cụt đầu ra ở 3 bản.** Đo bằng `--dry-run` ở 7c; chặn mềm bằng X15.
3. **Footage không đủ** là lý do phổ biến nhất khiến 7e thất bại tốn tiền. Cổng khả thi + `footage_fit` bắt được một phần; vẫn nên kiểm số clip ≥ 24 bằng tay trước khi bấm duyệt.
4. **Ngưỡng ước tính sai.** Bảng mục 3.2 là ngoại suy từ một lượt 15s; sai số có thể lớn (tỉ lệ token suy nghĩ thay đổi theo độ khó). Chấp nhận, hiệu chỉnh ở 7c (`--dry-run`) và 7e (số thật).
5. **Đặc tả gốc không còn trên máy:** có thể em đã bỏ sót yêu cầu của T03/T09/T18/T22. Nếu anh còn file, đưa em rà trước 7a.

---

## 9. Câu hỏi cần anh chốt

- **Q1 — Tiêu đề chương:** chữ đè 1,5s đầu chương (đề xuất) hay thẻ riêng 1,5s?
- **Q2 — Chương theo mục đích:** đồng ý bảng ở mục 2.1 (Giới thiệu xưởng, Bán hàng: 3 chương; Kể chuyện thương hiệu: tách *Con người* thành 2 chương)? Và **Q2b:** chữ trên hình ở 180s, đề xuất 1 dòng mỗi 4 cảnh, riêng Kể chuyện nghề ít hơn, anh chốt mức.
- **Q3 — Một lần hay từng chương:** đề xuất một lần trước, đo rồi tính.
- **Q4 — Trần chi phí:** đề xuất `cost_cap_vnd["180"] = 30.000đ`; và **ngân sách nghiệm thu 7e** (đề xuất tổng ≤ 15.000đ cho cả hai lượt).
- **Q5 — Footage:** anh hoặc bên quay cấp được bộ ≥ 24 clip (mỗi clip ≥ 3 đoạn tách xa nhau ≥ 3s) cho nghiệm thu chưa? Bao giờ?
- **Q6 — Phạm vi:** GĐ7 đợt đầu chỉ chế độ nhạc (Kể chuyện nghề, Review, Sang trọng) và để chế độ lời sang 7f; đồng ý?

Cho đến khi anh chốt Q1–Q4, em không mở nhánh hay viết code GĐ7.

---

## 10. Quyết định đã chốt (01/10/2026)

| # | Quyết định |
|---|---|
| Q1 | Tiêu đề chương là **chữ đè lên 1,5s đầu chương**, vẽ bằng bộ phụ đề ASS ở vùng an toàn phía trên; không chèn thẻ riêng. |
| Q2 | Bảng chương mục 2.1: hook 4–6s → ba chương theo tỉ lệ thời lượng **45 : 75 : 40** (giây mẫu của chủ) → CTA 10–14s. Chữ trên hình: bỏ trần "1 dòng mỗi khối"; **mỗi chương 3–5 cụm chữ** (khoảng 1 cụm mỗi 10–15s), trùng với các cú cắt quan trọng. |
| Q3 | Gọi Claude **một lần** cho cả kịch bản. Mặc định **2 bản nháp** để token ra dưới ~8.000–10.000, tránh chạm `max_tokens` 16.000. |
| Q4 | `cost_cap_vnd["180"] = 30.000đ`. Hạn mức nghiệm thu 7e: **dưới 15.000đ** (1–2 lượt). |
| Q5 | Pha 7a–7d dùng footage giả lập/nhân bản có offset (0đ). Trước 7e, chủ cấp **25–30 clip thật** của xưởng. |
| Q6 | Chế độ **nhạc** trước (7a→7e); chế độ lời để riêng ở **7f**. |

### Ghi chú triển khai 7a (để các pha sau nối tiếp)

- `durations["180"].enabled = false` khi phát hành: bố cục chương đã biên dịch được nhưng `compile_plan` vẫn từ chối 180s cho đến khi nghiệm thu thật ở 7e, để không ai vô tình tốn tiền trên đường chưa kiểm. Lật thành `true` ở 7e.
- Bố cục chương nằm trong `rules.json` (`durations.180.layouts[mục đích]`: `hook`, `cta`, chương `[id, khối nguồn, trọng số]`), không đoán trong code. Kể chuyện thương hiệu cắt *Con người* làm hai chương (`con_nguoi_1`, `con_nguoi_2`).
- Plan có thêm `chapters[]` (`id`, `role`, `start_beat`, `beats`, `title_beats`, `blocks`) và `PlanBlock.chapter`. Tiểu khối đặt tên `<chương>#<k>` (luôn có `#k`), khoảng 20 phách (`sub_block_beats`), tổng phách chia chính xác (`split_exact`).
- `text_rules` của 180s: bỏ `per_block`, thêm `per_chapter: [3, 5]` (chỉ dữ liệu; **phép kiểm V7 đọc khóa này làm ở 7c**).
- Nhãn bắt buộc của tiểu khối lấy từ khối nguồn của chương; khối hook/CTA lấy theo vai trò. Ở 7b cần chia ứng viên giữa các tiểu khối của cùng chương (hiện cùng một khối nguồn, chưa chia).
- Chế độ lời × 180s bị từ chối bằng `PLAN_CHAPTERS_SPEECH` cho đến 7f. X15 đã nhắc rủi ro bản nháp bị cắt và khuyên 2 bản.
- Đã xóa hai khóa chết `max_segments_per_clip`, `min_gap_sec` khỏi `durations["180"]`; giới hạn clip/khoảng cách chỉ đọc từ `vision.*`.

### Ghi chú triển khai 7b

- **Ứng viên theo chương:** `rank_plan` xếp hạng một lần cho cả chương (cảnh có nhãn bắt buộc trước, rồi bù bằng cảnh đẹp nhất còn lại: `rank_block(top_up=True)`) và `deal` chia cho các tiểu khối theo số chỗ còn trống, nên trong một chương không cảnh nào xuất hiện hai lần. Hook/CTA và mọi khối của video 15–60s xếp hạng y như trước (có test đối chiếu).
- **Trần tổng:** `pick.max_total_candidates = 160` (mỗi ứng viên ≈ 94 token vào). Vượt thì các khối giảm đều nhưng không dưới số cảnh tối thiểu của khối; trần không thể đáp ứng thì bỏ qua thay vì bỏ đói khối. Giới hạn cứng 20/khối giữ nguyên vì tiểu khối ~20 phách chỉ cần ~10.
- **Cổng khả thi theo cụm chương:** thử từng khối, rồi mọi tập con *bên trong* một chương, rồi mọi tập con các chương gộp lại (chỉ cả kế hoạch khi có hơn 12 cụm). Vẫn chỉ là điều kiện cần (không từ chối kế hoạch dựng được; test vét cạn ngẫu nhiên có chương). Thông báo gọi tên "chương 'x'" thay vì liệt kê tiểu khối (từ 17/10/2026 bằng tên tiếng Việt trong `rules.block_names`, ví dụ "chương 'Quy trình'").
- **Footage giả:** `tests/mock_footage.py` tạo 28 clip × 3 đoạn (không có video), dùng cho test 7b và các pha sau; 28 clip đủ cho kế hoạch 180s 2 bản, 9 clip thì bị cổng chặn.
- **Chưa làm ở 7b:** prompt/schema/luật V để 7c. `footage_fit` gộp theo chương làm sau (17/10/2026): một dòng cho mỗi chương, `role` là id chương, như cổng khả thi gọi tên "chương 'x'".

### Ghi chú triển khai 7c

- **Schema/prompt:** khi plan có chương, `response_schema(letters, chapters)` thêm `chapter_titles` (mỗi chương một `{chapter, title}`, `chapter` là enum id chương) và `system_prompt_for(plan)` nối thêm `CHAPTERS_PROMPT` (luật 8–10). Video ngắn giữ nguyên prompt và schema. Dữ liệu gửi Claude có thêm mục `chapters` (id, beats, title_beats, blocks) và `text_rules.per_chapter`, `title_max_words` lấy từ `rules.json` (không chép số vào prompt).
- **V1:** mỗi bản phải có đúng một tiêu đề cho mỗi chương. **V7:** `per_chapter` [3, 5] đếm dòng chữ của các tiểu khối trong chương (hook và CTA không tính). **V10 (mới, mức `ai`):** tiêu đề không rỗng, ≤ `title_max_words` (6) từ, không cụm cấm, không trùng tiêu đề chương khác trong cùng bản. Mỗi chương bắt đầu đúng phách là hiển nhiên (mọi cú cắt nằm trên lưới phách) nên không thêm phép kiểm riêng.
- **Báo cáo V:** `verify_report.json` có thêm `extra_codes` (`["V10"]` khi plan có chương); API và giao diện thêm hàng V10 ngay trước SNAP. Video ngắn và báo cáo cũ không đổi.
- **Trần:** `ai.cost_cap_vnd["180"] = 30000`. Mặc định 2 bản: `X15` cảnh báo 3 bản (bản nháp dễ bị cắt ở `max_tokens`); mặc định số bản ở wizard thuộc 7g.
- **Đo trên bộ giả (ước tính offline, không gọi Claude):** 13 khối, 136 ứng viên, prompt ≈ 13.710 token vào; một lần gọi xấu nhất (vào + `max_tokens` 16.000) ≈ 4.779đ; câu trả lời hợp lệ của 2 bản ≈ 98 cảnh ≈ 6.000 token ra nhìn thấy (chưa tính token suy nghĩ). Khớp dự báo ở mục 3.2; số thật chỉ có ở 7e.
- `SnappedDraft.chapter_titles` mang tiêu đề sang bước dựng (7d vẽ chữ đè `title_beats` phách đầu chương).

### Ghi chú triển khai 7d (số đo thật trên máy dựng)

Dự án giả: 47 cảnh lấy từ 9 clip thật, nhạc 84 BPM dài 200s, 3 chương có tiêu đề, bản 180s, đồ thị ffmpeg **một lần** (như bản ngắn). Máy: Dell i5-8265U, 12 GB RAM (còn trống ≈ 0,6–1,9 GB lúc đo vì các ứng dụng khác).

| | Nháp 540p | Bản cuối 1080×1920 |
|---|---|---|
| Một vòng dựng + soát | 147 s | ≈ 7–8 phút |
| Cả lượt (QC bắt dựng lại vòng 2) | 304 s | 999 s |
| ffmpeg đỉnh RAM | ≈ 1,36 GB | ≈ 2,42 GB |
| File | 63 MB | 209 MB |

- **Kết luận đồ thị:** 47 ngõ vào không tràn RAM, không lệch timebase; **giữ đồ thị một lần**, không cần phương án dựng từng chương rồi nối. Phương án đó vẫn là đường lui nếu sau này cảnh nhiều hơn hoặc máy ít RAM hơn.
- **Tiêu đề chương:** `Caption(kind="title")` từ `draft.chapter_titles`, vẽ bằng ASS (style `ChapterTitle`, giữa-trên, dưới hàng logo: `render.chapter_title_offset`, cỡ `font_px.chapter_title` 88) trong `title_beats` phách đầu chương, không chèn thời lượng. Tiêu đề không vào `phude.srt` (không phải phụ đề và sẽ chồng dòng đầu). QC: tiêu đề nằm làn trên, không tính T6 chồng chữ với chữ dưới; T7 co cỡ riêng `chapter_title`.
- **Chuyển cảnh:** dissolve của phong cách chỉ diễn ra khi **đổi chương** (hook → chương → chương → CTA), không giữa các tiểu khối trong một chương.
- **Đỉnh âm thanh (phát hiện nhờ bản 180s, không riêng độ dài):** mix trước khi mã hóa −14,0 LUFS / −1,9 dBTP, sau AAC −0,3 dBTP (QC trượt). Dựng hai lượt loudnorm không đổi gì (đo: −1,9 cả hai). Nay khi âm lượng đúng mà đỉnh vượt, QC dựng lại **một lần** với trần đỉnh thấp hơn bằng đúng phần bộ mã hóa đã cộng thêm (`qc.peak_retry_margin_db` 0,3, sàn `qc.min_true_peak_db` −6): trần −3 dBTP cho ra −14,3 LUFS / −1,6 dBTP, QC đạt. Còn trượt thì dừng, không lặp.
- **Giới hạn hàng đợi theo độ dài:** `queue.render_sec_per_video_sec` (nháp 1,7; cuối 5,5). `expected_sec` (thanh tiến độ, ETA) = max(giá trị cũ, hệ số × độ dài video); cửa sổ treo `stall_sec` không thấp hơn `expected` (ffmpeg im lặng hàng phút trong một vòng: 600 s sẽ giết job khỏe); `timeout` không thấp hơn 2 × `expected`. Video 15–30s giữ nguyên.
- **Chưa đo:** nhạc ngắn hơn 180s (đường lặp X13 ở quy mô này), clip HDR/4K, footage thật của xưởng (7e).

### Ghi chú triển khai 7g (giao diện)

- **API:** `GET /api/health` có `features.long_video` (= `durations.180.enabled`); job `draft`/`render` của plan có chương có `structure` (mở đầu / chương / kêu gọi theo giây, kèm tiêu đề chương của bản nháp đó). Xem `docs/api-jobs.md`.
- **Wizard:** nút "3 phút" bị khóa (kèm lời giải thích) khi máy chủ chưa mở; chọn 3 phút thì 3 bản tự hạ còn 2 (quyết định Q3). Máy chủ cũ không nói gì → không khóa.
- **Chi tiết job:** dòng thời gian các chương (bấm để tua video tới đầu chương), kèm một hàng riêng cho tiêu đề chương ("đầu khung hình", tách khỏi phụ đề ở cuối khung hình, đúng Q1); mỗi bản nháp A/B có tiêu đề riêng. Hộp duyệt chi phí hiện cấu trúc theo kế hoạch (chưa có tiêu đề).
- **Soát chất lượng:** các việc QC tự chỉnh (ví dụ "dựng lại vòng 2 với trần đỉnh −3 dBTP") hiện thành một dòng "Tự chỉnh" thay vì chỉ thấy "vòng 2/3".
- Kiểm bằng chính bản dựng thật 180s của 7d: bấm "Quy trình" tua video 182s tới 0:50 và phát; các đoạn rộng đúng tỉ lệ 2,4 / 25 / 42 / 23 / 7%.

