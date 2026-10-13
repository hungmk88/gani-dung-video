/**
 * Words and verdicts for the "this track cannot build this video" warning (ticket 13/10/2026).
 *
 * Pure functions: no React, no fetch — unit-tested with plain `node --test`. The table comes
 * from the Python repo (`config/rules.json` → music.bpm_safe_by_style_duration, measured with
 * the real planner); `bpmSafety.test.ts` fails when `data/bpmSafe.ts` drifts from it. The
 * server remains the judge: it warns with the same table at preflight and stops at plan time.
 */

export type BpmLevel = 'OK' | 'WARNING' | 'BLOCKING';
export type BpmRange = [number, number];

export interface BpmSafeEntry {
  /** Plan more than ±gapPct % off the chosen length: delivery would be stopped. */
  blocked: BpmRange[];
  /** The tail is cut, within ±gapPct %: the video is built. */
  warning: BpmRange[];
}

export interface BpmSafeTable {
  /** Whole-BPM span the table was measured over; outside it nothing is claimed. */
  domain: BpmRange;
  /** qc.duration_plan_gap_pct as a percentage (10 = ±10 %). */
  gapPct: number;
  byStyleDuration: Record<string, Record<string, BpmSafeEntry>>;
}

export interface BpmCheck {
  level: BpmLevel;
  /** BPM ranges inside the domain that are not BLOCKING. */
  buildable: BpmRange[];
}

function inside(bpm: number, ranges: BpmRange[]): boolean {
  return ranges.some(([lo, hi]) => lo <= bpm && bpm <= hi);
}

/** The ranges of [lo, hi] that `blocked` leaves free. */
export function complement(blocked: BpmRange[], lo: number, hi: number): BpmRange[] {
  const out: BpmRange[] = [];
  let cur = lo;
  for (const [a, b] of [...blocked].sort((x, y) => x[0] - y[0])) {
    if (a > cur) out.push([cur, a - 1]);
    cur = Math.max(cur, b + 1);
  }
  if (cur <= hi) out.push([cur, hi]);
  return out;
}

/** '66–82, 88–99' (a single BPM stays a single number). */
export function formatRanges(ranges: BpmRange[]): string {
  return ranges.map(([a, b]) => (a === b ? `${a}` : `${a}–${b}`)).join(', ');
}

/**
 * Looks the track's BPM up (nearest whole BPM). Outside the measured domain, or for a style or
 * length the table does not list (speech style), the answer is OK: the table says nothing there.
 */
export function checkBpmSafe(t: BpmSafeTable, style: string, duration: number, bpm: number): BpmCheck {
  const [lo, hi] = t.domain;
  const entry = t.byStyleDuration[style]?.[String(duration)];
  const whole = Math.round(bpm);
  if (!entry || whole < lo || whole > hi) return { level: 'OK', buildable: [[lo, hi]] };
  const buildable = complement(entry.blocked, lo, hi);
  if (inside(whole, entry.blocked)) return { level: 'BLOCKING', buildable };
  if (inside(whole, entry.warning)) return { level: 'WARNING', buildable };
  return { level: 'OK', buildable };
}

export interface BpmNotice {
  level: 'WARNING' | 'BLOCKING';
  headline: string;
  body: string;
}

/** The yellow box shown under the track list, or null when the track fits. */
export function bpmNotice(
  t: BpmSafeTable,
  style: string,
  styleName: string,
  duration: number,
  bpm: number,
): BpmNotice | null {
  const chk = checkBpmSafe(t, style, duration, bpm);
  if (chk.level === 'OK') return null;
  const where = `Với phong cách "${styleName}" và độ dài ${duration} giây`;
  if (chk.level === 'BLOCKING') {
    return {
      level: 'BLOCKING',
      headline: `Bài này ở BPM ${bpm}`,
      body:
        `${where}, video sẽ không dựng được: kế hoạch lệch quá ±${t.gapPct}% độ dài đã chọn và bị ` +
        `chặn khi giao. BPM dựng được: ${formatRanges(chk.buildable)}. Chọn bài khác, hoặc đổi độ dài video.`,
    };
  }
  return {
    level: 'WARNING',
    headline: `Bài này ở BPM ${bpm}`,
    body:
      `${where}, phần cuối video sẽ bị cắt bớt cho đúng độ dài (trong ±${t.gapPct}%). ` +
      'Muốn không cắt, chọn bài có BPM nằm ngoài các đoạn cắt đuôi.',
  };
}
