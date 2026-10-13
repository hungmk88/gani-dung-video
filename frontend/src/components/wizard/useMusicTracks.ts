import { useEffect, useState } from 'react';
import { MUSIC_POLL_MS, MUSIC_RETRY_MS, shouldPoll } from '../../lib/music';
import { api } from '../../services/api';
import type { MusicListInfo } from '../../types/rules';

function messageOf(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/**
 * The machine's music list (GET /api/music). Asks again every few seconds while the machine is
 * still measuring tracks; a failed refresh keeps the list already on screen.
 */
export function useMusicTracks(enabled: boolean): { list: MusicListInfo | null; error: string | null } {
  const [list, setList] = useState<MusicListInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function load(): Promise<void> {
      try {
        const next = await api.listMusic();
        if (cancelled) return;
        setList(next);
        setError(null);
        if (shouldPoll(next)) timer = setTimeout(() => void load(), MUSIC_POLL_MS);
      } catch (e) {
        if (cancelled) return;
        setError(messageOf(e));
        timer = setTimeout(() => void load(), MUSIC_RETRY_MS);
      }
    }

    void load();
    return () => {
      cancelled = true;
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [enabled]);

  return { list, error };
}
