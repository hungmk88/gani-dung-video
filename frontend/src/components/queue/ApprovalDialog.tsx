import { AlertTriangle, Clock3, Coins, Loader2, ShieldCheck, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { canApprove, estimateVnd, formatRemaining, suggestMaxVnd, validateMax } from '../../lib/approval';
import { formatVnd } from '../../lib/format';
import { api } from '../../services/api';
import type { HealthInfo, RenderJob } from '../../types/rules';

/**
 * Cost approval for the Claude call of a draft job — the only place in the app where a
 * person can make money be spent. Deliberately strict (decided with the owner, 30/09/2026):
 *  - the suggested cap is pre-filled (min(cap, 1.5 × worst call), rounded up to 500đ), never
 *    applied by itself;
 *  - "Xác nhận duyệt" stays disabled until the box is ticked FOR THE AMOUNT SHOWN; editing
 *    the amount afterwards un-ticks it;
 *  - no auto-approve, no "approve all".
 * Mount it with a `key` built from the estimate so a re-estimate resets consent.
 */
export function ApprovalDialog({
  job,
  onClose,
  onApproved,
}: {
  job: RenderJob;
  onClose: () => void;
  onApproved: (next: RenderJob) => void;
}) {
  const ap = job.approval;
  const [maxText, setMaxText] = useState(() => (ap ? String(suggestMaxVnd(ap.worstCallVnd, ap.capVnd)) : ''));
  const [confirmedFor, setConfirmedFor] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [health, setHealth] = useState<HealthInfo | null>(null);

  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 1000);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => {
      clearInterval(t);
      window.removeEventListener('keydown', onKey);
    };
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    api
      .health()
      .then((h) => {
        if (!cancelled) setHealth(h);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  if (!ap) return null;

  const maxVnd = maxText.trim() === '' ? Number.NaN : Number(maxText);
  const amountError = validateMax(maxVnd, ap.worstCallVnd, ap.capVnd);
  const check = canApprove({
    maxVnd,
    worstCallVnd: ap.worstCallVnd,
    capVnd: ap.capVnd,
    confirmedFor,
    expiresAt: ap.expiresAt,
    nowMs,
    submitting,
  });
  const ticked = confirmedFor !== null && confirmedFor === maxVnd;
  const b = health?.budget;

  async function confirm() {
    if (!check.ok) return;
    setSubmitting(true);
    setError(null);
    try {
      onApproved(await api.approve(job, maxVnd));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setConfirmedFor(null); // whatever changed on the server, ask for consent again
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Duyệt chi phí gọi Claude"
        onClick={(e) => e.stopPropagation()}
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-xl border border-amber-700/60 bg-slate-900 shadow-2xl"
      >
        <div className="flex items-start justify-between gap-3 border-b border-slate-800 px-4 py-3">
          <div>
            <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-100">
              <Coins className="h-4 w-4 text-amber-400" />
              Duyệt chi phí gọi Claude
            </h3>
            <p className="mt-0.5 text-[11px] text-slate-500">{job.title}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-slate-500 hover:bg-slate-800 hover:text-slate-200"
            aria-label="Đóng"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-4 p-4 text-xs text-slate-300">
          {health && !health.aiKeyConfigured && (
            <p className="flex items-start gap-2 rounded-lg border border-rose-800/50 bg-rose-950/30 px-3 py-2 text-rose-300">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              Máy dựng chưa có khóa API: job sẽ báo lỗi trước khi gọi Claude (chi phí 0đ).
            </p>
          )}

          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-lg border border-slate-800 bg-slate-950/40 p-3">
            <dt className="text-slate-500">Một lần gọi, tệ nhất</dt>
            <dd className="text-right font-semibold text-slate-100">{formatVnd(estimateVnd(ap.worstCallVnd))}</dd>
            <dt className="text-slate-500">Token vào (ước tính)</dt>
            <dd className="text-right">{ap.inputTokens.toLocaleString('vi-VN')}</dd>
            <dt className="text-slate-500">Trần cho video này</dt>
            <dd className="text-right">{formatVnd(ap.capVnd)}</dd>
            <dt className="flex items-center gap-1 text-slate-500">
              <Clock3 className="h-3 w-3" /> Hạn duyệt còn
            </dt>
            <dd className="font-mono-studio text-right">{formatRemaining(ap.expiresAt, nowMs)}</dd>
            <dt className="text-slate-500">Hạn mức ngày</dt>
            <dd className="text-right">
              {b === undefined
                ? '…'
                : b.limitVnd === null
                  ? 'không giới hạn'
                  : `${formatVnd(b.spent24hVnd + b.reservedVnd)} / ${formatVnd(b.limitVnd)}`}
            </dd>
          </dl>

          <p className="text-slate-500">
            Job có thể gọi Claude tối đa vài vòng để sửa lỗi, nhưng tổng không bao giờ vượt số bạn
            duyệt dưới đây. Ước tính đổi thì job quay lại chờ duyệt, không tốn đồng nào.
          </p>

          <label className="block">
            <span className="text-slate-400">Số tiền tối đa cho job này (đồng)</span>
            <input
              type="number"
              inputMode="numeric"
              min={1}
              step={500}
              value={maxText}
              onChange={(e) => {
                setMaxText(e.target.value);
                setConfirmedFor(null);
              }}
              className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-amber-500 focus:outline-none"
            />
            {amountError && <span className="mt-1 block text-rose-400">{amountError}</span>}
          </label>

          <label className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-slate-700 bg-slate-950/40 px-3 py-2.5">
            <input
              type="checkbox"
              checked={ticked}
              disabled={amountError !== null}
              onChange={(e) => setConfirmedFor(e.target.checked ? maxVnd : null)}
              className="mt-0.5 h-3.5 w-3.5 accent-amber-500"
            />
            <span className="text-slate-200">
              <ShieldCheck className="mr-1 inline h-3.5 w-3.5 text-amber-400" />
              Tôi đồng ý chi tối đa{' '}
              <strong>{Number.isFinite(maxVnd) ? maxVnd.toLocaleString('vi-VN') : '—'}đ</strong> cho
              lần gọi này
            </span>
          </label>

          {error && (
            <p className="flex items-start gap-2 rounded-lg border border-rose-800/50 bg-rose-950/30 px-3 py-2 text-rose-300">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {error}
            </p>
          )}

          <div className="flex items-center justify-between gap-3">
            <span className="text-[11px] text-slate-500">{check.ok ? '' : check.reason}</span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={onClose}
                className="rounded-lg border border-slate-700 px-3 py-2 text-xs font-medium text-slate-300 hover:border-slate-500"
              >
                Để sau
              </button>
              <button
                type="button"
                disabled={!check.ok}
                onClick={() => void confirm()}
                className={`flex items-center gap-1.5 rounded-lg px-4 py-2 text-xs font-semibold transition ${
                  check.ok
                    ? 'bg-gradient-to-r from-amber-500 to-orange-600 text-slate-950 hover:brightness-110'
                    : 'cursor-not-allowed bg-slate-800 text-slate-600'
                }`}
              >
                {submitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                Xác nhận duyệt
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
