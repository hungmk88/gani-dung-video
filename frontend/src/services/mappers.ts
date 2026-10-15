/**
 * Pure mapping between the wire types of the local API (types/api.ts) and the UI types
 * (types/rules.ts), following docs/api-jobs.md §5. No fetch, no React — unit-tested with
 * plain `node --test` (relative value imports carry an explicit `.ts` for that reason).
 */

import type {
  ApiEvent,
  ApiFootage,
  ApiHealth,
  ApiJob,
  ApiJobDetail,
  ApiMusicList,
  ApiPipeline,
  ApiPreflightIssue,
  ApiPreflightResult,
  ApiProject,
  ApiQa,
  ApiStructure,
  ApiVerify,
  ApiVerifyCheck,
} from '../types/api.ts';
import {
  DURATION_IDS,
  MUSIC_MODE_IDS,
  PIPELINE_STEPS,
  PURPOSE_IDS,
  STYLE_IDS,
} from '../types/rules.ts';
import type {
  ApprovalInfo,
  ConsoleLine,
  DraftOutput,
  FootageInfo,
  HealthInfo,
  JobStatus,
  MusicListInfo,
  PipelineJobInfo,
  PipelineStep,
  PipelineStepId,
  PreflightIssue,
  PreflightResult,
  ProjectInfo,
  QaGroup,
  QaReport,
  RenderJob,
  Structure,
  StructureKind,
  VerifyCheck,
  VerifyFinal,
  VerifyReport,
  VerifyStatus,
  VideoOptions,
} from '../types/rules.ts';
import { latestReview, toReview } from '../lib/review.ts';

// rules.json → qc.max_rounds; the API does not echo it, so it is mirrored here.
const QC_MAX_ROUNDS = 3;

const JOB_STATUSES: readonly JobStatus[] = [
  'queued',
  'running',
  'awaiting_approval',
  'qa_review',
  'done',
  'error',
];
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
    aiKeyConfigured: h.ai_key_configured,
    longVideo: h.features ? h.features.long_video : null,
    budget: {
      limitVnd: h.budget.limit_vnd,
      spent24hVnd: h.budget.spent_24h_vnd,
      reservedVnd: h.budget.reserved_vnd,
    },
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
  return { id: e.id, ts: e.ts, level, text: e.text };
}

const TEXT_GROUP = 'Chữ & phụ đề (T6, T7, T8)';

/** Why T8 (Whisper listening to the finished video) left no row, by `qa.t8`. */
const T8_NOTES: Record<string, string> = {
  skipped_draft: 'T8: không áp dụng cho bản nháp',
  skipped_music: 'T8: không áp dụng cho chế độ nhạc',
  skipped_no_asr: 'T8: đã tắt kiểm tra lời',
  unavailable: 'T8: chưa có số đo (bản dựng cũ hoặc thiếu Whisper)',
};

/**
 * Label for the T8 status. `ran` and an absent field (older server) give none: the T8 row
 * itself says what was measured. An unknown status is treated as `unavailable`.
 */
export function t8Note(t8: string | undefined): string | undefined {
  if (t8 === undefined || t8 === 'ran') return undefined;
  return T8_NOTES[t8] ?? T8_NOTES.unavailable;
}

// Where each backend check code lands. V1–V9 have no per-check data yet (api-jobs.md §6.1).
const QA_GROUP_OF: Record<string, string> = {
  T1: 'Kỹ thuật video (T1, T2, T4, T5)',
  T2: 'Kỹ thuật video (T1, T2, T4, T5)',
  T4: 'Kỹ thuật video (T1, T2, T4, T5)',
  T5: 'Kỹ thuật video (T1, T2, T4, T5)',
  T6: TEXT_GROUP,
  T7: TEXT_GROUP,
  T8: TEXT_GROUP,
  T3: 'Âm thanh (T3)',
};
const QA_GROUP_ORDER = [
  'Kỹ thuật video (T1, T2, T4, T5)',
  TEXT_GROUP,
  'Âm thanh (T3)',
];

const VERIFY_STATUSES: readonly VerifyStatus[] = ['pass', 'fail', 'fixed', 'warn', 'skipped'];
const VERIFY_FINALS: readonly VerifyFinal[] = ['passed', 'failed', 'cost_cap', 'api_error', 'interrupted'];

function toVerifyCheck(c: ApiVerifyCheck): VerifyCheck {
  // A status this UI does not know must stay visible, so it reads as a warning, not a pass.
  const status = (VERIFY_STATUSES as readonly string[]).includes(c.status)
    ? (c.status as VerifyStatus)
    : 'warn';
  return { code: c.code, label: c.label, status, detail: c.detail };
}

/** V1–V9 / S1–S6 history of a draft job (phase 6i), tolerant of values a newer server adds. */
export function toVerify(v: ApiVerify): VerifyReport {
  return {
    mode: v.mode === 'speech' ? 'speech' : 'music',
    finalStatus: (VERIFY_FINALS as readonly string[]).includes(v.final_status)
      ? (v.final_status as VerifyFinal)
      : 'interrupted',
    roundsUsed: v.rounds_used,
    maxRounds: v.max_rounds,
    checks: v.checks.map(toVerifyCheck),
    rounds: v.rounds.map((r) => ({
      round: r.round,
      drafts: r.drafts,
      parsed: r.parsed,
      passed: r.passed,
      costVnd: r.cost_vnd,
      checks: r.checks.map(toVerifyCheck),
    })),
  };
}

const STRUCTURE_KINDS: readonly StructureKind[] = ['opening', 'chapter', 'closing'];

/** Hook / chapters / CTA of a 3-minute plan (phase 7g). An unknown kind shows as a chapter. */
export function toStructure(s: ApiStructure): Structure {
  return {
    durationSec: s.duration_sec,
    items: s.items.map((i) => ({
      kind: (STRUCTURE_KINDS as readonly string[]).includes(i.kind)
        ? (i.kind as StructureKind)
        : 'chapter',
      id: i.id,
      startSec: i.start_sec,
      endSec: i.end_sec,
      title: i.title,
    })),
  };
}

/** Footage room for the approval dialog (phase 6h). */
export function toFootage(f: ApiFootage): FootageInfo {
  return {
    available: f.scenes_available,
    neededMin: f.scenes_needed_min,
    neededMax: f.scenes_needed_max,
    warnBelow: f.warn_below,
    tight: f.tight,
    blocks: f.blocks.map((b) => ({
      role: b.role,
      candidates: b.candidates,
      beatsNeeded: b.beats_needed,
      scenesMin: b.scenes_min,
      scenesMax: b.scenes_max,
      tight: b.tight,
    })),
  };
}

/** `hasVerify`: the job's own V/S table is shown elsewhere, so no "no data" placeholder. */
export function toQaReport(qa: ApiQa, hasVerify = false): QaReport {
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
  const note = t8Note(qa.t8);
  const text = groups.find((g) => g.title === TEXT_GROUP);
  if (note && text) text.note = note;
  const visible = groups.filter((g) => g.checks.length > 0 || g.emptyNote || g.note);
  if (other.checks.length > 0) visible.push(other);
  if (!hasVerify) {
    visible.push({
      title: 'AI viết kịch bản (V1–V9)',
      checks: [],
      emptyNote:
        'Chưa có dữ liệu: kịch bản này chạy trước khi backend lưu kết quả V1–V9, ' +
        'hoặc job kịch bản không nằm trong chuỗi đang xem.',
    });
  }
  return {
    groups: visible,
    fixes: qa.fixes,
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
    noAsr: job.no_asr,
    title: job.title,
    options: optionsFromApi(job.options),
    status: toStatus(job.frontend_status),
    createdAt: job.created_at,
    etaSec: job.eta_sec,
    steps: toSteps(job),
    logLines: 'events' in job ? job.events.map(toConsoleLine) : [],
    previewUrl: newestUrl(group, 'preview'),
    finalUrl: newestUrl(group, 'final'),
    qa: job.qa ? toQaReport(job.qa, job.verify != null) : null,
    verify: job.verify ? toVerify(job.verify) : null,
    ...(job.structure ? { structure: toStructure(job.structure) } : {}),
    ...(latestReview(group) ? { review: latestReview(group) } : {}),
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

/* ───────────────────────── pipelines (phase 6c) ───────────────────────── */

const PIPELINE_PREFIX = 'p:';

export function pipelineCardId(pipelineId: number): string {
  return `${PIPELINE_PREFIX}${pipelineId}`;
}

/** `p:7` → 7; anything else → null (a plain job id). */
export function parsePipelineCardId(id: string): number | null {
  if (!id.startsWith(PIPELINE_PREFIX)) return null;
  const n = Number(id.slice(PIPELINE_PREFIX.length));
  return Number.isInteger(n) && n > 0 ? n : null;
}

/** Which job kind actually runs each pipeline step (docs/api-jobs.md §4b, "steps"). */
const STEP_OWNER: Record<PipelineStepId, string> = {
  inventory: 'analyze',
  nhan_dang: 'analyze',
  plan_compile: 'draft',
  pick: 'draft',
  ai_draft: 'draft',
  render_preview: 'render',
  qc_verify: 'render',
  final_export: 'render',
};

/** One stepper for the whole chain: each step is judged only by the jobs that own it. */
export function mergeSteps(jobs: ApiJob[]): PipelineStep[] {
  return PIPELINE_STEPS.map((id) => {
    const owners = jobs.filter((j) => j.kind === STEP_OWNER[id]);
    const seen = (owners.length > 0 ? owners : jobs).flatMap((j) =>
      j.steps.filter((s) => s.id === id).map((s) => ({ ...s })),
    );
    const pick = (status: PipelineStep['status']) => seen.find((s) => s.status === status);
    const failed = pick('error');
    const running = pick('running');
    const status: PipelineStep['status'] = failed
      ? 'error'
      : running
        ? 'running'
        : seen.length > 0 && seen.every((s) => s.status === 'done')
          ? 'done'
          : 'queued';
    const detail = (failed ?? running ?? seen.find((s) => s.detail !== null))?.detail;
    return { id, label: seen[0]?.label ?? id, status, detail: detail ?? undefined };
  });
}

/** Whole-chain status in the UI's vocabulary — same order as `PipelineView.status`. */
export function pipelineStatus(jobs: ApiJob[]): string {
  const of = (s: string) => jobs.some((j) => j.frontend_status === s);
  if (of('error')) return 'error';
  if (of('awaiting_approval')) return 'awaiting_approval';
  if (of('running')) return 'running';
  if (jobs.every((j) => j.frontend_status === 'queued')) return 'queued';
  if (of('queued')) return 'running'; // between two jobs
  if (of('qa_review')) return 'qa_review';
  return 'done';
}

function toApproval(jobs: ApiJob[]): ApprovalInfo | undefined {
  const j = jobs.find((x) => x.kind === 'draft' && x.needs_approval && x.cost);
  if (!j?.cost) return undefined;
  return {
    jobId: String(j.id),
    inputTokens: j.cost.input_tokens,
    worstCallVnd: j.cost.worst_call_vnd,
    capVnd: j.cost.cap_vnd,
    expiresAt: j.approval_expires_at ?? null,
    ...(j.footage ? { footage: toFootage(j.footage) } : {}),
  };
}

function toJobInfo(j: ApiJob): PipelineJobInfo {
  const status = toStatus(j.frontend_status);
  return {
    id: String(j.id),
    kind: j.kind,
    draft: j.draft,
    status,
    errorMsg: j.error_msg,
    retryable: status === 'error' || status === 'qa_review',
    spentVnd: j.cost?.spent_vnd ?? null,
    ...(j.review ? { review: toReview(j.review, j.id) } : {}),
  };
}

function toOutputs(jobs: ApiJob[], hasVerify: boolean): DraftOutput[] {
  const renders = jobs.filter((j) => j.kind === 'render');
  const letters = [...new Set(renders.map((j) => j.draft))].sort();
  return letters.map((draft) => {
    const own = renders.filter((j) => j.draft === draft);
    const withQa = own
      .filter((j) => j.qa !== null && j.status === 'completed')
      .sort((a, b) => (a.size === b.size ? b.id - a.id : a.size === 'final' ? -1 : 1))[0];
    const planned = [...own].sort((a, b) => b.id - a.id).find((j) => j.structure != null)?.structure;
    return {
      draft,
      ...(planned ? { structure: toStructure(planned) } : {}),
      previewUrl: newestUrl(own, 'preview'),
      finalUrl: newestUrl(own, 'final'),
      qa: withQa?.qa ? toQaReport(withQa.qa, hasVerify) : null,
    };
  });
}

const KIND_TAG: Record<string, string> = { analyze: 'phân tích', draft: 'kịch bản', render: 'dựng' };
const MAX_LOG_LINES = 400;

function pipelineLog(details: ApiJobDetail[]): ConsoleLine[] {
  return details
    .flatMap((d) =>
      d.events.map((e) => ({
        key: `${e.ts}|${String(d.id).padStart(8, '0')}|${String(e.id).padStart(8, '0')}`,
        line: {
          ...toConsoleLine(e),
          text: `[${KIND_TAG[d.kind] ?? d.kind}${d.draft ? ` ${d.draft}` : ''}] ${e.text}`,
        },
      })),
    )
    .sort((a, b) => (a.key < b.key ? -1 : 1))
    .slice(-MAX_LOG_LINES)
    .map((x) => x.line);
}

/**
 * One UI card for a whole analyze → draft → render chain. `details` (optional) carry the
 * console lines; the list view passes none and gets an empty log.
 */
export function toPipelineCard(p: ApiPipeline, details: ApiJobDetail[] = []): RenderJob {
  const jobs = [...p.jobs].sort((a, b) => a.id - b.id);
  // The chain has one draft job; the newest report wins should a retry have left several.
  const draftWithVerify = jobs
    .filter((j) => j.kind === 'draft' && j.verify != null)
    .sort((a, b) => b.id - a.id)[0];
  const outputs = toOutputs(jobs, draftWithVerify !== undefined);
  const first = outputs[0];
  const planJob = jobs.find((j) => Object.keys(j.options).length > 0);
  // The first draft's own structure carries its titles; before any draft, the plan's (draft job).
  const planned = jobs.find((j) => j.kind === 'draft' && j.structure != null)?.structure;
  const cardStructure =
    outputs.find((o) => o.structure)?.structure ?? (planned ? toStructure(planned) : undefined);
  // What the chain renders, so "Render lại" asks the same letters / size / ASR switch again.
  const renders = jobs.filter((j) => j.kind === 'render');
  const letters = new Set(renders.map((j) => j.draft));
  return {
    id: pipelineCardId(p.id),
    pipelineId: p.id,
    projectId: p.project_id ?? undefined,
    draft: letters.size === 1 ? [...letters][0] : 'all',
    size: renders.some((j) => j.size === 'final') ? 'final' : 'preview',
    noAsr: renders.some((j) => j.no_asr),
    title: `${p.project_name} — pipeline #${p.id}`,
    options: optionsFromApi(planJob?.options ?? {}),
    status: toStatus(p.status),
    createdAt: jobs[0]?.created_at ?? '',
    etaSec: jobs.find((j) => j.frontend_status === 'running')?.eta_sec ?? null,
    steps: mergeSteps(jobs),
    logLines: pipelineLog(details),
    approval: toApproval(jobs),
    jobs: jobs.map(toJobInfo),
    ...(latestReview(jobs) ? { review: latestReview(jobs) } : {}),
    outputs,
    previewUrl: first?.previewUrl ?? null,
    finalUrl: first?.finalUrl ?? null,
    qa: first?.qa ?? null,
    verify: draftWithVerify?.verify ? toVerify(draftWithVerify.verify) : null,
    ...(cardStructure ? { structure: cardStructure } : {}),
  };
}

// Newest first by the highest server job id a card contains.
function sortKey(c: RenderJob): number {
  const ids = (c.jobs ?? []).map((j) => Number(j.id));
  return ids.length > 0 ? Math.max(...ids) : Number(c.id);
}

/** The queue list: pipeline chains as one card each, lone jobs as before (newest first). */
export function buildCards(jobs: ApiJob[]): RenderJob[] {
  const chains = new Map<number, ApiJob[]>();
  const lone: ApiJob[] = [];
  for (const j of jobs) {
    if (j.pipeline_id != null) chains.set(j.pipeline_id, [...(chains.get(j.pipeline_id) ?? []), j]);
    else lone.push(j);
  }
  const cards: RenderJob[] = [];
  for (const [id, group] of chains) {
    const head = group[0];
    cards.push(
      toPipelineCard({
        id,
        project_id: head.project_id,
        project_name: head.project_name,
        status: pipelineStatus(group),
        needs_approval: group.some((j) => j.needs_approval === true),
        jobs: group,
      }),
    );
  }
  cards.push(...groupLatest(lone.filter((j) => j.kind === 'render')));
  cards.push(...lone.filter((j) => j.kind !== 'render').map((j) => toRenderJob(j, [])));
  return cards.sort((a, b) => sortKey(b) - sortKey(a));
}

/** Body of POST /api/jobs that renders a card again with the same draft / size / ASR switch. */
export function rerenderBody(job: RenderJob): { project_id: string; draft: string; final: boolean; no_asr: boolean } {
  if (!job.projectId) throw new Error('Job này không thuộc dự án nào nên không dựng lại được.');
  return {
    project_id: job.projectId,
    draft: job.draft || 'all',
    final: job.size === 'final',
    no_asr: job.noAsr ?? false,
  };
}

/** Body of POST /api/pipelines: `options` share the preflight shape, plus the run switches. */
export function pipelineBody(
  o: VideoOptions,
  run: { projectId: string; draft: string; final: boolean; noAsr: boolean; forceAnalyze: boolean },
): Record<string, unknown> {
  return {
    project_id: run.projectId,
    options: withoutFacts(preflightBody(o).options),
    draft: run.draft,
    final: run.final,
    no_asr: run.noAsr,
    force_analyze: run.forceAnalyze,
  };
}

/* ───────────────────────── preflight ───────────────────────── */

/** The machine's music folder as the picker shows it (GET /api/music). */
export function toMusicList(r: ApiMusicList): MusicListInfo {
  return {
    configured: r.configured,
    pending: r.pending,
    tracks: r.tracks.map((t) => ({
      id: t.file,
      title: t.title,
      bpm: t.bpm,
      duration_sec: t.duration_sec,
      genre: '',
      rights: t.rights,
      file: t.file,
      audio_url: `/media/music/${encodeURIComponent(t.file)}`,
      bpm_source: t.bpm_source,
    })),
  };
}

/** Facts the UI only knows to ask the preflight; the plan step measures them itself. */
function withoutFacts(options: Record<string, unknown>): Record<string, unknown> {
  const { nhac_bpm: _bpm, ...rest } = options;
  return rest;
}

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
      // Delivery naming (Dev Package 03): the queue hands both to `render`; blank = defaults.
      ...(o.san_pham ? { san_pham: o.san_pham } : {}),
      ...(o.chien_dich?.trim() ? { chien_dich: o.chien_dich.trim() } : {}),
      // Only sent when known / chosen: the server warns about a track its BPM cannot build with.
      ...(typeof o.nhac_bpm === 'number' ? { nhac_bpm: o.nhac_bpm } : {}),
      ...(o.bpm_override === true ? { bpm_override: true } : {}),
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
