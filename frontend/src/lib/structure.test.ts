// Run with `npm test`. Phase 7g: the chapter timeline of a 3-minute video and its API mapping.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { ApiHealth, ApiJob, ApiPipeline, ApiQa, ApiStructure } from '../types/api.ts';
import { toHealthInfo, toPipelineCard, toQaReport, toStructure } from '../services/mappers.ts';
import { RULES } from '../data/rules.ts';
import {
  chapterName,
  clock,
  draftsFor,
  seekTarget,
  timelineSegments,
  titleLane,
} from './structure.ts';

const API_STRUCTURE: ApiStructure = {
  duration_sec: 180,
  items: [
    { kind: 'opening', id: 'hook', start_sec: 0, end_sec: 4.3, title: null },
    { kind: 'chapter', id: 've_sinh', start_sec: 4.3, end_sec: 49.3, title: 'Khởi đầu sạch sẽ' },
    { kind: 'chapter', id: 'quy_trinh', start_sec: 49.3, end_sec: 124.3, title: 'Từng mẻ giò thủ công' },
    { kind: 'chapter', id: 'bang_chung', start_sec: 124.3, end_sec: 166.8, title: null },
    { kind: 'closing', id: 'cta', start_sec: 166.8, end_sec: 180, title: null },
  ],
};

// ───────────── mapping

test('toStructure keeps the timeline and the titles', () => {
  const s = toStructure(API_STRUCTURE);
  assert.equal(s.durationSec, 180);
  assert.deepEqual(
    s.items.map((i) => [i.kind, i.id]),
    [
      ['opening', 'hook'],
      ['chapter', 've_sinh'],
      ['chapter', 'quy_trinh'],
      ['chapter', 'bang_chung'],
      ['closing', 'cta'],
    ],
  );
  assert.equal(s.items[1].title, 'Khởi đầu sạch sẽ');
  assert.equal(s.items[3].title, null);
  assert.equal(s.items[2].startSec, 49.3);
});

test('toStructure never crashes on a kind a newer server adds', () => {
  const s = toStructure({
    duration_sec: 10,
    items: [{ kind: 'interlude', id: 'x', start_sec: 0, end_sec: 10, title: null }],
  });
  assert.equal(s.items[0].kind, 'chapter'); // shown, not dropped
});

function health(over: Partial<ApiHealth>): ApiHealth {
  return {
    ok: true, rules_version: '2.0', daemon_online: true, daemon_pid: 1, daemon_stopping: null,
    jobs: {}, allowed_roots_configured: true, ai_key_configured: true,
    budget: { limit_vnd: null, spent_24h_vnd: 0, reserved_vnd: 0 }, ...over,
  };
}

test('health says whether 3-minute videos are open; an older server says nothing', () => {
  assert.equal(toHealthInfo(health({ features: { long_video: false } })).longVideo, false);
  assert.equal(toHealthInfo(health({ features: { long_video: true } })).longVideo, true);
  assert.equal(toHealthInfo(health({})).longVideo, null);
});

function job(over: Partial<ApiJob>): ApiJob {
  return {
    id: 1, pipeline_id: 1, depends_on: null, project_id: '0/x', project_name: 'x', title: 't',
    kind: 'render', draft: 'A', size: 'preview', status: 'completed', result: 'passed',
    frontend_status: 'done', attempts: 1, max_attempts: 2, progress_pct: 100, stage: null,
    eta_sec: null, created_at: '2026-10-01T01:00:00Z', started_at: null, finished_at: null,
    error_msg: null, no_asr: false, options: {}, video_url: '/media/1/video', cover_url: null,
    srt_url: null, steps: [], qa: null, ...over,
  };
}

const QA: ApiQa = {
  passed: true, rounds: 2,
  fixes: ['T3: dựng lại vòng 2 với trần đỉnh -3 dBTP (bộ mã hóa AAC làm đỉnh cao hơn mức đã giới hạn)'],
  loudness_two_pass: false, lufs: -14.3, true_peak_db: -1.6, checks: [],
};

test('the QA report keeps the automatic fixes, e.g. the lowered peak ceiling of round 2', () => {
  const q = toQaReport(QA);
  assert.equal(q.rounds_used, 2);
  assert.deepEqual(q.fixes, QA.fixes);
  assert.deepEqual(toQaReport({ ...QA, fixes: [] }).fixes, []);
});

test('a pipeline card gives each draft its own structure (titles differ per draft)', () => {
  const a = toStructureWithTitle('Bản A');
  const b = toStructureWithTitle('Bản B');
  const p: ApiPipeline = {
    id: 1, project_id: '0/x', project_name: 'x', status: 'done', needs_approval: false,
    jobs: [
      job({ id: 3, draft: 'A', structure: a, qa: QA }),
      job({ id: 4, draft: 'B', structure: b, qa: QA }),
    ],
  };
  const card = toPipelineCard(p);
  assert.equal(card.outputs?.[0].structure?.items[1].title, 'Bản A');
  assert.equal(card.outputs?.[1].structure?.items[1].title, 'Bản B');
  assert.equal(card.structure?.items[1].title, 'Bản A'); // the card shows the first draft
});

function toStructureWithTitle(title: string): ApiStructure {
  return { ...API_STRUCTURE, items: API_STRUCTURE.items.map((i) => (i.id === 've_sinh' ? { ...i, title } : i)) };
}

test('a short video has no structure', () => {
  const card = toPipelineCard({
    id: 1, project_id: '0/x', project_name: 'x', status: 'done', needs_approval: false,
    jobs: [job({ draft: 'A' })],
  });
  assert.equal(card.structure, undefined);
  assert.equal(card.outputs?.[0].structure, undefined);
});

test('the draft job at the approval gate keeps the planned structure on the card', () => {
  const card = toPipelineCard({
    id: 1, project_id: '0/x', project_name: 'x', status: 'awaiting_approval', needs_approval: true,
    jobs: [job({ id: 2, kind: 'draft', draft: '', status: 'awaiting_approval', frontend_status: 'awaiting_approval', video_url: null, structure: API_STRUCTURE })],
  });
  assert.equal(card.structure?.items.length, 5);
});

// ───────────── timeline

test('timelineSegments are proportional and add up to 100 %', () => {
  const seg = timelineSegments(toStructure(API_STRUCTURE));
  assert.equal(seg.length, 5);
  assert.ok(Math.abs(seg.reduce((n, s) => n + s.pct, 0) - 100) < 1e-6);
  assert.ok(Math.abs(seg[2].pct - (75 / 180) * 100) < 1e-6);
  assert.equal(seg[2].label, 'Quy trình');
  assert.equal(seg[2].title, 'Từng mẻ giò thủ công');
});

test('a short stretch keeps its place but hides its label', () => {
  const seg = timelineSegments(toStructure(API_STRUCTURE));
  assert.equal(seg[0].showLabel, false); // the 4 s hook: 2.4 % of the bar
  assert.equal(seg[2].showLabel, true);
});

test('chapterName speaks Vietnamese and falls back to the id', () => {
  assert.equal(chapterName('ve_sinh'), 'Vệ sinh');
  assert.equal(chapterName('con_nguoi_2'), 'Con người (2)');
  assert.equal(chapterName('hook'), 'Mở đầu');
  assert.equal(chapterName('cta'), 'Kêu gọi');
  assert.equal(chapterName('mot_khoi_moi'), 'mot khoi moi');
  assert.equal(chapterName('quy_trinh#2'), 'Quy trình (đoạn 2)'); // a sub-block of a chapter
});

test('every block of every purpose in rules.json has a Vietnamese name', () => {
  for (const purpose of Object.values(RULES.purposes)) {
    for (const [role] of purpose.blocks) {
      assert.notEqual(chapterName(role), role.replace(/_/g, ' '), `${purpose.id}: ${role}`);
    }
  }
});

test('the title lane lists only the chapters that have a title, at their start', () => {
  const lane = titleLane(toStructure(API_STRUCTURE));
  assert.deepEqual(
    lane.map((l) => [l.startSec, l.title]),
    [
      [4.3, 'Khởi đầu sạch sẽ'],
      [49.3, 'Từng mẻ giò thủ công'],
    ],
  );
});

test('clock formats seconds as m:ss', () => {
  assert.equal(clock(0), '0:00');
  assert.equal(clock(4.3), '0:04');
  assert.equal(clock(49.3), '0:49');
  assert.equal(clock(124.3), '2:04');
  assert.equal(clock(180), '3:00');
});

test('seekTarget lands just after the start, inside the video', () => {
  assert.equal(seekTarget(49.3, 182), 49.3);
  assert.equal(seekTarget(-3, 182), 0);
  assert.equal(seekTarget(500, 182), 182);
  assert.equal(seekTarget(10, Number.NaN), 10);
});

// ───────────── the wizard

test('3-minute videos default to 2 drafts (decision Q3); other lengths are left alone', () => {
  assert.equal(draftsFor(180, 3), 2);
  assert.equal(draftsFor(180, 1), 1);
  assert.equal(draftsFor(180, 2), 2);
  assert.equal(draftsFor(60, 3), 3);
  assert.equal(draftsFor(15, 3), 3);
});
