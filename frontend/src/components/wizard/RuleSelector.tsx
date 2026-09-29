import { Copy, Gauge, Music, Radio } from 'lucide-react';
import { PACING_LABELS, RULES } from '../../data/rules';
import { countWords } from '../../lib/format';
import { derivePacing } from '../../lib/preflight';
import {
  DRAFT_COUNTS,
  DURATION_IDS,
  PURPOSE_IDS,
  STYLE_IDS,
  type CompatLevel,
  type VideoOptions,
} from '../../types/rules';

const COMPAT_DOT: Record<CompatLevel, string> = {
  ok: 'bg-emerald-500',
  warn: 'bg-amber-500',
  bad: 'bg-rose-500',
};

function SectionTitle({ icon: Icon, children }: { icon: typeof Music; children: React.ReactNode }) {
  return (
    <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-200">
      <Icon className="h-4 w-4 text-amber-400" />
      {children}
    </h3>
  );
}

export function RuleSelector({
  options,
  onChange,
}: {
  options: VideoOptions;
  onChange: (patch: Partial<VideoOptions>) => void;
}) {
  const pacing = derivePacing(options);
  const noteWords = countWords(options.ghi_chu);

  return (
    <div className="space-y-6">
      {/* Phong cách */}
      <div>
        <SectionTitle icon={Radio}>Phong cách (6)</SectionTitle>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {STYLE_IDS.map((id) => {
            const s = RULES.styles[id];
            const selected = options.phong_cach === id;
            const compat =
              options.muc_dich && RULES.compatStylePurpose[id][options.muc_dich];
            return (
              <button
                key={id}
                type="button"
                onClick={() => onChange({ phong_cach: id })}
                className={`relative rounded-lg border px-3 py-2.5 text-left transition ${
                  selected
                    ? 'border-amber-500 bg-amber-500/10 text-amber-100'
                    : 'border-slate-800 bg-slate-900/40 text-slate-300 hover:border-slate-600'
                }`}
              >
                {compat && (
                  <span
                    className={`absolute right-2 top-2 h-1.5 w-1.5 rounded-full ${COMPAT_DOT[compat]}`}
                    title={`Hợp với mục đích đã chọn: ${compat}`}
                  />
                )}
                <div className="text-sm font-medium">{s.name}</div>
                <div className="mt-0.5 text-[11px] text-slate-500">
                  {s.bpm_range ? `${s.bpm_range[0]}–${s.bpm_range[1]} BPM` : 'không theo BPM'} · driver: {s.driver}
                </div>
              </button>
            );
          })}
        </div>
        {options.phong_cach === 'theo_mo_ta' && (
          <div className="mt-3">
            <textarea
              value={options.ghi_chu}
              onChange={(e) => onChange({ ghi_chu: e.target.value })}
              rows={3}
              placeholder="Mở bằng cảnh cắt lát giò, nhịp chậm, chữ trắng tối giản, kết bằng logo và số Zalo…"
              className="w-full rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-amber-500 focus:outline-none"
            />
            <div
              className={`mt-1 text-right text-xs ${
                noteWords < (RULES.styles.theo_mo_ta.note_min_words ?? 20)
                  ? 'text-rose-400'
                  : 'text-emerald-400'
              }`}
            >
              {noteWords} / {RULES.styles.theo_mo_ta.note_min_words} từ (X4)
            </div>
          </div>
        )}
      </div>

      {/* Mục đích */}
      <div>
        <SectionTitle icon={Gauge}>Mục đích (4)</SectionTitle>
        <div className="grid grid-cols-2 gap-2">
          {PURPOSE_IDS.map((id) => {
            const p = RULES.purposes[id];
            const selected = options.muc_dich === id;
            return (
              <button
                key={id}
                type="button"
                onClick={() => onChange({ muc_dich: id })}
                className={`rounded-lg border px-3 py-2.5 text-left transition ${
                  selected
                    ? 'border-amber-500 bg-amber-500/10 text-amber-100'
                    : 'border-slate-800 bg-slate-900/40 text-slate-300 hover:border-slate-600'
                }`}
              >
                <div className="text-sm font-medium">{p.name}</div>
                <div className="mt-0.5 text-[11px] text-slate-500">CTA: {p.cta}</div>
              </button>
            );
          })}
        </div>
        {options.muc_dich === 'khuyen_mai' && (
          <div className="mt-3 grid grid-cols-2 gap-2 rounded-lg border border-slate-800 bg-slate-900/40 p-3">
            <label className="col-span-1 text-xs text-slate-400">
              Mức ưu đãi
              <input
                value={options.khuyen_mai?.uu_dai ?? ''}
                onChange={(e) =>
                  onChange({
                    khuyen_mai: {
                      uu_dai: e.target.value,
                      thoi_han: options.khuyen_mai?.thoi_han ?? '',
                      dieu_kien: options.khuyen_mai?.dieu_kien ?? '',
                    },
                  })
                }
                placeholder="Giảm 20%"
                className="mt-1 w-full rounded border border-slate-700 bg-slate-950/60 px-2 py-1.5 text-sm text-slate-200 placeholder:text-slate-600 focus:border-amber-500 focus:outline-none"
              />
            </label>
            <label className="col-span-1 text-xs text-slate-400">
              Thời hạn
              <input
                value={options.khuyen_mai?.thoi_han ?? ''}
                onChange={(e) =>
                  onChange({
                    khuyen_mai: {
                      uu_dai: options.khuyen_mai?.uu_dai ?? '',
                      thoi_han: e.target.value,
                      dieu_kien: options.khuyen_mai?.dieu_kien ?? '',
                    },
                  })
                }
                placeholder="31/10/2026"
                className="mt-1 w-full rounded border border-slate-700 bg-slate-950/60 px-2 py-1.5 text-sm text-slate-200 placeholder:text-slate-600 focus:border-amber-500 focus:outline-none"
              />
            </label>
          </div>
        )}
      </div>

      {/* Thời lượng + số bản nháp */}
      <div className="grid grid-cols-2 gap-6">
        <div>
          <SectionTitle icon={Gauge}>Thời lượng (5)</SectionTitle>
          <div className="flex flex-wrap gap-2">
            {DURATION_IDS.map((d) => {
              const selected = options.do_dai_giay === d;
              const compat = options.phong_cach && RULES.compatStyleDuration[options.phong_cach][String(d)];
              return (
                <button
                  key={d}
                  type="button"
                  onClick={() => onChange({ do_dai_giay: d })}
                  className={`flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium transition ${
                    selected
                      ? 'border-amber-500 bg-amber-500/10 text-amber-100'
                      : 'border-slate-800 bg-slate-900/40 text-slate-300 hover:border-slate-600'
                  }`}
                >
                  {compat && <span className={`h-1.5 w-1.5 rounded-full ${COMPAT_DOT[compat]}`} />}
                  {d >= 60 ? `${d / 60} phút` : `${d}s`}
                </button>
              );
            })}
          </div>
        </div>

        <div>
          <SectionTitle icon={Copy}>Số bản nháp</SectionTitle>
          <div className="flex gap-2">
            {DRAFT_COUNTS.map((n) => (
              <button
                key={n}
                type="button"
                onClick={() => onChange({ so_ban_nhap: n })}
                className={`h-9 w-9 rounded-lg border text-sm font-semibold transition ${
                  options.so_ban_nhap === n
                    ? 'border-amber-500 bg-amber-500/10 text-amber-100'
                    : 'border-slate-800 bg-slate-900/40 text-slate-300 hover:border-slate-600'
                }`}
              >
                {n}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Pacing — read-only, suy ra từ driver của phong cách (không phải field backend) */}
      <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-3">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Dẫn nhịp (suy ra từ phong cách)
          </span>
          <span className="rounded-full border border-slate-700 bg-slate-950 px-2.5 py-1 text-xs font-semibold text-amber-300">
            {pacing.mode === 'tu_dong' ? 'TỰ ĐỘNG' : PACING_LABELS[pacing.mode].label}
          </span>
        </div>
        {pacing.note && <p className="mt-1.5 text-xs text-amber-300/90">{pacing.note}</p>}
      </div>
    </div>
  );
}
