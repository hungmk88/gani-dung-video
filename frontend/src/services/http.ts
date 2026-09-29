import type { ApiErrorBody } from '../types/api';

/** Error carrying the server's own code + Vietnamese message ({"error": {...}}). */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

const NETWORK_MSG =
  'Không kết nối được máy chủ dựng (127.0.0.1:8765). Bật `python -m gani_dung_video serve` rồi thử lại.';

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
    const body = (await res.json().catch(() => null)) as ApiErrorBody | null;
    // Vite's proxy answers 502/504 (empty body) when nothing listens on the target port.
    if (body === null && (res.status === 502 || res.status === 504)) {
      throw new ApiError(res.status, 'khong_ket_noi', NETWORK_MSG);
    }
    throw new ApiError(
      res.status,
      body?.error.code ?? 'loi_khong_ro',
      body?.error.message ?? `Máy chủ trả lỗi ${res.status}.`,
    );
  }
  return (await res.json()) as T;
}
