import { AlertTriangle, CheckCircle2, Info, Loader2, ShieldAlert, Zap } from 'lucide-react';
import type { PreflightResult } from '../../types/rules';

export function PreflightValidator({
  result,
  incomplete,
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
  const disabled = renderOnly
    ? missingProject || submitting
    : incomplete ||
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
        <h3 className="text-sm font-semibold text-slate-200">Preflight Validator</h3>
        <span className="text-[11px] text-slate-500">— kiểm tra trước khi gọi AI (X1–X18)</span>
      </div>

      {renderOnly ? (
        <p className="rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2 text-xs text-slate-500">
          Dựng lại theo plan.json và kịch bản có sẵn của dự án — không gọi AI, không cần kiểm tra tuỳ chọn.
        </p>
      ) : incomplete ? (
        <p className="rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2 text-xs text-slate-500">
          Chọn đủ phong cách, mục đích, thời lượng và chế độ nhạc để bắt đầu kiểm tra.
        </p>
      ) : result === null ? (
        <p className="flex items-center gap-2 rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2 text-xs text-slate-500">
          {error === null && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {error === null ? 'Đang kiểm tra…' : 'Chưa kiểm tra được.'}
        </p>
      ) : (
        <div className={`space-y-2 ${checking ? 'opacity-60' : ''}`}>
          {result.errors.length === 0 && result.warnings.length === 0 && (
            <div className="flex items-center gap-2 rounded-lg border border-emerald-800/50 bg-emerald-950/30 px-3 py-2 text-xs text-emerald-300">
              <CheckCircle2 className="h-4 w-4 shrink-0" />
              Không có xung đột — tổ hợp lựa chọn sẵn sàng gửi xử lý.
            </div>
          )}

          {result.errors.map((e, i) => (
            <div
              key={`err-${i}`}
              className="flex items-start gap-2 rounded-lg border border-rose-800/50 bg-rose-950/30 px-3 py-2 text-xs text-rose-300"
            >
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                <span className="font-mono-studio font-semibold">Lỗi {e.code}:</span> {e.message}
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
                <span className="font-mono-studio font-semibold">{w.code}:</span> {w.message}
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
                <span className="font-mono-studio font-semibold">{a.code} (tự xử lý):</span>{' '}
                {a.message}
              </span>
            </div>
          ))}

          {result.scenes_needed !== null && (
            <p className="pt-1 text-[11px] text-slate-500">
              Cần {result.scenes_needed} cảnh dùng được (bảng scenes_needed)
              {result.scenes_estimated !== null && <> — ước tính hiện có ~{result.scenes_estimated}.</>}
            </p>
          )}
        </div>
      )}

      {error && <ErrorNote message={error} />}
      {submitError && <ErrorNote message={submitError} />}
      {missingProject && (renderOnly || !incomplete) && (
        <p className="mt-3 text-[11px] text-amber-400/90">Chọn một dự án ở mục 1 để gửi xử lý.</p>
      )}

      <button
        type="button"
        disabled={disabled}
        onClick={onSubmit}
        className={`mt-4 flex w-full items-center justify-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold transition ${
          disabled
            ? 'cursor-not-allowed bg-slate-800 text-slate-600'
            : 'bg-gradient-to-r from-amber-500 to-orange-600 text-slate-950 hover:brightness-110'
        }`}
      >
        <Zap className="h-4 w-4" />
        {submitting ? 'Đang gửi…' : renderOnly ? 'Xếp hàng dựng lại' : 'Gửi xử lý (Queue Render)'}
      </button>
    </div>
  );
}

function ErrorNote({ message }: { message: string }) {
  return (
    <p className="mt-3 flex items-start gap-2 rounded-lg border border-rose-800/50 bg-rose-950/30 px-3 py-2 text-xs text-rose-300">
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      {message}
    </p>
  );
}
