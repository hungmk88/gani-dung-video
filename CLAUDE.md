# Quy ước cho agent và người làm trên repo này

## Git: không bao giờ đẩy thẳng lên `main`

`main` chưa bật branch protection trên GitHub (chủ quyết định 16/10/2026), nên quy ước dưới đây là
hàng rào duy nhất. Mọi agent (Claude Code trên máy dựng, phiên cloud, …) đều phải theo.

1. Làm trên **nhánh riêng** tách từ `main` mới nhất (`fix/...`, `feat/...`, `docs/...`, hoặc nhánh
   được giao). Không commit trên `main`, không `git push origin main`.
2. Đưa vào `main` **chỉ qua Pull Request**, và **chỉ merge khi CI xanh** cả hai job:
   `Python (ruff, mypy, pytest)` và `Frontend (test, tsc, lint, build)`.
3. **Chỉ merge khi chủ repo bảo merge** (một PR cụ thể, hoặc "merge khi xanh"). Mở PR xong thì
   báo link, không tự merge.
4. Không viết lại lịch sử nhánh đã đẩy: không `push --force`, không rebase/amend commit đã push.
   Cần cập nhật từ `main` thì merge `main` vào nhánh.
5. Một PR làm một việc; mô tả PR ghi vì sao, thay đổi gì, đã kiểm tra gì và **chưa** kiểm tra gì.
6. PR merge xong, việc tiếp theo bắt đầu từ `main` mới, không chồng commit lên nhánh đã merge.

## Kiểm tra trước khi push

Python (từ thư mục gốc):

```
ruff check .
ruff format --check .
mypy --strict src tests
pytest
```

Test dựng video cần **ffmpeg ≥ 7** (lệnh `-/filter_complex`). Với ffmpeg cũ hơn, các test đó tự bỏ
qua trên máy; CI đặt `CI=true` nên chúng bắt buộc chạy.

Frontend (thư mục `frontend/`):

```
npm test
npx tsc -b
npm run lint
npm run build:real
```

## Sửa số liệu

- Mọi con số và quy tắc nằm trong `config/rules.json` và `config/glossary.json`; sửa ở đó, không
  hardcode trong mã.
- `music.bpm_safe_by_style_duration` là bảng **đo bằng bộ lập kế hoạch**
  (`domain/bpm_measure.sweep_entry`). Đổi phong cách, độ dài hay luật phách thì tạo lại bảng, rồi
  chép sang `frontend/src/data/bpmSafe.ts`; test hai bên sẽ báo nếu lệch.
- Danh sách cụm cấm (`banned_claims`) do NVH chốt: không tự thêm hay bớt cụm.

## Tài liệu

`docs/VAN-HANH.md` (vận hành), `docs/api-jobs.md` (API cho giao diện), `docs/KE-HOACH.md` (giai
đoạn). Đổi hành vi người dùng thấy được thì cập nhật tài liệu trong cùng PR.
