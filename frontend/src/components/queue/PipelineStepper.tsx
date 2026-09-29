import { CheckCircle2, CircleDot, Loader2, XCircle } from 'lucide-react';
import type { PipelineStep } from '../../types/rules';

function StatusIcon({ status }: { status: PipelineStep['status'] }) {
  switch (status) {
    case 'done':
      return <CheckCircle2 className="h-5 w-5 text-emerald-400" />;
    case 'running':
      return <Loader2 className="h-5 w-5 animate-spin text-amber-400" />;
    case 'error':
      return <XCircle className="h-5 w-5 text-rose-400" />;
    default:
      return <CircleDot className="h-5 w-5 text-slate-600" />;
  }
}

export function PipelineStepper({ steps, etaSec }: { steps: PipelineStep[]; etaSec: number | null }) {
  const doneCount = steps.filter((s) => s.status === 'done').length;

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-200">
          Pipeline 8 bước ({doneCount}/{steps.length})
        </h3>
        {etaSec !== null && etaSec > 0 && (
          <span className="font-mono-studio text-xs text-amber-300">ETA ~{etaSec}s</span>
        )}
      </div>

      <ol className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {steps.map((s, i) => (
          <li
            key={s.id}
            className={`relative rounded-lg border px-3 py-2.5 ${
              s.status === 'running'
                ? 'border-amber-600/60 bg-amber-950/20 animate-pulse-amber'
                : s.status === 'done'
                  ? 'border-emerald-800/40 bg-emerald-950/10'
                  : s.status === 'error'
                    ? 'border-rose-800/50 bg-rose-950/20'
                    : 'border-slate-800 bg-slate-950/30'
            }`}
          >
            <div className="flex items-center gap-2">
              <StatusIcon status={s.status} />
              <span className="text-[11px] font-medium text-slate-500">Bước {i + 1}</span>
            </div>
            <div className="mt-1 text-xs font-medium text-slate-200">{s.label}</div>
            {s.detail && <div className="mt-0.5 truncate text-[10px] text-slate-500">{s.detail}</div>}
          </li>
        ))}
      </ol>
    </div>
  );
}
