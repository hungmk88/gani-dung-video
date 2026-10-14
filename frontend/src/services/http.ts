import type { ApiErrorBody } from '../types/api';

/** Error carrying the server's own code + Vietnamese message ({"error": {...}}). */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  /** Sent with 409 `pipeline_active` / `project_locked`: the chain/job holding the project. */
  readonly pipelineId?: number;
  readonly jobId?: number;

  constructor(status: number, code: string, message: string, extra: { pipelineId?: number; jobId?: number } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    if (extra.pipelineId !== undefined) this.pipelineId = extra.pipelineId;
    if (extra.jobId !== undefined) this.jobId = extra.jobId;
  }
}

const NETWORK_MSG =
  'Không kết nối được máy chủ dựng (127.0.0.1:8765). Bật `python -m gani_dung_video serve` rồi thử lại.';

const asInt = (v: unknown): number | undefined => (Number.isInteger(v) ? (v as number) : undefined);

/** JSON request against the local API. Relative URLs go through the Vite proxy. */
export async function requestJson<T>(path: string, init?: { method?: 'GET' | 'POST'; body?: unknown }): Promise<T> {
  const method = init?.method ?? 'GET';
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: method === 'POST' ? { 'Content-Type': 'application/json' } : undefined,
      body: method === 'POST' ? JSON.stringify(init?.body ?? {}) : undefined,
    });
  } catch {
    throw new ApiError(0, 'khong_ket_noi', NETWORK_MSG);
  }
  if (!res.ok) {
    // Anything but the documented {"error": {...}} (an HTML error page, a bare string…) must
    // still end up as an ApiError, never as a TypeError thrown while reading it.
    const body = (await res.json().catch(() => null)) as Partial<ApiErrorBody> | null;
    // Vite's proxy answers 502/504 (empty body) when nothing listens on the target port.
    if (body === null && (res.status === 502 || res.status === 504)) {
      throw new ApiError(res.status, 'khong_ket_noi', NETWORK_MSG);
    }
    const err = body?.error;
    throw new ApiError(
      res.status,
      typeof err?.code === 'string' ? err.code : 'loi_khong_ro',
      typeof err?.message === 'string' ? err.message : `Máy chủ trả lỗi ${res.status}.`,
      { pipelineId: asInt(err?.pipeline_id), jobId: asInt(err?.job_id) },
    );
  }
  try {
    return (await res.json()) as T;
  } catch {
    // An empty (204) or non-JSON 2xx body: a server/proxy fault, not a raw SyntaxError to show.
    throw new ApiError(res.status, 'phan_hoi_khong_hop_le', `Máy chủ trả dữ liệu không đọc được (HTTP ${res.status}).`);
  }
}
