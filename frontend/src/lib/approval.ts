/**
 * Cost-approval rules of the draft job (docs/api-jobs.md §4b). Pure functions: no React,
 * no fetch, nothing imported — unit-tested with plain `node --test`.
 *
 * The one place the UI can make a person spend money, so it stays small and strict:
 * the amount is never approved implicitly; consent is tied to the exact amount shown.
 */

/** Suggested amounts and the ceiling are rounded to this many đồng. */
export const ROUND_TO_VND = 500;
/** Headroom over one worst-case call offered as the default (a job may call up to 3 times). */
export const SUGGEST_FACTOR = 1.5;

/** min(cap, 1.5 × worst call), rounded UP to 500đ (never below one worst call), capped again. */
export function suggestMaxVnd(worstCallVnd: number, capVnd: number): number {
  const raw = Math.min(capVnd, SUGGEST_FACTOR * worstCallVnd);
  return Math.min(capVnd, Math.ceil(raw / ROUND_TO_VND) * ROUND_TO_VND);
}

/** The estimate the person saw, rounded to whole đồng — the server compares it back. */
export function estimateVnd(worstCallVnd: number): number {
  return Math.round(worstCallVnd);
}

/** Message (Vietnamese) when `maxVnd` is not acceptable, else null. Mirrors the server rules. */
export function validateMax(maxVnd: number, worstCallVnd: number, capVnd: number): string | null {
  if (!Number.isFinite(maxVnd) || !Number.isInteger(maxVnd) || maxVnd <= 0) {
    return 'Nhập số đồng nguyên lớn hơn 0.';
  }
  if (maxVnd < worstCallVnd) {
    return `Thấp hơn một lần gọi tệ nhất (${estimateVnd(worstCallVnd).toLocaleString('vi-VN')}đ).`;
  }
  if (maxVnd > capVnd) {
    return `Vượt trần cho video này (${capVnd.toLocaleString('vi-VN')}đ).`;
  }
  return null;
}

export function isExpired(expiresAt: string | null, nowMs: number): boolean {
  return expiresAt !== null && Date.parse(expiresAt) <= nowMs;
}

/** "23:59:12" left before the approval lapses; "00:00:00" once expired. */
export function formatRemaining(expiresAt: string | null, nowMs: number): string {
  if (expiresAt === null) return '—';
  const sec = Math.max(0, Math.floor((Date.parse(expiresAt) - nowMs) / 1000));
  const p = (n: number): string => String(n).padStart(2, '0');
  return `${p(Math.floor(sec / 3600))}:${p(Math.floor((sec % 3600) / 60))}:${p(sec % 60)}`;
}

export interface ApprovalCheck {
  maxVnd: number;
  worstCallVnd: number;
  capVnd: number;
  /** The amount the tick box was ticked for; null when unticked. */
  confirmedFor: number | null;
  expiresAt: string | null;
  nowMs: number;
  submitting: boolean;
}

/** Whether the "Xác nhận duyệt" button may be enabled, with the reason when it may not. */
export function canApprove(c: ApprovalCheck): { ok: boolean; reason: string | null } {
  if (isExpired(c.expiresAt, c.nowMs)) return { ok: false, reason: 'Đã hết hạn duyệt.' };
  const bad = validateMax(c.maxVnd, c.worstCallVnd, c.capVnd);
  if (bad) return { ok: false, reason: bad };
  // Consent belongs to one amount: editing the amount after ticking un-ticks by design.
  if (c.confirmedFor !== c.maxVnd) return { ok: false, reason: 'Tick xác nhận số tiền để tiếp tục.' };
  if (c.submitting) return { ok: false, reason: 'Đang gửi…' };
  return { ok: true, reason: null };
}

/** Body of POST /api/jobs/{id}/approve. */
export function approveBody(maxVnd: number, worstCallVnd: number): { max_vnd: number; estimate_vnd: number } {
  return { max_vnd: maxVnd, estimate_vnd: estimateVnd(worstCallVnd) };
}
