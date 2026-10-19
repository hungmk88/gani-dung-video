import { PRODUCT_NAMES } from '../../data/products';
import { RULES } from '../../data/rules';
import type { VideoOptions } from '../../types/rules';

/**
 * Product and campaign of the delivered file: GANI_{sản-phẩm}_…mp4 inside
 * edit/delivery/{chiến-dịch}/. Both optional; the server applies the defaults.
 */
export function DeliveryFields({
  options,
  onChange,
}: {
  options: VideoOptions;
  onChange: (p: Partial<VideoOptions>) => void;
}) {
  const product = options.san_pham || 'chung';
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <label className="space-y-1.5 text-xs text-slate-300">
        <span className="block font-medium">Sản phẩm</span>
        <select
          value={product}
          onChange={(e) => onChange({ san_pham: e.target.value === 'chung' ? undefined : e.target.value })}
          className="w-full rounded-lg border border-slate-700 bg-slate-950/60 px-3 py-2 text-sm text-slate-100 focus:border-amber-500 focus:outline-none"
        >
          {RULES.deliveryProducts.map((p) => (
            <option key={p} value={p}>
              {PRODUCT_NAMES[p] ?? p}
            </option>
          ))}
        </select>
        <span className="block text-slate-400">
          Ghi vào tên file giao: GANI_<span className="font-mono-studio">{product}</span>_…mp4
        </span>
      </label>
      <label className="space-y-1.5 text-xs text-slate-300">
        <span className="block font-medium">Chiến dịch</span>
        <input
          value={options.chien_dich ?? ''}
          onChange={(e) => onChange({ chien_dich: e.target.value })}
          maxLength={60}
          placeholder="Bỏ trống = tên thư mục dự án"
          className="w-full rounded-lg border border-slate-700 bg-slate-950/60 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-amber-500 focus:outline-none"
        />
        <span className="block text-slate-400">Video giao nằm trong thư mục edit/delivery/&lt;chiến dịch&gt;/.</span>
      </label>
    </div>
  );
}
