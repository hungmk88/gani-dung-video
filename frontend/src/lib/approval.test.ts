// Run with `npm test`.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  approveBody,
  canApprove,
  estimateVnd,
  formatRemaining,
  isExpired,
  suggestMaxVnd,
  validateMax,
} from './approval.ts';

// Numbers from docs/api-jobs.md §4b (draft job waiting for approval).
const WORST = 4165.58;
const CAP = 20000;
const NOW = Date.parse('2026-09-30T08:00:00Z');
const EXPIRES = '2026-09-30T09:31:00Z';

test('suggestMaxVnd = min(cap, 1.5 × worst) rounded up to 500đ', () => {
  assert.equal(suggestMaxVnd(WORST, CAP), 6500); // 6248.37 → 6500
  assert.equal(suggestMaxVnd(4000, CAP), 6000); // exactly 6000
});

test('suggestMaxVnd never exceeds the cap and never drops below one worst call', () => {
  assert.equal(suggestMaxVnd(18000, CAP), 20000);
  assert.equal(suggestMaxVnd(19990, 20000), 20000);
  assert.equal(suggestMaxVnd(120, CAP), 500); // 180 → up to 500, still ≥ 120
  assert.equal(suggestMaxVnd(4000, 5250), 5250); // capped, not rounded past the cap
});

test('estimateVnd rounds to whole đồng, as the server compares it', () => {
  assert.equal(estimateVnd(4165.58), 4166);
  assert.equal(estimateVnd(4165.4), 4165);
});

test('validateMax mirrors the server rules', () => {
  assert.equal(validateMax(6000, WORST, CAP), null);
  assert.equal(validateMax(4166, WORST, CAP), null); // one worst call is enough
  assert.match(validateMax(4000, WORST, CAP) ?? '', /Thấp hơn/);
  assert.match(validateMax(20001, WORST, CAP) ?? '', /Vượt trần/);
  assert.match(validateMax(0, WORST, CAP) ?? '', /lớn hơn 0/);
  assert.match(validateMax(-5, WORST, CAP) ?? '', /lớn hơn 0/);
  assert.match(validateMax(Number.NaN, WORST, CAP) ?? '', /lớn hơn 0/);
  assert.match(validateMax(6000.5, WORST, CAP) ?? '', /nguyên/);
});

test('expiry helpers', () => {
  assert.equal(isExpired(EXPIRES, NOW), false);
  assert.equal(isExpired(EXPIRES, Date.parse(EXPIRES)), true);
  assert.equal(isExpired(null, NOW), false);
  assert.equal(formatRemaining(EXPIRES, NOW), '01:31:00');
  assert.equal(formatRemaining(EXPIRES, Date.parse(EXPIRES) + 5000), '00:00:00');
  assert.equal(formatRemaining(null, NOW), '—');
});

const BASE = {
  maxVnd: 6500,
  worstCallVnd: WORST,
  capVnd: CAP,
  confirmedFor: 6500,
  expiresAt: EXPIRES,
  nowMs: NOW,
  submitting: false,
};

test('canApprove allows only a ticked, valid, unexpired, idle approval', () => {
  assert.deepEqual(canApprove(BASE), { ok: true, reason: null });
});

test('canApprove refuses without consent for THIS amount', () => {
  assert.equal(canApprove({ ...BASE, confirmedFor: null }).ok, false);
  // amount edited after ticking: consent no longer matches
  assert.equal(canApprove({ ...BASE, maxVnd: 7000 }).ok, false);
});

test('canApprove refuses invalid amounts, expiry and double submit', () => {
  assert.equal(canApprove({ ...BASE, maxVnd: 100, confirmedFor: 100 }).ok, false);
  assert.match(canApprove({ ...BASE, nowMs: Date.parse(EXPIRES) }).reason ?? '', /hết hạn/);
  assert.equal(canApprove({ ...BASE, submitting: true }).ok, false);
});

test('approveBody sends the shown estimate rounded, not the cap', () => {
  assert.deepEqual(approveBody(6500, WORST), { max_vnd: 6500, estimate_vnd: 4166 });
});
