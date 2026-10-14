/** Review of a delivered final video (T-F): the data the queue badge needs. Pure functions. */
import type { ApiJob, ApiReview } from '../types/api';
import type { ReviewInfo } from '../types/rules';

export function toReview(r: ApiReview | null | undefined, jobId: number | string): ReviewInfo | null {
  if (!r) return null;
  return {
    jobId: String(jobId),
    status: r.status,
    reason: r.reason,
    decidedBy: r.decided_by,
    decidedAt: r.decided_at,
    overdue: r.overdue,
    failedChecks: r.failed_checks,
  };
}

export interface ReviewBadgeData {
  label: string;
  /** Tailwind classes, same family as the job status pills. */
  cls: string;
  /** Hover text: the reason when changes were asked for, who decided, whether it is overdue. */
  title: string;
}

export function reviewBadge(r: ReviewInfo): ReviewBadgeData {
  if (r.status === 'approved') {
    return {
      label: 'Đã duyệt',
      cls: 'bg-emerald-500/20 text-emerald-300',
      title: r.decidedBy ? `Đã duyệt bởi ${r.decidedBy}` : 'Đã duyệt',
    };
  }
  if (r.status === 'changes_requested') {
    const who = r.decidedBy ? ` (${r.decidedBy})` : '';
    return {
      label: 'Cần sửa',
      cls: 'bg-rose-500/20 text-rose-300',
      title: `Yêu cầu sửa${who}: ${r.reason ?? 'không có lý do'}`,
    };
  }
  const waiting = r.overdue ? 'Chờ duyệt quá hạn: đã nhắc một lần, vẫn chờ.' : 'Chờ người duyệt.';
  const bad = r.failedChecks.length > 0 ? ` Chưa đạt: ${r.failedChecks.join('; ')}` : '';
  return {
    label: r.overdue ? 'Chờ duyệt (quá hạn)' : 'Chờ duyệt',
    cls: 'bg-amber-500/20 text-amber-300',
    title: waiting + bad,
  };
}

/** The review of a draft's newest delivered FINAL render (a preview is never reviewed). */
export function latestReview(jobs: ApiJob[]): ReviewInfo | null {
  const mine = jobs
    .filter((j) => j.kind === 'render' && j.size === 'final' && j.review != null)
    .sort((a, b) => b.id - a.id)[0];
  return mine ? toReview(mine.review, mine.id) : null;
}

/** One decision a person can take on a card: the final video of one draft. */
export interface ReviewItem {
  label: string;
  review: ReviewInfo;
}

/**
 * Every reviewable final video of a card, one per draft. A pipeline lists its render jobs
 * (a re-render's newer final replaces the older one of the same draft); a lone card has one.
 */
export function cardReviews(job: {
  draft?: string;
  review?: ReviewInfo | null;
  jobs?: { kind: string; draft: string; review?: ReviewInfo | null }[];
}): ReviewItem[] {
  const chain = (job.jobs ?? []).filter((j) => j.kind === 'render' && j.review);
  if (chain.length > 0) {
    const newest = new Map<string, ReviewInfo>();
    for (const j of chain) {
      const seen = newest.get(j.draft);
      if (!seen || Number(j.review!.jobId) > Number(seen.jobId)) newest.set(j.draft, j.review!);
    }
    return [...newest.entries()]
      .sort(([a], [b]) => (a < b ? -1 : 1))
      .map(([draft, review]) => ({ label: `Bản cuối ${draft}`.trim(), review }));
  }
  if (!job.review) return [];
  const draft = job.draft && job.draft !== 'all' ? ` ${job.draft}` : '';
  return [{ label: `Bản cuối${draft}`, review: job.review }];
}

/** Why a decision cannot be sent yet (Vietnamese), or null. Same rules as the server. */
export function reviewProblem(decision: 'approve' | 'reject', reviewer: string, reason: string): string | null {
  if (!reviewer.trim()) return 'Ghi tên người duyệt.';
  if (decision === 'reject' && !reason.trim()) return 'Yêu cầu sửa cần ghi lý do (sửa gì, ở giây nào).';
  return null;
}
