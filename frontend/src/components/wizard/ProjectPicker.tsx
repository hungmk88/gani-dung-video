import { CheckCircle2, CircleSlash, FolderOpen, Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../../services/api';
import type { DraftChoice, ProjectInfo } from '../../types/rules';

export interface RealJobChoice {
  projectId: string | null;
  draft: DraftChoice;
  final: boolean;
  noAsr: boolean;
}

/**
 * Real-mode replacement of the footage dropzone: the server has no upload endpoint
 * (docs/api-jobs.md §2), footage already sits in a project folder on the render machine.
 */
export function ProjectPicker({
  value,
  onChange,
}: {
  value: RealJobChoice;
  onChange: (patch: Partial<RealJobChoice>) => void;
}) {
  const [projects, setProjects] = useState<ProjectInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .listProjects()
      .then((p) => {
        if (!cancelled) setProjects(p);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return (
      <p className="rounded-lg border border-rose-800/50 bg-rose-950/30 px-3 py-2 text-xs text-rose-300">
        {error}
      </p>
    );
  }
  if (projects === null) {
    return (
      <p className="flex items-center gap-2 text-xs text-slate-500">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Đang lấy danh sách dự án…
      </p>
    );
  }
  if (projects.length === 0) {
    return (
      <p className="text-xs text-slate-500">
        Không có dự án nào trong GANI_DV_ALLOWED_ROOTS của máy dựng.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <ul className="grid gap-2 sm:grid-cols-2">
        {projects.map((p) => {
          const selected = value.projectId === p.id;
          return (
            <li key={p.id}>
              <button
                type="button"
                disabled={!p.ready_to_render}
                onClick={() => onChange({ projectId: p.id })}
                className={`w-full rounded-lg border px-3 py-2.5 text-left transition ${
                  selected
                    ? 'border-amber-500 bg-amber-500/10'
                    : p.ready_to_render
                      ? 'border-slate-800 bg-slate-900/40 hover:border-slate-600'
                      : 'cursor-not-allowed border-slate-800 bg-slate-900/20 opacity-60'
                }`}
              >
                <div className="flex items-center gap-2 text-sm font-medium text-slate-200">
                  <FolderOpen className="h-4 w-4 shrink-0 text-amber-400" />
                  <span className="truncate">{p.name}</span>
                </div>
                <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-500">
                  <Flag ok={p.has_inventory} label="inventory" />
                  <Flag ok={p.has_plan} label="plan" />
                  <Flag ok={p.has_drafts} label="kịch bản" />
                </div>
                {!p.ready_to_render && (
                  <div className="mt-1 text-[11px] text-amber-400/80">
                    Chưa sẵn sàng — chạy plan/pick/draft trước.
                  </div>
                )}
              </button>
            </li>
          );
        })}
      </ul>

      <div className="flex flex-wrap items-center gap-x-6 gap-y-3 text-xs text-slate-300">
        <label className="flex items-center gap-2">
          Bản nháp
          <select
            value={value.draft}
            onChange={(e) => onChange({ draft: e.target.value as DraftChoice })}
            className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200"
          >
            <option value="all">Tất cả</option>
            <option value="A">A</option>
            <option value="B">B</option>
            <option value="C">C</option>
          </select>
        </label>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={value.final}
            onChange={(e) => onChange({ final: e.target.checked })}
            className="h-3.5 w-3.5 accent-amber-500"
          />
          Dựng bản cuối 1080×1920 (mặc định: nháp 540p)
        </label>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={value.noAsr}
            onChange={(e) => onChange({ noAsr: e.target.checked })}
            className="h-3.5 w-3.5 accent-amber-500"
          />
          Bỏ kiểm tra lời (T8)
        </label>
      </div>
      <p className="text-[11px] text-slate-500">
        Phong cách, độ dài… của bản dựng lấy từ plan.json của dự án; ma trận bên dưới chỉ để kiểm
        tra thử một tổ hợp bằng preflight thật.
      </p>
    </div>
  );
}

function Flag({ ok, label }: { ok: boolean; label: string }) {
  const Icon = ok ? CheckCircle2 : CircleSlash;
  return (
    <span className={`flex items-center gap-1 ${ok ? 'text-emerald-400' : 'text-slate-600'}`}>
      <Icon className="h-3 w-3" />
      {label}
    </span>
  );
}
