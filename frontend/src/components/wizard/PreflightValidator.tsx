import { AlertTriangle, CheckCircle2, Info, Loader2, ShieldAlert, Zap } from 'lucide-react';
import type { PreflightResult } from '../../types/rules';

/** An option still to choose, and the id of the page section where it is chosen. */
export interface MissingChoice {
  label: string;
  target: string;
}

function jumpTo(id: string) {
  const el = document.getElementById(id);
  el?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  el?.querySelector<HTMLElement>('button, textarea, input')?.focus({ preventScroll: true });
}

export function PreflightValidator({
  result,
  incomplete,
  missing,
  renderOnly,
  checking,
  error,
  submitError,
  missingProject,
  submitting,
  onSubmit,
}: {
  result: PreflightResult | null;
  incomplete: boolean;
  /** What is still to choose: shown as links that jump to the place it is chosen. */
  missing: MissingChoice[];
  /** Re-render of an existing plan: nothing to preflight, only a project to pick. */
  renderOnly: boolean;
  checking: boolean;
  /** The preflight call itself failed (not a rule violation). */
  error: string | null;
  submitError: string | null;
  missingProject: boolean;
  submitting: boolean;
  onSubmit: () => void;
}) {
  // `missing` also lists a platform not chosen yet (render-only runs too).
  const disabled = renderOnly
    ? missingProject || submitting || missing.length > 0
    : missing.length > 0 ||
      incomplete ||
      result === null ||
      !result.valid ||
      checking ||
      error !== null ||
      missingProject ||
      submitting;

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <div className="mb-3 flex items-center gap-2">
        <ShieldAlert className="h-4 w-4 text-amber-400" />
        <h3 className="text-sm font-semibold text-slate-200">Kiểm tra trước khi gửi</h3>
        <span className="text-xs text-slate-400">— miễn phí, chưa gọi AI</span>
      </div>

      {renderOnly ? (
        <p className="rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2 text-xs text-slate-400">
          Dựng lại theo plan.json và kịch bản có sẵn của dự án — không gọi AI, không cần kiểm tra tuỳ chọn.
        </p>
      ) : incomplete ? (
        <div className="rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2 text-xs text-slate-300">
          Còn thiếu:{' '}
          {missing.map((m, i) => (
            <span key={m.target}>
              {i > 0 && ', '}
              <button
                type="button"
                onClick={() => jumpTo(m.target)}
                className="font-semibold text-amber-300 underline decoration-dotted underline-offset-2 hover:text-amber-200"
              >
                {m.label}
              </button>
            </span>
          ))}
          . Chọn đủ thì app tự kiểm tra.
        </div>
      ) : result === null ? (
        <p
          aria-live="polite"
          className="flex items-center gap-2 rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2 text-xs text-slate-400"
        >
          {error === null && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {error === null ? 'Đang kiểm tra…' : 'Chưa kiểm tra được.'}
        </p>
      ) : (
        <div className={`space-y-2 ${checking ? 'opacity-60' : ''}`}>
          {result.errors.length === 0 && result.warnings.length === 0 && (
            <div className="flex items-center gap-2 rounded-lg border border-emerald-800/50 bg-emerald-950/30 px-3 py-2 text-xs text-emerald-300">
              <CheckCircle2 className="h-4 w-4 shrink-0" />
              Không có xung đột — sẵn sàng gửi dựng.
            </div>
          )}

          {result.errors.map((e, i) => (
            <div
              key={`err-${i}`}
              className="flex items-start gap-2 rounded-lg border border-rose-800/50 bg-rose-950/30 px-3 py-2 text-xs text-rose-300"
            >
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                {e.message} <Code value={e.code} />
              </span>
            </div>
          ))}

          {result.warnings.map((w, i) => (
            <div
              key={`warn-${i}`}
              className="flex items-start gap-2 rounded-lg border border-amber-800/40 bg-amber-950/20 px-3 py-2 text-xs text-amber-300"
            >
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                {w.message} <Code value={w.code} />
              </span>
            </div>
          ))}

          {result.adjustments.map((a, i) => (
            <div
              key={`adj-${i}`}
              className="flex items-start gap-2 rounded-lg border border-slate-700 bg-slate-950/40 px-3 py-2 text-xs text-slate-300"
            >
              <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400" />
              <span>
                <span className="font-semibold">Tự xử lý:</span> {a.message} <Code value={a.code} />
              </span>
            </div>
          ))}

          {result.scenes_needed !== null && (
            <p className="pt-1 text-xs text-slate-400">
              Cần khoảng {result.scenes_needed} cảnh dùng được
              {result.scenes_estimated !== null && <> — ước tính hiện có ~{result.scenes_estimated}.</>}
            </p>
          )}
        </div>
      )}

      {error && <ErrorNote message={error} />}
      {submitError && <ErrorNote message={submitError} />}
      {missingProject && (renderOnly || !incomplete) && (
        <p className="mt-3 text-xs text-amber-400/90">Chọn một dự án ở mục 1 để gửi dựng.</p>
      )}

      <button
        type="button"
        disabled={disabled}
        onClick={onSubmit}
        className={`mt-4 flex w-full items-center justify-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold transition ${
          disabled
            ? 'cursor-not-allowed bg-slate-800 text-slate-500'
            : 'bg-gradient-to-r from-amber-500 to-orange-600 text-slate-950 hover:brightness-110'
        }`}
      >
        <Zap className="h-4 w-4" />
        {submitting ? 'Đang gửi…' : renderOnly ? 'Xếp hàng dựng lại' : 'Gửi dựng video'}
      </button>
    </div>
  );
}

/** The rule code (X11, W_BPM…) kept for whoever reads rules.json, out of the way of the text. */
function Code({ value }: { value: string }) {
  return <span className="ml-1 font-mono-studio text-xs opacity-60">({value})</span>;
}

function ErrorNote({ message }: { message: string }) {
  return (
    <p
      aria-live="polite"
      className="mt-3 flex items-start gap-2 rounded-lg border border-rose-800/50 bg-rose-950/30 px-3 py-2 text-xs text-rose-300">
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      {message}
    </p>
  );
}
