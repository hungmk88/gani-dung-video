import { AlertTriangle, CheckCircle2, ShieldAlert, Zap } from 'lucide-react';
import type { PreflightResult } from '../../types/rules';

export function PreflightValidator({
  result,
  incomplete,
  submitting,
  onSubmit,
}: {
  result: PreflightResult;
  incomplete: boolean;
  submitting: boolean;
  onSubmit: () => void;
}) {
  const disabled = incomplete || !result.valid || submitting;

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <div className="mb-3 flex items-center gap-2">
        <ShieldAlert className="h-4 w-4 text-amber-400" />
        <h3 className="text-sm font-semibold text-slate-200">Preflight Validator</h3>
        <span className="text-[11px] text-slate-500">— kiểm tra trước khi gọi AI (X1–X18)</span>
      </div>

      {incomplete ? (
        <p className="rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2 text-xs text-slate-500">
          Chọn đủ phong cách, mục đích, thời lượng và chế độ nhạc để bắt đầu kiểm tra.
        </p>
      ) : (
        <div className="space-y-2">
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

          {result.scenes_needed !== null && (
            <p className="pt-1 text-[11px] text-slate-500">
              Cần {result.scenes_needed} cảnh dùng được (bảng scenes_needed)
              {result.scenes_estimated !== null && <> — ước tính hiện có ~{result.scenes_estimated}.</>}
            </p>
          )}
        </div>
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
        {submitting ? 'Đang gửi…' : 'Gửi xử lý (Queue Render)'}
      </button>
    </div>
  );
}
