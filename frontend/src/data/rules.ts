/**
 * Real values copied from ../../../config/rules.json (rules_version 2.0) and
 * ../../../config/glossary.json — NOT invented. The browser's copy: the wizard reads its
 * labels, styles, purposes, platforms and products from here, and the demo mode
 * (services/mockApi.ts, lib/preflight.ts) its rules. The server serves no rules endpoint.
 *
 * Source of truth stays the Python repo; if rules.json changes, re-sync this file by hand.
 * rules.test.ts compares the copy with the two JSON files and fails when they drift apart.
 */

import type { RulesBundle } from '../types/rules';

export const RULES: RulesBundle = {
  rules_version: '2.0',

  styles: {
    tvc_cat_nhanh: {
      id: 'tvc_cat_nhanh',
      name: 'TVC cắt nhanh',
      driver: 'music',
      driver_fallback: 'grid',
      scene_avg_sec: 1.3,
      bpm_range: [110, 130],
      max_duration: 60,
      max_effects: 3,
      text_max_words: 4,
      grade: 'high_contrast',
    },
    ban_hang_co_loi: {
      id: 'ban_hang_co_loi',
      name: 'Bán hàng có lời',
      driver: 'speech',
      scene_avg_sec: 2.5,
      bpm_range: [85, 110],
      requires_speech: true,
    },
    ke_chuyen_nghe: {
      id: 'ke_chuyen_nghe',
      name: 'Kể chuyện nghề',
      driver: 'music',
      scene_avg_sec: 3.0,
      bpm_range: [70, 95],
      max_effects: 4,
      text_max_words: 8,
    },
    review_mon_an: {
      id: 'review_mon_an',
      name: 'Review món ăn',
      driver: 'music',
      scene_avg_sec: 2.0,
      bpm_range: [95, 120],
      max_effects: 3,
      text_max_words: 6,
      grade: 'mon-an',
    },
    sang_trong: {
      id: 'sang_trong',
      name: 'Sang trọng',
      driver: 'music',
      scene_avg_sec: 4.0,
      bpm_range: [60, 85],
      max_effects: null,
      text_max_words: 5,
    },
    theo_mo_ta: {
      id: 'theo_mo_ta',
      name: 'Theo mô tả của tôi',
      driver: 'auto',
      scene_avg_sec: 3.0,
      bpm_range: null,
      max_effects: 3,
      note_min_words: 20,
    },
  },

  purposes: {
    ban_hang: {
      id: 'ban_hang',
      name: 'Bán hàng',
      blocks: [
        ['hook', 0.1],
        ['nhu_cau', 0.15],
        ['san_pham', 0.45],
        ['bang_chung', 0.15],
        ['cta', 0.15],
      ],
      cta: 'Nhắn tin đặt hàng',
      max_duration: 180,
    },
    gioi_thieu_xuong: {
      id: 'gioi_thieu_xuong',
      name: 'Giới thiệu xưởng',
      blocks: [
        ['hook', 0.1],
        ['ve_sinh', 0.25],
        ['quy_trinh', 0.35],
        ['bang_chung', 0.15],
        ['cta', 0.15],
      ],
      cta: 'Nhắn tin nhận báo giá sỉ',
      max_duration: 180,
    },
    khuyen_mai: {
      id: 'khuyen_mai',
      name: 'Khuyến mãi',
      blocks: [
        ['hook_uu_dai', 0.15],
        ['san_pham', 0.35],
        ['chi_tiet_uu_dai', 0.35],
        ['cta_gap', 0.15],
      ],
      cta: 'Ưu đãi đến [ngày]',
      max_duration: 60,
      required_fields: ['uu_dai', 'thoi_han'],
    },
    ke_chuyen_thuong_hieu: {
      id: 'ke_chuyen_thuong_hieu',
      name: 'Kể chuyện thương hiệu',
      blocks: [
        ['mo', 0.15],
        ['con_nguoi', 0.5],
        ['gia_tri', 0.2],
        ['logo', 0.15],
      ],
      cta: 'Theo dõi GANI',
      max_duration: 180,
    },
  },

  durations: [15, 30, 45, 60, 180],

  musicModes: {
    ai_tu_chon: { id: 'ai_tu_chon', name: 'AI tự chọn', needs_rights_tick: false },
    khong_nhac: { id: 'khong_nhac', name: 'Không nhạc', needs_rights_tick: false },
    thu_vien: { id: 'thu_vien', name: 'Nhạc có sẵn trong thư viện', needs_rights_tick: false },
    tai_len: { id: 'tai_len', name: 'Tải nhạc của tôi', needs_rights_tick: true },
  },

  draftCounts: [1, 2, 3],

  compatStylePurpose: {
    tvc_cat_nhanh: {
      ban_hang: 'ok',
      gioi_thieu_xuong: 'warn',
      khuyen_mai: 'ok',
      ke_chuyen_thuong_hieu: 'warn',
    },
    ban_hang_co_loi: {
      ban_hang: 'ok',
      gioi_thieu_xuong: 'ok',
      khuyen_mai: 'ok',
      ke_chuyen_thuong_hieu: 'warn',
    },
    ke_chuyen_nghe: {
      ban_hang: 'warn',
      gioi_thieu_xuong: 'ok',
      khuyen_mai: 'bad',
      ke_chuyen_thuong_hieu: 'ok',
    },
    review_mon_an: {
      ban_hang: 'ok',
      gioi_thieu_xuong: 'bad',
      khuyen_mai: 'ok',
      ke_chuyen_thuong_hieu: 'warn',
    },
    sang_trong: {
      ban_hang: 'warn',
      gioi_thieu_xuong: 'warn',
      khuyen_mai: 'bad',
      ke_chuyen_thuong_hieu: 'ok',
    },
    theo_mo_ta: {
      ban_hang: 'warn',
      gioi_thieu_xuong: 'warn',
      khuyen_mai: 'warn',
      ke_chuyen_thuong_hieu: 'warn',
    },
  },

  compatStyleDuration: {
    tvc_cat_nhanh: { '15': 'ok', '30': 'ok', '45': 'warn', '60': 'warn', '180': 'bad' },
    ban_hang_co_loi: { '15': 'ok', '30': 'ok', '45': 'ok', '60': 'ok', '180': 'ok' },
    ke_chuyen_nghe: { '15': 'warn', '30': 'ok', '45': 'ok', '60': 'ok', '180': 'ok' },
    review_mon_an: { '15': 'ok', '30': 'ok', '45': 'ok', '60': 'warn', '180': 'warn' },
    sang_trong: { '15': 'warn', '30': 'ok', '45': 'ok', '60': 'ok', '180': 'warn' },
    theo_mo_ta: { '15': 'ok', '30': 'ok', '45': 'ok', '60': 'ok', '180': 'ok' },
  },

  // Bảng 5.3.A — TVC 15s = 11 is intentional (CEO kept it 28/09/2026), not a rounding bug.
  scenesNeeded: {
    tvc_cat_nhanh: { '15': 11, '30': 23, '45': 35, '60': 46, '180': 138 },
    ban_hang_co_loi: { '15': 6, '30': 12, '45': 18, '60': 24, '180': 72 },
    ke_chuyen_nghe: { '15': 5, '30': 10, '45': 15, '60': 20, '180': 60 },
    review_mon_an: { '15': 8, '30': 15, '45': 23, '60': 30, '180': 90 },
    sang_trong: { '15': 4, '30': 8, '45': 11, '60': 15, '180': 45 },
    theo_mo_ta: { '15': 5, '30': 10, '45': 15, '60': 20, '180': 60 },
  },

  // Only the subset of X1–X18 the browser can evaluate before any footage/AI call
  // (mirrors domain/preflight.py::_check_structure + _choice_issues + music checks).
  conflicts: {
    X1: {
      code: 'X1',
      level: 'block',
      stage: 'preflight',
      desc: 'TVC cắt nhanh × 3 phút (vượt styles.*.max_duration)',
      msg: 'TVC cắt nhanh không dựng được 3 phút (cần khoảng 140 cảnh). Đổi sang 60s, hoặc đổi phong cách Kể chuyện nghề.',
    },
    X2: {
      code: 'X2',
      level: 'block',
      stage: 'preflight',
      desc: 'Khuyến mãi × 3 phút (vượt purposes.*.max_duration)',
      msg: 'Video khuyến mãi không nên dài 3 phút — người xem không chờ để biết ưu đãi. Chọn 15–30s.',
    },
    X3: {
      code: 'X3',
      level: 'block',
      stage: 'preflight',
      desc: 'Khuyến mãi thiếu mức ưu đãi/thời hạn',
      msg: 'Khuyến mãi cần nhập đủ mức ưu đãi và thời hạn. Còn thiếu: {missing}.',
    },
    X4: {
      code: 'X4',
      level: 'block',
      stage: 'preflight',
      desc: 'Theo mô tả × ghi chú < 20 từ',
      msg: "Phong cách 'Theo mô tả của tôi' cần ghi chú ít nhất {min} từ (hiện có {count}). Ví dụ: 'Mở bằng cảnh cắt lát giò, nhịp chậm, chữ trắng tối giản, kết bằng logo và số Zalo…'",
    },
    X5: {
      code: 'X5',
      level: 'block',
      stage: 'preflight',
      desc: 'Số cảnh dùng được < số cảnh cần',
      msg: 'Chỉ có {usable} cảnh dùng được (ước tính), cần ít nhất {need}. Thiếu {short} cảnh — giảm độ dài, đổi phong cách chậm hơn, hoặc quay bổ sung.',
    },
    X6: {
      code: 'X6',
      level: 'warn',
      stage: 'preflight',
      desc: 'Số cảnh dùng được < số cảnh cần × 1,3',
      msg: 'Có {usable} cảnh (ước tính), nên có {ideal} để AI có lựa chọn. Sẽ lấy nhiều đoạn từ cùng clip; chất lượng có thể giảm.',
    },
    X7: {
      code: 'X7',
      level: 'warn',
      stage: 'preflight',
      desc: 'Bán hàng có lời × chưa rõ có lời nói',
      msg: 'Chưa quét được lời nói trong footage vừa tải (cần chạy inventory thật để biết chắc). Nếu footage không có ai nói, đổi phong cách hoặc tải voice-over lên.',
    },
    X9: {
      code: 'X9',
      level: 'warn',
      stage: 'preflight',
      desc: 'Phong cách × Mục đích lệch (bad ở compat_style_purpose)',
      msg: "'{style}' ít hợp với mục đích '{purpose}' (lệch giọng điệu/nội dung). Vẫn dựng được.",
    },
    X10: {
      code: 'X10',
      level: 'warn',
      stage: 'preflight',
      desc: 'Sang trọng hoặc Kể chuyện nghề × 15s',
      msg: "Với '{style}', 15 giây chỉ đủ khoảng 4–5 cảnh. Vẫn dựng.",
    },
    X14: {
      code: 'X14',
      level: 'block',
      stage: 'preflight',
      desc: 'Nhạc tải lên chưa tick quyền sử dụng',
      msg: "Nhạc tải lên phải tick 'Tôi có quyền sử dụng bài này' trước khi dựng.",
    },
    X15: {
      code: 'X15',
      level: 'warn',
      stage: 'preflight',
      desc: '3 bản × 3 phút',
      msg: '3 bản nháp × 3 phút sẽ tốn thời gian và chi phí gấp 3, và câu trả lời của Claude dễ bị cắt vì quá dài. Nên chọn 2 bản; kiểm tra ước tính trước khi chạy.',
    },
    X11: {
      code: 'X11',
      level: 'warn',
      stage: 'preflight',
      desc: 'Không nhạc × phong cách dẫn bằng nhạc có driver_fallback = grid (TVC)',
      msg: "'{style}' mà không có nhạc sẽ mất lực đẩy. Sẽ dùng lưới nhịp ảo {grid}s.",
    },
  },

  // config/glossary.json → banned_claims (V7 checks these on AI-written on-screen text).
  // Same text, same order: src/data/rules.test.ts fails when this copy drifts from the file.
  bannedClaims: [
    '100%',
    'duy nhất',
    'số 1',
    'điều trị',
    'đặc trị',
    'chữa trị',
    'chữa bệnh',
    'trị bệnh',
    'phòng bệnh',
    'ngon nhất',
    'tốt nhất',
    'rẻ nhất',
    'chất lượng nhất',
    'sạch nhất',
    'uy tín nhất',
    'thần dược',
    'minh long',
    'atala',
    'foco',
    '100 phần trăm tự nhiên',
    '100 phần trăm thiên nhiên',
    'tự nhiên 100 phần trăm',
    'thiên nhiên 100 phần trăm',
  ],

  // config/rules.json → delivery.naming.products (the products a delivered file may be named
  // for). Same list, same order: src/data/rules.test.ts fails when this copy drifts.
  deliveryProducts: [
    'gio-lua',
    'cha-lua',
    'cha-bo',
    'nem-chua',
    'bo-vien',
    'chung',
  ],

  // config/rules.json → platforms (16/10/2026): profiles in order, `default` = ticked at start.
  // src/data/rules.test.ts fails when this copy drifts.
  platforms: [
    { id: 'tiktok', name: 'TikTok', short: 'TikTok', zaloOnEndCard: false, default: true },
    { id: 'facebook', name: 'Facebook Reels', short: 'Reels', zaloOnEndCard: true, default: true },
  ],

  // config/rules.json → block_names (17/10/2026): what a person reads instead of a block id.
  // src/data/rules.test.ts fails when this copy drifts.
  blockNames: {
    hook: 'Mở đầu',
    nhu_cau: 'Nhu cầu',
    san_pham: 'Sản phẩm',
    bang_chung: 'Bằng chứng',
    cta: 'Kêu gọi',
    ve_sinh: 'Vệ sinh',
    quy_trinh: 'Quy trình',
    hook_uu_dai: 'Mở đầu ưu đãi',
    chi_tiet_uu_dai: 'Chi tiết ưu đãi',
    cta_gap: 'Kêu gọi gấp',
    mo: 'Mở đầu',
    con_nguoi: 'Con người',
    gia_tri: 'Giá trị',
    logo: 'Logo',
    con_nguoi_1: 'Con người (1)',
    con_nguoi_2: 'Con người (2)',
  },

  // config/rules.json → pick.tag_names (CLIP visual-dictionary labels, phase 4b; read by
  // domain/pick.py). src/data/rules.test.ts fails when this copy drifts.
  pickTagNames: {
    nguoi_thao_tac: 'Công nhân thao tác',
    thiet_bi: 'Máy móc, thiết bị',
    khu_vuc: 'Toàn cảnh khu sản xuất',
    thanh_pham: 'Thành phẩm',
    mon_an: 'Món ăn đã nấu',
    mat_cat: 'Mặt cắt sản phẩm',
    chung_nhan: 'Giấy chứng nhận',
    nguoi_noi: 'Người nói trước ống kính',
    khac: 'Màn chữ / logo',
  },

  // config/rules.json → footage.min_usable_ratio (X6 "ideal" = ceil(need × ratio); the demo also
  // divides raw footage by it to estimate usable scenes). src/data/rules.test.ts checks it.
  minUsableRatio: 1.3,

  // config/rules.json → qc.max_rounds (the API does not echo it). src/data/rules.test.ts checks it.
  qcMaxRounds: 3,
};

export const PACING_LABELS: Record<'loi' | 'nhac' | 'luoi_ao', { label: string; hint: string }> = {
  loi: { label: 'LỜI', hint: 'Audio-first — cắt theo lời nói (driver: speech)' },
  nhac: { label: 'NHẠC', hint: 'Beat-sync — cắt theo phách nhạc (driver: music)' },
  luoi_ao: { label: 'LƯỚI ẢO', hint: 'Grid-based — không có nhạc/lời rõ, dùng lưới nhịp ảo' },
};
