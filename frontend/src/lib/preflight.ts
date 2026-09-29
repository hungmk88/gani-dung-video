/**
 * Client-side Preflight Validator — mirrors domain/preflight.py's rules-only checks
 * (spec v2.1 §6.3: "chặn tổ hợp hỏng trước khi gọi AI, không tốn tiền"). Only the codes
 * that depend solely on option values are reproduced here (X1–X4, X9, X10, X11, X14,
 * X15 + the compat-matrix warnings); X5/X6 are shown as an ESTIMATE from the footage the
 * user dropped in (real numbers only come from the `inventory`/`scenes` pipeline steps,
 * which this browser-only prototype cannot run). X7, X8, X12, X13, X16–X18 need footage
 * facts, an AI call, or the render step and are intentionally left out rather than faked.
 */

import { RULES } from '../data/rules';
import type { FootageFile, PreflightIssue, PreflightResult, VideoOptions } from '../types/rules';
import { countWords } from './format';

// footage.min_usable_ratio from rules.json — raw footage should be this many times the
// scene count actually usable after blur/shake/duplicate filtering (phase 3b estimate).
const MIN_USABLE_RATIO = 1.3;

function issue(code: keyof typeof RULES.conflicts, vars: Record<string, string | number>): PreflightIssue {
  const c = RULES.conflicts[code];
  let msg = c.msg;
  for (const [k, v] of Object.entries(vars)) {
    msg = msg.replaceAll(`{${k}}`, String(v));
  }
  return { code, level: c.level, message: msg };
}

export interface PacingInfo {
  mode: 'loi' | 'nhac' | 'luoi_ao' | 'tu_dong';
  note: string | null;
}

/** Pacing is NOT a field the backend accepts directly — it's implied by the chosen
 *  style's `driver` (domain/options.py has no `pacing` key). This derives the same
 *  read-only badge the real pipeline would end up using, instead of offering a fake
 *  selector that doesn't map to anything rules.json understands. */
export function derivePacing(o: VideoOptions): PacingInfo {
  if (!o.phong_cach) return { mode: 'nhac', note: null };
  const style = RULES.styles[o.phong_cach as keyof typeof RULES.styles];
  if (style.driver === 'speech') return { mode: 'loi', note: null };
  if (style.driver === 'auto') {
    return { mode: 'tu_dong', note: "'Theo mô tả của tôi' để AI tự quyết định nhạc hay lưới ảo." };
  }
  // driver === 'music'
  if (o.nhac === 'khong_nhac') {
    if (style.driver_fallback === 'grid') {
      return { mode: 'luoi_ao', note: 'Không chọn nhạc — dùng lưới nhịp ảo (X11).' };
    }
    return {
      mode: 'nhac',
      note: `'${style.name}' dẫn nhịp theo nhạc — chưa chọn nhạc thì nhịp cắt sẽ không chính xác.`,
    };
  }
  return { mode: 'nhac', note: null };
}

export interface PreflightInput {
  options: VideoOptions;
  footage: FootageFile[];
}

export function isOptionsComplete(o: VideoOptions): boolean {
  return Boolean(o.phong_cach && o.muc_dich && o.do_dai_giay && o.nhac);
}

export function runPreflight({ options: o, footage }: PreflightInput): PreflightResult {
  const errors: PreflightIssue[] = [];
  const warnings: PreflightIssue[] = [];

  if (!isOptionsComplete(o)) {
    return { valid: false, errors: [], warnings: [], scenes_needed: null, scenes_estimated: null };
  }

  const style = RULES.styles[o.phong_cach as keyof typeof RULES.styles];
  const purpose = RULES.purposes[o.muc_dich as keyof typeof RULES.purposes];
  const dur = o.do_dai_giay;

  // X1 — style.max_duration
  if (style.max_duration !== undefined && dur > style.max_duration) {
    errors.push(issue('X1', {}));
  }
  // X2 — purpose.max_duration
  if (purpose.max_duration !== undefined && dur > purpose.max_duration) {
    errors.push(issue('X2', {}));
  }
  // X3 — khuyen_mai required fields
  if (o.muc_dich === 'khuyen_mai') {
    const missing: string[] = [];
    if (!o.khuyen_mai?.uu_dai.trim()) missing.push('mức ưu đãi');
    if (!o.khuyen_mai?.thoi_han.trim()) missing.push('thời hạn');
    if (missing.length > 0) errors.push(issue('X3', { missing: missing.join(', ') }));
  }
  // X4 — theo_mo_ta note length
  if (o.phong_cach === 'theo_mo_ta') {
    const n = countWords(o.ghi_chu);
    const min = style.note_min_words ?? 20;
    if (n < min) errors.push(issue('X4', { min, count: n }));
  }
  // X9 / warn — style×purpose compat matrix
  const spCompat = RULES.compatStylePurpose[style.id][purpose.id];
  if (spCompat === 'bad') {
    warnings.push(issue('X9', { style: style.name, purpose: purpose.name }));
  } else if (spCompat === 'warn') {
    warnings.push({
      code: 'W_STYLE_PURPOSE',
      level: 'warn',
      message: `'${style.name}' dùng được cho '${purpose.name}' nhưng không phải lựa chọn tối ưu.`,
    });
  }
  // duration compat matrix (warn only — 'bad' cases are already covered by X1)
  const sdCompat = RULES.compatStyleDuration[style.id][String(dur)];
  if (sdCompat === 'warn') {
    warnings.push({
      code: 'W_STYLE_DURATION',
      level: 'warn',
      message: `'${style.name}' ở ${dur}s dùng được nhưng không phải độ dài tối ưu.`,
    });
  }
  // X10 — sang_trong / ke_chuyen_nghe × 15s
  if ((style.id === 'sang_trong' || style.id === 'ke_chuyen_nghe') && dur === 15) {
    warnings.push(issue('X10', { style: style.name }));
  }
  // X14 — uploaded music without rights tick
  if (o.nhac === 'tai_len' && !o.nhac_quyen_su_dung) {
    errors.push(issue('X14', {}));
  }
  // X15 — 3 drafts × 180s
  if (o.so_ban_nhap === 3 && dur === 180) {
    warnings.push(issue('X15', {}));
  }
  // X11 — no music × style whose only defined fallback is the virtual grid (TVC)
  if (style.driver === 'music' && style.driver_fallback === 'grid' && o.nhac === 'khong_nhac') {
    warnings.push(issue('X11', { style: style.name, grid: '0.5' }));
  }
  // X7 — speech-driven style, footage speech unknown until real `inventory`/`transcribe` runs
  if (style.requires_speech) {
    warnings.push({
      code: 'X7',
      level: 'warn',
      message:
        'Chưa quét được lời nói trong footage (bước inventory/transcribe thật chưa chạy). ' +
        'Nếu footage không có ai nói trước ống kính, đổi phong cách hoặc tải voice-over.',
    });
  }

  // X5 / X6 — usable-scene estimate from dropped footage, NOT the real scanner
  const scenesNeeded = RULES.scenesNeeded[style.id][String(dur)];
  let scenesEstimated: number | null = null;
  if (footage.length > 0) {
    const totalRawSec = footage.reduce((sum, f) => sum + f.duration_sec, 0);
    const rawAtSceneLength = totalRawSec / style.scene_avg_sec;
    scenesEstimated = Math.floor(rawAtSceneLength / MIN_USABLE_RATIO);
    if (scenesEstimated < scenesNeeded) {
      errors.push(
        issue('X5', {
          usable: `~${scenesEstimated}`,
          need: scenesNeeded,
          short: Math.max(0, scenesNeeded - scenesEstimated),
        }),
      );
    } else if (scenesEstimated < scenesNeeded * 1.3) {
      warnings.push(
        issue('X6', { usable: `~${scenesEstimated}`, ideal: Math.ceil(scenesNeeded * 1.3) }),
      );
    }
  }

  return {
    valid: errors.length === 0,
    errors,
    warnings,
    scenes_needed: scenesNeeded,
    scenes_estimated: scenesEstimated,
  };
}
