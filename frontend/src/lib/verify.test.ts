// Run with `npm test`. Phase 6h/6i: the yellow footage warning and the V/S check table.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { ApiFootage, ApiJob, ApiVerify } from '../types/api.ts';
import type { FootageInfo, VerifyReport } from '../types/rules.ts';
import { toFootage, toPipelineCard, toQaReport, toVerify } from '../services/mappers.ts';
import { footageWarning, roundStatus, verifyHeadline } from './verify.ts';

const API_FOOTAGE: ApiFootage = {
  scenes_available: 9,
  scenes_needed_min: 8,
  scenes_needed_max: 9,
  warn_below: 12,
  tight: true,
  blocks: [
    { role: 'hook', candidates: 9, beats_needed: 4, max_fill: 54, scenes_min: 1, scenes_max: 1, tight: false },
    { role: 'quy_trinh', candidates: 3, beats_needed: 16, max_fill: 18, scenes_min: 3, scenes_max: 4, tight: true },
  ],
};

const V4_MSG = "Cảnh 'VID_150353_s0_2' dùng 2 lần.";
function checks(v4: string, v4detail = '') {
  return [
    { code: 'V1', label: 'Cấu trúc câu trả lời và mã cảnh', status: 'pass', detail: '' },
    { code: 'V4', label: 'Không dùng lặp cảnh', status: v4, detail: v4detail },
    { code: 'V6', label: 'Đủ cảnh bắt buộc (cảnh báo)', status: 'warn', detail: 'Khối hook thiếu cảnh nhãn.' },
  ];
}
const API_VERIFY: ApiVerify = {
  mode: 'music',
  final_status: 'failed',
  rounds_used: 3,
  max_rounds: 3,
  checks: checks('fail', V4_MSG),
  rounds: [1, 2, 3].map((n) => ({
    round: n,
    drafts: n === 1 ? ['A', 'B'] : ['A'],
    parsed: true,
    passed: false,
    cost_vnd: 900,
    checks: checks('fail', V4_MSG),
  })),
};

// ───────────── footage

test('toFootage renames the wire fields and keeps the server verdict', () => {
  const f = toFootage(API_FOOTAGE);
  assert.deepEqual(
    [f.available, f.neededMin, f.neededMax, f.warnBelow, f.tight],
    [9, 8, 9, 12, true],
  );
  assert.equal(f.blocks[1].role, 'quy_trinh');
  assert.equal(f.blocks[1].scenesMax, 4);
  assert.equal(f.blocks[1].tight, true);
});

test('footageWarning: nothing when the footage has room, numbers when it is tight', () => {
  const roomy: FootageInfo = { ...toFootage(API_FOOTAGE), tight: false };
  assert.equal(footageWarning(roomy), null);
  const w = footageWarning(toFootage(API_FOOTAGE));
  assert.ok(w !== null);
  assert.match(w.headline, /Footage sát nhu cầu/);
  assert.match(w.body, /9 cảnh/);
  assert.match(w.body, /8–9/); // the plan needs 8–9 scenes
  assert.match(w.body, /12/); // and the room the server asks for
  assert.deepEqual(w.tightBlocks, ['quy_trinh']);
});

test('footageWarning shows a single number when min equals max', () => {
  const f: FootageInfo = { ...toFootage(API_FOOTAGE), neededMin: 9, neededMax: 9 };
  const w = footageWarning(f);
  assert.ok(w !== null);
  assert.match(w.body, /cần 9 cảnh/);
});

function card(over: Partial<ApiJob>) {
  const base = {
    id: 2, pipeline_id: 1, depends_on: 1, project_id: '0/x', project_name: 'x', title: 't',
    kind: 'draft', draft: '', size: 'preview', status: 'awaiting_approval', result: null,
    frontend_status: 'awaiting_approval', attempts: 0, max_attempts: 2, progress_pct: 0,
    stage: null, eta_sec: null, created_at: '2026-10-01T01:00:00Z', started_at: null,
    finished_at: null, error_msg: null, no_asr: false, options: {}, video_url: null,
    cover_url: null, srt_url: null, steps: [], qa: null,
  } as ApiJob;
  return toPipelineCard({
    id: 1, project_id: '0/x', project_name: 'x', status: 'awaiting_approval', needs_approval: true,
    jobs: [{ ...base, ...over }],
  });
}

test('the approval of a pipeline card carries the footage numbers', () => {
  const c = card({
    needs_approval: true,
    cost: { input_tokens: 1678, worst_call_vnd: 4165.58, cap_vnd: 20000, approved_vnd: null, spent_vnd: null },
    footage: API_FOOTAGE,
  });
  assert.equal(c.approval?.footage?.tight, true);
  assert.equal(c.approval?.footage?.available, 9);
});

test('an older server (no footage field) gives an approval without footage', () => {
  const c = card({
    needs_approval: true,
    cost: { input_tokens: 1, worst_call_vnd: 1, cap_vnd: 2, approved_vnd: null, spent_vnd: null },
  });
  assert.ok(c.approval);
  assert.equal(c.approval?.footage, undefined);
});

// ───────────── verify

test('toVerify keeps every round and maps statuses', () => {
  const v = toVerify(API_VERIFY);
  assert.equal(v.mode, 'music');
  assert.equal(v.finalStatus, 'failed');
  assert.equal(v.roundsUsed, 3);
  assert.equal(v.rounds.length, 3);
  assert.deepEqual(v.rounds[1].drafts, ['A']);
  assert.equal(v.rounds[0].costVnd, 900);
  assert.equal(v.checks.find((c) => c.code === 'V4')?.status, 'fail');
  assert.equal(v.checks.find((c) => c.code === 'V4')?.detail, V4_MSG);
});

test('toVerify never crashes on values from a newer server', () => {
  const v = toVerify({
    ...API_VERIFY,
    mode: 'video',
    final_status: 'mystery',
    checks: [{ code: 'V1', label: '', status: 'odd', detail: '' }],
    rounds: [],
  });
  assert.equal(v.mode, 'music');
  assert.equal(v.finalStatus, 'interrupted');
  assert.equal(v.checks[0].status, 'warn'); // visible, not silently a pass
});

test('roundStatus follows one check across the rounds', () => {
  const v: VerifyReport = toVerify(API_VERIFY);
  assert.deepEqual(roundStatus(v, 'V4'), ['fail', 'fail', 'fail']);
  assert.deepEqual(roundStatus(v, 'V1'), ['pass', 'pass', 'pass']);
  assert.deepEqual(roundStatus(v, 'V9'), [null, null, null]); // never listed in these rounds
});

test('verifyHeadline says why the draft failed, in words', () => {
  const failed = verifyHeadline(toVerify(API_VERIFY));
  assert.equal(failed.tone, 'bad');
  assert.match(failed.text, /Trượt cả 3 vòng/);
  assert.match(failed.text, /V4/);
  assert.doesNotMatch(failed.text, /V6/); // a warning is not why it failed
  assert.match(failed.cost, /2\.700đ/); // 3 × 900đ

  const passed = verifyHeadline(
    toVerify({ ...API_VERIFY, final_status: 'passed', rounds_used: 1, rounds: [API_VERIFY.rounds[0]], checks: checks('pass') }),
  );
  assert.equal(passed.tone, 'ok');
  assert.match(passed.text, /Đạt ngay vòng 1/);

  const later = verifyHeadline(toVerify({ ...API_VERIFY, final_status: 'passed', checks: checks('pass') }));
  assert.match(later.text, /Đạt sau 3\/3 vòng/);

  assert.match(verifyHeadline(toVerify({ ...API_VERIFY, final_status: 'cost_cap' })).text, /trần chi phí/);
  assert.match(verifyHeadline(toVerify({ ...API_VERIFY, final_status: 'api_error' })).text, /gọi Claude/);
  assert.equal(verifyHeadline(toVerify({ ...API_VERIFY, final_status: 'interrupted' })).tone, 'warn');
});

test('the speech-mode headline names S-checks', () => {
  const v = toVerify({
    ...API_VERIFY,
    mode: 'speech',
    checks: [{ code: 'S3', label: 'Không dùng lặp câu và B-roll', status: 'fail', detail: 'x' }],
  });
  assert.match(verifyHeadline(v).text, /S3/);
});

// ───────────── the QA panel placeholder

test('the V-group placeholder of a QA report is dropped once the verify data exists', () => {
  const qa = {
    passed: true, rounds: 1, fixes: [], loudness_two_pass: false, lufs: null, true_peak_db: null, checks: [],
  };
  assert.ok(toQaReport(qa).groups.some((g) => g.title.includes('V1–V9')));
  assert.ok(!toQaReport(qa, true).groups.some((g) => g.title.includes('V1–V9')));
});

test('a pipeline card carries the verify report of its draft job', () => {
  const c = card({ status: 'failed', frontend_status: 'error', verify: API_VERIFY });
  assert.equal(c.verify?.finalStatus, 'failed');
  assert.equal(card({}).verify, null);
});
