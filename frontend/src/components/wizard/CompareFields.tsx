import type { SoSanhFields, VideoOptions } from '../../types/rules';

const EMPTY: SoSanhFields = { tren: '', duoi: '', nhan_tren: '', nhan_duoi: '' };
const INPUT =
  'w-full rounded-lg border border-slate-700 bg-slate-950/60 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-amber-500 focus:outline-none';

/**
 * Split screen (26/10/2026, rules.json → effects.split_screen): two clips of the footage folder
 * stacked in the first product shot, each with its label. Both clip names or none; the plan
 * step refuses a name that is not in the folder.
 */
export function CompareFields({
  options,
  onChange,
  clips,
}: {
  options: VideoOptions;
  onChange: (p: Partial<VideoOptions>) => void;
  /** Clip file names of the chosen project; [] = not known yet, the name is typed instead. */
  clips: readonly string[];
}) {
  const s = options.so_sanh ?? EMPTY;
  const set = (k: keyof SoSanhFields, v: string) => onChange({ so_sanh: { ...s, [k]: v } });
  const clip = (k: 'tren' | 'duoi', label: string, placeholder: string) =>
    clips.length === 0 ? (
      field(k, label, placeholder)
    ) : (
      <label className="space-y-1.5 text-xs text-slate-300">
        <span className="block font-medium">{label}</span>
        <select value={s[k]} onChange={(e) => set(k, e.target.value)} className={INPUT}>
          <option value="">— chọn clip —</option>
          {clips.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </label>
    );
  const field = (k: keyof SoSanhFields, label: string, placeholder: string, max?: number) => (
    <label className="space-y-1.5 text-xs text-slate-300">
      <span className="block font-medium">{label}</span>
      <input
        value={s[k]}
        onChange={(e) => set(k, e.target.value)}
        maxLength={max}
        placeholder={placeholder}
        className={INPUT}
      />
    </label>
  );
  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-400">
        Tùy chọn. Cảnh đầu của phần Sản phẩm chia đôi: clip trên và clip dưới chạy cùng lúc, mỗi
        nửa một nhãn.{' '}
        {clips.length === 0 ? 'Ghi đúng tên file trong thư mục footage (danh sách clip có sau bước phân tích). ' : ''}
        So sánh với hàng hay thương hiệu khác thì đưa NVH xem trước.
      </p>
      <div className="grid gap-4 sm:grid-cols-2">
        {clip('tren', 'Clip nửa trên', 'ví dụ tu-lam.mp4')}
        {field('nhan_tren', 'Nhãn nửa trên', 'ví dụ Tự làm: 60 phút', 40)}
        {clip('duoi', 'Clip nửa dưới', 'ví dụ dung-gani.mp4')}
        {field('nhan_duoi', 'Nhãn nửa dưới', 'ví dụ Dùng GANI: 5 phút', 40)}
      </div>
    </div>
  );
}
