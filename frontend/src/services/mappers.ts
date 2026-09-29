/**
 * Pure mapping between the wire types of the local API (types/api.ts) and the UI types
 * (types/rules.ts), following docs/api-jobs.md §5. No fetch, no React — unit-tested with
 * plain `node --test` (relative value imports carry an explicit `.ts` for that reason).
 */

import type {
  ApiEvent,
  ApiHealth,
  ApiJob,
  ApiJobDetail,
  ApiPreflightIssue,
  ApiPreflightResult,
  ApiProject,
  ApiQa,
} from '../types/api.ts';
import {
  DURATION_IDS,
  MUSIC_MODE_IDS,
  PIPELINE_STEPS,
  PURPOSE_IDS,
  STYLE_IDS,
} from '../types/rules.ts';
import type {
  ConsoleLine,
  HealthInfo,
  JobStatus,
  PipelineStep,
  PipelineStepId,
  PreflightIssue,
  PreflightResult,
  ProjectInfo,
  QaGroup,
  QaReport,
  RenderJob,
  VideoOptions,
} from '../types/rules.ts';

// rules.json → qc.max_rounds; the API does not echo it, so it is mirrored here.
const QC_MAX_ROUNDS = 3;

const JOB_STATUSES: readonly JobStatus[] = ['queued', 'running', 'qa_review', 'done', 'error'];
const STEP_IDS: readonly string[] = PIPELINE_STEPS;

export function basename(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() ?? path;
}

export function toProjectInfo(p: ApiProject): ProjectInfo {
  return {
    id: p.id,
    name: p.name,
    ready_to_render: p.ready_to_render,
    has_inventory: p.has_inventory,
    has_plan: p.has_plan,
    has_drafts: p.has_drafts,
  };
}

export function toHealthInfo(h: ApiHealth): HealthInfo {
  return {
    online: h.ok,
    daemonOnline: h.daemon_online,
    rulesVersion: h.rules_version,
    jobs: h.jobs,
    allowedRootsConfigured: h.allowed_roots_configured,
  };
}

/** plan.json's English option keys → the Vietnamese `VideoOptions` the UI uses. */
export function optionsFromApi(o: Record<string, unknown>): VideoOptions {
  const pick = <T extends string | number>(value: unknown, allowed: readonly T[]): T | '' =>
    allowed.includes(value as T) ? (value as T) : '';
  const drafts = Number(o.drafts);
  return {
    phong_cach: pick(o.style, STYLE_IDS),
    muc_dich: pick(o.purpose, PURPOSE_IDS),
    do_dai_giay: pick(o.duration, DURATION_IDS) || 0,
    so_ban_nhap: drafts === 2 || drafts === 3 ? drafts : 1,
    nhac: pick(o.music, MUSIC_MODE_IDS),
    nhac_file: null,
    nhac_quyen_su_dung: null,
    khuyen_mai: null,
    ghi_chu: '',
    pacing: 'nhac',
  };
}

function toStatus(s: string): JobStatus {
  return (JOB_STATUSES as readonly string[]).includes(s) ? (s as JobStatus) : 'error';
}

export function toConsoleLine(e: ApiEvent): ConsoleLine {
  const level = e.level === 'warn' || e.level === 'error' ? e.level : 'info';
  return { ts: e.ts, level, text: e.text };
}

// Where each backend check code lands. V1–V9 have no per-check data yet (api-jobs.md §6.1).
const QA_GROUP_OF: Record<string, string> = {
  T1: 'Kỹ thuật video (T1, T2, T4, T5)',
  T2: 'Kỹ thuật video (T1, T2, T4, T5)',
  T4: 'Kỹ thuật video (T1, T2, T4, T5)',
  T5: 'Kỹ thuật video (T1, T2, T4, T5)',
  T6: 'Chữ & phụ đề (T6, T7, T8)',
  T7: 'Chữ & phụ đề (T6, T7, T8)',
  T8: 'Chữ & phụ đề (T6, T7, T8)',
  T3: 'Âm thanh (T3)',
};
const QA_GROUP_ORDER = [
  'Kỹ thuật video (T1, T2, T4, T5)',
  'Chữ & phụ đề (T6, T7, T8)',
  'Âm thanh (T3)',
];

export function toQaReport(qa: ApiQa): QaReport {
  const groups: QaGroup[] = QA_GROUP_ORDER.map((title) => ({ title, checks: [] }));
  const other: QaGroup = { title: 'Khác', checks: [] };
  for (const c of qa.checks) {
    const group = groups.find((g) => g.title === QA_GROUP_OF[c.code]) ?? other;
    group.checks.push({ code: c.code, label: c.label, passed: c.passed, detail: c.detail });
  }
  const audio = groups[2];
  if (qa.lufs !== null && qa.true_peak_db !== null && audio.checks.length === 0) {
    audio.emptyNote = `Đo được ${qa.lufs} LUFS, đỉnh thật ${qa.true_peak_db} dBTP.`;
  }
  const visible = groups.filter((g) => g.checks.length > 0 || g.emptyNote);
  if (other.checks.length > 0) visible.push(other);
  visible.push({
    title: 'AI viết kịch bản (V1–V9)',
    checks: [],
    emptyNote: 'Chưa có dữ liệu thật: backend chưa lưu kết quả V1–V9 theo từng kiểm tra.',
  });
  return {
    groups: visible,
    rounds_used: qa.rounds,
    max_rounds: QC_MAX_ROUNDS,
    overall_pass: qa.passed,
  };
}

export function toSteps(job: ApiJob): PipelineStep[] {
  return job.steps
    .filter((s) => STEP_IDS.includes(s.id))
    .map((s) => ({
      id: s.id as PipelineStepId,
      label: s.label,
      status: s.status,
      detail: s.detail ?? undefined,
    }));
}

function newestUrl(jobs: ApiJob[], size: 'preview' | 'final'): string | null {
  const hit = jobs
    .filter((j) => j.size === size && j.video_url !== null && j.status === 'completed')
    .sort((a, b) => b.id - a.id)[0];
  return hit?.video_url ?? null;
}

/**
 * One UI job per (project, draft): docs/api-jobs.md §2 leaves merging preview + final to
 * the UI. `siblings` must hold every job of the same project + draft (it may include `job`).
 */
export function toRenderJob(job: ApiJob | ApiJobDetail, siblings: ApiJob[]): RenderJob {
  const group = siblings.some((s) => s.id === job.id) ? siblings : [...siblings, job];
  return {
    id: String(job.id),
    projectId: job.project_id ?? undefined,
    draft: job.draft,
    size: job.size,
    title: job.title,
    options: optionsFromApi(job.options),
    status: toStatus(job.frontend_status),
    createdAt: job.created_at,
    etaSec: job.eta_sec,
    steps: toSteps(job),
    logLines: 'events' in job ? job.events.map(toConsoleLine) : [],
    previewUrl: newestUrl(group, 'preview'),
    finalUrl: newestUrl(group, 'final'),
    qa: job.qa ? toQaReport(job.qa) : null,
  };
}

/** Latest job of every (project, draft) pair, newest first — the queue list. */
export function groupLatest(jobs: ApiJob[]): RenderJob[] {
  const byKey = new Map<string, ApiJob[]>();
  for (const j of jobs) {
    const key = `${j.project_id ?? j.project_name}|${j.draft}`;
    byKey.set(key, [...(byKey.get(key) ?? []), j]);
  }
  return [...byKey.values()]
    .map((group) => toRenderJob(group.sort((a, b) => b.id - a.id)[0], group))
    .sort((a, b) => Number(b.id) - Number(a.id));
}

/* ───────────────────────── preflight ───────────────────────── */

/** Body of POST /api/preflight (Appendix D keys; the server rejects unknown keys, so the
 *  UI-only `pacing` is left out and blanks become null). */
export function preflightBody(o: VideoOptions): { options: Record<string, unknown> } {
  return {
    options: {
      phong_cach: o.phong_cach,
      muc_dich: o.muc_dich,
      do_dai_giay: o.do_dai_giay,
      so_ban_nhap: o.so_ban_nhap,
      nhac: o.nhac,
      nhac_file: o.nhac_file ? basename(o.nhac_file) : null,
      nhac_quyen_su_dung: o.nhac === 'tai_len' ? Boolean(o.nhac_quyen_su_dung) : null,
      khuyen_mai: o.muc_dich === 'khuyen_mai' ? (o.khuyen_mai ?? null) : null,
      ghi_chu: o.ghi_chu.trim() === '' ? null : o.ghi_chu,
    },
  };
}

function toIssue(i: ApiPreflightIssue): PreflightIssue {
  const level = i.level === 'block' || i.level === 'auto' ? i.level : 'warn';
  return { code: i.code, level, message: i.msg };
}

export function toPreflightResult(r: ApiPreflightResult): PreflightResult {
  return {
    valid: r.valid,
    errors: r.errors.map(toIssue),
    warnings: r.warnings.map(toIssue),
    adjustments: r.adjustments.map(toIssue),
    scenes_needed: r.scenes_needed,
    scenes_estimated: null,
  };
}
