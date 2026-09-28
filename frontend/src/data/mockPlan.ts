/**
 * Verbatim copy of a REAL plan.json produced by the `plan` CLI command (repo root,
 * `Xuong GANI/edit/plan.json`, run 29/09/2026 — review_mon_an × ban_hang × 30s ×
 * thu_vien/108bpm). Shown in the "Xem plan.json" modal so the shape matches exactly what
 * phase 4a's biên dịch plan step really emits, instead of a hand-invented JSON shape.
 */
export const SAMPLE_PLAN_JSON = {
  rules_version: '2.0',
  options: {
    style: 'review_mon_an',
    purpose: 'ban_hang',
    duration: 30,
    drafts: 2,
    music: 'thu_vien',
  },
  driver: 'music',
  music: {
    track: '108bpm-review-mon-an.m4a',
    bpm: 108.0,
    effective_bpm: 108.0,
    fold: 'giu_nguyen',
    first_beat_sec: 0.5,
    audio_in_sec: 0.5,
    duration_sec: 200.0,
    source: 'detected',
    loop_sec: null,
    repeats: null,
    path: 'E:\\GANI BRAINSTORM\\Nhac test BPM\\108bpm-review-mon-an.m4a',
  },
  beat_sec: 0.5555555555555556,
  target_beats: 54,
  planned_beats: 54,
  beats_per_scene_allowed: [2, 4],
  blocks: [
    { role: 'hook', beats: 6, required_tags: [], text: null },
    { role: 'nhu_cau', beats: 8, required_tags: [], text: null },
    { role: 'san_pham', beats: 24, required_tags: ['thanh_pham', 'mon_an'], text: null },
    { role: 'bang_chung', beats: 8, required_tags: [], text: null },
    { role: 'cta', beats: 8, required_tags: [], text: 'Nhắn tin đặt hàng' },
  ],
  text_rules: { min_words: 3, max_words: 6 },
  draft_strategy: ['toi_uu', 'doi_hook_doi_mach'],
  warnings: [
    'X6: Có 18 cảnh, nên có 20 để AI có lựa chọn. Sẽ lấy nhiều đoạn từ cùng clip; chất lượng có thể giảm.',
    'X8: Thiếu cảnh bắt buộc: mon_an. Sẽ dựng bằng cảnh gần nhất.',
  ],
  adjustments: [] as string[],
};
