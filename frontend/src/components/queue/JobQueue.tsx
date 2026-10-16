import { Ban, Clock3, Coins, Inbox, RotateCw } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { startPolling } from '../../lib/poll';
import { api } from '../../services/api';
import type { JobStatus, PipelineJobInfo, RenderJob } from '../../types/rules';
import { ApprovalDialog } from './ApprovalDialog';
import { ConsoleLog } from './ConsoleLog';
import { FootageWarning } from './FootageWarning';
import { PipelineStepper } from './PipelineStepper';
import { PlanModal } from './PlanModal';
import { platformShort } from '../../lib/platforms';
import { cardReviews } from '../../lib/review';
import { ReviewBadge } from './ReviewBadge';
import { ReviewPanel } from './ReviewPanel';
import { VerifyPanel } from './VerifyPanel';
import { VideoPlayerWithQA } from './VideoPlayerWithQA';

const STATUS_LABEL: Record<JobStatus, { label: string; cls: string }> = {
  queued: { label: 'Chờ xử lý', cls: 'bg-slate-700 text-slate-200' },
  running: { label: 'Đang chạy', cls: 'bg-amber-500/20 text-amber-300' },
  awaiting_approval: { label: 'Chờ duyệt chi phí', cls: 'bg-amber-500 text-slate-950' },
  qa_review: { label: 'Đang soát', cls: 'bg-amber-500/20 text-amber-300' },
  done: { label: 'Hoàn tất', cls: 'bg-emerald-500/20 text-emerald-300' },
  error: { label: 'Lỗi', cls: 'bg-rose-500/20 text-rose-300' },
};

const KIND_LABEL: Record<string, string> = { analyze: 'Phân tích', draft: 'Kịch bản', render: 'Dựng' };
const LIVE: JobStatus[] = ['queued', 'running', 'awaiting_approval'];

export function JobQueue({
  selectedJobId,
  onSelectJob,
}: {
  selectedJobId: string | null;
  onSelectJob: (id: string) => void;
}) {
  const [jobs, setJobs] = useState<RenderJob[]>([]);
  const [activeJob, setActiveJob] = useState<RenderJob | null>(null);
  const [showPlan, setShowPlan] = useState(false);
  const [showApproval, setShowApproval] = useState(false);
  // Tied to the card it came from: switching cards must not show another card's failure.
  const [actionError, setActionError] = useState<{ jobId: string | null; message: string } | null>(null);
  // Bumped after an approval: restarting the subscription drops a poll that was already in
  // flight before it, whose stale "awaiting approval" answer would otherwise win.
  const [subscription, setSubscription] = useState(0);

  // Poll the job list (neither the mock nor the server pushes list changes).
  useEffect(
    () =>
      startPolling(async (signal) => {
        try {
          const list = await api.listJobs();
          if (!signal.aborted) setJobs(list);
        } catch {
          // Server offline: keep the last list, the header badge already says so.
        }
      }, 2000),
    [],
  );

  useEffect(() => {
    if (!selectedJobId) return;
    return api.subscribeJob(selectedJobId, setActiveJob);
  }, [selectedJobId, subscription]);

  // Derived instead of reset via a second effect: avoids a stale job flashing while the
  // new subscription's first update is still in flight after switching selection.
  const displayedJob = activeJob && activeJob.id === selectedJobId ? activeJob : null;
  const live =
    displayedJob !== null &&
    (LIVE.includes(displayedJob.status) || (displayedJob.jobs ?? []).some((j) => LIVE.includes(j.status)));

  const shownError = actionError && actionError.jobId === selectedJobId ? actionError.message : null;

  const closePlan = useCallback(() => setShowPlan(false), []);
  const closeApproval = useCallback(() => setShowApproval(false), []);

  async function run(action: () => Promise<unknown>) {
    const jobId = selectedJobId;
    setActionError(null);
    try {
      await action();
    } catch (e) {
      setActionError({ jobId, message: e instanceof Error ? e.message : String(e) });
    }
  }

  return (
    <div className="mx-auto grid max-w-7xl gap-6 px-4 py-6 sm:px-6 lg:grid-cols-[280px_1fr]">
      <aside className="space-y-2">
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
          Video đang làm ({jobs.length})
        </h2>
        {jobs.length === 0 && (
          <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-slate-800 px-4 py-8 text-center text-xs text-slate-400">
            <Inbox className="h-6 w-6" />
            Chưa có video nào. Bấm "Tạo video mới" ở trên để bắt đầu.
          </div>
        )}
        {jobs.map((j) => (
          <button
            key={j.id}
            type="button"
            onClick={() => onSelectJob(j.id)}
            className={`w-full rounded-lg border px-3 py-2.5 text-left transition ${
              j.id === selectedJobId
                ? 'border-amber-500 bg-amber-500/10'
                : 'border-slate-800 bg-slate-900/40 hover:border-slate-600'
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-xs font-medium text-slate-200">{j.title}</span>
            </div>
            <div className="mt-1.5 flex items-center justify-between">
              <span className="flex items-center gap-1.5">
                <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_LABEL[j.status].cls}`}>
                  {STATUS_LABEL[j.status].label}
                </span>
                <ReviewBadge review={j.review} />
              </span>
              <span className="flex items-center gap-1 text-xs text-slate-400">
                <Clock3 className="h-3 w-3" />
                {new Date(j.createdAt).toLocaleTimeString('vi-VN')}
              </span>
            </div>
          </button>
        ))}
      </aside>

      <div className="space-y-4">
        {!displayedJob ? (
          <div className="flex h-64 items-center justify-center rounded-xl border border-dashed border-slate-800 text-sm text-slate-400">
            Chọn một video ở danh sách để xem tiến trình.
          </div>
        ) : (
          <>
            {shownError && (
              <p
                aria-live="polite"
                className="rounded-lg border border-rose-800/50 bg-rose-950/30 px-3 py-2 text-xs text-rose-300"
              >
                {shownError}
              </p>
            )}

            {/* Keyed on the approval itself, not the chain status: after a draft retry the chain can still
                read "error" (a cancelled render) while the draft waits for a fresh approval. */}
            {displayedJob.approval && (
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-600/60 bg-amber-950/20 px-4 py-3">
                <p className="flex items-center gap-2 text-xs text-amber-200">
                  <Coins className="h-4 w-4 shrink-0 text-amber-400" />
                  Kịch bản đã sẵn sàng gọi Claude — cần bạn duyệt chi phí trước khi chạy tiếp. Chưa có
                  đồng nào được chi.
                </p>
                <button
                  type="button"
                  onClick={() => setShowApproval(true)}
                  className="rounded-lg bg-amber-500 px-3 py-1.5 text-xs font-semibold text-slate-950 hover:brightness-110"
                >
                  Xem & duyệt chi phí
                </button>
              </div>
            )}

            {displayedJob.approval?.footage && <FootageWarning footage={displayedJob.approval.footage} compact />}

            <PipelineStepper steps={displayedJob.steps} etaSec={displayedJob.etaSec} />

            {displayedJob.jobs && displayedJob.jobs.length > 0 && (
              <PipelineJobs
                jobs={displayedJob.jobs}
                onRetry={(id) => void run(() => api.retryJob(id))}
              />
            )}

            {displayedJob.verify && <VerifyPanel report={displayedJob.verify} />}

            {live && (
              <button
                type="button"
                onClick={() => void run(() => api.cancelJob(displayedJob.id))}
                className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-300 hover:border-rose-500 hover:text-rose-300"
              >
                <Ban className="h-3.5 w-3.5" />
                Hủy video này
              </button>
            )}

            <VideoPlayerWithQA
              // Draft/tab choices belong to one card: a new card starts from its own defaults.
              key={displayedJob.id}
              job={displayedJob}
              rerenderBlocked={live}
              // plan.json is only shown as a real sample in mock mode; the API has no route for it.
              onShowPlan={api.mode === 'mock' ? () => setShowPlan(true) : undefined}
              onRerender={() =>
                void run(async () => {
                  const next = await api.rerender(displayedJob);
                  onSelectJob(next.id);
                })
              }
            />
            {cardReviews(displayedJob).length > 0 && (
              <ReviewPanel
                // A decision belongs to one card: switching cards drops typed notes and errors.
                key={`review|${displayedJob.id}`}
                items={cardReviews(displayedJob)}
                onDecide={async (jobId, decision, reviewer, reason) => {
                  await api.decideReview(jobId, decision, reviewer, reason);
                  setSubscription((n) => n + 1);
                }}
              />
            )}
            <ConsoleLog lines={displayedJob.logLines} />
          </>
        )}
      </div>

      {showPlan && <PlanModal onClose={closePlan} />}
      {showApproval && displayedJob?.approval && (
        <ApprovalDialog
          // A new estimate is a new decision: remounting drops any earlier tick.
          key={`${displayedJob.approval.jobId}|${displayedJob.approval.worstCallVnd}|${displayedJob.approval.capVnd}`}
          job={displayedJob}
          onClose={closeApproval}
          onApproved={(next) => {
            setActiveJob(next);
            setSubscription((n) => n + 1);
            setShowApproval(false);
          }}
        />
      )}
    </div>
  );
}

function PipelineJobs({ jobs, onRetry }: { jobs: PipelineJobInfo[]; onRetry: (jobId: string) => void }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-3">
      <h3 className="mb-2 text-xs font-semibold text-slate-300">Các công việc trong chuỗi</h3>
      <ul className="divide-y divide-slate-800/70">
        {jobs.map((j) => (
          <li key={j.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5 text-xs">
            <span className="w-32 font-medium text-slate-200">
              {KIND_LABEL[j.kind] ?? j.kind}
              {j.draft ? ` ${j.draft}` : ''}
              {j.platform ? ` · ${platformShort(j.platform)}` : ''}
            </span>
            <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_LABEL[j.status].cls}`}>
              {STATUS_LABEL[j.status].label}
            </span>
            <ReviewBadge review={j.review} />
            {j.spentVnd !== null && (
              <span className="text-slate-400">đã chi {j.spentVnd.toLocaleString('vi-VN')}đ</span>
            )}
            {j.errorMsg && <span className="min-w-0 flex-1 truncate text-rose-400" title={j.errorMsg}>{j.errorMsg}</span>}
            {j.retryable && (
              <button
                type="button"
                onClick={() => onRetry(j.id)}
                title={j.kind === 'draft' ? 'Thử lại kịch bản cần duyệt lại chi phí' : undefined}
                className="ml-auto flex items-center gap-1 rounded border border-slate-700 px-2 py-0.5 text-xs text-slate-300 hover:border-amber-500 hover:text-amber-300"
              >
                <RotateCw className="h-3 w-3" />
                Thử lại{j.kind === 'draft' ? ' (duyệt lại)' : ''}
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
