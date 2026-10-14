// Run with `npm test` (plain node:test — no test-runner dependency).
import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { ApiJob, ApiJobDetail } from '../types/api.ts';
import type { VideoOptions } from '../types/rules.ts';
import {
  basename,
  groupLatest,
  optionsFromApi,
  pipelineBody,
  preflightBody,
  toMusicList,
  toPreflightResult,
  toPipelineCard,
  toQaReport,
  toRenderJob,
} from './mappers.ts';

// Shape copied from docs/api-jobs.md §4 (job 1, completed preview render).
function job(over: Partial<ApiJob> = {}): ApiJob {
  return {
    id: 1,
    project_id: '0/Xuong GANI',
    project_name: 'Xuong GANI',
    title: 'Xuong GANI — bản A (nháp)',
    kind: 'render',
    draft: 'A',
    size: 'preview',
    status: 'completed',
    result: 'passed',
    frontend_status: 'done',
    attempts: 1,
    max_attempts: 2,
    progress_pct: 100,
    stage: 'khoi_dong',
    eta_sec: null,
    created_at: '2026-09-29T04:56:08Z',
    started_at: '2026-09-29T04:56:08Z',
    finished_at: '2026-09-29T04:56:08Z',
    error_msg: null,
    no_asr: false,
    options: { style: 'ke_chuyen_nghe', purpose: 'gioi_thieu_xuong', duration: 15, drafts: 2, music: 'khong_nhac' },
    video_url: '/media/1/video',
    cover_url: '/media/1/cover',
    srt_url: '/media/1/srt',
    steps: [
      { id: 'inventory', label: 'Inventory', status: 'done', detail: null },
      { id: 'unknown_step', label: 'lạ', status: 'done', detail: null },
      { id: 'render_preview', label: 'Dựng', status: 'error', detail: 'hỏng' },
    ],
    qa: null,
    ...over,
  };
}

test('optionsFromApi maps English plan keys to the Vietnamese VideoOptions', () => {
  const o = optionsFromApi(job().options);
  assert.equal(o.phong_cach, 'ke_chuyen_nghe');
  assert.equal(o.muc_dich, 'gioi_thieu_xuong');
  assert.equal(o.do_dai_giay, 15);
  assert.equal(o.so_ban_nhap, 2);
  assert.equal(o.nhac, 'khong_nhac');
});

test('optionsFromApi blanks unknown values instead of inventing them', () => {
  const o = optionsFromApi({ style: 'nope', purpose: 3, duration: 7, drafts: 9, music: null });
  assert.deepEqual([o.phong_cach, o.muc_dich, o.do_dai_giay, o.so_ban_nhap, o.nhac], ['', '', 0, 1, '']);
});

test('toRenderJob uses frontend_status and keeps only known pipeline steps', () => {
  const r = toRenderJob(job({ frontend_status: 'qa_review' }), []);
  assert.equal(r.status, 'qa_review');
  assert.deepEqual(r.steps.map((s) => s.id), ['inventory', 'render_preview']);
  assert.equal(r.steps[1].detail, 'hỏng');
  assert.equal(r.id, '1');
});

test('toRenderJob falls back to error for an unknown frontend_status', () => {
  assert.equal(toRenderJob(job({ frontend_status: 'mystery' }), []).status, 'error');
});

test('toRenderJob merges preview and final urls of the same draft (newest completed wins)', () => {
  const preview = job({ id: 1, size: 'preview', video_url: '/media/1/video' });
  const preview2 = job({ id: 3, size: 'preview', video_url: '/media/3/video' });
  const final = job({ id: 2, size: 'final', video_url: '/media/2/video' });
  const running = job({ id: 4, size: 'final', status: 'processing', video_url: '/media/4/video' });
  const r = toRenderJob(final, [preview, preview2, final, running]);
  assert.equal(r.previewUrl, '/media/3/video');
  assert.equal(r.finalUrl, '/media/2/video');
});

test('toRenderJob maps events of a detail to console lines and coerces odd levels', () => {
  const detail: ApiJobDetail = {
    ...job(),
    events: [
      { id: 1, ts: '2026-09-29T04:56:08Z', level: 'info', text: 'a' },
      { id: 2, ts: '2026-09-29T04:56:09Z', level: 'debug', text: 'b' },
      { id: 3, ts: '2026-09-29T04:56:10Z', level: 'warn', text: 'c' },
    ],
  };
  assert.deepEqual(toRenderJob(detail, []).logLines.map((l) => l.level), ['info', 'info', 'warn']);
});

test('groupLatest yields one job per (project, draft), newest first', () => {
  const list = [
    job({ id: 1, draft: 'A' }),
    job({ id: 2, draft: 'A', size: 'final', video_url: '/media/2/video' }),
    job({ id: 3, draft: 'B' }),
  ];
  const g = groupLatest(list);
  assert.deepEqual(g.map((j) => j.id), ['3', '2']);
  assert.equal(g[1].finalUrl, '/media/2/video');
  assert.equal(g[1].previewUrl, '/media/1/video');
});

test('toQaReport groups T-checks, never fabricates V1–V9 rows', () => {
  const qa = toQaReport({
    passed: true,
    rounds: 1,
    fixes: [],
    loudness_two_pass: false,
    lufs: -13.9,
    true_peak_db: -2.0,
    checks: [
      { code: 'T3', label: 'Độ to', passed: true, detail: 'ok' },
      { code: 'T1', label: 'Khung', passed: true, detail: 'ok' },
      { code: 'T9', label: 'Lạ', passed: false, detail: 'x' },
    ],
  });
  const titles = qa.groups.map((g) => g.title);
  assert.deepEqual(titles, [
    'Kỹ thuật video (T1, T2, T4, T5)',
    'Âm thanh (T3)',
    'Khác',
    'AI viết kịch bản (V1–V9)',
  ]);
  const ai = qa.groups[3];
  assert.equal(ai.checks.length, 0);
  assert.match(ai.emptyNote ?? '', /Chưa có dữ liệu/);
  assert.equal(qa.max_rounds, 3);
  assert.equal(qa.overall_pass, true);
});

const OPTS: VideoOptions = {
  phong_cach: 'tvc_cat_nhanh',
  muc_dich: 'ban_hang',
  do_dai_giay: 30,
  so_ban_nhap: 1,
  nhac: 'thu_vien',
  nhac_file: '/audio/124bpm-tvc-cat-nhanh.m4a',
  nhac_quyen_su_dung: false,
  khuyen_mai: { uu_dai: 'x', thoi_han: 'y', dieu_kien: '' },
  ghi_chu: '   ',
  pacing: 'nhac',
};

test('preflightBody drops UI-only keys and normalises blanks to null', () => {
  const { options } = preflightBody(OPTS);
  assert.equal('pacing' in options, false);
  assert.equal(options.nhac_file, '124bpm-tvc-cat-nhanh.m4a');
  assert.equal(options.nhac_quyen_su_dung, null); // only meaningful for uploaded music
  assert.equal(options.khuyen_mai, null); // purpose is not khuyen_mai
  assert.equal(options.ghi_chu, null);
});

test('preflightBody keeps the rights tick for uploaded music and promo fields for khuyen_mai', () => {
  const { options } = preflightBody({ ...OPTS, nhac: 'tai_len', muc_dich: 'khuyen_mai', nhac_quyen_su_dung: true });
  assert.equal(options.nhac_quyen_su_dung, true);
  assert.deepEqual(options.khuyen_mai, { uu_dai: 'x', thoi_han: 'y', dieu_kien: '' });
});

test('toPreflightResult renames msg→message and keeps auto adjustments apart', () => {
  const r = toPreflightResult({
    valid: false,
    error_code: 'X14',
    error_msg: 'm',
    errors: [{ code: 'X14', level: 'block', msg: 'thiếu tick' }],
    warnings: [{ code: 'X11', level: 'warn', msg: 'không nhạc' }],
    adjustments: [{ code: 'X12', level: 'auto', msg: 'nhân đôi BPM' }],
    scenes_needed: 23,
  });
  assert.equal(r.valid, false);
  assert.equal(r.errors[0].message, 'thiếu tick');
  assert.equal(r.adjustments[0].level, 'auto');
  assert.equal(r.scenes_needed, 23);
  assert.equal(r.scenes_estimated, null);
});

test('basename handles both separators', () => {
  assert.equal(basename('C:\\a\\b.m4a'), 'b.m4a');
  assert.equal(basename('/audio/b.m4a'), 'b.m4a');
});

test('preflightBody sends the library track BPM and the override only when they are known', () => {
  const known = preflightBody({ ...OPTS, nhac_bpm: 72, bpm_override: true });
  assert.equal(known.options.nhac_bpm, 72);
  assert.equal(known.options.bpm_override, true);
  const plain = preflightBody({ ...OPTS });
  assert.equal('nhac_bpm' in plain.options, false);
  assert.equal('bpm_override' in plain.options, false);
  assert.equal('nhac_bpm' in preflightBody({ ...OPTS, nhac_bpm: null }).options, false);
});

test('the pipeline options keep the override but not the UI-known track BPM', () => {
  const body = pipelineBody(
    { ...OPTS, nhac_bpm: 72, bpm_override: true },
    { projectId: 'p1', draft: 'all', final: false, noAsr: false, forceAnalyze: false },
  );
  const options = body.options as Record<string, unknown>;
  assert.equal(options.bpm_override, true);
  assert.equal('nhac_bpm' in options, false); // the plan step measures the real BPM itself
});

test('toMusicList keeps the file name as identity and builds the preview URL from it', () => {
  const list = toMusicList({
    configured: true,
    pending: 1,
    tracks: [
      {
        file: 'Nhạc nền 01.mp3', path: 'Nhạc nền 01.mp3', title: 'Nhạc nền', bpm: 92,
        bpm_source: 'library', first_beat_sec: 0.4, duration_sec: 187.4, rights: 'royalty_free',
      },
      {
        file: 'moi.wav', path: 'moi.wav', title: 'moi', bpm: null, bpm_source: 'pending',
        first_beat_sec: null, duration_sec: null, rights: 'thu_vien_noi_bo',
      },
    ],
  });
  assert.equal(list.configured, true);
  assert.equal(list.pending, 1);
  assert.equal(list.tracks[0].id, 'Nhạc nền 01.mp3');
  assert.equal(list.tracks[0].audio_url, '/media/music/Nh%E1%BA%A1c%20n%E1%BB%81n%2001.mp3');
  assert.equal(list.tracks[1].bpm, null);
  assert.equal(list.tracks[1].bpm_source, 'pending');
});

test('a final render with a review gives the card and the pipeline row their badge data', () => {
  const review = {
    status: 'changes_requested' as const, requested_at: '2026-10-15T08:00:00Z', decided_at: null,
    decided_by: 'NVH', reason: 'chữ cuối bị che', reminded_at: null, overdue: false, failed_checks: [],
  };
  const final = job({ id: 9, size: 'final', review });
  const card = toRenderJob(final, [job({ id: 8, size: 'preview' }), final]);
  assert.equal(card.review?.status, 'changes_requested');
  assert.equal(card.review?.reason, 'chữ cuối bị che');
  const chain = toPipelineCard({
    id: 3, project_id: '0/x', project_name: 'x', status: 'done', needs_approval: false,
    jobs: [job({ id: 8, size: 'preview', pipeline_id: 3 }), { ...final, pipeline_id: 3 }],
  });
  assert.equal(chain.review?.status, 'changes_requested');
  assert.equal(chain.jobs?.find((j) => j.id === '9')?.review?.status, 'changes_requested');
  assert.equal(chain.jobs?.find((j) => j.id === '8')?.review ?? null, null);
});

test('a card without any review has none', () => {
  assert.equal(toRenderJob(job(), []).review ?? null, null);
});
