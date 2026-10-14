// Run with `npm test` (plain node:test — no test-runner dependency).
import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { ApiError, requestJson } from './http.ts';

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

function answer(status: number, body: string | null): void {
  globalThis.fetch = async () => new Response(body, { status });
}

async function caught(p: Promise<unknown>): Promise<ApiError> {
  try {
    await p;
  } catch (e) {
    assert.ok(e instanceof ApiError, `expected ApiError, got ${String(e)}`);
    return e;
  }
  return assert.fail('expected a rejection');
}

test('requestJson returns the parsed JSON of a 2xx answer', async () => {
  answer(200, '{"ok": true}');
  assert.deepEqual(await requestJson('/api/health'), { ok: true });
});

test('requestJson keeps the server code, message and the 409 pipeline_id / job_id', async () => {
  answer(
    409,
    JSON.stringify({ error: { code: 'pipeline_active', message: 'Dự án đang bận.', pipeline_id: 7, job_id: 31 } }),
  );
  const e = await caught(requestJson('/api/pipelines', { method: 'POST', body: {} }));
  assert.equal(e.status, 409);
  assert.equal(e.code, 'pipeline_active');
  assert.equal(e.message, 'Dự án đang bận.');
  assert.equal(e.pipelineId, 7);
  assert.equal(e.jobId, 31);
});

test('requestJson does not crash on an error body without `error`', async () => {
  answer(500, '{"detail": "boom"}');
  const e = await caught(requestJson('/api/jobs'));
  assert.equal(e.code, 'loi_khong_ro');
  assert.equal(e.message, 'Máy chủ trả lỗi 500.');
  assert.equal(e.pipelineId, undefined);
  answer(500, '"boom"');
  assert.equal((await caught(requestJson('/api/jobs'))).code, 'loi_khong_ro');
});

test('requestJson maps an empty 502 from the proxy to "not connected"', async () => {
  answer(502, null);
  assert.equal((await caught(requestJson('/api/health'))).code, 'khong_ket_noi');
});

test('requestJson wraps an empty 204 / non-JSON 2xx body in ApiError', async () => {
  answer(204, null);
  assert.equal((await caught(requestJson('/api/jobs/1/cancel', { method: 'POST' }))).code, 'phan_hoi_khong_hop_le');
  answer(200, '<html>');
  assert.equal((await caught(requestJson('/api/health'))).code, 'phan_hoi_khong_hop_le');
});

test('requestJson turns a network failure into ApiError status 0', async () => {
  globalThis.fetch = async () => {
    throw new TypeError('Failed to fetch');
  };
  const e = await caught(requestJson('/api/health'));
  assert.equal(e.status, 0);
  assert.equal(e.code, 'khong_ket_noi');
});
