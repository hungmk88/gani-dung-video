import { Cpu, Database, KeyRound, Music2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { musicBadge } from '../../lib/music';
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
    <div className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs ${toneClasses}`}>
      <Icon className="h-3.5 w-3.5 shrink-0" />
      <span className="text-slate-500">{label}</span>
      <span className="font-medium">{value}</span>
    </div>
  );
}

export type TabId = 'wizard' | 'queue';

const HEALTH_POLL_MS = 5000;

// Daemon status badge: mock always reads "online"; real polls GET /api/health.
function useHealth(): HealthInfo | null {
  const [health, setHealth] = useState<HealthInfo | null>(null);
  useEffect(() => {
    let cancelled = false;
    const tick = () =>
      api
        .health()
        .then((h) => !cancelled && setHealth(h))
        .catch(() => !cancelled && setHealth(null));
    void tick();
    const t = setInterval(tick, HEALTH_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);
  return health;
}

function queueBadge(health: HealthInfo | null): { value: string; tone: 'emerald' | 'amber' | 'slate' } {
  if (api.mode === 'mock') return { value: 'Daemon · SQLite Queue (mô phỏng)', tone: 'slate' };
  if (health === null) return { value: 'Máy chủ dựng: không kết nối', tone: 'amber' };
  if (!health.daemonOnline) return { value: 'Máy chủ OK · daemon TẮT', tone: 'amber' };
  const waiting = (health.jobs.queued ?? 0) + (health.jobs.processing ?? 0);
  const approvals = health.jobs.awaiting_approval ?? 0;
  const tail = approvals > 0 ? ` · ${approvals} chờ duyệt chi phí` : '';
  return { value: `Daemon online · ${waiting} job đang chờ/chạy${tail}`, tone: approvals > 0 ? 'amber' : 'emerald' };
}

export function Header({ tab, onTabChange }: { tab: TabId; onTabChange: (t: TabId) => void }) {
  const health = useHealth();
  const queue = queueBadge(health);
  const { list } = useMusicTracks(true);
  const music = musicBadge(api.mode, list);
  return (
    <header className="sticky top-0 z-20 border-b border-slate-800 bg-slate-950/95 backdrop-blur">
      <div className="mx-auto flex max-w-7xl flex-col gap-3 px-4 py-3 sm:px-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-amber-500 to-orange-600 font-bold text-slate-950">
              G
            </div>
            <div className="leading-tight">
              <div className="text-sm font-semibold tracking-wide text-slate-100">
                GANI FOODY <span className="text-amber-400">AI STUDIO</span>
              </div>
              <div className="text-[11px] text-slate-500">
                Video Editing Pipeline v2.1 — dựng dọc tự động
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Badge icon={Database} label="Hàng đợi" value={queue.value} tone={queue.tone} />
            {health && !health.aiKeyConfigured && (
              <Badge icon={KeyRound} label="Khóa API Claude" value="chưa cấu hình — không thể gọi AI" tone="amber" />
            )}
            <Badge
              icon={Music2}
              label="Thư viện nhạc"
              value={music.value}
              tone={music.tone}
            />
            <Badge icon={Cpu} label="Thiết bị CLIP/thẩm mỹ" value="auto: MPS (Mac) / CPU int8 (Dell)" />
          </div>
        </div>

        <nav className="flex gap-1 rounded-lg border border-slate-800 bg-slate-900/50 p-1 text-sm">
          {(
            [
              { id: 'wizard', label: 'Tạo video mới (Studio Wizard)' },
              { id: 'queue', label: 'Hàng đợi & Tiến trình (Job Queue)' },
            ] as const
          ).map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => onTabChange(t.id)}
              className={`flex-1 rounded-md px-4 py-2 font-medium transition sm:flex-none ${
                tab === t.id
                  ? 'bg-amber-500 text-slate-950 shadow'
                  : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </div>
    </header>
  );
}
