/**
 * The timeline of a 3-minute video (phase 7g): hook, 3–4 chapters, CTA. Pure functions, no
 * React, no fetch — unit-tested with plain `node --test`. All numbers come from the server's
 * plan; this file only turns them into widths, labels and click targets.
 */

import type { Structure, StructureItem } from '../types/rules.ts';

/** A stretch narrower than this share of the bar keeps its place but hides its label. */
export const MIN_LABEL_PCT = 8;

const NAMES: Record<string, string> = {
  hook: 'Mở đầu',
  cta: 'Kêu gọi',
  mo: 'Mở đầu',
  logo: 'Logo',
  ve_sinh: 'Vệ sinh',
  quy_trinh: 'Quy trình',
  bang_chung: 'Bằng chứng',
  nhu_cau: 'Nhu cầu',
  san_pham: 'Sản phẩm',
  con_nguoi_1: 'Con người (1)',
  con_nguoi_2: 'Con người (2)',
  gia_tri: 'Giá trị',
};

/** Vietnamese name of a chapter / hook / CTA id; an unknown id reads as its words. */
export function chapterName(id: string): string {
  return NAMES[id] ?? id.replace(/_/g, ' ');
}

/** m:ss, rounded down like a video player. */
export function clock(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

export interface Segment {
  id: string;
  kind: StructureItem['kind'];
  label: string;
  pct: number;
  startSec: number;
  endSec: number;
  title: string | null;
  showLabel: boolean;
}

export function timelineSegments(s: Structure): Segment[] {
  const total = s.durationSec > 0 ? s.durationSec : 1;
  return s.items.map((i) => {
    const pct = ((i.endSec - i.startSec) / total) * 100;
    return {
      id: i.id,
      kind: i.kind,
      label: chapterName(i.id),
      pct,
      startSec: i.startSec,
      endSec: i.endSec,
      title: i.title,
      showLabel: pct >= MIN_LABEL_PCT,
    };
  });
}

/** The chapters that have a title, at the moment it appears on screen (top of the frame). */
export function titleLane(s: Structure): { startSec: number; title: string }[] {
  return s.items
    .filter((i) => i.kind === 'chapter' && i.title !== null && i.title !== '')
    .map((i) => ({ startSec: i.startSec, title: i.title as string }));
}

/** Where a click on a chapter seeks the video to, kept inside the file. */
export function seekTarget(startSec: number, videoSec: number): number {
  const lo = Math.max(0, startSec);
  return Number.isFinite(videoSec) && videoSec > 0 ? Math.min(lo, videoSec) : lo;
}

/**
 * Drafts for a length (decision Q3 of 01/10/2026): 3 drafts of a 3-minute video risk a cut-off
 * answer and cost more, so picking 3 minutes steps 3 drafts down to 2. Other lengths unchanged.
 */
export function draftsFor(durationSec: number, drafts: 1 | 2 | 3): 1 | 2 | 3 {
  return durationSec === 180 && drafts === 3 ? 2 : drafts;
}
