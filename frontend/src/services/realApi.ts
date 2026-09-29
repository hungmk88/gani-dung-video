/**
 * StudioApi over the local HTTP API (phase 6b, docs/api-jobs.md). Job updates come from
 * polling: the server has no push channel, the mock's fake WebSocket has no real twin.
 */

import type { ApiHealth, ApiJob, ApiJobDetail, ApiPreflightResult, ApiProject } from '../types/api';
import type { RenderJob, VideoOptions } from '../types/rules';
import type { StudioApi } from './api';
import { requestJson } from './http';
import {
  groupLatest,
  preflightBody,
  toHealthInfo,
  toPreflightResult,
  toProjectInfo,
  toRenderJob,
} from './mappers';

const POLL_MS = 1000;
// A finished job can still come back (retry / re-render keeps its id), so keep watching slowly.
const POLL_IDLE_MS = 3000;
const TERMINAL = ['completed', 'failed', 'cancelled'];

async function siblingsOf(job: ApiJob): Promise<ApiJob[]> {
  if (job.project_id === null) return [job];
  const list = await requestJson<ApiJob[]>(
    `/api/jobs?project_id=${encodeURIComponent(job.project_id)}&limit=50`,
  );
  return list.filter((j) => j.draft === job.draft);
}

async function detail(id: string): Promise<RenderJob> {
  const job = await requestJson<ApiJobDetail>(`/api/jobs/${encodeURIComponent(id)}`);
  return toRenderJob(job, await siblingsOf(job));
}

function post(path: string): Promise<ApiJob> {
  return requestJson<ApiJob>(path, { method: 'POST', body: {} });
}

export const realApi: StudioApi = {
  mode: 'real',

  async health() {
    return toHealthInfo(await requestJson<ApiHealth>('/api/health'));
  },

  async listProjects() {
    return (await requestJson<ApiProject[]>('/api/projects')).map(toProjectInfo);
  },

  async preflight(options: VideoOptions) {
    const r = await requestJson<ApiPreflightResult>('/api/preflight', {
      method: 'POST',
      body: preflightBody(options),
    });
    return toPreflightResult(r);
  },

  async submitJob(req) {
    if (!req.projectId) throw new Error('Chưa chọn dự án để dựng.');
    const jobs = await requestJson<ApiJob[]>('/api/jobs', {
      method: 'POST',
      body: {
        project_id: req.projectId,
        draft: req.draft ?? 'all',
        final: req.final ?? false,
        no_asr: req.noAsr ?? false,
      },
    });
    return toRenderJob(jobs[0], jobs);
  },

  async listJobs() {
    return groupLatest(await requestJson<ApiJob[]>('/api/jobs?limit=200'));
  },

  async getJob(id) {
    return detail(id);
  },

  subscribeJob(id, onUpdate) {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async (): Promise<void> => {
      let terminal = false;
      try {
        const job = await requestJson<ApiJobDetail>(`/api/jobs/${encodeURIComponent(id)}`);
        if (stopped) return;
        onUpdate(toRenderJob(job, await siblingsOf(job)));
        terminal = TERMINAL.includes(job.status);
      } catch {
        // Server hiccup: keep polling, the next tick recovers on its own.
      }
      if (!stopped) timer = setTimeout(() => void tick(), terminal ? POLL_IDLE_MS : POLL_MS);
    };
    void tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  },

  async cancelJob(id) {
    await post(`/api/jobs/${encodeURIComponent(id)}/cancel`);
  },

  async rerender(job) {
    // Failed / cancelled / QC-failed jobs are retried in place; a passed job is queued anew.
    if (job.status === 'error' || job.status === 'qa_review') {
      await post(`/api/jobs/${encodeURIComponent(job.id)}/retry`);
      return detail(job.id);
    }
    if (!job.projectId) throw new Error('Job này không thuộc dự án nào nên không dựng lại được.');
    const jobs = await requestJson<ApiJob[]>('/api/jobs', {
      method: 'POST',
      body: { project_id: job.projectId, draft: job.draft ?? 'all', final: job.size === 'final' },
    });
    return toRenderJob(jobs[0], jobs);
  },
};
