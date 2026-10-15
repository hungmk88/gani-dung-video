import { CheckCircle2, MessageSquareWarning, ShieldCheck } from 'lucide-react';
import { useState } from 'react';
import { reviewProblem, type ReviewItem } from '../../lib/review';
import { ReviewBadge } from './ReviewBadge';

const REVIEWER_KEY = 'gani.nguoiDuyet';

function savedReviewer(): string {
  try {
    return localStorage.getItem(REVIEWER_KEY) ?? '';
  } catch {
    return '';
  }
}

function saveReviewer(name: string): void {
  try {
    localStorage.setItem(REVIEWER_KEY, name);
  } catch {
    // Private window / blocked storage: the name is simply asked again next time.
  }
}

function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString('vi-VN') : '';
}

/**
 * Approve or ask for changes on each delivered final video of a card (T-F). Approving moves
 * the file to final/; asking for changes needs a reason. Either decision is final.
 */
export function ReviewPanel({
  items,
  onDecide,
}: {
  items: ReviewItem[];
  onDecide: (jobId: string, decision: 'approve' | 'reject', reviewer: string, reason: string) => Promise<void>;
}) {
  const [reviewer, setReviewer] = useState(savedReviewer);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<{ jobId: string; message: string } | null>(null);
  const anyPending = items.some((i) => i.review.status === 'pending');

  async function decide(jobId: string, decision: 'approve' | 'reject') {
    const reason = reasons[jobId] ?? '';
    const bad = reviewProblem(decision, reviewer, reason);
    if (bad) {
      setError({ jobId, message: bad });
      return;
    }
    const sure = decision === 'approve'
      ? 'Duyệt video này? Video được chuyển sang thư mục final/ và không đổi lại được.'
      : 'Gửi yêu cầu sửa? Video ở lại drafts/; muốn làm lại thì dựng bản mới.';
    if (!window.confirm(sure)) return;
    setBusy(jobId);
    setError(null);
    saveReviewer(reviewer.trim());
    try {
      await onDecide(jobId, decision, reviewer, reason);
    } catch (e) {
      setError({ jobId, message: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="space-y-3 rounded-xl border border-slate-800 bg-slate-900/40 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-200">
          <ShieldCheck className="h-4 w-4 text-amber-400" />
          Duyệt video cuối
        </h3>
        {anyPending && (
          <label className="flex items-center gap-2 text-xs text-slate-300">
            Người duyệt
            <input
              value={reviewer}
              onChange={(e) => setReviewer(e.target.value)}
              placeholder="Tên của bạn"
              className="w-40 rounded-md border border-slate-700 bg-slate-950/60 px-2 py-1 text-xs text-slate-100 focus:border-amber-500 focus:outline-none"
            />
          </label>
        )}
      </div>

      {items.map(({ label, review }) => (
        <div key={review.jobId} className="space-y-2 rounded-lg border border-slate-800 bg-slate-950/30 p-3">
          <div className="flex items-center gap-2 text-xs">
            <span className="font-medium text-slate-200">{label}</span>
            <ReviewBadge review={review} />
          </div>

          {review.status === 'approved' && (
            <p className="flex items-center gap-1.5 text-xs text-emerald-300">
              <CheckCircle2 className="h-3.5 w-3.5" />
              Đã duyệt{review.decidedBy ? ` bởi ${review.decidedBy}` : ''} {when(review.decidedAt)}
              {review.reason ? ` — ${review.reason}` : ''}. Video nằm trong thư mục final/.
            </p>
          )}
          {review.status === 'changes_requested' && (
            <p className="flex items-start gap-1.5 text-xs text-rose-300">
              <MessageSquareWarning className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              Yêu cầu sửa{review.decidedBy ? ` (${review.decidedBy})` : ''}: {review.reason}. Bấm "Dựng lại"
              để làm bản mới.
            </p>
          )}

          {review.status === 'pending' && (
            <>
              {review.failedChecks.length > 0 && (
                <ul className="list-inside list-disc text-xs text-rose-300">
                  <li className="list-none text-slate-300">Chưa duyệt được, còn mục kiểm tra hỏng:</li>
                  {review.failedChecks.map((c) => (
                    <li key={c}>{c}</li>
                  ))}
                </ul>
              )}
              <textarea
                aria-label={`Lý do hoặc ghi chú cho ${label}`}
                value={reasons[review.jobId] ?? ''}
                onChange={(e) => setReasons((r) => ({ ...r, [review.jobId]: e.target.value }))}
                rows={2}
                placeholder="Ghi chú (bắt buộc khi yêu cầu sửa: sửa gì, ở giây nào)"
                className="w-full rounded-md border border-slate-700 bg-slate-950/60 px-2 py-1.5 text-xs text-slate-100 focus:border-amber-500 focus:outline-none"
              />
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={busy !== null || review.failedChecks.length > 0}
                  title={review.failedChecks.length > 0 ? 'Còn mục kiểm tra hỏng: chỉ yêu cầu sửa được.' : undefined}
                  onClick={() => void decide(review.jobId, 'approve')}
                  className="rounded-lg bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-slate-950 hover:brightness-110 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
                >
                  Duyệt video
                </button>
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() => void decide(review.jobId, 'reject')}
                  className="rounded-lg border border-rose-600 px-3 py-1.5 text-xs font-semibold text-rose-300 hover:bg-rose-950/40 disabled:cursor-not-allowed disabled:border-slate-700 disabled:text-slate-400"
                >
                  Yêu cầu sửa
                </button>
              </div>
            </>
          )}

          {error?.jobId === review.jobId && (
            <p aria-live="polite" className="text-xs text-rose-300">
              {error.message}
            </p>
          )}
        </div>
      ))}
    </section>
  );
}
