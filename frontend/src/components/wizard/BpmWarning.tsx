import { TriangleAlert } from 'lucide-react';
import { BPM_SAFE } from '../../data/bpmSafe';
import { MUSIC_LIBRARY } from '../../data/mockMusic';
import { RULES } from '../../data/rules';
import { bpmNotice } from '../../lib/bpmSafety';
import type { VideoOptions } from '../../types/rules';

/**
 * Yellow box under the track list when the chosen track's BPM cannot build the chosen style and
 * length (ticket 13/10/2026). Never blocks: "Vẫn dùng bài này" goes on and records
 * `bpm_override`, and the render still stops at E1-DUR-GAP if the plan is more than ±10% off.
 * The track BPM comes from the track list the picker shows; the server measures the real one.
 */
export function BpmWarning({
  options,
  onChange,
}: {
  options: VideoOptions;
  onChange: (patch: Partial<VideoOptions>) => void;
}) {
  const track = MUSIC_LIBRARY.find((t) => t.file === options.nhac_file);
  if (!track || !options.phong_cach || !options.do_dai_giay) return null;
  if (options.nhac !== 'thu_vien' && options.nhac !== 'ai_tu_chon') return null;
  const notice = bpmNotice(
    BPM_SAFE,
    options.phong_cach,
    RULES.styles[options.phong_cach].name,
    options.do_dai_giay,
    track.bpm,
  );
  if (!notice) return null;
  const blocking = notice.level === 'BLOCKING';
  const chosen = options.bpm_override === true;
  const tone = blocking
    ? 'border-yellow-600/60 bg-yellow-950/30 text-yellow-200'
    : 'border-slate-700 bg-slate-900/40 text-slate-300';

  return (
    <div role="alert" className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-xs ${tone}`}>
      <TriangleAlert className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${blocking ? 'text-yellow-400' : 'text-slate-400'}`} />
      <div className="space-y-2">
        <p>
          <strong className={blocking ? 'text-yellow-300' : 'text-slate-200'}>{notice.headline}.</strong>{' '}
          {notice.body}
        </p>
        {blocking && !chosen && (
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => onChange({ nhac_file: null })}
              className="rounded-md border border-yellow-500/60 px-2.5 py-1 font-medium text-yellow-100 hover:bg-yellow-500/10"
            >
              Chọn bài khác
            </button>
            <button
              type="button"
              onClick={() => onChange({ bpm_override: true })}
              className="rounded-md border border-slate-600 px-2.5 py-1 font-medium text-slate-300 hover:bg-slate-800"
            >
              Vẫn dùng bài này
            </button>
          </div>
        )}
        {blocking && chosen && (
          <p className="text-[11px] text-yellow-300/80">
            Bạn đã chọn dùng bài này. Khi giao, video vẫn bị chặn nếu kế hoạch lệch quá ±{BPM_SAFE.gapPct}%.{' '}
            <button
              type="button"
              onClick={() => onChange({ bpm_override: null })}
              className="underline hover:text-yellow-200"
            >
              Hoàn tác
            </button>
          </p>
        )}
      </div>
    </div>
  );
}
