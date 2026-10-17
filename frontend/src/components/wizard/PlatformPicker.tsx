import type { PlatformInfo } from '../../types/rules';

/**
 * Where the video is posted (14/10/2026): one render per ticked platform, same script and cuts.
 * TikTok gets a call-to-action without "nhắn tin"/Zalo and no Zalo number on the end card.
 */
export function PlatformPicker({
  platforms,
  chosen,
  onChange,
}: {
  platforms: readonly PlatformInfo[];
  chosen: string[];
  onChange: (ids: string[]) => void;
}) {
  function toggle(id: string) {
    const next = chosen.includes(id) ? chosen.filter((x) => x !== id) : [...chosen, id];
    // Keep the rules.json order whatever the click order was.
    onChange(platforms.map((p) => p.id).filter((x) => next.includes(x)));
  }
  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        {platforms.map((p) => {
          const on = chosen.includes(p.id);
          return (
            <label
              key={p.id}
              className={`flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5 text-xs transition ${
                on ? 'border-amber-500 bg-amber-500/10' : 'border-slate-700 hover:border-slate-500'
              }`}
            >
              <input
                type="checkbox"
                checked={on}
                onChange={() => toggle(p.id)}
                className="mt-0.5 h-4 w-4 accent-amber-500"
              />
              <span className="space-y-0.5">
                <span className="block text-sm font-medium text-slate-100">{p.name}</span>
                <span className="block text-slate-400">
                  {p.zaloOnEndCard
                    ? 'Khung kết có số Zalo.'
                    : 'Khung kết không ghi số Zalo; câu kêu gọi mời bình luận, không mời nhắn tin.'}
                </span>
              </span>
            </label>
          );
        })}
      </div>
      <p className="text-xs text-slate-400">
        Mỗi nền tảng là một video riêng (cùng kịch bản, cùng nhịp cắt): chọn 2 nền tảng thì thời gian
        dựng gấp đôi, không tốn thêm tiền AI. Mỗi bản được duyệt riêng.
      </p>
      {chosen.length === 0 && (
        <p aria-live="polite" className="text-xs text-rose-300">
          Chọn ít nhất một nền tảng.
        </p>
      )}
    </div>
  );
}
