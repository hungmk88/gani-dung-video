import { Cpu, Database, KeyRound, Music2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { musicBadge } from '../../lib/music';
import { startPolling } from '../../lib/poll';
import { api } from '../../services/api';
import { useMusicTracks } from '../wizard/useMusicTracks';
import type { HealthInfo } from '../../types/rules';

function Badge({
  icon: Icon,
  label,
  value,
  tone = 'slate',
}: {
  icon: typeof Cpu;
  label: string;
  value: string;
  tone?: 'slate' | 'emerald' | 'amber';
}) {
  const toneClasses = {
    slate: 'border-slate-700 bg-slate-900/60 text-slate-300',
    emerald: 'border-emerald-800/60 bg-emerald-950/40 text-emerald-300',
    amber: 'border-amber-800/60 bg-amber-950/40 text-amber-300',
  }[tone];

  return (
    <div
      title={`${label}: ${value}`}
      className={`flex items-center gap-2 rounded-lg border px-2.5 py-1 text-xs sm:px-3 sm:py-1.5 ${toneClasses}`}
    >
      <Icon className="h-3.5 w-3.5 shrink-0" />
      {/* On a phone the icon names the badge; the label comes back from the sm breakpoint. */}
      <span className="hidden text-slate-400 sm:inline">{label}</span>
      <span className="font-medium">{value}</span>
    </div>
  );
}

export type TabId = 'wizard' | 'queue';

const HEALTH_POLL_MS = 5000;

// Daemon status badge: mock always reads "online"; real polls GET /api/health.
function useHealth(): HealthInfo | null {
  const [health, setHealth] = useState<HealthInfo | null>(null);
  useEffect(
    () =>
      startPolling(
        (signal) =>
          api
            .health()
            .then((h) => !signal.aborted && setHealth(h))
            .catch(() => !signal.aborted && setHealth(null)),
        HEALTH_POLL_MS,
      ),
    [],
  );
  return health;
}

function queueBadge(health: HealthInfo | null): { value: string; tone: 'emerald' | 'amber' | 'slate' } {
  if (api.mode === 'mock') return { value: 'bản mô phỏng', tone: 'slate' };
  if (health === null) return { value: 'không kết nối được máy dựng', tone: 'amber' };
  if (!health.daemonOnline) return { value: 'máy dựng đang TẮT — chạy lệnh daemon', tone: 'amber' };
  const waiting = (health.jobs.queued ?? 0) + (health.jobs.processing ?? 0);
  const approvals = health.jobs.awaiting_approval ?? 0;
  const tail = approvals > 0 ? ` · ${approvals} chờ duyệt chi phí` : '';
  return { value: `đang chạy · ${waiting} video chờ/đang dựng${tail}`, tone: approvals > 0 ? 'amber' : 'emerald' };
}

export function Header({ tab, onTabChange }: { tab: TabId; onTabChange: (t: TabId) => void }) {
  const health = useHealth();
  const queue = queueBadge(health);
  const { list } = useMusicTracks(true);
  const music = musicBadge(api.mode, list);
  return (
    <>
      <header className="border-b border-slate-800 bg-slate-950">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-amber-500 to-orange-600 font-bold text-slate-950">
              G
            </div>
            <div className="leading-tight">
              <div className="text-sm font-semibold tracking-wide text-slate-100">
                GANI FOODY <span className="text-amber-400">AI STUDIO</span>
              </div>
              <div className="text-xs text-slate-400">Dựng video dọc tự động</div>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Badge icon={Database} label="Máy dựng" value={queue.value} tone={queue.tone} />
            {health && !health.aiKeyConfigured && (
              <Badge icon={KeyRound} label="Khóa Claude" value="chưa cài — chưa viết được kịch bản" tone="amber" />
            )}
            <Badge icon={Music2} label="Thư viện nhạc" value={music.value} tone={music.tone} />
            <div className="hidden sm:block">
              <Badge icon={Cpu} label="Thiết bị nhận diện hình" value="tự chọn: MPS (Mac) / CPU (Dell)" />
            </div>
          </div>
        </div>
      </header>

      {/* Only the tabs stay on screen while scrolling: the whole header, badges included,
          took a third of a phone screen (273 of 844 px). */}
      <div className="sticky top-0 z-20 border-b border-slate-800 bg-slate-950/95 backdrop-blur">
        <nav className="mx-auto flex max-w-7xl gap-1 px-4 py-2 text-sm sm:px-6">
          {(
            [
              { id: 'wizard', label: 'Tạo video mới' },
              { id: 'queue', label: 'Video đang làm' },
            ] as const
          ).map((t) => (
            <button
              key={t.id}
              type="button"
              aria-current={tab === t.id ? 'page' : undefined}
              onClick={() => onTabChange(t.id)}
              className={`flex-1 rounded-md px-4 py-2 font-medium transition sm:flex-none ${
                tab === t.id
                  ? 'bg-amber-500 text-slate-950 shadow'
                  : 'text-slate-300 hover:bg-slate-800 hover:text-slate-100'
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </div>
    </>
  );
}
