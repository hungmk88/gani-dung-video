import { CheckCircle2, CircleSlash, FolderOpen, Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../../services/api';
import type { DraftChoice, ProjectInfo } from '../../types/rules';

/** 'pipeline': analyze → draft → render from the options below (POST /api/pipelines).
 *  'render': only re-render a project that already has plan + drafts (POST /api/jobs). */
export type RunMode = 'pipeline' | 'render';

export interface RealJobChoice {
  mode: RunMode;
  projectId: string | null;
  draft: DraftChoice;
  final: boolean;
  noAsr: boolean;
  forceAnalyze: boolean;
}

const MODES: Array<{ id: RunMode; label: string; hint: string }> = [
  {
    id: 'pipeline',
    label: 'Chạy cả chuỗi',
    hint: 'phân tích → kịch bản (duyệt chi phí) → dựng, theo ma trận tuỳ chọn bên dưới',
  },
  {
    id: 'render',
    label: 'Chỉ dựng lại',
    hint: 'dự án đã có kịch bản; phong cách, độ dài… lấy từ plan.json',
  },
];

/**
 * Real-mode replacement of the footage dropzone: the server has no upload endpoint
 * (docs/api-jobs.md §2), footage already sits in a project folder on the render machine.
 */
export function ProjectPicker({
  value,
  draftLetters,
  onChange,
  onProjects,
}: {
  value: RealJobChoice;
  /** The project list once loaded (the wizard reads the chosen project's clips from it). */
  onProjects?: (projects: ProjectInfo[]) => void;
  /** Draft letters the chosen options produce (so_ban_nhap) — only these can be picked. */
  draftLetters: readonly string[];
  onChange: (patch: Partial<RealJobChoice>) => void;
}) {
  const [projects, setProjects] = useState<ProjectInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .listProjects()
      .then((p) => {
        if (!cancelled) {
          setProjects(p);
          onProjects?.(p);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [onProjects]);

  const pipeline = value.mode === 'pipeline';
  const draftValue = value.draft === 'all' || !pipeline || draftLetters.includes(value.draft) ? value.draft : 'all';

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2" role="tablist" aria-label="Chế độ chạy">
        {MODES.map((m) => (
          <button
            key={m.id}
            type="button"
            role="tab"
            aria-selected={value.mode === m.id}
            onClick={() => onChange({ mode: m.id })}
            className={`rounded-lg border px-3 py-2 text-left transition ${
              value.mode === m.id
                ? 'border-amber-500 bg-amber-500/10 text-amber-100'
                : 'border-slate-800 bg-slate-900/40 text-slate-300 hover:border-slate-600'
            }`}
          >
            <div className="text-xs font-semibold">{m.label}</div>
            <div className="mt-0.5 max-w-64 text-xs text-slate-400">{m.hint}</div>
          </button>
        ))}
      </div>

      {error ? (
        <p aria-live="polite" className="rounded-lg border border-rose-800/50 bg-rose-950/30 px-3 py-2 text-xs text-rose-300">
          {error}
        </p>
      ) : projects === null ? (
        <p className="flex items-center gap-2 text-xs text-slate-400">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> Đang lấy danh sách dự án…
        </p>
      ) : projects.length === 0 ? (
        <p className="text-xs text-slate-400">Không có dự án nào trong GANI_DV_ALLOWED_ROOTS của máy dựng.</p>
      ) : (
        <ul className="grid gap-2 sm:grid-cols-2">
          {[...projects]
            .sort((a, b) => Number(b.has_inventory) - Number(a.has_inventory))
            .map((p) => {
              const selectable = pipeline || p.ready_to_render;
              const selected = value.projectId === p.id;
              return (
                <li key={p.id}>
                  <button
                    type="button"
                    disabled={!selectable}
                    aria-pressed={selected}
                    onClick={() => onChange({ projectId: p.id })}
                    className={`w-full rounded-lg border px-3 py-2.5 text-left transition ${
                      selected
                        ? 'border-amber-500 bg-amber-500/10'
                        : selectable
                          ? 'border-slate-800 bg-slate-900/40 hover:border-slate-600'
                          : 'cursor-not-allowed border-slate-800 bg-slate-900/20 opacity-60'
                    }`}
                  >
                    <div className="flex items-center gap-2 text-sm font-medium text-slate-200">
                      <FolderOpen className="h-4 w-4 shrink-0 text-amber-400" />
                      <span className="truncate">{p.name}</span>
                    </div>
                    <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-400">
                      <Flag ok={p.has_inventory} label="inventory" />
                      <Flag ok={p.has_plan} label="plan" />
                      <Flag ok={p.has_drafts} label="kịch bản" />
                    </div>
                    {!selectable && (
                      <div className="mt-1 text-xs text-amber-400/80">
                        Chưa có kịch bản — dùng "Chạy cả chuỗi".
                      </div>
                    )}
                  </button>
                </li>
              );
            })}
        </ul>
      )}

      <div className="flex flex-wrap items-center gap-x-6 gap-y-3 text-xs text-slate-300">
        <label className="flex items-center gap-2">
          Bản nháp
          <select
            value={draftValue}
            onChange={(e) => onChange({ draft: e.target.value as DraftChoice })}
            className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200"
          >
            <option value="all">Tất cả</option>
            {(pipeline ? draftLetters : ['A', 'B', 'C']).map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
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
        {pipeline && (
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={value.forceAnalyze}
              onChange={(e) => onChange({ forceAnalyze: e.target.checked })}
              className="h-3.5 w-3.5 accent-amber-500"
            />
            Phân tích lại dù footage không đổi
          </label>
        )}
      </div>
      {pipeline && (
        <p className="text-xs text-slate-400">
          Footage phải nằm sẵn trong thư mục dự án trên máy dựng (không tải lên qua trình duyệt).
          Bước gọi Claude luôn dừng lại chờ bạn duyệt chi phí.
        </p>
      )}
    </div>
  );
}

function Flag({ ok, label }: { ok: boolean; label: string }) {
  const Icon = ok ? CheckCircle2 : CircleSlash;
  return (
    <span className={`flex items-center gap-1 ${ok ? 'text-emerald-400' : 'text-slate-400'}`}>
      <Icon className="h-3 w-3" />
      {label}
    </span>
  );
}
