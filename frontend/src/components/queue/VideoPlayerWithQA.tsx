import { CheckCircle2, Download, FileJson, RefreshCw, XCircle } from 'lucide-react';
import { useState } from 'react';
import type { QaCheck, QaReport, RenderJob } from '../../types/rules';

function CheckRow({ c }: { c: QaCheck }) {
  return (
    <li className="flex items-start gap-2 py-1.5 text-xs">
      {c.passed ? (
        <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-400" />
      ) : (
        <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-400" />
      )}
      <span className="text-slate-300">
        <span className="font-mono-studio text-slate-500">{c.code}</span> {c.label}
        <span className="block text-[11px] text-slate-500">{c.detail}</span>
      </span>
    </li>
  );
}

function QaGroup({ title, checks }: { title: string; checks: QaCheck[] }) {
  const allPass = checks.every((c) => c.passed);
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950/30 p-3">
      <div className="mb-1 flex items-center justify-between">
        <h4 className="text-xs font-semibold text-slate-300">{title}</h4>
        <span
          className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
            allPass ? 'bg-emerald-500/15 text-emerald-300' : 'bg-rose-500/15 text-rose-300'
          }`}
        >
          {allPass ? 'ĐẠT' : 'LỖI'}
        </span>
      </div>
      <ul className="divide-y divide-slate-800/60">
        {checks.map((c, i) => (
          <CheckRow key={i} c={c} />
        ))}
      </ul>
    </div>
  );
}

function QaReportPanel({ qa }: { qa: QaReport }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-200">Soát chất lượng (verify.py, 3 nhóm)</h3>
        <span className="text-[11px] text-slate-500">
          vòng {qa.rounds_used}/{qa.max_rounds}
        </span>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <QaGroup title="Pass 1 · Đồng bộ nhịp & hình ảnh (V2–V4, T1)" checks={qa.pass1_sync} />
        <QaGroup title="Pass 2 · Từ khoá cấm / đối thủ (glossary.json, V7–V8)" checks={qa.pass2_glossary} />
        <QaGroup title="Pass 3 · Cân bằng âm thanh (qc.T3)" checks={qa.pass3_audio} />
      </div>
    </div>
  );
}

export function VideoPlayerWithQA({
  job,
  onRerender,
  onShowPlan,
}: {
  job: RenderJob;
  onRerender: () => void;
  onShowPlan: () => void;
}) {
  const [tab, setTab] = useState<'preview' | 'final'>('preview');
  const src = tab === 'final' ? job.finalUrl : job.previewUrl;
  const res = tab === 'final' ? '1080×1920' : '540×960';

  return (
    <div className="space-y-4 rounded-xl border border-slate-800 bg-slate-900/40 p-4">
      <div className="flex items-center justify-between">
        <div className="flex gap-1 rounded-lg border border-slate-800 bg-slate-950/40 p-0.5 text-xs">
          <button
            type="button"
            onClick={() => setTab('preview')}
            className={`rounded-md px-3 py-1 ${tab === 'preview' ? 'bg-amber-500 text-slate-950' : 'text-slate-400'}`}
          >
            preview.mp4 (540p)
          </button>
          <button
            type="button"
            onClick={() => setTab('final')}
            disabled={!job.finalUrl}
            className={`rounded-md px-3 py-1 disabled:opacity-40 ${
              tab === 'final' ? 'bg-amber-500 text-slate-950' : 'text-slate-400'
            }`}
          >
            final.mp4 (1080×1920)
          </button>
        </div>
        <span className="font-mono-studio text-[11px] text-slate-500">{res}</span>
      </div>

      <div className="flex justify-center rounded-lg bg-black">
        {src ? (
          <video key={src} src={src} controls className="max-h-[520px] rounded-lg" />
        ) : (
          <div className="flex h-64 w-full items-center justify-center text-xs text-slate-600">
            Chưa có file — chờ bước "Dựng bản xem trước" hoàn tất.
          </div>
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        <ActionButton icon={Download} label="Tải video final" disabled={!job.finalUrl} href={job.finalUrl ?? undefined} />
        <ActionButton icon={FileJson} label="Xem plan.json" onClick={onShowPlan} />
        <ActionButton icon={RefreshCw} label="Render lại" onClick={onRerender} />
      </div>

      {job.qa && <QaReportPanel qa={job.qa} />}
    </div>
  );
}

function ActionButton({
  icon: Icon,
  label,
  disabled,
  onClick,
  href,
}: {
  icon: typeof Download;
  label: string;
  disabled?: boolean;
  onClick?: () => void;
  href?: string;
}) {
  const cls = `flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition ${
    disabled
      ? 'cursor-not-allowed border-slate-800 text-slate-600'
      : 'border-slate-700 text-slate-300 hover:border-amber-500 hover:text-amber-300'
  }`;
  if (href && !disabled) {
    return (
      <a href={href} download className={cls}>
        <Icon className="h-3.5 w-3.5" />
        {label}
      </a>
    );
  }
  return (
    <button type="button" disabled={disabled} onClick={onClick} className={cls}>
      <Icon className="h-3.5 w-3.5" />
      {label}
    </button>
  );
}
