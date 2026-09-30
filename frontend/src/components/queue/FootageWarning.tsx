import { TriangleAlert } from 'lucide-react';
import { footageWarning } from '../../lib/verify';
import type { FootageInfo } from '../../types/rules';

/**
 * Yellow warning shown before a person approves a Claude call when the picked footage barely
 * covers the plan (phase 6h). Never blocks: approving stays possible. Renders nothing when the
 * server says there is room to spare, or sent no numbers at all.
 */
export function FootageWarning({ footage, compact = false }: { footage?: FootageInfo; compact?: boolean }) {
  const w = footage ? footageWarning(footage) : null;
  if (!w) return null;
  return (
    <div
      role="alert"
      className="flex items-start gap-2 rounded-lg border border-yellow-600/60 bg-yellow-950/30 px-3 py-2 text-xs text-yellow-200"
    >
      <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-yellow-400" />
      <div>
        <strong className="text-yellow-300">{w.headline}.</strong> {w.body}
        {!compact && w.tightBlocks.length > 0 && (
          <span className="mt-1 block text-[11px] text-yellow-300/80">
            Khối thiếu dư nhất: {w.tightBlocks.join(', ')}.
          </span>
        )}
      </div>
    </div>
  );
}
