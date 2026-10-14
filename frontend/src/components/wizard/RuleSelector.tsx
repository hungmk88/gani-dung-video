import { Copy, Gauge, Music, Radio } from 'lucide-react';
import { RULES } from '../../data/rules';
import { countWords } from '../../lib/format';
import { derivePacing } from '../../lib/preflight';
import { draftsFor } from '../../lib/structure';
import {
  DRAFT_COUNTS,
  DURATION_IDS,
  PURPOSE_IDS,
  STYLE_IDS,
  type CompatLevel,
  type VideoOptions,
} from '../../types/rules';

// A symbol as well as a colour (colour alone fails colour-blind users), and Vietnamese words
// instead of the rule values ok/warn/bad.
const COMPAT: Record<CompatLevel, { mark: string; word: string; cls: string }> = {
  ok: { mark: '✓', word: 'hợp', cls: 'text-emerald-400' },
  warn: { mark: '!', word: 'tạm được', cls: 'text-amber-400' },
  bad: { mark: '✕', word: 'không nên', cls: 'text-rose-400' },
};

const PACING_WORDS = {
  loi: 'theo lời nói',
  nhac: 'theo phách nhạc',
  luoi_ao: 'lưới nhịp ảo (không nhạc)',
  tu_dong: 'tự động',
} as const;

function CompatMark({ level, against }: { level: CompatLevel; against: string }) {
  const c = COMPAT[level];
  return (
    <span className={`font-bold ${c.cls}`} title={`${c.word} với ${against} đã chọn`}>
      <span aria-hidden="true">{c.mark}</span>
      <span className="sr-only">{`${c.word} với ${against} đã chọn`}</span>
    </span>
  );
}

function CompatLegend({ against }: { against: string }) {
  return (
    <p className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-400">
      {(Object.keys(COMPAT) as CompatLevel[]).map((k) => (
        <span key={k}>
          <span className={`font-bold ${COMPAT[k].cls}`} aria-hidden="true">
            {COMPAT[k].mark}
          </span>{' '}
          {COMPAT[k].word}
        </span>
      ))}
      <span>— so với {against} đã chọn</span>
    </p>
  );
}

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
  longVideo = true,
}: {
  options: VideoOptions;
  onChange: (patch: Partial<VideoOptions>) => void;
  /** false = the server has not opened 3-minute videos yet (phase 7e); null/true = no lock. */
  longVideo?: boolean | null;
}) {
  const pacing = derivePacing(options);
  const noteWords = countWords(options.ghi_chu);

  return (
    <div className="space-y-6">
      {/* Phong cách */}
      <div id="chon-phong-cach" className="scroll-mt-20">
        <SectionTitle icon={Radio}>Phong cách</SectionTitle>
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
                aria-pressed={selected}
                onClick={() => onChange({ phong_cach: id })}
                className={`relative rounded-lg border px-3 py-2.5 text-left transition ${
                  selected
                    ? 'border-amber-500 bg-amber-500/10 text-amber-100'
                    : 'border-slate-800 bg-slate-900/40 text-slate-300 hover:border-slate-600'
                }`}
              >
                {compat && (
                  <span className="absolute right-2 top-1.5 text-sm">
                    <CompatMark level={compat} against="mục đích" />
                  </span>
                )}
                <div className="pr-4 text-sm font-medium">{s.name}</div>
                <div className="mt-0.5 text-xs text-slate-400">
                  {s.driver === 'speech'
                    ? 'dẫn bằng lời nói'
                    : s.bpm_range
                      ? `nhạc ${s.bpm_range[0]}–${s.bpm_range[1]} BPM`
                      : 'theo mô tả của bạn'}
                </div>
              </button>
            );
          })}
        </div>
        {options.muc_dich && <CompatLegend against="mục đích" />}
        {options.phong_cach === 'theo_mo_ta' && (
          <div className="mt-3">
            <textarea
              aria-label="Mô tả phong cách mong muốn"
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
              {noteWords} / tối thiểu {RULES.styles.theo_mo_ta.note_min_words} từ
            </div>
          </div>
        )}
      </div>

      {/* Mục đích */}
      <div id="chon-muc-dich" className="scroll-mt-20">
        <SectionTitle icon={Gauge}>Mục đích</SectionTitle>
        <div className="grid grid-cols-2 gap-2">
          {PURPOSE_IDS.map((id) => {
            const p = RULES.purposes[id];
            const selected = options.muc_dich === id;
            return (
              <button
                key={id}
                type="button"
                aria-pressed={selected}
                onClick={() => onChange({ muc_dich: id })}
                className={`rounded-lg border px-3 py-2.5 text-left transition ${
                  selected
                    ? 'border-amber-500 bg-amber-500/10 text-amber-100'
                    : 'border-slate-800 bg-slate-900/40 text-slate-300 hover:border-slate-600'
                }`}
              >
                <div className="text-sm font-medium">{p.name}</div>
                <div className="mt-0.5 text-xs text-slate-400">Lời kêu gọi: {p.cta}</div>
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
        <div id="chon-thoi-luong" className="scroll-mt-20">
          <SectionTitle icon={Gauge}>Thời lượng</SectionTitle>
          <div className="flex flex-wrap gap-2">
            {DURATION_IDS.map((d) => {
              const selected = options.do_dai_giay === d;
              const compat = options.phong_cach && RULES.compatStyleDuration[options.phong_cach][String(d)];
              const locked = d === 180 && longVideo === false;
              return (
                <button
                  key={d}
                  type="button"
                  disabled={locked}
                  aria-pressed={selected}
                  title={locked ? 'Video 3 phút chưa mở: đang chờ nghiệm thu bằng footage thật.' : undefined}
                  onClick={() =>
                    onChange({ do_dai_giay: d, so_ban_nhap: draftsFor(d, options.so_ban_nhap) })
                  }
                  className={`flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium transition ${
                    locked
                      ? 'cursor-not-allowed border-slate-800 bg-slate-900/20 text-slate-600'
                      : selected
                        ? 'border-amber-500 bg-amber-500/10 text-amber-100'
                        : 'border-slate-800 bg-slate-900/40 text-slate-300 hover:border-slate-600'
                  }`}
                >
                  {compat && <CompatMark level={compat} against="phong cách" />}
                  {d >= 60 ? `${d / 60} phút` : `${d}s`}
                </button>
              );
            })}
          </div>
          {options.phong_cach && <CompatLegend against="phong cách" />}
          {longVideo === false && (
            <p className="mt-2 text-xs text-slate-400">
              Video 3 phút chưa mở trên máy chủ này (chờ nghiệm thu bằng footage thật).
            </p>
          )}
        </div>

        <div>
          <SectionTitle icon={Copy}>Số bản nháp</SectionTitle>
          <div className="flex gap-2">
            {DRAFT_COUNTS.map((n) => (
              <button
                key={n}
                type="button"
                aria-pressed={options.so_ban_nhap === n}
                onClick={() => onChange({ so_ban_nhap: n })}
                title={
                  options.do_dai_giay === 180 && n === 3
                    ? 'Video 3 phút: 3 bản dễ bị cắt và tốn gấp đôi, nên chọn 2 bản.'
                    : undefined
                }
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

      {/* Pacing: read-only, implied by the style's driver (not a field the backend takes). */}
      <p className="text-xs text-slate-400">
        Nhịp dựng:{' '}
        <span className="font-semibold text-amber-300">
          {PACING_WORDS[pacing.mode]}
        </span>{' '}
        (tự suy ra từ phong cách)
        {pacing.note && <span className="mt-1 block text-amber-300/90">{pacing.note}</span>}
      </p>
    </div>
  );
}
