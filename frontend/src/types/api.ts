/**
 * Wire types of the local web API (phase 6b) — a hand-copy of `domain/api_models.py`,
 * contract in `docs/api-jobs.md`. Field names stay exactly as the server sends them.
 * Only `import type` here so the mapper tests can run under plain `node --test`.
 */

export interface ApiErrorBody {
  /** `pipeline_id` / `job_id` only on 409 `pipeline_active` / `project_locked`. */
  error: { code: string; message: string; pipeline_id?: number; job_id?: number };
}

export interface ApiProject {
  id: string;
  name: string;
  has_inventory: boolean;
  has_plan: boolean;
  has_drafts: boolean;
  ready_to_render: boolean;
}

export interface ApiPlatform {
  id: string;
  name: string;
  short: string;
  zalo_on_end_card: boolean;
  default: boolean;
}

export interface ApiFeatures {
  /** 3-minute videos are open (switched on after the real acceptance run, phase 7e). */
  long_video: boolean;
  /** Platforms the server renders for (14/10/2026); absent on older servers. */
  platforms?: ApiPlatform[];
}

export interface ApiHealth {
  ok: boolean;
  rules_version: string;
  daemon_online: boolean;
  daemon_pid: number | null;
  daemon_stopping: string | null;
  jobs: Record<string, number>;
  allowed_roots_configured: boolean;
  /** Yes/no only — the server never returns the key itself. */
  ai_key_configured: boolean;
  budget: { limit_vnd: number | null; spent_24h_vnd: number; reserved_vnd: number };
  /** Absent on servers older than phase 7g. */
  features?: ApiFeatures;
}

export interface ApiEvent {
  id: number;
  ts: string;
  level: string;
  text: string;
}

export interface ApiStep {
  id: string;
  label: string;
  status: 'done' | 'running' | 'queued' | 'error';
  detail: string | null;
}

export interface ApiQaCheck {
  code: string;
  label: string;
  passed: boolean;
  detail: string;
}

export interface ApiQa {
  passed: boolean;
  rounds: number;
  fixes: string[];
  loudness_two_pass: boolean;
  lufs: number | null;
  true_peak_db: number | null;
  checks: ApiQaCheck[];
  /** Whisper post-check (T8) status: ran | skipped_draft | skipped_music | skipped_no_asr |
   *  unavailable. Optional: servers older than phase 6f do not send it. */
  t8?: string;
}

export interface ApiCost {
  input_tokens: number;
  worst_call_vnd: number;
  cap_vnd: number;
  approved_vnd: number | null;
  spent_vnd: number | null;
}

export interface ApiFootageBlock {
  role: string;
  candidates: number;
  beats_needed: number;
  max_fill: number;
  scenes_min: number;
  scenes_max: number;
  tight: boolean;
}

/** How much room the picked footage leaves a draft waiting for approval (phase 6h). */
export interface ApiFootage {
  scenes_available: number;
  scenes_needed_min: number;
  scenes_needed_max: number;
  warn_below: number;
  tight: boolean;
  blocks: ApiFootageBlock[];
}

export interface ApiVerifyCheck {
  code: string;
  label: string;
  /** pass | fail | fixed | warn | skipped */
  status: string;
  detail: string;
}

export interface ApiVerifyRound {
  round: number;
  drafts: string[];
  parsed: boolean;
  passed: boolean;
  cost_vnd: number;
  checks: ApiVerifyCheck[];
}

/** V1–V9 (music) / S1–S6 (speech) results of a finished draft job (phase 6i). */
export interface ApiVerify {
  mode: string;
  /** passed | failed | cost_cap | api_error | interrupted */
  final_status: string;
  rounds_used: number;
  max_rounds: number;
  checks: ApiVerifyCheck[];
  rounds: ApiVerifyRound[];
}

export interface ApiStructureItem {
  /** opening (hook) | chapter | closing (CTA) */
  kind: string;
  id: string;
  start_sec: number;
  end_sec: number;
  /** Chapter title written by Claude; null for opening/closing or before the draft exists. */
  title: string | null;
}

/** Hook / chapters / CTA of a 3-minute plan on its timeline (phase 7g). */
export interface ApiStructure {
  duration_sec: number;
  items: ApiStructureItem[];
}

/** `review` of a job: GET /api/reviews, POST .../review/approve|reject answer with it too. */
export interface ApiReview {
  status: 'pending' | 'approved' | 'changes_requested';
  requested_at: string;
  decided_at: string | null;
  decided_by: string | null;
  reason: string | null;
  reminded_at: string | null;
  overdue: boolean;
  /** "CODE: detail" of every check that did not pass. */
  failed_checks: string[];
}

export interface ApiJob {
  id: number;
  pipeline_id?: number | null;
  depends_on?: number | null;
  needs_approval?: boolean;
  approval_expires_at?: string | null;
  cost?: ApiCost | null;
  project_id: string | null;
  project_name: string;
  title: string;
  kind: string;
  draft: string;
  /** Render jobs: tiktok | facebook; "" (or absent on older servers) = no platform profile. */
  platform?: string;
  size: 'preview' | 'final';
  status: string;
  result: string | null;
  frontend_status: string;
  attempts: number;
  max_attempts: number;
  progress_pct: number;
  stage: string | null;
  eta_sec: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error_msg: string | null;
  no_asr: boolean;
  /** plan.json → options, English keys (style, purpose, duration, drafts, music). */
  options: Record<string, unknown>;
  video_url: string | null;
  cover_url: string | null;
  srt_url: string | null;
  steps: ApiStep[];
  qa: ApiQa | null;
  /** Only a draft job at the approval gate, music modes (phase 6h). */
  footage?: ApiFootage | null;
  /** Only a finished draft job whose run left a report (phase 6i). */
  verify?: ApiVerify | null;
  /** Only a draft / render job of a chapter plan (phase 7g). */
  structure?: ApiStructure | null;
  /** Only a finished FINAL render that was delivered: where it stands with its reviewer (T-F). */
  review?: ApiReview | null;
}

export interface ApiJobDetail extends ApiJob {
  events: ApiEvent[];
}

export interface ApiPreflightIssue {
  code: string;
  level: string;
  msg: string;
}

export interface ApiPreflightResult {
  valid: boolean;
  error_code: string;
  error_msg: string;
  errors: ApiPreflightIssue[];
  warnings: ApiPreflightIssue[];
  adjustments: ApiPreflightIssue[];
  scenes_needed: number | null;
}

export interface ApiPipeline {
  id: number;
  project_id: string | null;
  project_name: string;
  /** UI vocabulary: queued | running | awaiting_approval | qa_review | done | error. */
  status: string;
  needs_approval: boolean;
  jobs: ApiJob[];
}

export interface ApiMusicTrack {
  file: string;
  /** Relative to the music folder (which is not walked): the same name as `file`. */
  path: string;
  title: string;
  bpm: number | null;
  bpm_source: 'library' | 'measured' | 'unknown' | 'pending';
  first_beat_sec: number | null;
  duration_sec: number | null;
  rights: 'thu_vien_noi_bo' | 'mua_ban_quyen' | 'royalty_free';
}

export interface ApiMusicList {
  configured: boolean;
  pending: number;
  tracks: ApiMusicTrack[];
}
