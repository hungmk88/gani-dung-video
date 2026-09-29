import { Clock3, Inbox } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../../services/api';
import type { JobStatus, RenderJob } from '../../types/rules';
import { ConsoleLog } from './ConsoleLog';
import { PipelineStepper } from './PipelineStepper';
import { PlanModal } from './PlanModal';
import { VideoPlayerWithQA } from './VideoPlayerWithQA';

const STATUS_LABEL: Record<JobStatus, { label: string; cls: string }> = {
  queued: { label: 'Chờ xử lý', cls: 'bg-slate-700 text-slate-200' },
  running: { label: 'Đang chạy', cls: 'bg-amber-500/20 text-amber-300' },
  qa_review: { label: 'Đang soát', cls: 'bg-amber-500/20 text-amber-300' },
  done: { label: 'Hoàn tất', cls: 'bg-emerald-500/20 text-emerald-300' },
  error: { label: 'Lỗi', cls: 'bg-rose-500/20 text-rose-300' },
};

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

  // Poll the job list (mock daemon has no list-change push — real WS in GĐ6 would).
  useEffect(() => {
    let cancelled = false;
    async function tick() {
      const list = await api.listJobs();
      if (!cancelled) setJobs(list);
    }
    void tick();
    const t = setInterval(tick, 2000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

  useEffect(() => {
    if (!selectedJobId) return;
    return api.subscribeJob(selectedJobId, setActiveJob);
  }, [selectedJobId]);

  // Derived instead of reset via a second effect: avoids a stale job flashing while the
  // new subscription's first update is still in flight after switching selection.
  const displayedJob = activeJob && activeJob.id === selectedJobId ? activeJob : null;

  return (
    <div className="mx-auto grid max-w-7xl gap-6 px-4 py-6 sm:px-6 lg:grid-cols-[280px_1fr]">
      <aside className="space-y-2">
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Hàng đợi ({jobs.length})
        </h2>
        {jobs.length === 0 && (
          <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-slate-800 px-4 py-8 text-center text-xs text-slate-600">
            <Inbox className="h-6 w-6" />
            Chưa có tác vụ nào. Tạo video mới ở tab Studio Wizard.
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
              <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${STATUS_LABEL[j.status].cls}`}>
                {STATUS_LABEL[j.status].label}
              </span>
              <span className="flex items-center gap-1 text-[10px] text-slate-500">
                <Clock3 className="h-3 w-3" />
                {new Date(j.createdAt).toLocaleTimeString('vi-VN')}
              </span>
            </div>
          </button>
        ))}
      </aside>

      <div className="space-y-4">
        {!displayedJob ? (
          <div className="flex h-64 items-center justify-center rounded-xl border border-dashed border-slate-800 text-sm text-slate-600">
            Chọn một tác vụ ở danh sách bên trái để xem tiến trình.
          </div>
        ) : (
          <>
            <PipelineStepper steps={displayedJob.steps} etaSec={displayedJob.etaSec} />
            <VideoPlayerWithQA
              job={displayedJob}
              onShowPlan={() => setShowPlan(true)}
              onRerender={() => {
                /* GĐ6 thật: gọi lại api.submitJob với cùng options + footage đã lưu. */
              }}
            />
            <ConsoleLog lines={displayedJob.logLines} />
          </>
        )}
      </div>

      {showPlan && <PlanModal onClose={() => setShowPlan(false)} />}
    </div>
  );
}
