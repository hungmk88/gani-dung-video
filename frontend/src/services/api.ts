/**
 * API surface the UI depends on. Components only ever import `api` from this file —
 * never mockApi.ts / realApi.ts directly. `VITE_USE_MOCK=false` (see config.ts) switches
 * the whole UI from the in-browser simulation to the local server of phase 6b.
 */

import type {
  HealthInfo,
  MusicListInfo,
  PreflightResult,
  ProjectInfo,
  RenderJob,
  SubmitRequest,
  VideoOptions,
  FootageFile,
} from '../types/rules';
import { USE_MOCK } from './config';
import { mockApi } from './mockApi';
import { realApi } from './realApi';

export interface StudioApi {
  /** 'mock': footage is dropped in the browser; 'real': a project is picked on the server. */
  mode: 'mock' | 'real';
  health(): Promise<HealthInfo>;
  /** Projects the server can render; the mock has none (it has a footage dropzone). */
  listProjects(): Promise<ProjectInfo[]>;
  /** Real: GET /api/music (the machine's music folder). Mock: the 4 built-in test tracks. */
  listMusic(): Promise<MusicListInfo>;
  /** Real: POST /api/preflight (Python is the single source of truth). Mock: local copy. */
  preflight(options: VideoOptions, footage: FootageFile[]): Promise<PreflightResult>;
  submitJob(req: SubmitRequest): Promise<RenderJob>;
  listJobs(): Promise<RenderJob[]>;
  getJob(id: string): Promise<RenderJob | null>;
  /** Live updates of one job; returns the unsubscribe function. */
  subscribeJob(id: string, onUpdate: (job: RenderJob) => void): () => void;
  /** Cancels a job; for a pipeline card ("p:<n>") every job still waiting or running. */
  cancelJob(id: string): Promise<void>;
  /** Retry one failed / cancelled / QC-failed server job (a draft needs a fresh approval). */
  retryJob(jobId: string): Promise<void>;
  /**
   * Approve the Claude cost of the card's draft job. The ONLY call that can make money be
   * spent: it needs the exact amount a person confirmed, never a default.
   */
  approve(job: RenderJob, maxVnd: number): Promise<RenderJob>;
  /** Re-run a card: retry what failed (in order), otherwise queue fresh renders. */
  rerender(job: RenderJob): Promise<RenderJob>;
  /**
   * Decide on a delivered final video (T-F): approve moves it to final/, reject keeps it in
   * drafts/ with the reason. Final either way: a decided video cannot be decided again.
   */
  decideReview(jobId: string, decision: 'approve' | 'reject', reviewer: string, reason: string): Promise<void>;
}

export const api: StudioApi = USE_MOCK ? mockApi : realApi;
