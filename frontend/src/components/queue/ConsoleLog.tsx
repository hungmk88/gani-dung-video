import { useEffect, useRef } from 'react';
import { api } from '../../services/api';
import type { ConsoleLine } from '../../types/rules';

const LEVEL_COLOR: Record<ConsoleLine['level'], string> = {
  info: 'text-slate-400',
  warn: 'text-amber-400',
  error: 'text-rose-400',
};

export function ConsoleLog({ lines }: { lines: ConsoleLine[] }) {
  const boxRef = useRef<HTMLDivElement>(null);
  // Keyed on the newest line, not the count: at the 400-line cap the count stops changing.
  const last = lines[lines.length - 1];
  const lastKey = last ? `${last.id ?? ''}|${last.ts}|${last.text}` : '';

  useEffect(() => {
    boxRef.current?.scrollTo({ top: boxRef.current.scrollHeight });
  }, [lastKey]);

  return (
    <div className="rounded-xl border border-slate-800 bg-black/40">
      <div className="border-b border-slate-800 px-3 py-2 text-xs font-medium uppercase tracking-wide text-slate-400">
        Nhật ký xử lý{api.mode === 'mock' ? ' (mô phỏng)' : ''}
      </div>
      <div ref={boxRef} className="max-h-48 overflow-y-auto scroll-thin px-3 py-2 font-mono-studio text-xs">
        {lines.length === 0 && <p className="text-slate-400">Chưa có dòng nào.</p>}
        {lines.map((l, i) => (
          <div key={l.id ?? `i${i}`} className="flex gap-2">
            <span className="shrink-0 text-slate-400">
              {new Date(l.ts).toLocaleTimeString('vi-VN')}
            </span>
            <span className={LEVEL_COLOR[l.level]}>{l.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
