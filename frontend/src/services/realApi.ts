/**
 * StudioApi over the local HTTP API (phases 6b + 6c, docs/api-jobs.md). Job updates come
 * from polling: the server has no push channel, the mock's fake WebSocket has no real twin.
 *
 * Card ids: a plain job is its server id ("12"); a whole analyze → draft → render chain is
 * "p:<pipeline id>" (see mappers.pipelineCardId).
 */

import type {
  ApiHealth,
  ApiJob,
  ApiJobDetail,
  ApiMusicList,
  ApiPipeline,
  ApiPreflightResult,
  ApiProject,
} from '../types/api';
import type { RenderJob, VideoOptions } from '../types/rules';
import type { StudioApi } from './api';
import { ApiError, requestJson } from './http';
import {
  buildCards,
  parsePipelineCardId,
  pipelineBody,
  preflightBody,
  rerenderBody,
  toMusicList,
  toHealthInfo,
  toPipelineCard,
  toPreflightResult,
  toProjectInfo,
  toRenderJob,
} from './mappers';
import { approveBody } from '../lib/approval';

const POLL_MS = 1000;
// Waiting for a person to approve costs nothing to watch slowly; a finished card can still
// come back (retry / re-render keeps its id), so it is watched slowly too.
const POLL_WAITING_MS = 2000;
const POLL_IDLE_MS = 3000;
const SETTLED = ['done', 'error', 'qa_review'];

const enc = encodeURIComponent;

async function siblingsOf(job: ApiJob): Promise<ApiJob[]> {
  if (job.project_id === null) return [job];
  const list = await requestJson<ApiJob[]>(`/api/jobs?project_id=${enc(job.project_id)}&limit=50`);
  return list.filter((j) => j.draft === job.draft);
}

async function pipelineCard(pipelineId: number): Promise<RenderJob> {
  const p = await requestJson<ApiPipeline>(`/api/pipelines/${pipelineId}`);
  const details = await Promise.all(p.jobs.map((j) => requestJson<ApiJobDetail>(`/api/jobs/${j.id}`)));
  return toPipelineCard(p, details);
}

async function detail(id: string): Promise<RenderJob> {
  const pipelineId = parsePipelineCardId(id);
  if (pipelineId !== null) return pipelineCard(pipelineId);
  const job = await requestJson<ApiJobDetail>(`/api/jobs/${enc(id)}`);
  return toRenderJob(job, await siblingsOf(job));
}

function post<T>(path: string, body: unknown = {}): Promise<T> {
  return requestJson<T>(path, { method: 'POST', body });
}

/** 409 on cancel/retry means "already in that state" — fine when sweeping a whole chain. */
async function ignoreConflict(work: Promise<unknown>): Promise<void> {
  try {
    await work;
  } catch (e) {
    if (!(e instanceof ApiError && e.status === 409)) throw e;
  }
}

export const realApi: StudioApi = {
  mode: 'real',

  async health() {
    return toHealthInfo(await requestJson<ApiHealth>('/api/health'));
  },

  async listProjects() {
    return (await requestJson<ApiProject[]>('/api/projects')).map(toProjectInfo);
  },

  async listMusic() {
    return toMusicList(await requestJson<ApiMusicList>('/api/music'));
  },

  async preflight(options: VideoOptions) {
    const r = await post<ApiPreflightResult>('/api/preflight', preflightBody(options));
    return toPreflightResult(r);
  },

  async submitJob(req) {
    if (!req.projectId) throw new Error('Chưa chọn dự án để dựng.');
    if (req.mode === 'pipeline') {
      const p = await post<ApiPipeline>(
        '/api/pipelines',
        pipelineBody(req.options, {
          projectId: req.projectId,
          draft: req.draft ?? 'all',
          final: req.final ?? false,
          noAsr: req.noAsr ?? false,
          forceAnalyze: req.forceAnalyze ?? false,
        }),
      );
      return toPipelineCard(p);
    }
    const jobs = await post<ApiJob[]>('/api/jobs', {
      project_id: req.projectId,
      draft: req.draft ?? 'all',
      final: req.final ?? false,
      no_asr: req.noAsr ?? false,
    });
    return toRenderJob(jobs[0], jobs);
  },

  async listJobs() {
    return buildCards(await requestJson<ApiJob[]>('/api/jobs?limit=200'));
  },

  async getJob(id) {
    return detail(id);
  },

  subscribeJob(id, onUpdate) {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async (): Promise<void> => {
      let next = POLL_MS;
      try {
        const card = await detail(id);
        if (stopped) return;
        onUpdate(card);
        if (card.status === 'awaiting_approval') next = POLL_WAITING_MS;
        else if (SETTLED.includes(card.status)) next = POLL_IDLE_MS;
      } catch {
        // Server hiccup: keep polling, the next tick recovers on its own.
      }
      if (!stopped) timer = setTimeout(() => void tick(), next);
    };
    void tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  },

  async cancelJob(id) {
    const pipelineId = parsePipelineCardId(id);
    if (pipelineId === null) {
      await post(`/api/jobs/${enc(id)}/cancel`);
      return;
    }
    // Cancelling one job cancels the jobs waiting on it; sweep the rest that are still live.
    const p = await requestJson<ApiPipeline>(`/api/pipelines/${pipelineId}`);
    for (const j of p.jobs) {
      if (['queued', 'running', 'awaiting_approval'].includes(j.frontend_status)) {
        await ignoreConflict(post(`/api/jobs/${j.id}/cancel`));
      }
    }
  },

  async retryJob(jobId) {
    await post(`/api/jobs/${enc(jobId)}/retry`);
  },

  async approve(job, maxVnd) {
    if (!job.approval) throw new Error('Tác vụ này không có gì đang chờ duyệt chi phí.');
    // The server compares `estimate_vnd` with what it holds: refusing here is by design.
    await post(`/api/jobs/${enc(job.approval.jobId)}/approve`, approveBody(maxVnd, job.approval.worstCallVnd));
    return detail(job.id);
  },

  async rerender(job) {
    // Retry what failed, in order (analyze → draft → render); draft needs a fresh approval.
    const retryable = (job.jobs ?? []).filter((j) => j.retryable);
    if (retryable.length > 0) {
      for (const j of retryable) await post(`/api/jobs/${enc(j.id)}/retry`);
      return detail(job.id);
    }
    if (job.pipelineId === undefined && (job.status === 'error' || job.status === 'qa_review')) {
      await post(`/api/jobs/${enc(job.id)}/retry`);
      return detail(job.id);
    }
    const jobs = await post<ApiJob[]>('/api/jobs', rerenderBody(job));
    return toRenderJob(jobs[0], jobs);
  },
};
