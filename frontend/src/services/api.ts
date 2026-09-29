/**
 * API surface the UI depends on. Components only ever import `api` from this file —
 * never mockApi.ts / realApi.ts directly. `VITE_USE_MOCK=false` (see config.ts) switches
 * the whole UI from the in-browser simulation to the local server of phase 6b.
 */

import type {
  HealthInfo,
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
  /** Real: POST /api/preflight (Python is the single source of truth). Mock: local copy. */
  preflight(options: VideoOptions, footage: FootageFile[]): Promise<PreflightResult>;
  submitJob(req: SubmitRequest): Promise<RenderJob>;
  listJobs(): Promise<RenderJob[]>;
  getJob(id: string): Promise<RenderJob | null>;
  /** Live updates of one job; returns the unsubscribe function. */
  subscribeJob(id: string, onUpdate: (job: RenderJob) => void): () => void;
  cancelJob(id: string): Promise<void>;
  /** Re-run a job: retry when it failed / QC-failed, otherwise queue a fresh one. */
  rerender(job: RenderJob): Promise<RenderJob>;
}

export const api: StudioApi = USE_MOCK ? mockApi : realApi;
