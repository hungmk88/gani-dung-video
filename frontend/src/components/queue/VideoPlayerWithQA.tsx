import { AlertTriangle, CheckCircle2, Download, FileJson, Info, RefreshCw, Wrench, XCircle } from 'lucide-react';
import { useRef, useState } from 'react';
import { outputLabel } from '../../lib/platforms';
import { seekTarget } from '../../lib/structure';
import type { QaCheck, QaGroup as QaGroupData, QaReport, RenderJob } from '../../types/rules';
import { ChapterTimeline } from './ChapterTimeline';

function CheckRow({ c }: { c: QaCheck }) {
  return (
    <li className="flex items-start gap-2 py-1.5 text-xs">
      {c.passed && c.detail.startsWith('Cảnh báo') ? (
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-400" />
      ) : c.passed ? (
        <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-400" />
      ) : (
        <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-400" />
      )}
      <span className="text-slate-300">
        {c.label} <span className="font-mono-studio text-xs text-slate-400">({c.code})</span>
        <span className="block text-xs text-slate-400">{c.detail}</span>
      </span>
    </li>
  );
}

function QaGroup({ group }: { group: QaGroupData }) {
  const empty = group.checks.length === 0;
  const allPass = !empty && group.checks.every((c) => c.passed);
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950/30 p-3">
      <div className="mb-1 flex items-start justify-between gap-2">
        <h4 className="text-xs font-semibold text-slate-300">{group.title}</h4>
        <span
          className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${
            empty
              ? 'bg-slate-700/40 text-slate-400'
              : allPass
                ? 'bg-emerald-500/15 text-emerald-300'
                : 'bg-rose-500/15 text-rose-300'
          }`}
        >
          {empty ? 'CHƯA CÓ' : allPass ? 'ĐẠT' : 'LỖI'}
        </span>
      </div>
      {empty ? (
        <p className="py-1.5 text-xs text-slate-400">{group.emptyNote ?? 'Chưa có dữ liệu.'}</p>
      ) : (
        <ul className="divide-y divide-slate-800/60">
          {group.checks.map((c, i) => (
            <CheckRow key={i} c={c} />
          ))}
        </ul>
      )}
      {group.note && (
        <p className="mt-1.5 flex items-start gap-1.5 border-t border-slate-800/60 pt-1.5 text-xs text-slate-400">
          <Info className="mt-0.5 h-3 w-3 shrink-0" />
          {group.note}
        </p>
      )}
    </div>
  );
}

function QaReportPanel({ qa }: { qa: QaReport }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-200">Soát chất lượng</h3>
        <span className="text-xs text-slate-400">
          vòng {qa.rounds_used}/{qa.max_rounds}
        </span>
      </div>
      {qa.fixes.length > 0 && (
        <ul className="space-y-1 rounded-lg border border-sky-800/50 bg-sky-950/20 px-3 py-2 text-xs text-sky-200">
          {qa.fixes.map((f, i) => (
            <li key={i} className="flex items-start gap-1.5">
              <Wrench className="mt-0.5 h-3 w-3 shrink-0 text-sky-400" />
              <span>
                <strong>Tự chỉnh:</strong> {f}
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-3">
        {qa.groups.map((g) => (
          <QaGroup key={g.title} group={g} />
        ))}
      </div>
    </div>
  );
}

export function VideoPlayerWithQA({
  job,
  onRerender,
  rerenderBlocked = false,
  onShowPlan,
}: {
  job: RenderJob;
  onRerender: () => void;
  /** The card (or a job of its chain) is still queued / running / waiting for approval. */
  rerenderBlocked?: boolean;
  /** Omitted when there is nothing truthful to show (the real API has no plan.json route). */
  onShowPlan?: () => void;
}) {
  const [chosenTab, setChosenTab] = useState<'preview' | 'final' | null>(null);
  const [chosenDraft, setChosenDraft] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  // A pipeline carries one result per draft (A/B/C); a lone job shows its own files.
  const outputs = job.outputs ?? [];
  // One output per draft and platform: the key tells "A · TikTok" from "A · Reels".
  const keyOf = (o: { draft: string; platform?: string }) => `${o.draft}|${o.platform ?? ''}`;
  const output = outputs.find((o) => keyOf(o) === chosenDraft) ?? outputs[0];
  const previewUrl = output ? output.previewUrl : job.previewUrl;
  const finalUrl = output ? output.finalUrl : job.finalUrl;
  const qa = output ? output.qa : job.qa;
  const structure = output?.structure ?? job.structure;
  // Until the user picks a tab, show whichever file exists (a final-only job has no preview).
  const tab = chosenTab ?? (previewUrl || !finalUrl ? 'preview' : 'final');
  const setTab = setChosenTab;
  const src = tab === 'final' ? finalUrl : previewUrl;
  const res = tab === 'final' ? '1080×1920' : '540×960';

  return (
    <div className="space-y-4 rounded-xl border border-slate-800 bg-slate-900/40 p-4">
      {outputs.length > 1 && (
        <div className="flex flex-wrap gap-1 text-xs">
          {outputs.map((o) => (
            <button
              key={keyOf(o)}
              type="button"
              aria-pressed={output !== undefined && keyOf(o) === keyOf(output)}
              onClick={() => setChosenDraft(keyOf(o))}
              className={`rounded-md border px-3 py-1 ${
                output !== undefined && keyOf(o) === keyOf(output)
                  ? 'border-amber-500 bg-amber-500/10 text-amber-200'
                  : 'border-slate-800 text-slate-400 hover:border-slate-600'
              }`}
            >
              {outputLabel(o.draft, o.platform)}
            </button>
          ))}
        </div>
      )}
      {/* The video is 9:16: a portrait frame beside the details, not a wide black strip. */}
      <div className="grid gap-4 md:grid-cols-[minmax(0,300px)_1fr]">
        <div className="mx-auto w-full max-w-[300px] space-y-2">
          <div className="flex items-center justify-between gap-2">
            <div className="flex gap-1 rounded-lg border border-slate-800 bg-slate-950/40 p-0.5 text-xs">
              <button
                type="button"
                aria-pressed={tab === 'preview'}
                onClick={() => setTab('preview')}
                className={`rounded-md px-3 py-1 ${tab === 'preview' ? 'bg-amber-500 text-slate-950' : 'text-slate-300'}`}
              >
                Bản xem nhanh
              </button>
              <button
                type="button"
                aria-pressed={tab === 'final'}
                onClick={() => setTab('final')}
                disabled={!finalUrl}
                className={`rounded-md px-3 py-1 disabled:opacity-40 ${
                  tab === 'final' ? 'bg-amber-500 text-slate-950' : 'text-slate-300'
                }`}
              >
                Bản cuối
              </button>
            </div>
            <span className="font-mono-studio text-xs text-slate-400">{res}</span>
          </div>
          <div className="aspect-[9/16] w-full overflow-hidden rounded-lg border border-slate-800 bg-black">
            {src ? (
              <video ref={videoRef} key={src} src={src} controls className="h-full w-full object-contain" />
            ) : (
              <div className="flex h-full items-center justify-center px-6 text-center text-xs text-slate-400">
                Chưa có video — đợi bước "Dựng bản xem nhanh" xong.
              </div>
            )}
          </div>
        </div>

        <div className="min-w-0 space-y-4">
          <div className="flex flex-wrap gap-2">
            <ActionButton icon={Download} label="Tải bản cuối" disabled={!finalUrl} href={finalUrl ?? undefined} />
            {onShowPlan && <ActionButton icon={FileJson} label="Xem kế hoạch (mẫu)" onClick={onShowPlan} />}
            <ActionButton
              icon={RefreshCw}
              label="Dựng lại"
              onClick={onRerender}
              disabled={rerenderBlocked}
              title={rerenderBlocked ? 'Video còn đang chờ/đang dựng — đợi xong (hoặc hủy) rồi mới dựng lại.' : undefined}
            />
          </div>

          {structure && (
            <ChapterTimeline
              structure={structure}
              onSeek={
                src
                  ? (sec) => {
                      const v = videoRef.current;
                      if (!v) return;
                      v.currentTime = seekTarget(sec, v.duration);
                      void v.play().catch(() => undefined);
                    }
                  : undefined
              }
            />
          )}

          {qa ? (
            <QaReportPanel qa={qa} />
          ) : (
            <p className="text-xs text-slate-400">Kết quả soát chất lượng hiện ở đây khi dựng xong.</p>
          )}
        </div>
      </div>
    </div>
  );
}

function ActionButton({
  icon: Icon,
  label,
  disabled,
  onClick,
  href,
  title,
}: {
  icon: typeof Download;
  label: string;
  disabled?: boolean;
  onClick?: () => void;
  href?: string;
  title?: string;
}) {
  const cls = `flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition ${
    disabled
      ? 'cursor-not-allowed border-slate-800 text-slate-500'
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
    <button type="button" disabled={disabled} onClick={onClick} title={title} className={cls}>
      <Icon className="h-3.5 w-3.5" />
      {label}
    </button>
  );
}
