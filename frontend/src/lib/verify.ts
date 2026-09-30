/**
 * Words and numbers for the footage warning (phase 6h) and the V/S check table (phase 6i).
 * Pure functions: no React, no fetch — unit-tested with plain `node --test`. The verdicts
 * (`tight`, each check's status, the final status) come from the server; nothing is judged here.
 */

import type { FootageInfo, VerifyReport, VerifyStatus } from '../types/rules.ts';
import { formatVnd } from './format.ts';

export interface FootageWarning {
  headline: string;
  body: string;
  /** Roles of the blocks that are tight on their own. */
  tightBlocks: string[];
}

/** The yellow "footage is only just enough" warning, or null when there is room to spare. */
export function footageWarning(f: FootageInfo): FootageWarning | null {
  if (!f.tight) return null;
  const needed = f.neededMin === f.neededMax ? `${f.neededMax}` : `${f.neededMin}–${f.neededMax}`;
  return {
    headline: 'Footage sát nhu cầu',
    body:
      `Có ${f.available} cảnh khác nhau, kịch bản cần ${needed} cảnh (nên có ít nhất ${f.warnBelow}). ` +
      'Claude dễ dùng lặp cảnh (V4) và phải sửa nhiều vòng, mỗi vòng tốn tiền. Bạn vẫn duyệt được.',
    tightBlocks: f.blocks.filter((b) => b.tight).map((b) => b.role),
  };
}

export const VERIFY_STATUS_LABEL: Record<VerifyStatus, string> = {
  pass: 'Đạt',
  fail: 'Trượt',
  fixed: 'Code tự sửa',
  warn: 'Cảnh báo',
  skipped: 'Chưa kiểm',
};

/** Status of one check in every round, in order; null where the round did not list it. */
export function roundStatus(v: VerifyReport, code: string): (VerifyStatus | null)[] {
  return v.rounds.map((r) => r.checks.find((c) => c.code === code)?.status ?? null);
}

export interface VerifyHeadline {
  tone: 'ok' | 'bad' | 'warn';
  text: string;
  /** "Đã tốn …" for the rounds that ran ("" when no money was spent). */
  cost: string;
}

/** One sentence on how the checks ended, e.g. "Trượt cả 3 vòng kiểm — còn lỗi: V4". */
export function verifyHeadline(v: VerifyReport): VerifyHeadline {
  const spent = v.rounds.reduce((sum, r) => sum + r.costVnd, 0);
  const cost = spent > 0 ? `Đã tốn ${formatVnd(Math.round(spent))} cho ${v.roundsUsed} vòng.` : '';
  const failing = v.checks.filter((c) => c.status === 'fail').map((c) => c.code);
  const left = failing.length > 0 ? ` — còn lỗi: ${failing.join(', ')}` : '';
  switch (v.finalStatus) {
    case 'passed':
      return {
        tone: 'ok',
        text: v.roundsUsed <= 1 ? 'Đạt ngay vòng 1' : `Đạt sau ${v.roundsUsed}/${v.maxRounds} vòng`,
        cost,
      };
    case 'failed':
      return {
        tone: 'bad',
        text:
          v.roundsUsed >= v.maxRounds
            ? `Trượt cả ${v.roundsUsed} vòng kiểm${left}`
            : `Trượt sau ${v.roundsUsed}/${v.maxRounds} vòng${left}`,
        cost,
      };
    case 'cost_cap':
      return {
        tone: 'bad',
        text: `Dừng vì chạm trần chi phí (đã xong ${v.roundsUsed} vòng)${left}`,
        cost,
      };
    case 'api_error':
      return {
        tone: 'bad',
        text: `Lỗi khi gọi Claude (đã xong ${v.roundsUsed} vòng)${left}`,
        cost,
      };
    default:
      return {
        tone: 'warn',
        text: `Job bị ngắt giữa chừng (đã xong ${v.roundsUsed} vòng)${left}`,
        cost,
      };
  }
}
