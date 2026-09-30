import { AlertTriangle, CheckCircle2, Minus, Wrench, XCircle } from 'lucide-react';
import type { VerifyReport, VerifyStatus } from '../../types/rules';
import { roundStatus, VERIFY_STATUS_LABEL, verifyHeadline } from '../../lib/verify';

const STATUS_STYLE: Record<VerifyStatus, { icon: typeof XCircle; cls: string }> = {
  pass: { icon: CheckCircle2, cls: 'text-emerald-400' },
  fail: { icon: XCircle, cls: 'text-rose-400' },
  fixed: { icon: Wrench, cls: 'text-sky-400' },
  warn: { icon: AlertTriangle, cls: 'text-amber-400' },
  skipped: { icon: Minus, cls: 'text-slate-600' },
};

const TONE_CLS = {
  ok: 'border-emerald-800/50 bg-emerald-950/20 text-emerald-300',
  bad: 'border-rose-800/50 bg-rose-950/30 text-rose-300',
  warn: 'border-amber-700/50 bg-amber-950/20 text-amber-300',
} as const;

function StatusIcon({ status, title }: { status: VerifyStatus | null; title?: string }) {
  if (status === null) return <span className="text-slate-700" title="Vòng này không liệt kê">·</span>;
  const { icon: Icon, cls } = STATUS_STYLE[status];
  return (
    <span title={title ?? VERIFY_STATUS_LABEL[status]}>
      <Icon className={`inline h-3.5 w-3.5 ${cls}`} aria-label={VERIFY_STATUS_LABEL[status]} />
    </span>
  );
}

/**
 * V1–V9 (music) / S1–S6 (speech) results of the draft job, round by round (phase 6i): one row
 * per check, one column per round, so "V4 failed three rounds in a row" is visible at a glance.
 * The verdicts and Vietnamese labels come from the server (rules.verify_labels).
 */
export function VerifyPanel({ report }: { report: VerifyReport }) {
  const head = verifyHeadline(report);
  const range = report.mode === 'speech' ? 'S1–S6' : 'V1–V9';
  const showDetail = (c: VerifyReport['checks'][number]) => c.detail !== '' && c.status !== 'pass';
  return (
    <div className="space-y-3 rounded-xl border border-slate-800 bg-slate-900/40 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-200">Kiểm tra kịch bản AI ({range})</h3>
        <span className="text-[11px] text-slate-500">
          vòng {report.roundsUsed}/{report.maxRounds}
        </span>
      </div>

      <p className={`rounded-lg border px-3 py-2 text-xs ${TONE_CLS[head.tone]}`}>
        <strong>{head.text}.</strong> {head.cost}
      </p>

      {report.checks.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-[10px] uppercase tracking-wide text-slate-500">
              <tr>
                <th className="py-1 pr-2 font-medium">Mã</th>
                <th className="py-1 pr-2 font-medium">Phép kiểm</th>
                {report.rounds.map((r) => (
                  <th
                    key={r.round}
                    className="px-2 py-1 text-center font-medium"
                    title={`Vòng ${r.round}: hỏi bản ${r.drafts.join(', ') || '—'}${r.parsed ? '' : ' (câu trả lời không đọc được)'}`}
                  >
                    Vòng {r.round}
                  </th>
                ))}
                <th className="py-1 pl-2 font-medium">Kết quả vòng cuối</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {report.checks.map((c) => (
                <tr key={c.code} className={c.status === 'fail' ? 'bg-rose-950/10' : undefined}>
                  <td className="py-1.5 pr-2 font-mono-studio text-slate-500">{c.code}</td>
                  <td className="py-1.5 pr-2 text-slate-300">{c.label || '—'}</td>
                  {roundStatus(report, c.code).map((s, i) => (
                    <td key={i} className="px-2 py-1.5 text-center">
                      <StatusIcon status={s} />
                    </td>
                  ))}
                  <td className="py-1.5 pl-2 text-slate-400">
                    <span className="inline-flex items-center gap-1.5">
                      <StatusIcon status={c.status} />
                      <span>{VERIFY_STATUS_LABEL[c.status]}</span>
                    </span>
                    {showDetail(c) && <span className="mt-0.5 block text-[11px] text-slate-500">{c.detail}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-[11px] text-slate-600">
        Mỗi cột là một vòng gọi Claude; vòng sau chỉ hỏi lại bản bị lỗi. “Code tự sửa” là lỗi làm tròn phách mà code
        tự chỉnh, không gửi lại Claude.
      </p>
    </div>
  );
}
