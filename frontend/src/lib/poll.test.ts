// Run with `npm test` (plain node:test — no test-runner dependency).
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { startPolling } from './poll.ts';

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

function deferred(): { promise: Promise<void>; resolve: () => void } {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

test('startPolling never starts a request while the previous one is in flight', async () => {
  const pending: Array<() => void> = [];
  const stop = startPolling(() => {
    const d = deferred();
    pending.push(d.resolve);
    return d.promise;
  }, 2);
  await wait(30);
  assert.equal(pending.length, 1); // slow answer: no second request piled up behind it
  pending[0]();
  await wait(30);
  assert.ok(pending.length >= 2);
  stop();
  pending.forEach((r) => r());
});

test('startPolling stops: the signal aborts and an in-flight answer schedules nothing', async () => {
  let calls = 0;
  let seen: AbortSignal | undefined;
  const d = deferred();
  const stop = startPolling((signal) => {
    calls += 1;
    seen = signal;
    return d.promise;
  }, 2);
  stop();
  assert.equal(seen?.aborted, true);
  d.resolve();
  await wait(20);
  assert.equal(calls, 1);
});

test('startPolling keeps going after a failed tick', async () => {
  let calls = 0;
  const stop = startPolling(async () => {
    calls += 1;
    throw new Error('offline');
  }, 2);
  await wait(30);
  stop();
  assert.ok(calls >= 2);
});
