import { X } from 'lucide-react';
import { SAMPLE_PLAN_JSON } from '../../data/mockPlan';

export function PlanModal({ onClose }: { onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="max-h-[80vh] w-full max-w-2xl overflow-hidden rounded-xl border border-slate-800 bg-slate-900 shadow-2xl"
      >
        <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
          <div>
            <h3 className="text-sm font-semibold text-slate-200">edit/plan.json</h3>
            <p className="text-[11px] text-slate-500">
              Mẫu thật từ lần chạy 29/09/2026 (review_mon_an · ban_hang · 30s) — minh hoạ hình
              dạng dữ liệu, không phải tác vụ đang chọn.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-slate-500 hover:bg-slate-800 hover:text-slate-200"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <pre className="max-h-[65vh] overflow-auto scroll-thin p-4 font-mono-studio text-[11px] leading-relaxed text-emerald-300">
          {JSON.stringify(SAMPLE_PLAN_JSON, null, 1)}
        </pre>
      </div>
    </div>
  );
}
