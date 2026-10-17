import assert from 'node:assert/strict';
import test from 'node:test';
import type { ApiJob } from '../types/api.ts';
import { cardReviews, latestReview, reviewBadge, reviewProblem, toReview } from './review.ts';

const api = (over: Record<string, unknown> = {}) => ({
  status: 'pending' as const,
  requested_at: '2026-10-15T08:00:00Z',
  decided_at: null,
  decided_by: null,
  reason: null,
  reminded_at: null,
  overdue: false,
  failed_checks: [],
  ...over,
});

function job(id: number, over: Partial<ApiJob> = {}): ApiJob {
  return {
    id, project_id: '0/x', project_name: 'x', title: 't', kind: 'render', draft: 'A', size: 'final',
    status: 'completed', result: 'passed', frontend_status: 'done', attempts: 1, max_attempts: 1,
    progress_pct: 100, stage: null, eta_sec: null, created_at: '', started_at: null, finished_at: null,
    error_msg: null, no_asr: false, options: {}, video_url: null, cover_url: null, srt_url: null,
    steps: [], qa: null, ...over,
  };
}

test('toReview maps the three states and the null cases', () => {
  assert.equal(toReview(null, 1), null);
  assert.equal(toReview(undefined, 1), null);
  const p = toReview(api(), 7);
  assert.deepEqual(p, {
    jobId: '7', status: 'pending', reason: null, decidedBy: null, decidedAt: null, overdue: false, failedChecks: [],
  });
  const c = toReview(api({ status: 'changes_requested', reason: 'chữ cuối bị che', decided_by: 'NVH' }), 7);
  assert.equal(c?.status, 'changes_requested');
  assert.equal(c?.reason, 'chữ cuối bị che');
  assert.equal(c?.decidedBy, 'NVH');
});

test('the badge names the state and puts the reason in the hover text', () => {
  const pending = reviewBadge(toReview(api(), 7)!);
  assert.equal(pending.label, 'Chờ duyệt');
  assert.match(pending.cls, /amber/);
  const approved = reviewBadge(toReview(api({ status: 'approved', decided_by: 'Hùng' }), 7)!);
  assert.equal(approved.label, 'Đã duyệt');
  assert.match(approved.cls, /emerald/);
  assert.match(approved.title, /Hùng/);
  const changes = reviewBadge(toReview(api({ status: 'changes_requested', reason: 'chữ cuối bị che' }), 7)!);
  assert.equal(changes.label, 'Cần sửa');
  assert.match(changes.cls, /rose/);
  assert.match(changes.title, /chữ cuối bị che/);
});

test('an overdue pending video says so', () => {
  const late = reviewBadge(toReview(api({ overdue: true }), 7)!);
  assert.match(late.label, /Chờ duyệt/);
  assert.match(late.title, /quá hạn/i);
});

test('latestReview takes the newest final render that has a review, and ignores the rest', () => {
  const jobs = [
    job(1, { review: api({ status: 'approved' }) }),
    job(3, { review: api({ status: 'changes_requested', reason: 'sửa' }) }),
    job(5, { size: 'preview', review: api() }), // a preview never counts
    job(7, { kind: 'draft', review: api() }),
  ];
  assert.equal(latestReview(jobs)?.status, 'changes_requested');
  assert.equal(latestReview(jobs)?.jobId, '3'); // the job approve/reject is sent to
  assert.equal(latestReview([job(2)]), null);
  assert.equal(latestReview([]), null);
});

test('cardReviews lists the newest final video of each draft of a pipeline, in letter order', () => {
  const r = (jobId: string, status: 'pending' | 'approved' = 'pending') => ({ ...toReview(api({ status }), jobId)! });
  const items = cardReviews({
    jobs: [
      { kind: 'render', draft: 'B', review: r('9') },
      { kind: 'render', draft: 'A', review: r('4', 'approved') },
      { kind: 'render', draft: 'A', review: r('11') }, // re-rendered: the newer one counts
      { kind: 'render', draft: 'C' }, // a preview: nothing to decide
      { kind: 'draft', draft: '', review: r('2') },
    ],
  });
  assert.deepEqual(items.map((i) => [i.label, i.review.jobId]), [['Bản cuối A', '11'], ['Bản cuối B', '9']]);
});

test('cardReviews of a lone card is its own review, and nothing without one', () => {
  const review = toReview(api(), 5)!;
  assert.deepEqual(cardReviews({ draft: 'B', review }), [{ label: 'Bản cuối B', review }]);
  assert.deepEqual(cardReviews({ draft: 'all', review }), [{ label: 'Bản cuối', review }]);
  assert.deepEqual(cardReviews({}), []);
});

test('reviewProblem asks for a name, and a reason only when asking for changes', () => {
  assert.match(reviewProblem('approve', ' ', '')!, /tên/);
  assert.equal(reviewProblem('approve', 'Hùng', ''), null);
  assert.match(reviewProblem('reject', 'Hùng', '  ')!, /lý do/);
  assert.equal(reviewProblem('reject', 'Hùng', 'chữ cuối bị che'), null);
});

test('cardReviews keeps the TikTok and the Reels file of one draft apart', () => {
  const r = (jobId: string) => ({ ...toReview(api(), jobId)! });
  const items = cardReviews({
    jobs: [
      { kind: 'render', draft: 'A', platform: 'facebook', review: r('6') },
      { kind: 'render', draft: 'A', platform: 'tiktok', review: r('5') },
    ],
  });
  assert.deepEqual(
    items.map((i) => [i.label, i.review.jobId]),
    [
      ['Bản cuối A · TikTok', '5'],
      ['Bản cuối A · Reels', '6'],
    ],
  );
});
