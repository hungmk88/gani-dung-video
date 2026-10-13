/**
 * Words and rules for the music picker (ticket 14/10/2026). Pure functions: no React, no fetch.
 * The list itself comes from GET /api/music; nothing is judged here except what to show.
 */

import type { MusicListInfo, MusicTrack } from '../types/rules.ts';
import { formatBpm } from './bpmSafety.ts';

/** How often the picker asks again while the machine is still measuring tracks. */
export const MUSIC_POLL_MS = 3000;
/** How long it waits before trying again after a failed answer (server not up yet, etc.). */
export const MUSIC_RETRY_MS = 10000;

export type MusicView = 'loading' | 'error' | 'not_configured' | 'empty' | 'ready';

/** Which state of the picker to draw. A real machine never falls back to invented tracks. */
export function musicView(list: MusicListInfo | null, error: string | null): MusicView {
  if (list === null) return error !== null ? 'error' : 'loading';
  if (!list.configured) return 'not_configured';
  return list.tracks.length === 0 ? 'empty' : 'ready';
}

/** Ask again only while something is still being measured. */
export function shouldPoll(list: MusicListInfo | null): boolean {
  return list !== null && list.configured && list.pending > 0;
}

/** The header badge: the real machine's folder, or the honest "demo" count of the mock. */
export function musicBadge(
  mode: 'mock' | 'real',
  list: MusicListInfo | null,
): { value: string; tone: 'slate' | 'emerald' | 'amber' } {
  const n = list?.tracks.length ?? 0;
  if (mode === 'mock') return { value: `${n}/24 bài (demo)`, tone: 'amber' };
  if (list === null) return { value: 'đang lấy danh sách…', tone: 'slate' };
  if (!list.configured) return { value: 'chưa đặt thư mục nhạc', tone: 'amber' };
  const measuring = list.pending > 0 ? `, đang đo ${list.pending}` : '';
  return { value: `${n} bài${measuring}`, tone: n > 0 ? 'emerald' : 'amber' };
}

/** '92 BPM', or why there is no number. */
export function bpmLabel(t: MusicTrack): string {
  if (t.bpm !== null) return `${formatBpm(t.bpm)} BPM`;
  return t.bpm_source === 'pending' ? 'đang đo nhịp' : 'không đo được nhịp';
}
