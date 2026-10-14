import { Captions, Clapperboard } from 'lucide-react';
import { clock, timelineSegments } from '../../lib/structure';
import type { Structure } from '../../types/rules';

const KIND_CLS = {
  opening: 'bg-slate-700/70 text-slate-200',
  closing: 'bg-slate-700/70 text-slate-200',
} as const;
const CHAPTER_CLS = ['bg-amber-500/25 text-amber-100', 'bg-amber-600/20 text-amber-100'];

/**
 * Hook / chapters / CTA of a 3-minute video on one bar (phase 7g), plus the lane the chapter
 * titles are drawn in. The title lane is deliberately a separate row: on screen the title sits
 * at the top of the frame for the first ~1.5 s of a chapter, apart from the captions at the
 * bottom (decision Q1), and the QC treats them as two lanes. `onSeek` makes a stretch clickable.
 */
export function ChapterTimeline({
  structure,
  onSeek,
}: {
  structure: Structure;
  onSeek?: (sec: number) => void;
}) {
  const segs = timelineSegments(structure);
  let chapterIndex = 0;
  const cls = segs.map((s) => (s.kind === 'chapter' ? CHAPTER_CLS[chapterIndex++ % 2] : KIND_CLS[s.kind]));
  return (
    <div className="space-y-1.5 rounded-lg border border-slate-800 bg-slate-950/30 p-3">
      <div className="flex items-center justify-between">
        <h4 className="flex items-center gap-1.5 text-xs font-semibold text-slate-300">
          <Clapperboard className="h-3.5 w-3.5 text-amber-400" />
          Cấu trúc video {clock(structure.durationSec)}
        </h4>
        <span className="text-xs text-slate-400">
          {segs.filter((s) => s.kind === 'chapter').length} chương
          {onSeek ? ' · bấm để tua tới' : ''}
        </span>
      </div>

      <div className="flex h-9 gap-0.5 overflow-hidden rounded-md" role="list" aria-label="Các chương của video">
        {segs.map((s, i) => {
          const body = (
            <>
              {s.showLabel && <span className="truncate px-1.5 text-xs font-medium">{s.label}</span>}
              <span className="sr-only">
                {s.label} {clock(s.startSec)}–{clock(s.endSec)}
              </span>
            </>
          );
          const common = `flex min-w-0 items-center justify-center ${cls[i]} ${
            onSeek ? 'transition hover:brightness-125' : ''
          }`;
          const style = { width: `${s.pct}%` };
          const tip = `${s.label} · ${clock(s.startSec)}–${clock(s.endSec)}${s.title ? ` · “${s.title}”` : ''}`;
          return onSeek ? (
            <button
              key={s.id}
              type="button"
              role="listitem"
              title={tip}
              style={style}
              className={common}
              onClick={() => onSeek(s.startSec)}
            >
              {body}
            </button>
          ) : (
            <div key={s.id} role="listitem" title={tip} style={style} className={common}>
              {body}
            </div>
          );
        })}
      </div>

      <div className="flex items-center gap-2">
        <span className="flex shrink-0 items-center gap-1 text-xs uppercase tracking-wide text-slate-400">
          <Captions className="h-3 w-3" />
          Tiêu đề (đầu khung hình)
        </span>
        <div className="flex min-w-0 flex-1 gap-0.5">
          {segs.map((s) => (
            <div key={s.id} style={{ width: `${s.pct}%` }} className="min-w-0 text-xs">
              {s.kind === 'chapter' && (
                <span
                  className={`block truncate rounded border px-1.5 py-0.5 ${
                    s.title
                      ? 'border-amber-700/50 bg-amber-950/30 text-amber-200'
                      : 'border-dashed border-slate-800 text-slate-400'
                  }`}
                  title={s.title ?? 'Chưa có tiêu đề'}
                >
                  {s.title ?? '—'}
                </span>
              )}
            </div>
          ))}
        </div>
      </div>
      <p className="text-xs text-slate-400">
        Tiêu đề chương hiện khoảng 1,5 giây đầu mỗi chương ở đầu khung hình; phụ đề nằm cuối khung hình.
      </p>
    </div>
  );
}
