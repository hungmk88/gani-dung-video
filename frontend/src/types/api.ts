/**
 * Wire types of the local web API (phase 6b) — a hand-copy of `domain/api_models.py`,
 * contract in `docs/api-jobs.md`. Field names stay exactly as the server sends them.
 * Only `import type` here so the mapper tests can run under plain `node --test`.
 */

export interface ApiErrorBody {
  error: { code: string; message: string };
}

export interface ApiProject {
  id: string;
  name: string;
  has_inventory: boolean;
  has_plan: boolean;
  has_drafts: boolean;
  ready_to_render: boolean;
}

export interface ApiHealth {
  ok: boolean;
  rules_version: string;
  daemon_online: boolean;
  daemon_pid: number | null;
  daemon_stopping: string | null;
  jobs: Record<string, number>;
  allowed_roots_configured: boolean;
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
}

export interface ApiJob {
  id: number;
  project_id: string | null;
  project_name: string;
  title: string;
  kind: string;
  draft: string;
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
