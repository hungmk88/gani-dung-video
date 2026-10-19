/**
 * Mock implementation of StudioApi (see api.ts). Simulates the SQLite queue daemon
 * described in the spec: jobs advance through the 8 pipeline steps on a timer and push
 * updates to subscribers, the same shape a real WebSocket payload from 127.0.0.1:8765
 * would use. Nothing here touches the network or the filesystem.
 *
 * The QA numbers in `buildQaReport` are not invented — they are the actual measured
 * results from the phase 5b real render run (docs memory, 29/09/2026): -13.9 LUFS,
 * -2.0 dBTP true peak, passed on round 1 of qc.max_rounds = 3. They're reused here as a
 * realistic reference sample since this prototype has no ffmpeg/ebur128 to measure with.
 */

import { MUSIC_LIBRARY } from '../data/mockMusic';
import { RULES } from '../data/rules';
import { validateMax } from '../lib/approval';
import { runPreflight } from '../lib/preflight';
import { reviewProblem } from '../lib/review';
import { PIPELINE_STEPS } from '../types/rules';
import type {
  ConsoleLine,
  FootageFile,
  PipelineStep,
  PipelineStepId,
  QaReport,
  RenderJob,
  ReviewInfo,
  SubmitRequest,
  VideoOptions,
} from '../types/rules';
import type { StudioApi } from './api';

const STEP_LABELS: Record<PipelineStepId, string> = {
  inventory: 'Kiểm kê footage',
  nhan_dang: 'Nghe lời & nhận diện hình',
  plan_compile: 'Lập kế hoạch nhịp',
  pick: 'Chọn cảnh ứng viên',
  ai_draft: 'AI viết kịch bản',
  render_preview: 'Dựng bản xem nhanh (540p)',
  qc_verify: 'Soát kỹ thuật',
  final_export: 'Xuất bản cuối (1080×1920)',
};

const STEP_TICK_MS = 1800;

function nowIso(): string {
  return new Date().toISOString();
}

function newSteps(): PipelineStep[] {
  return PIPELINE_STEPS.map((id, i) => ({
    id,
    label: STEP_LABELS[id],
    status: i === 0 ? 'running' : 'queued',
  }));
}

let lastLogId = 0;

function log(level: ConsoleLine['level'], text: string): ConsoleLine {
  lastLogId += 1;
  return { id: lastLogId, ts: nowIso(), level, text };
}

function stepDetail(id: PipelineStepId, options: VideoOptions, footage: FootageFile[]): string {
  const style = RULES.styles[options.phong_cach as keyof typeof RULES.styles];
  const totalSec = footage.reduce((s, f) => s + f.duration_sec, 0);
  switch (id) {
    case 'inventory':
      return `${footage.length} clip, ${Math.round(totalSec)}s thô, lọc trùng theo tên/băm 16MB đầu-cuối.`;
    case 'nhan_dang':
      return `faster-whisper (device=cpu, int8) + CLIP ViT-L/14-quickgelu + OpenCV cắt cảnh/đo nét.`;
    case 'plan_compile':
      return `Khối theo mục đích '${RULES.purposes[options.muc_dich as keyof typeof RULES.purposes].name}', driver=${style.driver}.`;
    case 'pick':
      return `2–20 ứng viên/khối, lọc mờ + nhãn 'khac' + ngưỡng món ăn 0,25.`;
    case 'ai_draft':
      return `model claude-sonnet-5-5, effort=medium — tham khảo lần chạy thật: ~2.400 token vào, ≤4.200đ/lần.`;
    case 'render_preview':
      return `540p, 2500 kbps, phụ đề ASS (Be Vietnam Pro), −14 LUFS mục tiêu.`;
    case 'qc_verify':
      return `T1–T7 trên chính file xuất, tối đa ${RULES_QC_MAX_ROUNDS} vòng.`;
    case 'final_export':
      return `1080×1920, ~9000 kbps, AAC 320kbps 48kHz.`;
    default:
      return '';
  }
}

const RULES_QC_MAX_ROUNDS = RULES.qcMaxRounds;

/** Demo QA of one output. `platform` and `noMusic` mirror what the real server reports: a
 *  TikTok render gets the platform-word warning row, a render without music says so under T3. */
function buildQaReport(platform = '', noMusic = false): QaReport {
  const platformGroup =
    platform === 'tiktok'
      ? [
          {
            title: 'Từ ngữ theo nền tảng (glossary.json → platform_risk_words, chỉ cảnh báo)',
            checks: [
              {
                code: 'E1-NEN-TANG',
                label: 'Từ ngữ rủi ro theo nền tảng',
                passed: true,
                detail:
                  "Cảnh báo: Bản tiktok: lời nói 'Hôm nay giảm giá' có từ 'giảm giá' mà nền tảng hay hạn chế (dễ bị giảm phân phối). Cân nhắc đổi câu ở bản nháp. (mô phỏng)",
              },
            ],
          },
        ]
      : [];
  return {
    fixes: [],
    groups: [
      {
        title: 'Pass 1 · Đồng bộ nhịp & hình ảnh (V2–V4, T1)',
        checks: [
          { code: 'V2', label: 'Tổng phách khớp plan', passed: true, detail: 'Tổng phách mỗi khối đúng như plan.json biên dịch.' },
          { code: 'V3', label: 'Phách ngoài dải → làm tròn', passed: true, detail: 'Tự động làm tròn về phách gần nhất cho phép (không cần AI sửa lại).' },
          { code: 'V4', label: 'Không lặp cảnh, cách nhau ≥ 3s', passed: true, detail: 'Tối đa 3 đoạn/clip, không có cặp đoạn trùng gần (similar_warn 0,95).' },
          { code: 'T1', label: 'Khung hình / fps / codec đúng cấu hình', passed: true, detail: '1080×1920, 30fps, H.264 — khớp render.final trong rules.json.' },
        ],
      },
      {
        title: 'Pass 2 · Từ khoá cấm / đối thủ (glossary.json, V7–V8)',
        checks: [
          {
            code: 'V7',
            label: `Không chứa cụm từ cấm (${RULES.bannedClaims.length} cụm trong glossary.json)`,
            passed: true,
            detail: `Đã soát: ${RULES.bannedClaims.slice(0, 5).join(', ')}… và các cụm y tế "chữa/trị/phòng bệnh".`,
          },
          { code: 'V8', label: 'Khối cuối hiện đúng CTA', passed: true, detail: 'CTA khớp purposes.*.cta cho mục đích đã chọn.' },
        ],
      },
      {
        title: 'Pass 3 · Cân bằng âm thanh (qc.T3)',
        checks: [
          { code: 'T3', label: 'Độ to trung bình (LUFS)', passed: true, detail: 'Đo được −13,9 LUFS, mục tiêu −14,0 ±1,0 LU (qc.lufs_tolerance).' },
          { code: 'T3', label: 'Đỉnh thật (dBTP)', passed: true, detail: 'Đo được −2,0 dBTP, trần qc.max_true_peak_db = −1,0 dBTP.' },
        ],
        ...(noMusic
          ? { note: 'Bản không nhúng nhạc: chỉ còn lời và tiếng hiện trường, nhạc chèn trên app khi đăng.' }
          : {}),
      },
      ...platformGroup,
    ],
    rounds_used: 1,
    max_rounds: RULES_QC_MAX_ROUNDS,
    overall_pass: true,
  };
}

interface JobRecord {
  job: RenderJob;
  timer: ReturnType<typeof setTimeout> | null;
  footage: SubmitRequest['footage'];
  /** Set once a person approved the (simulated) Claude cost. */
  approved: boolean;
  /** Ticked platforms (tiktok, facebook): one demo output each; empty = one plain output. */
  platforms: string[];
  noMusic: boolean;
}

// Reference sample from a real dry run (docs/api-jobs.md §4b): 1.678 input tokens, worst
// case one call 4.165,58đ, ceiling 20.000đ for a video up to 60s. Illustrative in the mock.
const MOCK_COST = { inputTokens: 1678, worstCallVnd: 4165.58, capVnd: 20000 };
const APPROVAL_TTL_MS = 24 * 60 * 60 * 1000;

const store = new Map<string, JobRecord>();
const listeners = new Map<string, Set<(job: RenderJob) => void>>();

function notify(id: string): void {
  const rec = store.get(id);
  if (!rec) return;
  // Shallow-clone before handing to React state setters — rec.job is mutated in place
  // as the canonical record, so subscribers need a fresh reference to actually re-render.
  const snapshot: RenderJob = { ...rec.job, steps: [...rec.job.steps], logLines: [...rec.job.logLines] };
  for (const fn of listeners.get(id) ?? []) fn(snapshot);
}

function advance(id: string): void {
  const rec = store.get(id);
  if (!rec) return;
  const { job, footage, platforms, noMusic } = rec;
  const runningIdx = job.steps.findIndex((s) => s.status === 'running');
  if (runningIdx === -1) return;

  const step = job.steps[runningIdx];
  step.status = 'done';
  step.detail = stepDetail(step.id, job.options, footage);
  job.logLines.push(log('info', `✓ ${step.label} — ${step.detail}`));

  if (step.id === 'render_preview') {
    job.previewUrl = '/preview/nhap_A_540p.mp4';
    job.logLines.push(log('info', 'Bản xem trước sẵn sàng: nhap_A_540p.mp4'));
  }

  const nextIdx = runningIdx + 1;
  if (nextIdx >= job.steps.length) {
    job.status = 'done';
    job.etaSec = 0;
    job.finalUrl = job.previewUrl; // demo: reuse the same real render as the "final" clip
    job.qa = buildQaReport(platforms[0], noMusic);
    if (platforms.length > 0) {
      // demo: the same clip stands in for every platform's render; tabs and QA differ as on the server
      job.outputs = platforms.map((platform) => ({
        draft: 'A',
        platform,
        previewUrl: job.previewUrl,
        finalUrl: job.finalUrl,
        qa: buildQaReport(platform, noMusic),
      }));
    }
    // demo: a delivered final video waits for its reviewer (T-F); the real API sets this from the manifest
    job.review = pendingReview(id);
    if (platforms.length > 0) {
      // As on the server: one render job, one delivered file and one decision per platform.
      job.jobs = platforms.map((platform) => ({
        id: renderJobId(id, platform),
        kind: 'render',
        draft: 'A',
        platform,
        ...(noMusic ? { noMusic: true } : {}),
        status: 'done' as const,
        errorMsg: null,
        retryable: false,
        spentVnd: null,
        review: pendingReview(renderJobId(id, platform)),
      }));
      job.review = job.jobs[0].review;
    }
    job.logLines.push(log('info', 'Hoàn tất pipeline — soát kỹ thuật ĐẠT ở vòng 1/3.'));
    notify(id);
    return;
  }

  job.steps[nextIdx].status = 'running';
  job.etaSec = Math.max(0, (job.steps.length - nextIdx) * 6);
  job.logLines.push(log('info', `▶ Bắt đầu: ${job.steps[nextIdx].label}`));

  // The Claude step never starts spending on its own: park until a person approves.
  if (job.steps[nextIdx].id === 'ai_draft' && !rec.approved) {
    job.status = 'awaiting_approval';
    job.steps[nextIdx].detail = 'Chờ duyệt chi phí';
    job.approval = {
      jobId: job.id,
      ...MOCK_COST,
      expiresAt: new Date(Date.now() + APPROVAL_TTL_MS).toISOString(),
    };
    job.logLines.push(log('warn', 'Đã có ước tính chi phí gọi Claude — chờ bạn duyệt (không tự chi tiền).'));
    notify(id);
    return;
  }
  job.status = 'running';
  notify(id);

  rec.timer = setTimeout(() => advance(id), STEP_TICK_MS);
}

function pendingReview(jobId: string): ReviewInfo {
  return { jobId, status: 'pending', reason: null, decidedBy: null, decidedAt: null, overdue: false, failedChecks: [] };
}

/** Id of a card's demo render job for one platform (what decideReview receives). */
function renderJobId(cardId: string, platform: string): string {
  return `${cardId}:${platform}`;
}

let idCounter = 0;
function makeId(): string {
  idCounter += 1;
  return `job-${Date.now()}-${idCounter}`;
}

export const mockApi: StudioApi = {
  mode: 'mock',

  async health() {
    return {
      online: true,
      daemonOnline: true,
      rulesVersion: RULES.rules_version,
      jobs: {},
      allowedRootsConfigured: true,
      aiKeyConfigured: true,
      longVideo: true,
      platforms: [...RULES.platforms],
      budget: { limitVnd: null, spent24hVnd: 0, reservedVnd: 0 },
    };
  },

  async listMusic() {
    return { configured: true, pending: 0, tracks: MUSIC_LIBRARY };
  },

  async listProjects() {
    return [];
  },

  async preflight(options, footage) {
    return runPreflight({ options, footage });
  },

  async submitJob({ options, footage, platforms = [], noMusic = false }: SubmitRequest): Promise<RenderJob> {
    const id = makeId();
    const style = RULES.styles[options.phong_cach as keyof typeof RULES.styles];
    const purpose = RULES.purposes[options.muc_dich as keyof typeof RULES.purposes];
    const job: RenderJob = {
      id,
      title: `${style.name} · ${purpose.name} · ${options.do_dai_giay}s${noMusic ? ' · không nhạc' : ''}`,
      options,
      status: 'queued',
      createdAt: nowIso(),
      etaSec: PIPELINE_STEPS.length * 6,
      steps: newSteps(),
      logLines: [log('info', 'Đã nhận tác vụ, xếp vào hàng đợi SQLite (daemon mode, không chặn giao diện).')],
      previewUrl: null,
      finalUrl: null,
      qa: null,
      ...(platforms.length > 0 ? { platforms: [...platforms] } : {}),
      ...(noMusic ? { noMusic: true } : {}),
    };
    const rec: JobRecord = { job, timer: null, footage, approved: false, platforms: [...platforms], noMusic };
    store.set(id, rec);
    rec.timer = setTimeout(() => advance(id), STEP_TICK_MS);
    return job;
  },

  async listJobs(): Promise<RenderJob[]> {
    return [...store.values()]
      .map((r) => ({ ...r.job, steps: [...r.job.steps], logLines: [...r.job.logLines] }))
      .sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1));
  },

  async getJob(id: string): Promise<RenderJob | null> {
    const job = store.get(id)?.job;
    return job ? { ...job, steps: [...job.steps], logLines: [...job.logLines] } : null;
  },

  subscribeJob(id: string, onUpdate: (job: RenderJob) => void): () => void {
    if (!listeners.has(id)) listeners.set(id, new Set());
    listeners.get(id)!.add(onUpdate);
    const rec = store.get(id);
    if (rec) onUpdate(rec.job);
    return () => listeners.get(id)?.delete(onUpdate);
  },

  async cancelJob(id: string): Promise<void> {
    const rec = store.get(id);
    if (!rec) return;
    if (rec.timer) clearTimeout(rec.timer);
    rec.job.status = 'error';
    rec.job.logLines.push(log('warn', 'Đã huỷ theo yêu cầu.'));
    notify(id);
  },

  async retryJob(): Promise<void> {
    // Mock jobs never fail on their own; nothing to retry.
  },

  async approve(job: RenderJob, maxVnd: number): Promise<RenderJob> {
    const rec = store.get(job.id);
    const ap = rec?.job.approval;
    if (!rec || !ap) throw new Error('Tác vụ này không có gì đang chờ duyệt chi phí.');
    const bad = validateMax(maxVnd, ap.worstCallVnd, ap.capVnd);
    if (bad) throw new Error(bad);
    rec.approved = true;
    rec.job.approval = undefined;
    rec.job.status = 'running';
    rec.job.logLines.push(log('info', `Đã duyệt: tối đa ${maxVnd.toLocaleString('vi-VN')}đ cho lần gọi Claude (mô phỏng, không tốn tiền thật).`));
    rec.timer = setTimeout(() => advance(job.id), STEP_TICK_MS);
    notify(job.id);
    return { ...rec.job };
  },

  async decideReview(jobId, decision, reviewer, reason): Promise<void> {
    // A platform render's id is "<card id>:<platform>"; a plain card is reviewed under its own id.
    const cardId = jobId.split(':')[0];
    const rec = store.get(cardId);
    const render = rec?.job.jobs?.find((j) => j.id === jobId);
    const review = render ? render.review : rec?.job.review;
    if (!rec || !review) throw new Error('Video này không có gì chờ duyệt.');
    const bad = reviewProblem(decision, reviewer, reason);
    if (bad) throw new Error(bad);
    if (review.status !== 'pending') throw new Error('Video này đã được quyết định rồi, không đổi được nữa.');
    const decided: ReviewInfo = {
      ...review,
      status: decision === 'approve' ? 'approved' : 'changes_requested',
      decidedBy: reviewer.trim(),
      decidedAt: new Date().toISOString(),
      reason: reason.trim() || null,
    };
    if (render) {
      render.review = decided;
      // The card's badge follows the first file still waiting, else the one just decided.
      rec.job.review = rec.job.jobs?.find((j) => j.review?.status === 'pending')?.review ?? decided;
    } else {
      rec.job.review = decided;
    }
    const which = render?.platform ? ` (bản ${render.platform})` : '';
    rec.job.logLines.push(log('info', decision === 'approve'
      ? `Đã duyệt${which} bởi ${reviewer.trim()} (mô phỏng: chuyển sang final/).`
      : `Yêu cầu sửa${which} (${reviewer.trim()}): ${reason.trim()}`));
    notify(cardId);
  },

  async rerender(job: RenderJob): Promise<RenderJob> {
    const footage = store.get(job.id)?.footage ?? [];
    return mockApi.submitJob({
      options: job.options,
      footage,
      platforms: job.platforms ?? [],
      noMusic: job.noMusic ?? false,
    });
  },
};
