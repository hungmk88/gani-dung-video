/**
 * Types mirroring the REAL backend schema (config/rules.json, domain/options.py,
 * domain/preflight.py, domain/verify.py, domain/qc.py — repo `gani-dung-video`, rules
 * v2.0). Field names are kept in Vietnamese/snake_case exactly as the Python side uses
 * them so a JSON payload from the future FastAPI server at 127.0.0.1:8765 needs zero
 * remapping — see src/services/config.ts for the swap-over point.
 */

export type CompatLevel = 'ok' | 'warn' | 'bad';
export type IssueLevel = 'block' | 'warn' | 'auto';

export const STYLE_IDS = [
  'tvc_cat_nhanh',
  'ban_hang_co_loi',
  'ke_chuyen_nghe',
  'review_mon_an',
  'sang_trong',
  'theo_mo_ta',
] as const;
export type StyleId = (typeof STYLE_IDS)[number];

export const PURPOSE_IDS = [
  'ban_hang',
  'gioi_thieu_xuong',
  'khuyen_mai',
  'ke_chuyen_thuong_hieu',
] as const;
export type PurposeId = (typeof PURPOSE_IDS)[number];

export const DURATION_IDS = [15, 30, 45, 60, 180] as const;
export type DurationId = (typeof DURATION_IDS)[number];

export const MUSIC_MODE_IDS = ['ai_tu_chon', 'khong_nhac', 'thu_vien', 'tai_len'] as const;
export type MusicModeId = (typeof MUSIC_MODE_IDS)[number];

export const DRAFT_COUNTS = [1, 2, 3] as const;
export type DraftCount = (typeof DRAFT_COUNTS)[number];

/** driver: what dictates the cut rhythm. 'auto' (theo_mo_ta) picks music or grid at plan time. */
export type Driver = 'music' | 'speech' | 'auto';

export interface StyleDef {
  id: StyleId;
  name: string;
  driver: Driver;
  /** Only tvc_cat_nhanh sets this in rules.json: falls back to a virtual beat grid
   *  when no music is picked, instead of failing. */
  driver_fallback?: 'grid';
  scene_avg_sec: number;
  bpm_range: [number, number] | null;
  max_duration?: number;
  max_effects?: number | null;
  requires_speech?: boolean;
  note_min_words?: number;
  text_max_words?: number;
  grade?: string;
}

export interface PurposeDef {
  id: PurposeId;
  name: string;
  blocks: Array<[string, number]>;
  cta: string;
  max_duration?: number;
  required_fields?: string[];
}

export interface MusicModeDef {
  id: MusicModeId;
  name: string;
  needs_rights_tick: boolean;
}

export interface ConflictDef {
  code: string;
  level: IssueLevel;
  stage: 'preflight' | 'plan' | 'ai' | 'render';
  desc: string;
  msg: string;
}

/** One row of the client-known Rules Matrix the wizard ships with (subset of rules.json). */
export interface RulesBundle {
  rules_version: string;
  styles: Record<StyleId, StyleDef>;
  purposes: Record<PurposeId, PurposeDef>;
  durations: readonly DurationId[];
  musicModes: Record<MusicModeId, MusicModeDef>;
  draftCounts: readonly DraftCount[];
  compatStylePurpose: Record<StyleId, Record<PurposeId, CompatLevel>>;
  compatStyleDuration: Record<StyleId, Record<string, CompatLevel>>;
  scenesNeeded: Record<StyleId, Record<string, number>>;
  conflicts: Record<string, ConflictDef>;
  bannedClaims: string[];
  pickTagNames: Record<string, string>;
}

/** Pacing mode is a UI-facing grouping of `driver` used by the brief (LỜI/NHẠC/LƯỚI ẢO). */
export type PacingMode = 'loi' | 'nhac' | 'luoi_ao';

export interface MusicTrack {
  id: string;
  title: string;
  bpm: number;
  duration_sec: number;
  genre: string;
  rights: 'thu_vien_noi_bo' | 'mua_ban_quyen' | 'royalty_free';
  file: string;
}

export interface KhuyenMaiFields {
  uu_dai: string;
  thoi_han: string;
  dieu_kien: string;
}

export interface VideoOptions {
  phong_cach: StyleId | '';
  muc_dich: PurposeId | '';
  do_dai_giay: DurationId | 0;
  so_ban_nhap: DraftCount;
  nhac: MusicModeId | '';
  nhac_file: string | null;
  nhac_quyen_su_dung: boolean | null;
  khuyen_mai: KhuyenMaiFields | null;
  ghi_chu: string;
  pacing: PacingMode;
}

export interface FootageFile {
  id: string;
  name: string;
  size_bytes: number;
  duration_sec: number;
}

export interface PreflightIssue {
  code: string;
  level: IssueLevel;
  message: string;
}

export interface PreflightResult {
  valid: boolean;
  errors: PreflightIssue[];
  warnings: PreflightIssue[];
  /** Auto-applied corrections (level 'auto', e.g. X12/X13) — informational only. */
  adjustments: PreflightIssue[];
  scenes_needed: number | null;
  scenes_estimated: number | null;
}

/* ───────────────────── Pipeline / Queue (GĐ6) ───────────────────── */

// Real CLI command order (repo `gani-dung-video`: inventory → scenes/semantics/speech →
// plan → pick → draft → render → qc), not the generic 8-step sketch from the original
// brief (which named a non-existent `pack.py`). "Nhận dạng" bundles the 3 CLI commands
// that all run at that stage (scenes, semantics, speech).
export const PIPELINE_STEPS = [
  'inventory',
  'nhan_dang',
  'plan_compile',
  'pick',
  'ai_draft',
  'render_preview',
  'qc_verify',
  'final_export',
] as const;
export type PipelineStepId = (typeof PIPELINE_STEPS)[number];

export type StepStatus = 'done' | 'running' | 'queued' | 'error';

export interface PipelineStep {
  id: PipelineStepId;
  label: string;
  status: StepStatus;
  detail?: string;
}

export type JobStatus =
  | 'queued'
  | 'running'
  | 'awaiting_approval'
  | 'qa_review'
  | 'done'
  | 'error';

/** A Claude call waiting for a person's cost approval (draft job, phase 6c). */
export interface ApprovalInfo {
  /** Server id of the draft job to approve. */
  jobId: string;
  inputTokens: number;
  /** One call, worst case — what the person is asked to accept. */
  worstCallVnd: number;
  /** Ceiling for this video length. */
  capVnd: number;
  expiresAt: string | null;
}

/** One server job inside a pipeline card (analyze / draft / render). */
export interface PipelineJobInfo {
  id: string;
  kind: string;
  draft: string;
  status: JobStatus;
  errorMsg: string | null;
  /** failed / cancelled / QC-failed: `POST /api/jobs/{id}/retry` accepts it. */
  retryable: boolean;
  spentVnd: number | null;
}

/** Playable result of one draft (A/B/C) of a pipeline. */
export interface DraftOutput {
  draft: string;
  previewUrl: string | null;
  finalUrl: string | null;
  qa: QaReport | null;
}

export interface RenderJob {
  id: string;
  /** Real API: set for a whole analyze → draft → render chain (`id` is then `p:<n>`). */
  pipelineId?: number;
  approval?: ApprovalInfo;
  jobs?: PipelineJobInfo[];
  outputs?: DraftOutput[];
  /** Real API only: the project/draft/size this job renders (needed to re-render). */
  projectId?: string;
  draft?: string;
  size?: 'preview' | 'final';
  title: string;
  options: VideoOptions;
  status: JobStatus;
  createdAt: string;
  etaSec: number | null;
  steps: PipelineStep[];
  logLines: ConsoleLine[];
  previewUrl: string | null;
  finalUrl: string | null;
  qa: QaReport | null;
}

export interface ConsoleLine {
  ts: string;
  level: 'info' | 'warn' | 'error';
  text: string;
}

/** One check as the backend reports it. Codes: V1–V9 (AI draft, domain/verify.py) and
 *  T1–T8 (technical QC, domain/qc.py). */
export interface QaCheck {
  code: string;
  label: string;
  passed: boolean;
  detail: string;
}

/** A titled group of checks. `emptyNote` explains an empty group instead of faking rows
 *  (e.g. V1–V9 are not stored per check by the backend yet — docs/api-jobs.md §6.1). */
export interface QaGroup {
  title: string;
  checks: QaCheck[];
  emptyNote?: string;
  /** Info line shown under the rows, e.g. why T8 is not applicable. */
  note?: string;
}

export interface QaReport {
  groups: QaGroup[];
  rounds_used: number;
  max_rounds: number;
  overall_pass: boolean;
}

/* ───────────────────── Local API (phase 6b) ───────────────────── */

export interface ProjectInfo {
  id: string;
  name: string;
  ready_to_render: boolean;
  has_inventory: boolean;
  has_plan: boolean;
  has_drafts: boolean;
}

export interface HealthInfo {
  online: boolean;
  daemonOnline: boolean;
  rulesVersion: string | null;
  jobs: Record<string, number>;
  allowedRootsConfigured: boolean;
  aiKeyConfigured: boolean;
  budget: { limitVnd: number | null; spent24hVnd: number; reservedVnd: number };
}

export type DraftChoice = 'A' | 'B' | 'C' | 'all';

/** What the wizard hands to `api.submitJob`. Mock uses options+footage; the real API takes
 *  its options from the project's plan.json and only needs project/draft/final. */
export interface SubmitRequest {
  options: VideoOptions;
  footage: FootageFile[];
  projectId?: string;
  /** 'pipeline': POST /api/pipelines (analyze → draft → render). 'render': POST /api/jobs on a
   *  project that already has plan + drafts. Ignored by the mock. */
  mode?: 'pipeline' | 'render';
  draft?: DraftChoice;
  final?: boolean;
  noAsr?: boolean;
  forceAnalyze?: boolean;
}
