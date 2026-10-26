/** Platforms a draft is rendered for (TikTok, Facebook Reels — 16/10/2026). Pure functions. */
import { RULES } from '../data/rules.ts';
import type { PlatformInfo } from '../types/rules.ts';

/** Short name shown next to a draft letter ("TikTok", "Reels"); an unknown id reads as itself. */
export function platformShort(id: string | undefined, known: readonly PlatformInfo[] = RULES.platforms): string {
  if (!id) return '';
  return known.find((p) => p.id === id)?.short ?? id;
}

/** "Bản A", "Bản A · TikTok". */
export function outputLabel(draft: string, platform: string | undefined, known?: readonly PlatformInfo[]): string {
  const p = platformShort(platform, known);
  return p ? `Bản ${draft} · ${p}` : `Bản ${draft}`;
}

/** Order of platforms as rules.json lists them; unknown ids last, in the order seen. */
export function platformOrder(ids: string[], known: readonly PlatformInfo[] = RULES.platforms): string[] {
  const rank = (id: string) => {
    const i = known.findIndex((p) => p.id === id);
    return i < 0 ? known.length : i;
  };
  return [...new Set(ids)].sort((a, b) => rank(a) - rank(b));
}

/** Platforms ticked when the wizard opens. */
export function defaultPlatforms(known: readonly PlatformInfo[]): string[] {
  return known.filter((p) => p.default).map((p) => p.id);
}
