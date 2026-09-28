/**
 * API surface the UI depends on. Components only ever import `api` from this file —
 * never mockApi.ts directly — so wiring GĐ6's real daemon later means implementing
 * `realApi` below and flipping USE_MOCK in config.ts, with zero component changes.
 */

import type { FootageFile, RenderJob, VideoOptions } from '../types/rules';
import { USE_MOCK } from './config';
import { mockApi } from './mockApi';

export interface StudioApi {
  submitJob(options: VideoOptions, footage: FootageFile[]): Promise<RenderJob>;
  listJobs(): Promise<RenderJob[]>;
  getJob(id: string): Promise<RenderJob | null>;
  /** Simulates the WebSocket push described in the spec. Returns an unsubscribe fn. */
  subscribeJob(id: string, onUpdate: (job: RenderJob) => void): () => void;
  cancelJob(id: string): Promise<void>;
}

const realApi: StudioApi = {
  async submitJob() {
    throw new Error('GĐ6 (daemon thật ở 127.0.0.1:8765) chưa được duyệt/triển khai.');
  },
  async listJobs() {
    throw new Error('GĐ6 chưa triển khai.');
  },
  async getJob() {
    throw new Error('GĐ6 chưa triển khai.');
  },
  subscribeJob() {
    throw new Error('GĐ6 chưa triển khai.');
  },
  async cancelJob() {
    throw new Error('GĐ6 chưa triển khai.');
  },
};

export const api: StudioApi = USE_MOCK ? mockApi : realApi;
