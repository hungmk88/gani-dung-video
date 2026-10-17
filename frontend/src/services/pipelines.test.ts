// Run with `npm test`. Phase 6c: pipeline cards, merged steps, approval data.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { ApiJob, ApiJobDetail, ApiStep } from '../types/api.ts';
import type { VideoOptions } from '../types/rules.ts';
import {
  buildCards,
  mergeSteps,
  parsePipelineCardId,
  pipelineBody,
  pipelineCardId,
  pipelineStatus,
  rerenderBody,
  toHealthInfo,
  toPipelineCard,
} from './mappers.ts';

const IDS = [
  'inventory',
  'nhan_dang',
  'plan_compile',
  'pick',
  'ai_draft',
  'render_preview',
  'qc_verify',
  'final_export',
] as const;

/** The 8 steps a job reports; `states` override the default 'queued' by step id. */
function steps(
  states: Partial<Record<(typeof IDS)[number], ApiStep['status']>>,
  detail: Record<string, string> = {},
): ApiStep[] {
  return IDS.map((id) => ({ id, label: id, status: states[id] ?? 'queued', detail: detail[id] ?? null }));
}

// Shape of docs/api-jobs.md §4b (pipeline #1 of project "0/Xuong GANI").
function pjob(over: Partial<ApiJob>): ApiJob {
  return {
    id: 1,
    pipeline_id: 1,
    depends_on: null,
    project_id: '0/Xuong GANI',
    project_name: 'Xuong GANI',
    title: 't',
    kind: 'render',
    draft: '',
    size: 'preview',
    status: 'queued',
    result: null,
    frontend_status: 'queued',
    attempts: 0,
    max_attempts: 2,
    progress_pct: 0,
    stage: null,
    eta_sec: null,
    created_at: '2026-09-29T04:56:08Z',
    started_at: null,
    finished_at: null,
    error_msg: null,
    no_asr: false,
    options: { style: 'ke_chuyen_nghe', purpose: 'gioi_thieu_xuong', duration: 15, drafts: 2, music: 'khong_nhac' },
    video_url: null,
    cover_url: null,
    srt_url: null,
    steps: steps({}),
    qa: null,
    ...over,
  };
}

const ANALYZE = pjob({
  id: 1,
  kind: 'analyze',
  status: 'completed',
  result: 'passed',
  frontend_status: 'done',
  steps: steps({ inventory: 'done', nhan_dang: 'done' }),
});
const DRAFT = pjob({
  id: 2,
  kind: 'draft',
  depends_on: 1,
  status: 'awaiting_approval',
  frontend_status: 'awaiting_approval',
  needs_approval: true,
  approval_expires_at: '2026-09-30T09:31:00Z',
  cost: { input_tokens: 1678, worst_call_vnd: 4165.58, cap_vnd: 20000, approved_vnd: null, spent_vnd: null },
  steps: steps(
    { inventory: 'done', nhan_dang: 'done', plan_compile: 'done', pick: 'done', ai_draft: 'running' },
    { ai_draft: 'Chờ duyệt chi phí' },
  ),
});
const RENDER_A = pjob({ id: 3, kind: 'render', draft: 'A', depends_on: 2 });
const RENDER_B = pjob({ id: 4, kind: 'render', draft: 'B', depends_on: 2 });

const OPTS: VideoOptions = {
  phong_cach: 'tvc_cat_nhanh',
  muc_dich: 'ban_hang',
  do_dai_giay: 30,
  so_ban_nhap: 1,
  nhac: 'thu_vien',
  nhac_file: '/audio/124bpm-tvc-cat-nhanh.m4a',
  nhac_quyen_su_dung: null,
  khuyen_mai: null,
  ghi_chu: '',
  pacing: 'nhac',
};

function chain(jobs: ApiJob[], status = 'running') {
  return { id: 1, project_id: '0/Xuong GANI', project_name: 'Xuong GANI', status, needs_approval: false, jobs };
}

test('pipeline card ids round-trip and reject plain ids', () => {
  assert.equal(pipelineCardId(7), 'p:7');
  assert.equal(parsePipelineCardId('p:7'), 7);
  assert.equal(parsePipelineCardId('7'), null);
  assert.equal(parsePipelineCardId('p:0'), null);
  assert.equal(parsePipelineCardId('p:x'), null);
});

test('pipelineStatus follows the documented precedence', () => {
  assert.equal(pipelineStatus([RENDER_A, RENDER_B]), 'queued');
  assert.equal(pipelineStatus([ANALYZE, RENDER_A]), 'running'); // between two jobs
  assert.equal(pipelineStatus([ANALYZE, DRAFT, RENDER_A]), 'awaiting_approval');
  assert.equal(pipelineStatus([ANALYZE, { ...DRAFT, frontend_status: 'running' }]), 'running');
  assert.equal(pipelineStatus([ANALYZE, { ...DRAFT, frontend_status: 'error' }, RENDER_A]), 'error');
  assert.equal(pipelineStatus([ANALYZE, { ...RENDER_A, frontend_status: 'qa_review' }]), 'qa_review');
  assert.equal(pipelineStatus([ANALYZE, { ...RENDER_A, frontend_status: 'done' }]), 'done');
});

test('mergeSteps judges each step only by the job kind that owns it', () => {
  const m = mergeSteps([ANALYZE, DRAFT, RENDER_A, RENDER_B]);
  const by = Object.fromEntries(m.map((s) => [s.id, s.status]));
  // render jobs report inventory as 'queued' (not theirs) — must not drag analyze's 'done' down
  assert.equal(by.inventory, 'done');
  assert.equal(by.nhan_dang, 'done');
  assert.equal(by.pick, 'done');
  assert.equal(by.ai_draft, 'running');
  assert.equal(by.render_preview, 'queued');
  assert.equal(m.find((s) => s.id === 'ai_draft')?.detail, 'Chờ duyệt chi phí');
});

test('mergeSteps: a failing owner makes the step an error; done needs every owner done', () => {
  const okA = { ...RENDER_A, steps: steps({ render_preview: 'done' }) };
  const badB = { ...RENDER_B, steps: steps({ render_preview: 'error' }, { render_preview: 'ffmpeg lỗi' }) };
  const runB = { ...RENDER_B, steps: steps({ render_preview: 'running' }) };
  const at = (jobs: ApiJob[]) => mergeSteps(jobs).find((s) => s.id === 'render_preview');
  assert.equal(at([okA, badB])?.status, 'error');
  assert.equal(at([okA, badB])?.detail, 'ffmpeg lỗi');
  assert.equal(at([okA, runB])?.status, 'running');
  assert.equal(at([okA, RENDER_B])?.status, 'queued');
  assert.equal(at([okA])?.status, 'done');
});

test('toPipelineCard exposes the pending approval with the numbers the server holds', () => {
  const card = toPipelineCard(chain([ANALYZE, DRAFT, RENDER_A], 'awaiting_approval'));
  assert.equal(card.id, 'p:1');
  assert.equal(card.status, 'awaiting_approval');
  assert.deepEqual(card.approval, {
    jobId: '2',
    inputTokens: 1678,
    worstCallVnd: 4165.58,
    capVnd: 20000,
    expiresAt: '2026-09-30T09:31:00Z',
  });
  assert.deepEqual(
    card.jobs?.map((j) => [j.id, j.kind, j.retryable]),
    [['1', 'analyze', false], ['2', 'draft', false], ['3', 'render', false]],
  );
});

test('toPipelineCard drops the approval once approved and marks failures retryable', () => {
  const approved = {
    ...DRAFT,
    status: 'queued',
    frontend_status: 'queued',
    needs_approval: false,
    cost: { ...DRAFT.cost!, approved_vnd: 6500 },
  };
  const failed = { ...RENDER_A, status: 'failed', frontend_status: 'error', error_msg: 'ffmpeg lỗi' };
  const card = toPipelineCard(chain([ANALYZE, approved, failed], 'error'));
  assert.equal(card.approval, undefined);
  const info = card.jobs?.find((j) => j.id === '3');
  assert.equal(info?.retryable, true);
  assert.equal(info?.errorMsg, 'ffmpeg lỗi');
});

test('toPipelineCard builds one output per draft and merges preview + final of a draft', () => {
  const qa = {
    passed: true,
    rounds: 1,
    fixes: [],
    loudness_two_pass: false,
    lufs: -13.9,
    true_peak_db: -2,
    checks: [{ code: 'T3', label: 'x', passed: true, detail: 'ok' }],
  };
  const done = (id: number, draft: string, size: 'preview' | 'final') =>
    pjob({
      id,
      kind: 'render',
      draft,
      size,
      status: 'completed',
      result: 'passed',
      frontend_status: 'done',
      video_url: `/media/${id}/video`,
      qa,
    });
  const card = toPipelineCard(
    chain([ANALYZE, done(3, 'A', 'preview'), done(4, 'B', 'preview'), done(5, 'A', 'final')], 'done'),
  );
  assert.deepEqual(
    card.outputs?.map((o) => [o.draft, o.previewUrl, o.finalUrl]),
    [['A', '/media/3/video', '/media/5/video'], ['B', '/media/4/video', null]],
  );
  assert.equal(card.previewUrl, '/media/3/video'); // the first draft feeds the flat fields
  assert.ok(card.outputs?.every((o) => o.qa !== null));
});

test('toPipelineCard merges console lines of all jobs in time order with a source tag', () => {
  const withEvents = (d: ApiJob, lines: Array<[number, string, string]>): ApiJobDetail => ({
    ...d,
    events: lines.map(([id, ts, text]) => ({ id, ts, level: 'info', text })),
  });
  const card = toPipelineCard(chain([ANALYZE, RENDER_A]), [
    withEvents(RENDER_A, [[1, '2026-09-29T05:00:02Z', 'dựng']]),
    withEvents(ANALYZE, [
      [1, '2026-09-29T05:00:01Z', 'quét'],
      [2, '2026-09-29T05:00:03Z', 'xong'],
    ]),
  ]);
  assert.deepEqual(
    card.logLines.map((l) => l.text),
    ['[phân tích] quét', '[dựng A] dựng', '[phân tích] xong'],
  );
});

test('buildCards: one card per pipeline, lone jobs separate, newest first', () => {
  const lone = pjob({ id: 9, pipeline_id: null, kind: 'render', draft: 'A' });
  const cards = buildCards([ANALYZE, DRAFT, RENDER_A, RENDER_B, lone]);
  assert.deepEqual(cards.map((c) => c.id), ['9', 'p:1']);
  const p = cards.find((c) => c.id === 'p:1');
  assert.equal(p?.status, 'awaiting_approval'); // derived locally with the server's rule
  assert.equal(p?.jobs?.length, 4);
});

test('pipelineBody sends the preflight-shaped options plus the run switches', () => {
  const body = pipelineBody(OPTS, {
    projectId: '0/Xuong GANI',
    draft: 'A',
    final: true,
    noAsr: false,
    forceAnalyze: true,
  });
  const options = body.options as Record<string, unknown>;
  assert.equal(body.project_id, '0/Xuong GANI');
  assert.equal(body.draft, 'A');
  assert.equal(body.final, true);
  assert.equal(body.force_analyze, true);
  assert.equal(body.no_music, false);
  assert.equal(options.nhac_file, '124bpm-tvc-cat-nhanh.m4a');
  assert.equal('pacing' in options, false);
});

test('toHealthInfo carries the API key flag and the daily budget', () => {
  const h = toHealthInfo({
    ok: true,
    rules_version: '2.0',
    daemon_online: true,
    daemon_pid: 1,
    daemon_stopping: null,
    jobs: { awaiting_approval: 1 },
    allowed_roots_configured: true,
    ai_key_configured: false,
    budget: { limit_vnd: 50000, spent_24h_vnd: 1000, reserved_vnd: 500 },
  });
  assert.equal(h.aiKeyConfigured, false);
  assert.deepEqual(h.budget, { limitVnd: 50000, spent24hVnd: 1000, reservedVnd: 500 });
});

test('toPipelineCard keeps what the chain renders so "Render lại" asks the same again', () => {
  const one = toPipelineCard(chain([ANALYZE, { ...RENDER_A, no_asr: true }, { ...RENDER_A, id: 5, size: 'final', no_asr: true }]));
  assert.deepEqual(rerenderBody(one), { project_id: '0/Xuong GANI', draft: 'A', final: true, no_asr: true, platforms: [], no_music: false });
  const two = toPipelineCard(chain([ANALYZE, DRAFT, RENDER_A, RENDER_B]));
  assert.deepEqual(rerenderBody(two), { project_id: '0/Xuong GANI', draft: 'all', final: false, no_asr: false, platforms: [], no_music: false });
});

test('a chain for TikTok and Reels has one output per draft and platform, in rules order', () => {
  const card = toPipelineCard(
    chain([
      ANALYZE,
      DRAFT,
      { ...RENDER_A, id: 3, platform: 'facebook' },
      { ...RENDER_A, id: 4, platform: 'tiktok' },
      { ...RENDER_B, id: 5, platform: 'tiktok' },
      { ...RENDER_B, id: 6, platform: 'facebook' },
    ]),
  );
  assert.deepEqual(
    card.outputs?.map((o) => `${o.draft}|${o.platform}`),
    ['A|tiktok', 'A|facebook', 'B|tiktok', 'B|facebook'],
  );
  assert.deepEqual(card.platforms, ['tiktok', 'facebook']);
  assert.deepEqual(rerenderBody(card).platforms, ['tiktok', 'facebook']);
  assert.equal(card.jobs?.find((j) => j.id === '4')?.platform, 'tiktok');
});

test('a chain rendered without music says so and "Dựng lại" keeps the music out (16/10/2026)', () => {
  const card = toPipelineCard(
    chain([ANALYZE, DRAFT, { ...RENDER_A, id: 3, platform: 'tiktok', no_music: true }]),
  );
  assert.equal(card.noMusic, true);
  assert.equal(rerenderBody(card).no_music, true);
  assert.equal(card.jobs?.find((j) => j.id === '3')?.noMusic, true);
  assert.equal(card.jobs?.find((j) => j.kind === 'draft')?.noMusic, undefined);
  const body = pipelineBody(OPTS, {
    projectId: 'p', draft: 'all', final: false, noAsr: false, forceAnalyze: false, noMusic: true,
  });
  assert.equal(body.no_music, true);
});
